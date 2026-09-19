import streamlit as st
import easyocr
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import pysrt
import time
from gtts import gTTS
import io
import translators as ts
import tempfile
import os
import subprocess
import hashlib
import re
from audio_recorder_streamlit import audio_recorder

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(
    page_title='Dịch Thuật AI', 
    layout='wide', 
    page_icon="logo.ico"
)

@st.cache_resource
def load_ocr():
    return easyocr.Reader(['vi', 'en'])

reader = load_ocr()

# --- DANH SÁCH BING (GẦN 100 NGÔN NGỮ) ---
LANGUAGES = {
    'Vietnamese': 'vi', 'English': 'en', 'Chinese (Simplified)': 'zh-Hans', 'Chinese (Traditional)': 'zh-Hant',
    'Japanese': 'ja', 'Korean': 'ko', 'French': 'fr', 'Spanish': 'es', 'Russian': 'ru', 'German': 'de',
    'Thai': 'th', 'Indonesian': 'id', 'Italian': 'it', 'Portuguese': 'pt', 'Arabic': 'ar', 'Hindi': 'hi', 
    'Dutch': 'nl', 'Turkish': 'tr', 'Polish': 'pl', 'Filipino': 'fil', 'Malay': 'ms', 'Swedish': 'sv', 
    'Greek': 'el', 'Czech': 'cs', 'Danish': 'da', 'Finnish': 'fi', 'Norwegian': 'no', 'Hungarian': 'hu', 
    'Romanian': 'ro', 'Ukrainian': 'uk', 'Bulgarian': 'bg', 'Croatian': 'hr', 'Serbian': 'sr-Cyrl', 
    'Slovak': 'sk', 'Slovenian': 'sl', 'Estonian': 'et', 'Latvian': 'lv', 'Lithuanian': 'lt', 'Hebrew': 'he'
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
    
    with open(vid_path, "wb") as f_vid: f_vid.write(mp4_bytes)
    with open(srt_path, "w", encoding="utf-8") as f_srt: f_srt.write(srt_string)
    
    try:
        subprocess.run(["ffmpeg", "-y", "-i", "input.mp4", "-vf", "subtitles=sub.srt", "-c:a", "copy", "-preset", "fast", "output.mp4"], cwd=temp_dir, check=True, capture_output=True)
        with open(out_path, "rb") as f: out_bytes = f.read()
        return out_bytes
    except Exception as e: return None
    finally:
        if os.path.exists(vid_path): os.remove(vid_path)
        if os.path.exists(srt_path): os.remove(srt_path)
        if os.path.exists(out_path): os.remove(out_path)
        try: os.rmdir(temp_dir)
        except: pass

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

# --- HÀM DỊCH QUA MICROSOFT BING ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text.strip(): return text
    try:
        src_bing = 'auto-detect' if src == 'auto' else src
        time.sleep(0.1)
        return ts.translate_text(text, translator='bing', from_language=src_bing, to_language=tgt)
    except: return text

# --- HÀM PHÁT ÂM ---
def speak(text, lang_code):
    try:
        if lang_code == 'en' or lang_code.startswith('en'):
            text = remove_vn_accents(text)
            tu_dien = {
                "Quy": "Kwee", "quy": "kwee",
                "Phu": "Foo", "phu": "foo",
                "Nguyen": "Nwin", "nguyen": "nwin"
            }
            for tu_goc, tu_moi in tu_dien.items():
                text = text.replace(tu_goc, tu_moi)

        tts_lang = lang_code.split('-')[0]
        if 'zh' in lang_code: tts_lang = 'zh-CN'
        tts = gTTS(text=text, lang=tts_lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp
    except: return None

st.title("🛡️ Siêu App Dịch Thuật AI Toàn Diện")
tab1, tab2, tab3 = st.tabs(["📝 Dịch Văn Bản", "📸 Google Lens (Dịch Đè)", "🎬 Phụ Đề Phim & Tách Lời AI"])

# ==========================================
# TAB 1: DỊCH VĂN BẢN (GHI ÂM MICRO)
# ==========================================
with tab1:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Nhập liệu")
        
        if "txt_in" not in st.session_state:
            st.session_state.txt_in = ""
            
        st.write("🎙️ **Đọc bằng Micro:**")
        audio_bytes = audio_recorder(text="Bấm vào mic để nói", recording_color="#ff4b4b", neutral_color="#888888")
        
        if audio_bytes:
            audio_hash = hashlib.md5(audio_bytes).hexdigest()
            if "last_audio" not in st.session_state or st.session_state.last_audio != audio_hash:
                st.session_state.last_audio = audio_hash
                with st.spinner("AI đang nghe và gõ chữ..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_audio:
                        tmp_audio.write(audio_bytes)
                        tmp_path = tmp_audio.name
                    try:
                        import whisper
                        model = whisper.load_model("base")
                        res = model.transcribe(tmp_path)
                        st.session_state.txt_in = res['text'].strip()
                    except Exception as e:
                        st.error("Lỗi khi nghe giọng nói!")
                    finally:
                        if os.path.exists(tmp_path): os.remove(tmp_path)
                if hasattr(st, "rerun"): st.rerun()
                else: st.experimental_rerun()

        text_in = st.text_area("Hoặc gõ văn bản gốc vào đây:", value=st.session_state.txt_in, key="txt_in_area", height=150)
        source_lang = st.selectbox("Từ:", ["Auto Detect"] + lang_names, key="src_lang")
        
        if st.button("🔊 Nghe bản gốc", key="btn_speak_orig"):
            if text_in.strip():
                s_code = 'en' if source_lang == "Auto Detect" else LANGUAGES[source_lang]
                audio_orig = speak(text_in, s_code)
                if audio_orig: st.audio(audio_orig)
    
    with c2:
        st.markdown("### Kết quả")
        target_lang = st.selectbox("Sang:", lang_names, index=lang_names.index('Vietnamese'), key="tgt_lang")
        if st.button("CHUYỂN NGỮ & PHÁT ÂM", key="btn_dich"):
            if text_in.strip():
                with st.spinner('Đang dịch...'):
                    t_code = LANGUAGES[target_lang]
                    s_code = 'auto' if source_lang == "Auto Detect" else LANGUAGES[source_lang]
                    translated = smart_translate(text_in, s_code, t_code)
                    st.success(translated)
                    audio_trans = speak(translated, t_code)
                    if audio_trans: st.audio(audio_trans)

# ==========================================
# TAB 2 & 3: DỊCH ẢNH VÀ VIDEO
# ==========================================
with tab2:
    st.markdown("### Dịch trực tiếp trên hình ảnh")
    up_file = st.file_uploader("Tải ảnh lên:", type=['jpg','png','jpeg'], key="up_img")
    if up_file:
        img = Image.open(up_file).convert("RGB")
        st.image(img, caption="Ảnh gốc", width=400)
        if st.button("QUÉT & DỊCH ĐÈ", key="btn_scan"):
            with st.spinner("AI đang xử lý..."):
                img_np = np.array(img)
                result = reader.readtext(img_np)
                draw = ImageDraw.Draw(img)
                try: font = ImageFont.truetype("arial.ttf", 20)
                except: font = ImageFont.load_default()
                for (bbox, text, prob) in result:
                    if prob > 0.2:
                        p1, p2, p3, p4 = [tuple(map(int, p)) for p in bbox]
                        draw.polygon([p1, p2, p3, p4], fill="white")
                        trans = smart_translate(text, 'auto', 'vi')
                        draw.text(p1, trans, fill="black", font=font)
                st.subheader("Kết quả Google Lens:")
                st.image(img, use_container_width=True)

with tab3:
    st.markdown("### 🎬 Xưởng Dịch Phim & Bóc Băng Tự Động")
    col_srt, col_mp4 = st.columns(2)
    with col_srt:
        srt_file = st.file_uploader("1. Tải phụ đề (.srt) - NẾU CÓ:", type=['srt'], key="up_srt")
    with col_mp4:
        mp4_file = st.file_uploader("2. Tải video (.mp4):", type=['mp4'], key="up_mp4")

    if srt_file or mp4_file:
        st.markdown("---")
        target_lang_srt = st.selectbox("3. Dịch sang ngôn ngữ:", lang_names, index=lang_names.index('Vietnamese'), key="tgt_srt")
        t_code_srt = LANGUAGES[target_lang_srt]
        
        if srt_file:
            content = srt_file.read().decode('utf-8')
            subs = pysrt.from_string(content)
            c3, c4 = st.columns(2)
            with c3:
                if st.button("▶️ DỊCH VÀ XEM NHÁP"):
                    area = st.empty()
                    for i in range(min(30, len(subs))):
                        trans = smart_translate(subs[i].text, 'auto', t_code_srt)
                        area.markdown(f"<div style='background:#1e1e1e; color:white; padding:15px; border-radius:10px; border-left: 5px solid #ffcc00; margin-bottom: 10px;'><small style='color:#888'>{subs[i].text}</small><br><strong style='font-size:20px; color:#ffcc00'>{trans}</strong></div>", unsafe_allow_html=True)
            with c4:
                if st.button("📥 DỊCH TOÀN BỘ PHIM"):
                    with st.spinner('Đang dịch...'):
                        progress_bar = st.progress(0)
                        for i, sub in enumerate(subs):
                            if sub.text.strip():
                                sub.text = smart_translate(sub.text, 'auto', t_code_srt)
                            progress_bar.progress((i + 1) / len(subs))
                        vtt_out, srt_out = generate_subtitles(subs)
                        st.success("🎉 Dịch hoàn tất!")
                        if mp4_file is not None:
                            _, vid_col, _ = st.columns([1, 3, 1])
                            with vid_col:
                                st.video(mp4_file, subtitles={f"{target_lang_srt}": vtt_out})
                            with st.spinner("🔥 Đang ép chữ..."):
                                muxed_vid = hardsub_video(mp4_file.getvalue(), srt_out)
                                if muxed_vid:
                                    st.download_button(label="📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ", data=muxed_vid, file_name=f"Vietsub_{mp4_file.name}", mime="video/mp4")
                        st.download_button(label="📥 Tải tệp phụ đề (.srt)", data=srt_out, file_name=f"Dich_{srt_file.name}", mime="text/plain")
        
        elif mp4_file and not srt_file:
            st.info("💡 AI sẽ tự động bóc băng video!")
            if st.button("🤖 KÍCH HOẠT AI NGHE"):
                with st.spinner("Đang tải AI (Mô hình Base)..."):
                    import whisper
                    model = whisper.load_model("base")
                with st.spinner("Đang nghe video..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                        tmp.write(mp4_file.read())
                        tmp_path = tmp.name
                    result = model.transcribe(tmp_path)
                    def format_time(seconds):
                        h, m, s = int(seconds // 3600), int((seconds % 3600) // 60), int(seconds % 60)
                        ms = int((seconds - int(seconds)) * 1000)
                        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                    raw_srt = ""
                    for i, seg in enumerate(result['segments']):
                        raw_srt += f"{i+1}\n{format_time(seg['start'])} --> {format_time(seg['end'])}\n{seg['text'].strip()}\n\n"
                    os.remove(tmp_path)
                subs = pysrt.from_string(raw_srt)
                progress_bar = st.progress(0)
                for i, sub in enumerate(subs):
                    if sub.text.strip():
                        sub.text = smart_translate(sub.text, 'auto', t_code_srt)
                    progress_bar.progress((i + 1) / len(subs))
                vtt_out, srt_out = generate_subtitles(subs)
                st.success("🎉 Hoàn tất!")
                _, vid_col_ai, _ = st.columns([1, 3, 1])
                with vid_col_ai:
                    st.video(mp4_file, subtitles={f"{target_lang_srt}": vtt_out})
                with st.spinner("🔥 Đang ép chữ..."):
                    muxed_vid = hardsub_video(mp4_file.getvalue(), srt_out)
                    if muxed_vid:
                        st.download_button(label="📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ", data=muxed_vid, file_name=f"AI_Vietsub_{mp4_file.name}", mime="video/mp4")
                st.download_button(label="📥 Tải tệp Vietsub (.srt)", data=srt_out, file_name="AI_Dich_Tudong.srt", mime="text/plain")