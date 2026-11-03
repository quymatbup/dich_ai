import streamlit as st
import easyocr
from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np
import pysrt, time, io, tempfile, os, subprocess, hashlib, re, html, json
import urllib.request, urllib.parse
from gtts import gTTS
from deep_translator import GoogleTranslator
import translators as ts

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title='Dịch Thuật AI', layout='wide', page_icon="logo.ico")

# --- CODE TÀNG HÌNH HUY HIỆU STREAMLIT ---
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

OCR_LANG_GROUPS = {
    "Tiếng Anh (Chuẩn nhất cho ảnh Tiếng Anh)": ['en'],
    "Tiếng Anh & Tiếng Việt": ['vi', 'en'],
    "Tiếng Trung (Giản thể) & Anh": ['ch_sim', 'en'],
    "Tiếng Trung (Phồn thể) & Anh": ['ch_tra', 'en'],
    "Tiếng Nhật & Anh": ['ja', 'en'],
    "Tiếng Hàn & Anh": ['ko', 'en'],
    "Tiếng Thái & Anh": ['th', 'en'],
    "Tiếng Nga & Anh": ['ru', 'en'],
    "Châu Âu (Pháp, Đức, TBN, Ý, BĐN)": ['fr', 'de', 'es', 'it', 'pt', 'en']
}

@st.cache_resource
def load_ocr(lang_tuple=('en',)):
    return easyocr.Reader(list(lang_tuple))

@st.cache_resource
def load_whisper_model():
    import whisper
    return whisper.load_model("base")

LANGUAGES = {
    'Vietnamese': 'vi', 'English': 'en', 'Chinese (Simplified)': 'zh-CN',
    'Chinese (Traditional)': 'zh-TW', 'Japanese': 'ja', 'Korean': 'ko',
    'French': 'fr', 'German': 'de', 'Spanish': 'es', 'Russian': 'ru',
    'Thai': 'th', 'Lao': 'lo', 'Khmer': 'km', 'Indonesian': 'id',
    'Malay': 'ms', 'Italian': 'it', 'Portuguese': 'pt', 'Arabic': 'ar',
    'Hindi': 'hi', 'Turkish': 'tr', 'Dutch': 'nl', 'Polish': 'pl'
}
lang_names = list(LANGUAGES.keys())

# --- CÁC HÀM XỬ LÝ VIDEO & PHỤ ĐỀ ---
def generate_subtitles(subs):
    vtt, srt = "WEBVTT\n\n", ""
    for i, sub in enumerate(subs):
        sv = f"{sub.start.hours:02d}:{sub.start.minutes:02d}:{sub.start.seconds:02d}.{sub.start.milliseconds:03d}"
        ev = f"{sub.end.hours:02d}:{sub.end.minutes:02d}:{sub.end.seconds:02d}.{sub.end.milliseconds:03d}"
        vtt += f"{sv} --> {ev}\n{sub.text}\n\n"
        ss = sv.replace(".", ",")
        es = ev.replace(".", ",")
        srt += f"{i+1}\n{ss} --> {es}\n{sub.text}\n\n"
    return vtt, srt

def hardsub_video(mp4_bytes, srt_string):
    tdir = tempfile.mkdtemp()
    v_p, s_p, o_p = os.path.join(tdir, "in.mp4"), os.path.join(tdir, "s.srt"), os.path.join(tdir, "out.mp4")
    with open(v_p, "wb") as f: f.write(mp4_bytes)
    with open(s_p, "w", encoding="utf-8") as f: f.write(srt_string)
    try:
        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "-vf", "subtitles=s.srt", "-c:a", "copy", "-preset", "fast", "out.mp4"]
        subprocess.run(cmd, cwd=tdir, check=True, capture_output=True)
        with open(o_p, "rb") as f: return f.read()
    except Exception:
        return None
    finally:
        for p in [v_p, s_p, o_p]:
            if os.path.exists(p): os.remove(p)
        try: os.rmdir(tdir)
        except Exception: pass

def remove_vn_accents(txt):
    pats = {
        '[àáảãạăắằẳẵặâấầẩẫậ]': 'a', '[ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬ]': 'A',
        '[èéẻẽẹêếềểễệ]': 'e', '[ÈÉẺẼẸÊẾỀỂỄỆ]': 'E',
        '[ìíỉĩị]': 'i', '[ÌÍỈĨỊ]': 'I',
        '[òóỏõọôốồổỗộơớờởỡợ]': 'o', '[ÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ]': 'O',
        '[ùúủũụưứừửữự]': 'u', '[ÙÚỦŨỤƯỨỪỬỮỰ]': 'U',
        '[ỳýỷỹỵ]': 'y', '[ỲÝỶỸỴ]': 'Y', '[đ]': 'd', '[Đ]': 'D'
    }
    for r, rep in pats.items(): txt = re.sub(r, rep, txt)
    return txt

# --- NHẬN DIỆN NGÔN NGỮ & DỊCH ĐOẠN VĂN CHUẨN XÁC ---
def detect_lang_code(text, tgt='vi'):
