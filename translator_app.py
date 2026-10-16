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
import urllib.request
from audio_recorder_streamlit import audio_recorder
from deep_translator import GoogleTranslator

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title='Dịch Thuật AI', layout='wide', page_icon="logo.ico")

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
    'Vietnamese': 'vi', 'English': 'en', 'Chinese (Simplified)': 'zh-CN', 'Chinese (Traditional)': 'zh-TW',
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

# --- HỆ THỐNG DỊCH THÔNG MINH (CẮT NHỎ CÂU ĐỂ CHỐNG LỖI ĐOẠN DÀI) ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip():
        return text

    # Hàm dịch cho 1 câu ngắn (Vẫn giữ 3 lớp bảo vệ)
    def translate_chunk(txt):
        txt = txt.strip()
        if not txt: return ""
        bing_tgt = 'zh-Hans' if tgt in ['zh-CN', 'zh-CHS'] else ('zh-Hant' if tgt in ['zh-TW', 'zh-CHT'] else tgt)
        ali_tgt = 'zh' if tgt in ['zh-CN', 'zh-CHS', 'zh-Hans'] else ('zh-tw' if tgt in ['zh-TW', 'zh-CHT', 'zh-Hant'] else tgt)

        try:
            import translators as ts
            time.sleep(0.1)
            return ts.translate_text(txt, translator='bing', to_language=bing_tgt)
        except Exception:
            pass
        try:
            import translators as ts
            time.sleep(0.1)
            return ts.translate_text(txt, translator='alibaba', to_language=ali_tgt)
        except Exception:
            pass
        try:
            return GoogleTranslator(source='auto', target=tgt).translate(txt)
        except Exception:
            return txt # Trả về nguyên gốc nếu sập cả 3 mạng để lát ghép lại

    clean_text = text.strip()
    
    # Nếu đoạn văn quá dài (>250 ký tự), tự động cắt nhỏ theo dấu chấm hoặc xuống dòng
    if len(clean_text) > 250:
        lines = clean_text.split('\n')
        translated_lines = []
        for line in lines:
            if len(line) > 200:
                # Cắt theo dấu chấm, chấm hỏi, chấm than (giữ lại dấu)
                sentences = re.split(r'(?<=[.!?])\s+', line)
                trans_sentences = [translate_chunk(s) for s in sentences if s.strip()]
                translated_lines.append(" ".join(trans_sentences))
            else:
                if line.strip():
                    translated_lines.append(translate_chunk(line))
                else:
                    translated_lines.append("")
        
        result = "\n".join(translated_lines)
        if result.strip() == clean_text:
            return f"{clean_text}\n\n(Lỗi mạng: Không thể kết nối máy chủ)"
        return result
    else:
        # Nếu câu ngắn, dịch luôn 1 lần
        res = translate_chunk(clean_text)
        if res == clean_text and not clean_text.isdigit():
             return f"{clean_text} (Lỗi mạng)"
        return res

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
        if 'zh' in lang_code:
            tts_lang = 'zh-CN'
        tts = gTTS(text=text, lang=tts_lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp
    except Exception:
        return None

# --- CÁC HÀM HỖ TRỢ OCR & VẼ CHỮ TRÊN ẢNH ---
@st.cache_resource
def get_font_path():
    system_fonts = [
        "NotoSans-Regular.ttf",
        "Roboto-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in system_fonts:
        if os.path.exists(path) and os.path.getsize(path) > 10000:
            return path

    font_path = "NotoSans-Regular.ttf"
    url = "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf"
    try:
        urllib.request.urlretrieve(url, font_path)
        if os.path.exists(font_path) and os.path.getsize(font_path) > 10000:
            return font_path
    except Exception:
        pass
    return None

def wrap_text_by_pixel(draw, text, font, max_width):
    words = text.split()
    if not words:
        return ""
    lines = []
    current_line = words[0]
    for word in words[1:]:
        test_line = f"{current_line} {word}"
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    lines.append(current_line)
    return "\n".join(lines)

def fit_text_to_box(draw, text, box_w, box_h, font_path, is_paragraph=False):
    if not font_path:
        return ImageFont.load_default(), text

    est_line_h = box_h / (text.count('\n') + 1) if is_paragraph else box_h
    max_font_size = max(16, min(160, int(est_line_h * 1.15)))
    min_font_size = 10
    padding = 2
    avail_w = max(20, box_w - padding * 2)
    avail_h = max(14, box_h - padding * 2)

    best_font = ImageFont.truetype(font_path, min_font_size)
    best_wrapped = text

    step = 2 if max_font_size > 50 else 1
    for size in range(max_font_size, min_font_size - 1, -step):
        font = ImageFont.truetype(font_path, size)
        wrapped = wrap_text_by_pixel(draw, text, font, avail_w)
        spacing = max(2, int(size * 0.18))
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= avail_w and text_h <= avail_h:
            return font, wrapped
        best_font, best_wrapped = font, wrapped

    return best_font, best_wrapped

def get_box_colors(img_np, x_min, y_min, x_max, y_max):
    h, w, _ = img_np.shape
    crop = img_np[max(0, y_min):min(h, y_max), max(0, x_min):min(w, x_max)]
    if crop.size == 0:
        return (255, 255, 255), (0, 0, 0)

    border_pixels = np.concatenate([
        crop[0, :], crop[-1, :], crop[:, 0], crop[:, -1]
    ], axis=0)
    bg_color = tuple(map(int, np.median(border_pixels, axis=0)))

    luminance = 0.299 * bg_color[0] + 0.587 * bg_color[1] + 0.114 * bg_color[2]
    text_color = (0, 0, 0) if luminance > 140 else (235, 240, 248)
    return bg_color, text_color

# ==========================================
# GIAO DIỆN CHÍNH CỦA ỨNG DỤNG
# ==========================================
st.title("🛡️ App Dịch Thuật AI")
tab1, tab2, tab3 = st.tabs(["📝 Dịch Văn Bản", "📸 Dịch hình ảnh", "🎬 Phụ Đề Phim & Tách Lời AI"])

# TAB 1: DỊCH VĂN BẢN
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
                        model = load_whisper_model()
                        res = model.transcribe(tmp_path)
                        st.session_state.txt_in = res['text'].strip()
                    except Exception:
                        st.error("Lỗi khi nghe giọng nói!")
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                if hasattr(st, "rerun"):
                    st.rerun()
                else:
                    st.experimental_rerun()

        text_in = st.text_area("Hoặc gõ văn bản gốc vào đây:", value=st.session_state.txt_in, key="txt_in_area", height=150)
        source_lang = st.selectbox("Từ:", ["Auto Detect"] + lang_names, key="src_lang")

        if st.button("🔊 Nghe bản gốc", key="btn_speak_orig"):
            if text_in.strip():
                s_code = 'en' if source_lang == "Auto Detect" else LANGUAGES[source_lang]
                audio_orig = speak(text_in, s_code)
                if audio_orig:
                    st.audio(audio_orig)

    with c2:
        st.markdown("### Kết quả")
        target_lang = st.selectbox("Sang:", lang_names, index=lang_names.index('Vietnamese'), key="tgt_lang")

        if st.button("CHUYỂN NGỮ & PHÁT ÂM", key="btn_dich", type="primary"):
            if text_in.strip():
                with st.spinner('Đang dịch (Tự động băm nhỏ để chống lỗi)...'):
                    t_code = LANGUAGES[target_lang]
                    translated = smart_translate(text_in, 'auto', t_code)
                    st.success(translated)
                    audio_trans = speak(translated, t_code)
                    if audio_trans:
                        st.audio(audio_trans)

# TAB 2: DỊCH HÌNH ẢNH
with tab2:
    st.markdown("### 📸 Dịch hình ảnh thông minh")
    up_file = st.file_uploader("Chọn hình ảnh:", type=['jpg', 'png', 'jpeg'], key="up_img")

    if up_file:
        img = Image.open(up_file).convert("RGB")
        img = ImageOps.exif_transpose(img)

        max_size = 1200
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        col_opt1, col_opt2, col_opt3 = st.columns(3)
        with col_opt1:
            ocr_group_label = st.selectbox("Ngôn ngữ chữ gốc:", list(OCR_LANG_GROUPS.keys()), index=0, key="ocr_src_group")
        with col_opt2:
            target_lang_img = st.selectbox("Dịch sang:", lang_names, index=lang_names.index('Vietnamese'), key="img_lang")
        with col_opt3:
            st.write("")
            merge_para = st.checkbox("Gộp đoạn (Mượt hơn)", value=True)

        if st.button("🔍 QUÉT & DỊCH ĐÈ LÊN ẢNH", type="primary", use_container_width=True):
            with st.spinner("AI đang quét & tự động lọc rác..."):
                ocr_langs = tuple(OCR_LANG_GROUPS[ocr_group_label])
                reader = load_ocr(ocr_langs)

                img_np = np.array(img)
                img_translated = img.copy()
                draw = ImageDraw.Draw(img_translated)
                font_path = get_font_path()
                t_code_img = LANGUAGES[target_lang_img]

                raw_results = reader.readtext(img_np, paragraph=merge_para)
                
                boxes = []
                for item in raw_results:
                    if len(item) == 3:
                        bbox, text, prob = item
                        if prob <= 0.15: continue
                    else:
                        bbox, text = item
                        
                    txt = text.strip()
                    if not txt: continue
                    
                    # --- BỘ LỌC RÁC: XÓA KHOẢNG TRẮNG ĐỂ CHẶN 'O O Q' ---
                    txt_clean = txt.lower().replace(" ", "")
                    trash_list = ['q', 'oqq', 'b9q', 'oq', 'o', '0q', 'b9', 'c', 'oo', 'ooq', '00q']
                    if txt_clean in trash_list or (len(txt_clean) <= 2 and not any(c.isalpha() for c in txt_clean)):
                        continue
                    # --------------------------------------------------------
                        
                    xs = [int(p[0]) for p in bbox]
                    ys = [int(p[1]) for p in bbox]
                    boxes.append({
                        'x_min': min(xs), 'y_min': min(ys),
                        'x_max': max(xs), 'y_max': max(ys),
                        'text': txt
                    })

                if not boxes:
                    st.warning("⚠️ Không tìm thấy chữ nào rõ ràng trên bức ảnh này!")
                else:
                    original_texts = [b['text'] for b in boxes]
                    # Ghép các khối chữ trên ảnh lại để dịch, dùng hàm băm nhỏ bên trên để xử lý
                    joined_text = " \n \n ".join(original_texts)
                    batch_trans = smart_translate(joined_text, 'auto', t_code_img)
                    
                    split_trans = [line.strip() for line in batch_trans.split(" \n \n ")]
                    if "(Lỗi mạng" not in batch_trans and len(split_trans) == len(original_texts):
                        translated_texts = split_trans
                    else:
                        translated_texts = [smart_translate(txt, 'auto', t_code_img) for txt in original_texts]

                    for b, trans in zip(boxes, translated_texts):
                        x_min, y_min, x_max, y_max = b['x_min'], b['y_min'], b['x_max'], b['y_max']

                        box_w = max(20, x_max - x_min)
                        box_h = max(16, y_max - y_min)

                        bg_color, text_color = get_box_colors(img_np, x_min, y_min, x_max, y_max)
                        font, wrapped_text = fit_text_to_box(draw, trans, box_w,
