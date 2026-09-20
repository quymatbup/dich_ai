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
import whisper
from audio_recorder_streamlit import audio_recorder
from deep_translator import GoogleTranslator, MyMemoryTranslator

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title='Siêu App Dịch Thuật AI', layout='wide', page_icon='🌍')

# --- TẢI MÔ HÌNH (CACHE) ---
@st.cache_resource
def load_ocr():
    return easyocr.Reader(['vi', 'en'])

@st.cache_resource
def load_whisper():
    return whisper.load_model("base")

reader = load_ocr()
whisper_model = load_whisper()

# --- TỰ ĐỘNG LẤY DANH SÁCH 130+ NGÔN NGỮ ---
@st.cache_data
def get_all_languages():
    # Lấy danh sách từ Google Translator (đầy đủ nhất thế giới)
    langs_dict = GoogleTranslator().get_supported_languages(as_dict=True)
    # Viết hoa chữ cái đầu cho đẹp
    return {k.title(): v for k, v in langs_dict.items()}

SUPPORTED_LANGUAGES = get_all_languages()
lang_names = sorted(list(SUPPORTED_LANGUAGES.keys()))

# --- HÀM DỊCH SIÊU BỀN (CHỐNG CHẶN IP TRÊN WEB) ---
def smart_translate(text, tgt_code):
    if not text or not text.strip(): return text
    try:
        # Ưu tiên 1: Google (Nhanh và chuẩn)
        return GoogleTranslator(source='auto', target=tgt_code).translate(text)
    except:
        try:
            # Ưu tiên 2: MyMemory (Bản dự phòng khi Google chặn IP server)
            return MyMemoryTranslator(source='auto', target=tgt_code).translate(text)
        except:
            return text

# --- CÁC HÀM XỬ LÝ PHỤ ĐỀ ---
def generate_subtitles(subs):
    vtt = "WEBVTT\n\n"
    srt = ""
    for i, sub in enumerate(subs):
        start = f"{sub.start.hours:02d}:{sub.start.minutes:02d}:{sub.start.seconds:02d}"
        end = f"{sub.end.hours:02d}:{sub.end.minutes:02d}:{sub.end.seconds:02d}"
        vtt += f"{start}.{sub.start.milliseconds:03d} --> {end}.{sub.end.milliseconds:03d}\n{sub.text}\n\n"
        srt += f"{i+1}\n{start},{sub.start.milliseconds:03d} --> {end},{sub.end.milliseconds:03d}\n{sub.text}\n\n"
    return vtt, srt

# --- GIAO DIỆN ---
st.title("🛡️ Siêu App Dịch Thuật AI Toàn Diện")
tab1, tab2, tab3 = st.tabs(["📝 Văn bản & Giọng nói", "📸 Dịch Ảnh", "🎬 Dịch Phim AI"])

# TAB 1: VĂN BẢN & GIỌNG NÓI
with tab1:
    c1, c2 = st.columns(2)
    if "speech_text" not in st.session_state: st.session_state.speech_text = ""
    
    with c1:
        st.write("🎙️ **Ghi âm giọng nói:**")
        audio_data = audio_recorder(text="Nhấn để nói", recording_color="#ff4b4b")
        if audio_data:
            with st.spinner("AI đang nghe..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                    tmp.write(audio_data)
                    res = whisper_model.transcribe(tmp.name)
                    st.session_state.speech_text = res['text']
                os.remove(tmp.name)
        
        text_in = st.text_area("Văn bản nguồn:", value=st.session_state.speech_text, height=150)
    
    with c2:
        target_lang = st.selectbox("Dịch sang ngôn ngữ:", lang_names, index=lang_names.index('Vietnamese'))
        if st.button("CHUYỂN NGỮ & PHÁT ÂM"):
            tgt_code = SUPPORTED_LANGUAGES[target_lang]
            translated = smart_translate(text_in, tgt_code)
            st.success(translated)
            # Phát âm
            try:
                tts = gTTS(text=translated, lang=tgt_code.split('-')[0])
                fp = io.BytesIO()
                tts.write_to_fp(fp)
                st.audio(fp)
            except: st.warning("Ngôn ngữ này không hỗ trợ phát âm.")

# TAB 2: DỊCH ẢNH (OCR)
with tab2:
    up_img = st.file_uploader("Tải ảnh lên:", type=['jpg','png','jpeg'])
    if up_img:
        img = Image.open(up_img).convert("RGB")
        if st.button("QUÉT & DỊCH TRỰC TIẾP"):
            with st.spinner("AI đang đọc chữ..."):
                draw = ImageDraw.Draw(img)
                results = reader.readtext(np.array(img))
                for (bbox, text, prob) in results:
                    if prob > 0.2:
                        p1 = tuple(map(int, bbox[0]))
                        trans = smart_translate(text, 'vi') # Mặc định dịch ảnh sang tiếng Việt
                        draw.text(p1, trans, fill="red")
                st.image(img, use_container_width=True)

# TAB 3: DỊCH PHIM (BẢN FIX LỖI SERVER)
with tab3:
    if "srt_final" not in st.session_state: st.session_state.srt_final = None
    if "vtt_final" not in st.session_state: st.session_state.vtt_final = None

    up_vid = st.file_uploader("Tải video (.mp4):", type=['mp4'])
    if up_vid:
        st.video(up_vid)
        tgt_vid_lang = st.selectbox("Chọn ngôn ngữ phụ đề:", lang_names, index=lang_names.index('Vietnamese'), key="vid_lang")
        
        if st.button("🤖 BẮT ĐẦU BÓC BĂNG & DỊCH PHIM"):
            with st.spinner("AI đang nghe video (Whisper)..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    tmp.write(up_vid.getvalue())
                    result = whisper_model.transcribe(tmp.name)
                os.remove(tmp.name)
                
                subs = pysrt.SubRipFile()
                tgt_code = SUPPORTED_LANGUAGES[tgt_vid_lang]
                
                progress = st.progress(0)
                for i, seg in enumerate(result['segments']):
                    item = pysrt.SubRipItem(index=i+1)
                    item.start.seconds = seg['start']
                    item.end.seconds = seg['end']
                    # Dịch từng câu
                    item.text = smart_translate(seg['text'], tgt_code)
                    subs.append(item)
                    progress.progress((i + 1) / len(result['segments']))
                    time.sleep(0.1) # Tránh bị server chặn vì yêu cầu quá nhanh
                
                vtt, srt = generate_subtitles(subs)
                st.session_state.srt_final = srt
                st.session_state.vtt_final = vtt
                st.success("✅ Đã xử lý xong!")

        if st.session_state.srt_final:
            st.markdown("### Kết quả:")
            st.video(up_vid, subtitles={"Dịch": st.session_state.vtt_final})
            st.download_button("📥 Tải tệp Phụ đề (.srt)", st.session_state.srt_final, "subtitles.srt")
