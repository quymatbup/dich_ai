import streamlit as st
import easyocr
from PIL import Image, ImageDraw, ImageFont
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
from audio_recorder_streamlit import audio_recorder
# Thay đổi thư viện dịch thuật để bền hơn trên Web
from deep_translator import GoogleTranslator, MyMemoryTranslator

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(
    page_title='Dịch Thuật AI', 
    layout='wide', 
    page_icon="https://cdn-icons-png.flaticon.com/512/5968/5968812.png"
)

@st.cache_resource
def load_ocr():
    return easyocr.Reader(['vi', 'en'])

@st.cache_resource
def load_whisper():
    import whisper
    return whisper.load_model("base")

reader = load_ocr()
whisper_model = load_whisper()

# --- DANH SÁCH NGÔN NGỮ ---
LANGUAGES = {
    'Vietnamese': 'vi', 'English': 'en', 'French': 'fr', 'Japanese': 'ja', 
    'Korean': 'ko', 'Chinese (Simplified)': 'zh-CN', 'Thai': 'th'
}
lang_names = list(LANGUAGES.keys())

# --- HÀM TỰ ĐỘNG DỊCH (BẢN SIÊU BỀN CHO WEB) ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip(): return text
    # Chuẩn hóa mã ngôn ngữ cho deep_translator
    src_code = 'auto' if src == 'Auto Detect' or src == 'auto' else src
    try:
        # Thử dịch bằng Google (Ưu tiên 1)
        return GoogleTranslator(source=src_code, target=tgt).translate(text)
    except:
        try:
            # Nếu Google chặn, chuyển sang MyMemory (Ưu tiên 2)
            return MyMemoryTranslator(source=src_code, target=tgt).translate(text)
        except:
            return text # Thất bại hết thì trả về chữ gốc

# --- CÁC HÀM XỬ LÝ KHÁC (GIỮ NGUYÊN LOGIC CỦA BẠN) ---
def generate_subtitles(subs):
    vtt_content = "WEBVTT\n\n"
    srt_content = ""
    for i, sub in enumerate(subs):
        start_vtt = f"{sub.start.hours:02d}:{sub.start.minutes:02d}:{sub.start.seconds:02d}.{sub.start.milliseconds:03d}"
        end_vtt = f"{sub.end.hours:02d}:{sub.end.minutes:02d}:{sub.end.seconds:02d}.{sub.end.milliseconds:03d}"
        vtt_content += f"{start_vtt} --> {end_vtt}\n{sub.text}\n\n"
        srt_content += f"{i+1}\n{start_vtt.replace('.', ',')} --> {end_vtt.replace('.', ',')}\n{sub.text}\n\n"
    return vtt_content, srt_content

def hardsub_video(mp4_bytes, srt_string):
    with tempfile.TemporaryDirectory() as temp_dir:
        vid_path = os.path.join(temp_dir, "input.mp4")
        srt_path = os.path.join(temp_dir, "sub.srt")
        out_path = os.path.join(temp_dir, "output.mp4")
        with open(vid_path, "wb") as f: f.write(mp4_bytes)
        with open(srt_path, "w", encoding="utf-8") as f: f.write(srt_string)
        try:
            subprocess.run(["ffmpeg", "-y", "-i", vid_path, "-vf", f"subtitles={srt_path}", "-c:a", "copy", out_path], check=True)
            with open(out_path, "rb") as f: return f.read()
        except: return None

def speak(text, lang_code):
    try:
        tts = gTTS(text=text, lang=lang_code.split('-')[0])
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp
    except: return None

# --- GIAO DIỆN CHÍNH ---
st.title("🛡️ Siêu App Dịch Thuật AI Toàn Diện")
tab1, tab2, tab3 = st.tabs(["📝 Dịch Văn Bản", "📸 Dịch hình ảnh", "🎬 Dịch Phim AI"])

# TAB 1: DỊCH VĂN BẢN
with tab1:
    c1, c2 = st.columns(2)
    if "txt_in" not in st.session_state: st.session_state.txt_in = ""
    with c1:
        st.write("🎙️ **Ghi âm:**")
        audio_bytes = audio_recorder(text="Bấm để nói", recording_color="#ff4b4b")
        if audio_bytes:
            with st.spinner("AI đang nghe..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                    tmp.write(audio_bytes)
                    res = whisper_model.transcribe(tmp.name)
                    st.session_state.txt_in = res['text']
                os.remove(tmp.name)
        
        text_in = st.text_area("Văn bản gốc:", value=st.session_state.txt_in, height=150)
        src_lang = st.selectbox("Từ:", ["auto"] + lang_names)
    
    with c2:
        tgt_lang = st.selectbox("Sang:", lang_names, index=0)
        if st.button("DỊCH NGAY"):
            res = smart_translate(text_in, src_lang, LANGUAGES[tgt_lang])
            st.success(res)
            st.audio(speak(res, LANGUAGES[tgt_lang]))

# TAB 2: DỊCH ẢNH (OCR)
with tab2:
    up_file = st.file_uploader("Tải ảnh:", type=['jpg','png','jpeg'])
    if up_file:
        img = Image.open(up_file).convert("RGB")
        if st.button("QUÉT & DỊCH"):
            with st.spinner("Đang xử lý ảnh..."):
                draw = ImageDraw.Draw(img)
                result = reader.readtext(np.array(img))
                for (bbox, text, prob) in result:
                    if prob > 0.2:
                        p1 = tuple(map(int, bbox[0]))
                        trans = smart_translate(text, 'auto', 'vi')
                        draw.text(p1, trans, fill="red")
                st.image(img, use_container_width=True)

# TAB 3: DỊCH PHIM (FIX LỖI DOWNLOAD)
with tab3:
    if "srt_out" not in st.session_state: st.session_state.srt_out = None
    if "vtt_out" not in st.session_state: st.session_state.vtt_out = None

    up_vid = st.file_uploader("Tải video (.mp4):", type=['mp4'])
    if up_vid:
        st.video(up_vid)
        target_lang_srt = st.selectbox("Dịch sub sang:", lang_names, key="sub_lang")
        
        if st.button("🤖 BẮT ĐẦU DỊCH PHIM"):
            with st.spinner("AI đang bóc băng & dịch..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    tmp.write(up_vid.getvalue())
                    result = whisper_model.transcribe(tmp.name)
                os.remove(tmp.name)
                
                subs = pysrt.SubRipFile()
                for i, seg in enumerate(result['segments']):
                    item = pysrt.SubRipItem(index=i+1)
                    item.start.seconds = seg['start']
                    item.end.seconds = seg['end']
                    item.text = smart_translate(seg['text'], 'auto', LANGUAGES[target_lang_srt])
                    subs.append(item)
                    time.sleep(0.1) # Tránh bị block IP
                
                vtt, srt = generate_subtitles(subs)
                st.session_state.srt_out = srt
                st.session_state.vtt_out = vtt
                st.success("Xong! Tải về bên dưới.")

        if st.session_state.srt_out:
            st.download_button("📥 Tải phụ đề (.srt)", st.session_state.srt_out, "sub.srt")
