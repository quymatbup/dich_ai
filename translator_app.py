import streamlit as st
import easyocr
from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np
import pysrt
import time
from gtts import gTTS
import io
import tempfile
import os
import subprocess
import hashlib
import re
import html
import json
import urllib.request
import urllib.parse
from audio_recorder_streamlit import audio_recorder
import translators as ts

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(
    page_title='Dịch Thuật AI',
    layout='wide',
    page_icon="logo.ico"
)

# --- TỐI ƯU CACHE CHO OCR & WHISPER ---
OCR_LANG_GROUPS = {
    "Tiếng Anh (Chuẩn nhất cho ảnh Tiếng Anh)": ['en'],
    "Tiếng Anh & Tiếng Việt": ['vi', 'en'],
    "Tiếng Trung (Giản thể) & Anh": ['ch_sim', 'en'],
    "Tiếng Trung (Phồn thể) & Anh": ['ch_tra', 'en'],
    "Tiếng Nhật & Anh": ['ja', 'en'],
    "Tiếng Hàn & Anh": ['ko', 'en'],
    "Tiếng Thái & Anh": ['th', 'en'],
    "Tiếng Nga & Anh": ['ru', 'en'],
    "Châu Âu (Pháp, Đức, Tây Ban Nha, Ý, Bồ Đào Nha)": ['fr', 'de', 'es', 'it', 'pt', 'en']
}

@st.cache_resource
def load_ocr(lang_tuple=('en',)):
    return easyocr.Reader(list(lang_tuple))

@st.cache_resource
def load_whisper_model():
    import whisper
    return whisper.load_model("base")

# --- DANH SÁCH NGÔN NGỮ DỊCH ---
LANGUAGES = {
    'Vietnamese': 'vi', 'English': 'en', 'Chinese (Simplified)': 'zh-CHS', 'Chinese (Traditional)': 'zh-CHT',
    'Japanese': 'ja', 'Korean': 'ko', 'French': 'fr', 'German': 'de', 'Spanish': 'es', 'Russian': 'ru',
    'Thai': 'th', 'Lao': 'lo', 'Khmer': 'km', 'Indonesian': 'id', 'Malay': 'ms', 'Italian': 'it',
    'Portuguese': 'pt', 'Arabic': 'ar', 'Hindi': 'hi', 'Turkish': 'tr', 'Dutch': 'nl', 'Polish': 'pl'
}
lang_names = list(LANGUAGES.keys())

# --- CÁC HÀM XỬ LÝ VIDEO & PHỤ ĐỀ ---
def generate_subtitles(subs):
    vtt_content = "WEBVTT\n\n"
    srt_content = ""
    for i, sub in enumerate(subs):
        start_vtt = f"{sub.start.hours:02d}:{sub.start.minutes:02d}:{sub.start.seconds:02d}.{sub.start.milliseconds:03d}"
        end_vtt = f"{sub.end.hours:02d}:{sub.end.minutes:02d}:{sub.end.seconds:02d}.{sub.end.milliseconds:03d}"
        vtt_content += f"{start_vtt} --> {end_vtt}\n{sub.text}\n\n"

        start_srt = f"{sub.start.hours:02d}:{sub.start.minutes:02d}:{sub.start.seconds:02d},{sub.start.milliseconds:03d}"
        end_srt = f"{sub.end.hours:02d}:{sub.end.minutes:02d}:{sub.end.seconds:02d},{sub.end.milliseconds:03d}"
        srt_content += f"{i+1}\n{start_srt} --> {end_srt}\n{sub.text}\n\n"
    return vtt_content, srt_content

def hardsub_video(mp4_bytes, srt_string):
    temp_dir = tempfile.mkdtemp()
    vid_path = os.path.join(temp_dir, "input.mp4")
    srt_path = os.path.join(temp_dir, "sub.srt")
    out_path = os.path.join(temp_dir, "output.mp4")

    with open(vid_path, "wb") as f_vid:
        f_vid.write(mp4_bytes)
    with open(srt_path, "w", encoding="utf-8") as f_srt:
        f_srt.write(srt_string)

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", "input.mp4", "-vf", "subtitles=sub.srt", "-c:a", "copy", "-preset", "fast", "output.mp4"],
            cwd=temp_dir, check=True, capture_output=True
        )
        with open(out_path, "rb") as f:
            out_bytes = f.read()
        return out_bytes
    except Exception:
        return None
    finally:
        for p in [vid_path, srt_path, out_path]:
            if os.path.exists(p):
                os.remove(p)
        try:
            os.rmdir(temp_dir)
        except Exception:
            pass

# --- HÀM TỰ ĐỘNG GỌT DẤU TIẾNG VIỆT ---
def remove_vn_accents(txt):
    patterns = {
        '[àáảãạăắằẳẵặâấầẩẫậ]': 'a', '[ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬ]': 'A',
        '[èéẻẽẹêếềểễệ]': 'e', '[ÈÉẺẼẸÊẾỀỂỄỆ]': 'E',
        '[ìíỉĩị]': 'i', '[ÌÍỈĨỊ]': 'I',
        '[òóỏõọôốồổỗộơớờởỡợ]': 'o', '[ÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ]': 'O',
        '[ùúủũụưứừửữự]': 'u', '[ÙÚỦŨỤƯỨỪỬỮỰ]': 'U',
        '[ỳýỷỹỵ]': 'y', '[ỲÝỶỸỴ]': 'Y',
        '[đ]': 'd', '[Đ]': 'D'
    }
    for regex, replace in patterns.items():
        txt = re.sub(regex, replace, txt)
    return txt

# --- HÀM DỊCH CHUẨN XÁC (ƯU TIÊN GOOGLE GTX + KHỬ LỖI &#39;) ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip():
        return text

    # Chuẩn hóa dấu nháy cong và ký tự lạ trước khi dịch
    clean_text = html.unescape(text.strip()).replace("’", "'").replace("‘", "'")

    # 1. Xử lý mã ngôn ngữ cho Google Translate API (Chuẩn & Mượt nhất)
    g_tgt = tgt
    if tgt in ['zh-CN', 'zh-CHS', 'zh-Hans']:
        g_tgt = 'zh-CN'
    elif tgt in ['zh-TW', 'zh-CHT', 'zh-Hant']:
        g_tgt = 'zh-TW'
    g_src = src if src != 'auto' else 'auto'

    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single?client=gtx"
            f"&sl={g_src}&tl={g_tgt}&dt=t&q={urllib.parse.quote(clean_text)}"
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            translated = "".join([part[0] for part in data[0] if part[0]])
            return html.unescape(translated)
    except Exception:
        pass

    # 2. Dự phòng bằng Bing & Alibaba nếu Google bị nghẽn
    bing_tgt = 'zh-Hans' if tgt in ['zh-CN', 'zh-CHS'] else ('zh-Hant' if tgt in ['zh-TW', 'zh-CHT'] else tgt)
