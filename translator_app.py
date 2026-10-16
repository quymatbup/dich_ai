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
from deep_translator import GoogleTranslator

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
    "Châu Âu (Pháp, Đức, Tây Ban Nha, Ý, Bồ Đào Nha)": [
        'fr', 'de', 'es', 'it', 'pt', 'en'
    ]
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
    'Vietnamese': 'vi',
    'English': 'en',
    'Chinese (Simplified)': 'zh-CN',
    'Chinese (Traditional)': 'zh-TW',
    'Japanese': 'ja',
    'Korean': 'ko',
    'French': 'fr',
    'German': 'de',
    'Spanish': 'es',
    'Russian': 'ru',
    'Thai': 'th',
    'Lao': 'lo',
    'Khmer': 'km',
    'Indonesian': 'id',
    'Malay': 'ms',
    'Italian': 'it',
    'Portuguese': 'pt',
    'Arabic': 'ar',
    'Hindi': 'hi',
    'Turkish': 'tr',
    'Dutch': 'nl',
    'Polish': 'pl'
}
lang_names = list(LANGUAGES.keys())

# --- CÁC HÀM XỬ LÝ VIDEO & PHỤ ĐỀ ---
def generate_subtitles(subs):
    vtt_content = "WEBVTT\n\n"
    srt_content = ""
    for i, sub in enumerate(subs):
        s_vtt = (
            f"{sub.start.hours:02d}:{sub.start.minutes:02d}:"
            f"{sub.start.seconds:02d}.{sub.start.milliseconds:03d}"
        )
        e_vtt = (
            f"{sub.end.hours:02d}:{sub.end.minutes:02d}:"
            f"{sub.end.seconds:02d}.{sub.end.milliseconds:03d}"
        )
        vtt_content += f"{s_vtt} --> {e_vtt}\n{sub.text}\n\n"

        s_srt = (
            f"{sub.start.hours:02d}:{sub.start.minutes:02d}:"
            f"{sub.start.seconds:02d},{sub.start.milliseconds:03d}"
        )
        e_srt = (
            f"{sub.end.hours:02d}:{sub.end.minutes:02d}:"
            f"{sub.end.seconds:02d},{sub.end.milliseconds:03d}"
        )
        srt_content += f"{i+1}\n{s_srt} --> {e_srt}\n{sub.text}\n\n"
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
        cmd = [
            "ffmpeg", "-y", "-i", "input.mp4",
            "-vf", "subtitles=sub.srt",
            "-c:a", "copy", "-preset", "fast", "output.mp4"
        ]
        subprocess.run(
            cmd, cwd=temp_dir, check=True, capture_output=True
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

def remove_vn_accents(txt):
    patterns = {
        '[àáảãạăắằẳẵặâấầẩẫậ]': 'a',
        '[ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬ]': 'A',
        '[èéẻẽẹêếềểễệ]': 'e',
        '[ÈÉẺẼẸÊẾỀỂỄỆ]': 'E',
        '[ìíỉĩị]': 'i',
        '[ÌÍỈĨỊ]': 'I',
        '[òóỏõọôốồổỗộơớờởỡợ]': 'o',
        '[ÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ]': 'O',
        '[ùúủũụưứừửữự]': 'u',
        '[ÙÚỦŨỤƯỨỪỬỮỰ]': 'U',
        '[ỳýỷỹỵ]': 'y',
        '[ỲÝỶỸỴ]': 'Y',
        '[đ]': 'd',
        '[Đ]': 'D'
    }
    for regex, replace in patterns.items():
        txt = re.sub(regex, replace, txt)
    return txt

# --- NHẬN DIỆN NGÔN NGỮ GỐC CHÍNH XÁC (CHỐNG NHẦM TÊN RIÊNG) ---
def detect_lang_code(text, tgt='vi'):
    vn_chars = (
        r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩị'
        r'òóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ'
        r'ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊ'
        r'ÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴĐ]'
    )
    if re.search(vn_chars, text):
        return 'vi'
    if re.search(r'[\u3040-\u30ff]', text):
        return 'ja'
    if re.search(r'[\uac00-\ud7af]', text):
        return 'ko'
    if re.search(r'[\u4e00-\u9fff]', text):
        return 'zh-CN'
    if re.search(r'[\u0e00-\u0e7f]', text):
        return 'th'
    if re.search(r'[\u0400-\u04ff]', text):
        return 'ru'
    return 'en' if tgt != 'en' else 'vi'

def is_valid_translation(orig, trans, src, tgt):
    if not trans or not trans.strip():
        return False
    t_clean = trans.strip()
    o_clean = orig.strip()
    if src != tgt and len(o_clean) > 4:
        if t_clean.lower() == o_clean.lower():
            return False
    return True

# --- HỆ THỐNG DỊCH 4 CỤM MÁY CHỦ CHUẨN (ĐÃ LOẠI BỎ ALIBABA) ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip():
        return text

    clean_text = html.unescape(text.strip())
    clean_text = clean_text.replace("’", "'").replace("‘", "'")

    g_tgt = tgt
    if tgt in ['zh-CN', 'zh-CHS', 'zh-Hans']:
        g_tgt = 'zh-CN'
    elif tgt in ['zh-TW', 'zh-CHT', 'zh-Hant']:
        g_tgt = 'zh-TW'

    real_src = detect_lang_code(clean_text, g_tgt) if src == 'auto' else src
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # 1. Cụm máy chủ Google Chrome Extension API (POST - Không giới hạn độ dài)
    try:
        url1 = (
            f"https://clients5.google.com/translate_a/t"
            f"?client=dict-chrome-ex&sl={real_src}&tl={g_tgt}&dt=t"
        )
        data_bytes = urllib.parse.urlencode({'q': clean_text}).encode('utf-8')
        req1 = urllib.request.Request(url1, data=data_bytes, headers=headers)
        with urllib.request.urlopen(req1, timeout=8) as resp:
            res_json = json.loads(resp.read().decode('utf-8'))
            out1 = ""
            if isinstance(res_json, list) and res_json:
                if isinstance(res_json[0], list):
                    out1 = "".join([x[0] for x in res_json if x and x[0]])
                elif isinstance(res_json[0], str):
                    out1 = res_json[0]
            out1 = html.unescape(out1.strip())
            if is_valid_translation(clean_text, out1, real_src, g_tgt):
                return out1
    except Exception:
        pass

    # 2. Cụm máy chủ Google GTX API (POST - Truyền nguyên đoạn văn)
    try:
        url2 = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={real_src}&tl={g_tgt}&dt=t"
        )
        data_bytes = urllib.parse.urlencode({'q': clean_text}).encode('utf-8')
        req2 = urllib.request.Request(url2, data=data_bytes, headers=headers)
        with urllib.request.urlopen(req2, timeout=8) as resp:
            res_json = json.loads(resp.read().decode('utf-8'))
            out2 = "".join([p[0] for p in res_json[0] if p and p[0]])
            out2 = html.unescape(out2.strip())
            if is_valid_translation(clean_text, out2, real_src, g_tgt):
                return out2
    except Exception:
        pass

    # 3. Cụm máy chủ Lingva Proxy (Vượt tường lửa khi IP Streamlit bị nghẽn)
    lingva_hosts = [
        "https://translate.plausibility.cloud",
        "https://lingva.lunar.icu"
    ]
    l_src = 'zh' if 'zh' in real_src else real_src
    l_tgt = 'zh' if 'zh' in g_tgt else g_tgt
    for host in lingva_hosts:
        try:
            q_enc = urllib.parse.quote(clean_text)
            url3 = f"{host}/api/v1/{l_src}/{l_tgt}/{q_enc}"
            req3 = urllib.request.Request(url3, headers=headers)
            with urllib.request.urlopen(req3, timeout=8) as resp:
                res_json = json.loads(resp.read().decode('utf-8'))
                out3 = html.unescape(res_json.get("translation", "").strip())
                if is_valid_translation(clean_text, out3, real_src, g_tgt):
                    return out3
        except Exception:
            pass

    # 4. Cụm máy chủ MyMemory Developer API (Có chia câu nếu đoạn > 400 ký tự)
    try:
        def mm_translate_piece(piece):
            piece = piece.strip()
            if not piece:
                return ""
            email = f"dev_{abs(hash(piece)) % 99999}@gmail.com"
            q_enc = urllib.parse.quote(piece)
            url4 = (
                f"https://api.mymemory.translated.net/get?q={q_enc}"
                f"&langpair={real_src}|{g_tgt}&de={email}"
            )
            req4 = urllib.request.Request(url4, headers=headers)
            with urllib.request.urlopen(req4, timeout=8) as resp:
                data4 = json.loads(resp.read().decode('utf-8'))
                t_txt = data4.get("responseData", {}).get("translatedText", "")
