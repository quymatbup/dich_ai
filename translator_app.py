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
import translators as ts

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(
    page_title='Dịch Thuật AI',
    layout='wide',
    page_icon="logo.ico"
)

# --- TỐI ƯU CACHE CHO OCR & WHISPER ---
OCR_LANG_GROUPS = {
    "Tiếng Anh & Tiếng Việt (Mặc định)": ['vi', 'en'],
    "Tiếng Trung (Giản thể) & Anh": ['ch_sim', 'en'],
    "Tiếng Trung (Phồn thể) & Anh": ['ch_tra', 'en'],
    "Tiếng Nhật & Anh": ['ja', 'en'],
    "Tiếng Hàn & Anh": ['ko', 'en'],
    "Tiếng Thái & Anh": ['th', 'en'],
    "Tiếng Nga & Anh": ['ru', 'en'],
    "Châu Âu (Pháp, Đức, Tây Ban Nha, Ý, Bồ Đào Nha)": ['fr', 'de', 'es', 'it', 'pt', 'en']
}

@st.cache_resource
def load_ocr(lang_tuple=('vi', 'en')):
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

# --- HÀM TỰ ĐỘNG DỊCH (CHỐNG NHẬN DIỆN NHẦM TIẾNG TRUNG) ---
def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip():
        return text

    # 1. Xử lý mã tiếng Trung riêng cho Bing
    bing_tgt = tgt
    if tgt in ['zh-CN', 'zh-CHS']:
        bing_tgt = 'zh-Hans'
    if tgt in ['zh-TW', 'zh-CHT']:
        bing_tgt = 'zh-Hant'

    # 2. Xử lý mã tiếng Trung riêng cho Alibaba
    ali_tgt = tgt
    if tgt in ['zh-CN', 'zh-CHS', 'zh-Hans']:
        ali_tgt = 'zh'
    if tgt in ['zh-TW', 'zh-CHT', 'zh-Hant']:
        ali_tgt = 'zh-tw'

    safe_src = src if src != 'auto' else 'auto'

    try:
        time.sleep(0.25)
        return ts.translate_text(text, translator='bing', from_language=safe_src, to_language=bing_tgt)
    except Exception:
        try:
            return ts.translate_text(text, translator='alibaba', from_language=safe_src, to_language=ali_tgt)
        except Exception as e:
            return f"{text} (Lỗi Cloud: {str(e)})"

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
        if 'zh' in lang_code:
            tts_lang = 'zh-CN'
        tts = gTTS(text=text, lang=tts_lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp
    except Exception:
        return None

# --- CÁC HÀM HỖ TRỢ TỐI ƯU FONT & VẼ CHỮ TRÊN ẢNH (TAB 2) ---
@st.cache_resource
def get_font_path():
    """Tìm font hỗ trợ tiếng Việt có sẵn hoặc tải NotoSans chuẩn từ Google."""
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
    """Tự động ngắt dòng văn bản dựa theo chiều rộng pixel thực tế."""
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

def fit_text_to_box(draw, text, box_w, box_h, font_path):
    """Tự động tìm cỡ chữ và cách ngắt dòng để vừa khít khung hình chữ nhật."""
    if not font_path:
        return ImageFont.load_default(), text

    max_font_size = max(12, min(60, int(box_h * 0.85)))
    min_font_size = 10
    padding = 4
    avail_w = max(20, box_w - padding * 2)
    avail_h = max(12, box_h - padding * 2)

    best_font = ImageFont.truetype(font_path, min_font_size)
    best_wrapped = text

    for size in range(max_font_size, min_font_size - 1, -1):
        font = ImageFont.truetype(font_path, size)
        wrapped = wrap_text_by_pixel(draw, text, font, avail_w)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= avail_w and text_h <= avail_h:
            return font, wrapped
        best_font, best_wrapped = font, wrapped

    return best_font, best_wrapped

def get_box_colors(img_np, x_min, y_min, x_max, y_max):
    """Lấy màu nền tự nhiên của vùng chữ và chọn màu chữ tương phản (Đen/Trắng)."""
    h, w, _ = img_np.shape
    crop = img_np[max(0, y_min):min(h, y_max), max(0, x_min):min(w, x_max)]
    if crop.size == 0:
        return (255, 255, 255), (0, 0, 0)

    border_pixels = np.concatenate([
        crop[0, :], crop[-1, :], crop[:, 0], crop[:, -1]
    ], axis=0)
    bg_color = tuple(map(int, np.median(border_pixels, axis=0)))

    luminance = 0.299 * bg_color[0] + 0.587 * bg_color[1] + 0.114 * bg_color[2]
    text_color = (0, 0, 0) if luminance > 140 else (255, 255, 255)
    return bg_color, text_color


# ==========================================
# GIAO DIỆN CHÍNH CỦA ỨNG DỤNG
# ==========================================
st.title("🛡️ App Dịch Thuật AI")
tab1, tab2, tab3 = st.tabs(["📝 Dịch Văn Bản", "📸 Dịch hình ảnh", "🎬 Phụ Đề Phim & Tách Lời AI"])

# ------------------------------------------
# TAB 1: DỊCH VĂN BẢN (GHI ÂM MICRO)
# ------------------------------------------
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
        st.caption("💡 Hãy ấn dịch lần 2 nếu mạng chập chờn chưa kịp phản hồi.")
        target_lang = st.selectbox("Sang:", lang_names, index=lang_names.index('Vietnamese'), key="tgt_lang")
        if st.button("CHUYỂN NGỮ & PHÁT ÂM", key="btn_dich", type="primary"):
            if text_in.strip():
                with st.spinner('Đang dịch...'):
                    t_code = LANGUAGES[target_lang]
                    s_code = 'auto' if source_lang == "Auto Detect" else LANGUAGES[source_lang]
                    translated = smart_translate(text_in, s_code, t_code)

                    if "(Lỗi Cloud" in translated:
                        st.error(translated)
                    else:
                        st.success(translated)
                        audio_trans = speak(translated, t_code)
                        if audio_trans:
                            st.audio(audio_trans)

# ------------------------------------------
# TAB 2: DỊCH HÌNH ẢNH THÔNG MINH
# ------------------------------------------
with tab2:
    st.markdown("### 📸 Dịch hình ảnh thông minh")

    up_file = st.file_uploader("Chọn hình ảnh:", type=['jpg', 'png', 'jpeg'], key="up_img")

    if up_file:
        # 1. Mở ảnh và tự động sửa lỗi xoay ngang của camera điện thoại
        img = Image.open(up_file).convert("RGB")
        img = ImageOps.exif_transpose(img)

        # 2. CHỐNG SẬP WEB: Tự động thu nhỏ nếu ảnh chụp từ camera quá to
        max_size = 1200
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        col_opt1, col_opt2, col_opt3 = st.columns(3)
        with col_opt1:
            ocr_group_label = st.selectbox(
                "Ngôn ngữ chữ trên ảnh gốc:",
                list(OCR_LANG_GROUPS.keys()),
                index=0,
                key="ocr_src_group"
            )
        with col_opt2:
            target_lang_img = st.selectbox(
                "Dịch ảnh sang:",
                lang_names,
                index=lang_names.index('Vietnamese'),
                key="img_lang"
            )
        with col_opt3:
            st.write("")
            merge_para = st.checkbox(
                "Gộp các dòng thành đoạn văn (Dịch mượt hơn)",
                value=True
            )

        if st.button("🔍 QUÉT & DỊCH ĐÈ LÊN ẢNH", type="primary", use_container_width=True):
            with st.spinner("AI đang bóc tách chữ và dịch ngữ cảnh..."):
                ocr_langs = tuple(OCR_LANG_GROUPS[ocr_group_label])
                reader = load_ocr(ocr_langs)

                img_np = np.array(img)
                img_translated = img.copy()
                draw = ImageDraw.Draw(img_translated)
                font_path = get_font_path()
                t_code_img = LANGUAGES[target_lang_img]

                # Quét OCR
                raw_results = reader.readtext(img_np, paragraph=merge_para)

                valid_boxes = []
                original_texts = []
                for item in raw_results:
                    if len(item) == 3:
                        bbox, text, prob = item
                        if prob <= 0.25 or not text.strip():
                            continue
                    else:
                        bbox, text = item
                        if not text.strip():
                            continue
                    valid_boxes.append(bbox)
                    original_texts.append(text.strip())

                if not original_texts:
                    st.warning("⚠️ Không tìm thấy chữ nào rõ ràng trên bức ảnh này!")
                else:
                    # Dịch gộp toàn bộ văn bản trong 1 lần gọi để giữ ngữ cảnh và tăng tốc
                    translated_texts = []
                    joined_text = "\n".join(original_texts)
                    batch_trans = smart_translate(joined_text, 'auto', t_code_img)
                    split_trans = [line.strip() for line in batch_trans.split("\n") if line.strip()]

                    if len(split_trans) == len(original_texts):
                        translated_texts = split_trans
                    else:
                        for txt in original_texts:
                            translated_texts.append(smart_translate(txt, 'auto', t_code_img))

                    # Vẽ đè bản dịch lên từng khung
                    for bbox, trans in zip(valid_boxes, translated_texts):
                        xs = [int(p[0]) for p in bbox]
                        ys = [int(p[1]) for p in bbox]
                        x_min, x_max = min(xs), max(xs)
                        y_min, y_max = min(ys), max(ys)

                        box_w = max(20, x_max - x_min)
                        box_h = max(16, y_max - y_min)

                        bg_color, text_color = get_box_colors(img_np, x_min, y_min, x_max, y_max)
                        font, wrapped_text = fit_text_to_box(draw, trans, box_w, box_h, font_path)

                        draw.rectangle([x_min, y_min, x_max, y_max], fill=bg_color)

                        t_bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=2)
                        tw, th = t_bbox[2] - t_bbox[0], t_bbox[3] - t_bbox[1]
                        text_x = x_min + max(2, (box_w - tw) // 2)
                        text_y = y_min + max(2, (box_h - th) // 2)

                        draw.multiline_text(
                            (text_x, text_y),
                            wrapped_text,
                            fill=text_color,
                            font=font,
                            spacing=2,
                            align="center"
                        )

                    # Hiển thị song song ảnh gốc và ảnh đã dịch
                    col_before, col_after = st.columns(2)
                    with col_before:
                        st.image(img, caption="Ảnh gốc", use_container_width=True)
                    with col_after:
                        st.image(img_translated, caption="Bản dịch đè trên ảnh", use_container_width=True)

                    # Nút tải ảnh đã dịch về máy
                    img_byte_arr = io.BytesIO()
                    img_translated.save(img_byte_arr, format='PNG')
                    st.download_button(
                        label="📥 TẢI ẢNH ĐÃ DỊCH VỀ MÁY (.PNG)",
                        data=img_byte_arr.getvalue(),
                        file_name=f"Dich_{up_file.name}.png",
                        mime="image/png",
                        use_container_width=True
                    )

                    # Bảng chi tiết văn bản để dễ copy
                    with st.expander("📄 Xem chi tiết văn bản quét được & bản dịch (Dễ copy)"):
                        for idx, (orig_t, trans_t) in enumerate(zip(original_texts, translated_texts), 1):
                            st.markdown(f"**Đoạn {idx}:** `{orig_t}` ➡️ **{trans_t}**")
        else:
            st.image(img, caption="Ảnh gốc", use_container_width=True)

# ------------------------------------------
# TAB 3: DỊCH PHIM & TÁCH LỜI AI
# ------------------------------------------
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
            content = srt_file.read().decode('utf-8', errors='replace')
            subs = pysrt.from_string(content)

            if st.button("🚀 BẮT ĐẦU DỊCH", use_container_width=True):
                with st.spinner('Đang dịch toàn bộ...'):
                    progress_bar = st.progress(0)
                    for i, sub in enumerate(subs):
                        if sub.text.strip():
                            sub.text = smart_translate(sub.text, 'auto', t_code_srt)
                        progress_bar.progress((i + 1) / len(subs))

                    vtt_out, srt_out = generate_subtitles(subs)
                    st.success("🎉 Dịch hoàn tất!")

                    st.markdown("#### 👁️ Bản xem trước:")
                    preview_area = st.container()
                    for i in range(min(3, len(subs))):
                        preview_area.markdown(
                            f"<div style='background:#1e1e1e; color:white; padding:15px; border-radius:10px; border-left: 5px solid #ffcc00; margin-bottom: 10px;'>"
                            f"<strong style='font-size:18px; color:#ffcc00'>{subs[i].text}</strong></div>",
                            unsafe_allow_html=True
                        )

                    if mp4_file is not None:
                        _, vid_col, _ = st.columns([1, 3, 1])
                        with vid_col:
                            st.video(mp4_file, subtitles={f"{target_lang_srt}": vtt_out})
                        with st.spinner("🔥 Đang ép phụ đề vào video..."):
                            muxed_vid = hardsub_video(mp4_file.getvalue(), srt_out)
                            if muxed_vid:
                                st.download_button(
                                    label="📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ",
                                    data=muxed_vid,
                                    file_name=f"Vietsub_{mp4_file.name}",
                                    mime="video/mp4"
                                )

                    st.download_button(
                        label="📥 Tải tệp phụ đề (.srt)",
                        data=srt_out,
                        file_name=f"Dich_{srt_file.name}",
                        mime="text/plain"
                    )

        elif mp4_file and not srt_file:
            st.info("💡 AI sẽ tự động bóc băng video!")
            if st.button("🤖 BẮT ĐẦU DỊCH", use_container_width=True):
                with st.spinner("Đang tải AI (Mô hình Base)..."):
                    model = load_whisper_model()
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
                    if os.path.exists(tmp_path):
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
                        st.download_button(
                            label="📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ",
                            data=muxed_vid,
                            file_name=f"AI_Vietsub_{mp4_file.name}",
                            mime="video/mp4"
                        )
                st.download_button(
                    label="📥 Tải tệp Vietsub (.srt)",
                    data=srt_out,
                    file_name="AI_Dich_Tudong.srt",
                    mime="text/plain"
                )
