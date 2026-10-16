import streamlit as st
import easyocr
from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np
import pysrt, time, io, tempfile, os, subprocess, hashlib, re, html, json
import urllib.request, urllib.parse
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder
from deep_translator import GoogleTranslator
import translators as ts

# --- CẤU HÌNH GIAO DIỆN ---
st.set_page_config(page_title='Dịch Thuật AI', layout='wide', page_icon="logo.ico")

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
    vn_re = r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]'
    if re.search(vn_re, text.lower()): return 'vi'
    if re.search(r'[\u3040-\u30ff]', text): return 'ja'
    if re.search(r'[\uac00-\ud7af]', text): return 'ko'
    if re.search(r'[\u4e00-\u9fff]', text): return 'zh-CN'
    if re.search(r'[\u0e00-\u0e7f]', text): return 'th'
    if re.search(r'[\u0400-\u04ff]', text): return 'ru'
    return 'en' if tgt != 'en' else 'vi'

def smart_translate(text, src='auto', tgt='vi'):
    if not text or not text.strip(): return text
    clean_txt = html.unescape(text.strip()).replace("’", "'").replace("‘", "'")
    g_tgt = 'zh-CN' if tgt in ['zh-CN', 'zh-CHS', 'zh-Hans'] else ('zh-TW' if tgt in ['zh-TW', 'zh-CHT', 'zh-Hant'] else tgt)
    real_src = detect_lang_code(clean_txt, g_tgt) if src == 'auto' else src
    headers = {'User-Agent': 'Mozilla/5.0'}
    body = urllib.parse.urlencode({'q': clean_txt}).encode('utf-8')

    # 1. Google GTX POST (Truyền nguyên đoạn văn dài, không cắt nhỏ)
    try:
        url1 = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={real_src}&tl={g_tgt}&dt=t"
        req1 = urllib.request.Request(url1, data=body, headers=headers)
        with urllib.request.urlopen(req1, timeout=8) as r:
            d1 = json.loads(r.read().decode('utf-8'))
            out1 = html.unescape("".join([p[0] for p in d1[0] if p and p[0]]).strip())
            if out1 and out1.lower() != clean_txt.lower(): return out1
    except Exception: pass

    # 2. Google Clients5 POST
    try:
        url2 = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={real_src}&tl={g_tgt}&dt=t"
        req2 = urllib.request.Request(url2, data=body, headers=headers)
        with urllib.request.urlopen(req2, timeout=8) as r:
            d2 = json.loads(r.read().decode('utf-8'))
            out2 = d2[0] if isinstance(d2[0], str) else "".join([x[0] for x in d2 if x and x[0]])
            out2 = html.unescape(out2.strip())
            if out2 and out2.lower() != clean_txt.lower(): return out2
    except Exception: pass

    # 3. DeepTranslator
    try:
        out3 = GoogleTranslator(source=real_src, target=g_tgt).translate(clean_txt)
        if out3 and out3.strip().lower() != clean_txt.lower():
            return html.unescape(out3.strip())
    except Exception: pass

    # 4. Bing Microsoft (Chỉ định rõ ngôn ngữ nguồn để không bị dịch sót)
    try:
        b_tgt = 'zh-Hans' if g_tgt == 'zh-CN' else ('zh-Hant' if g_tgt == 'zh-TW' else g_tgt)
        b_src = 'zh-Hans' if real_src == 'zh-CN' else real_src
        out4 = ts.translate_text(clean_txt, translator='bing', from_language=b_src, to_language=b_tgt)
        if out4: return html.unescape(out4.strip())
    except Exception: pass

    return f"{clean_txt} (Lỗi mạng)"

def speak(text, lang_code):
    try:
        if lang_code.startswith('en'):
            text = remove_vn_accents(text)
            for k, v in {"Quy": "Kwee", "quy": "kwee", "Phu": "Foo", "phu": "foo", "Nguyen": "Nwin", "nguyen": "nwin"}.items():
                text = text.replace(k, v)
        tts_lang = 'zh-CN' if 'zh' in lang_code else lang_code.split('-')[0]
        tts = gTTS(text=text, lang=tts_lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp
    except Exception: return None

# --- CÁC HÀM XỬ LÝ FONT & VẼ ẢNH ---
@st.cache_resource
def get_font_path():
    fonts = [
        "NotoSans-Regular.ttf", "Roboto-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    ]
    for p in fonts:
        if os.path.exists(p) and os.path.getsize(p) > 10000: return p
    f_p = "NotoSans-Regular.ttf"
    url = "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf"
    try:
        urllib.request.urlretrieve(url, f_p)
        if os.path.exists(f_p) and os.path.getsize(f_p) > 10000: return f_p
    except Exception: pass
    return None

def wrap_text_by_pixel(draw, text, font, max_w):
    words = text.split()
    if not words: return ""
    lines, cur = [], words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_w: cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return "\n".join(lines)

def fit_text_to_box(draw, text, box_w, box_h, font_path, is_para=False):
    if not font_path: return ImageFont.load_default(), text
    est_h = box_h / (text.count('\n') + 1) if is_para else box_h
    max_sz = max(16, min(160, int(est_h * 1.15)))
    avail_w, avail_h = max(20, box_w - 4), max(14, box_h - 4)
    best_f = ImageFont.truetype(font_path, 10)
    best_w = text
    step = 2 if max_sz > 50 else 1
    for sz in range(max_sz, 9, -step):
        f = ImageFont.truetype(font_path, sz)
        w_txt = wrap_text_by_pixel(draw, text, f, avail_w)
        sp = max(2, int(sz * 0.18))
        bb = draw.multiline_textbbox((0, 0), w_txt, font=f, spacing=sp)
        if (bb[2] - bb[0]) <= avail_w and (bb[3] - bb[1]) <= avail_h:
            return f, w_txt
        best_f, best_w = f, w_txt
    return best_f, best_w

def get_box_colors(img_np, x1, y1, x2, y2):
    h, w, _ = img_np.shape
    crop = img_np[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if crop.size == 0: return (255, 255, 255), (0, 0, 0)
    b_pix = np.concatenate([crop[0, :], crop[-1, :], crop[:, 0], crop[:, -1]], axis=0)
    bg = tuple(map(int, np.median(b_pix, axis=0)))
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fg = (0, 0, 0) if lum > 140 else (235, 240, 248)
    return bg, fg

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
        if "txt_in" not in st.session_state: st.session_state.txt_in = ""
        st.write("🎙️ **Đọc bằng Micro:**")
        aud_b = audio_recorder(text="Bấm vào mic để nói", recording_color="#ff4b4b", neutral_color="#888888")
        if aud_b:
            a_hash = hashlib.md5(aud_b).hexdigest()
            if st.session_state.get("last_audio") != a_hash:
                st.session_state.last_audio = a_hash
                with st.spinner("AI đang nghe và gõ chữ..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_a:
                        tmp_a.write(aud_b)
                        t_p = tmp_a.name
                    try:
                        res = load_whisper_model().transcribe(t_p)
                        st.session_state.txt_in = res['text'].strip()
                    except Exception: st.error("Lỗi khi nghe giọng nói!")
                    finally:
                        if os.path.exists(t_p): os.remove(t_p)
                st.rerun()

        text_in = st.text_area("Hoặc gõ văn bản gốc vào đây:", value=st.session_state.txt_in, key="txt_in_area", height=150)
        source_lang = st.selectbox("Từ:", ["Auto Detect"] + lang_names, key="src_lang")
        if st.button("🔊 Nghe bản gốc", key="btn_speak_orig") and text_in.strip():
            s_c = detect_lang_code(text_in, 'vi') if source_lang == "Auto Detect" else LANGUAGES[source_lang]
            aud_o = speak(text_in, s_c)
            if aud_o: st.audio(aud_o)

    with c2:
        st.markdown("### Kết quả")
        target_lang = st.selectbox("Sang:", lang_names, index=lang_names.index('Vietnamese'), key="tgt_lang")
        if st.button("CHUYỂN NGỮ & PHÁT ÂM", key="btn_dich", type="primary") and text_in.strip():
            with st.spinner('Đang dịch chuẩn ngữ cảnh...'):
                t_c = LANGUAGES[target_lang]
                s_c = 'auto' if source_lang == "Auto Detect" else LANGUAGES[source_lang]
                trans_out = smart_translate(text_in, s_c, t_c)
                st.success(trans_out)
                aud_t = speak(trans_out, t_c)
                if aud_t: st.audio(aud_t)

# TAB 2: DỊCH HÌNH ẢNH
with tab2:
    st.markdown("### 📸 Dịch hình ảnh thông minh")
    up_file = st.file_uploader("Chọn hình ảnh:", type=['jpg', 'png', 'jpeg'], key="up_img")
    if up_file:
        img = ImageOps.exif_transpose(Image.open(up_file).convert("RGB"))
        if img.width > 1200 or img.height > 1200:
            img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)

        co1, co2, co3 = st.columns(3)
        with co1: ocr_lbl = st.selectbox("Ngôn ngữ chữ gốc:", list(OCR_LANG_GROUPS.keys()), index=0)
        with co2: tgt_img_l = st.selectbox("Dịch sang:", lang_names, index=lang_names.index('Vietnamese'))
        with co3:
            st.write("")
            merge_p = st.checkbox("Gộp đoạn (Mượt hơn)", value=True)

        if st.button("🔍 QUÉT & DỊCH ĐÈ LÊN ẢNH", type="primary", use_container_width=True):
            with st.spinner("AI đang quét & dịch ảnh..."):
                reader = load_ocr(tuple(OCR_LANG_GROUPS[ocr_lbl]))
                img_np = np.array(img)
                img_tr = img.copy()
                draw = ImageDraw.Draw(img_tr)
                f_path = get_font_path()
                t_c_img = LANGUAGES[tgt_img_l]
                raw_res = reader.readtext(img_np, paragraph=merge_p)

                boxes = []
                trash_set = {'q', 'oqq', 'b9q', 'oq', 'o', '0q', 'b9', 'c', 'oo', 'ooq', '00q'}
                for item in raw_res:
                    if len(item) == 3 and item[2] <= 0.15: continue
                    bbox, txt = item[0], item[1].strip()
                    if not txt: continue
                    t_cl = txt.lower().replace(" ", "")
                    if t_cl in trash_set or (len(t_cl) <= 2 and not any(c.isalpha() for c in t_cl)):
                        continue
                    xs, ys = [int(p[0]) for p in bbox], [int(p[1]) for p in bbox]
                    boxes.append({'x1': min(xs), 'y1': min(ys), 'x2': max(xs), 'y2': max(ys), 'text': txt})

                if not boxes:
                    st.warning("⚠️ Không tìm thấy chữ nào rõ ràng trên ảnh!")
                else:
                    orig_list = [b['text'] for b in boxes]
                    trans_list = [smart_translate(t, 'auto', t_c_img) for t in orig_list]
                    for b, tr in zip(boxes, trans_list):
                        x1, y1, x2, y2 = b['x1'], b['y1'], b['x2'], b['y2']
                        bw, bh = max(20, x2 - x1), max(16, y2 - y1)
                        bg, fg = get_box_colors(img_np, x1, y1, x2, y2)
                        font, w_txt = fit_text_to_box(draw, tr, bw, bh, f_path, is_para=merge_p)
                        draw.rectangle([max(0, x1-2), max(0, y1-2), min(img.width, x2+2), min(img.height, y2+2)], fill=bg)
                        f_sz = font.size if hasattr(font, 'size') else 14
                        sp = max(2, int(f_sz * 0.18))
                        tb = draw.multiline_textbbox((0, 0), w_txt, font=font, spacing=sp)
                        tw, th = tb[2] - tb[0], tb[3] - tb[1]
                        tx = x1 + 2 if (merge_p or "\n" in w_txt) else x1 + max(0, (bw - tw) // 2)
                        ty = y1 + max(0, (bh - th) // 2)
                        al = "left" if (merge_p or "\n" in w_txt) else "center"
                        draw.multiline_text((tx, ty), w_txt, fill=fg, font=font, spacing=sp, align=al)

                    cb1, cb2 = st.columns(2)
                    cb1.image(img, caption="Ảnh gốc", use_container_width=True)
                    cb2.image(img_tr, caption="Bản dịch đè trên ảnh", use_container_width=True)
                    buf = io.BytesIO()
                    img_tr.save(buf, format='PNG')
                    st.download_button("📥 TẢI ẢNH ĐÃ DỊCH (.PNG)", buf.getvalue(), f"Dich_{up_file.name}.png", "image/png", use_container_width=True)
        else:
            st.image(img, caption="Ảnh gốc", use_container_width=True)

# TAB 3: DỊCH PHIM
with tab3:
    st.markdown("### 🎬 Xưởng Dịch Phim & Bóc Băng Tự Động")
    cs, cm = st.columns(2)
    srt_f = cs.file_uploader("1. Tải phụ đề (.srt) - NẾU CÓ:", type=['srt'])
    mp4_f = cm.file_uploader("2. Tải video (.mp4):", type=['mp4'])

    if srt_f or mp4_f:
        st.markdown("---")
        tgt_srt_l = st.selectbox("3. Dịch sang ngôn ngữ:", lang_names, index=lang_names.index('Vietnamese'))
        tc_srt = LANGUAGES[tgt_srt_l]

        if srt_f and st.button("🚀 BẮT ĐẦU DỊCH TỪ PHỤ ĐỀ", use_container_width=True):
            subs = pysrt.from_string(srt_f.read().decode('utf-8', errors='replace'))
            bar = st.progress(0)
            for i, s in enumerate(subs):
                if s.text.strip(): s.text = smart_translate(s.text, 'auto', tc_srt)
                bar.progress((i + 1) / len(subs))
            vtt_o, srt_o = generate_subtitles(subs)
            st.success("🎉 Dịch hoàn tất!")
            if mp4_f:
                st.video(mp4_f, subtitles={tgt_srt_l: vtt_o})
                mux = hardsub_video(mp4_f.getvalue(), srt_o)
                if mux: st.download_button("📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ", mux, f"Sub_{mp4_f.name}", "video/mp4")
            st.download_button("📥 Tải tệp phụ đề (.srt)", srt_o, f"Dich_{srt_f.name}", "text/plain")

        elif mp4_f and not srt_f and st.button("🤖 BẮT ĐẦU DỊCH TỪ ÂM THANH VIDEO", use_container_width=True):
            with st.spinner("Đang nghe và tách lời video..."):
                model = load_whisper_model()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                    tmp.write(mp4_f.read())
                    t_p = tmp.name
                res = model.transcribe(t_p)
                if os.path.exists(t_p): os.remove(t_p)
                raw_s = ""
                for i, seg in enumerate(res['segments']):
                    st_s, en_s = seg['start'], seg['end']
                    t1 = f"{int(st_s//3600):02d}:{int((st_s%3600)//60):02d}:{int(st_s%60):02d},{int((st_s-int(st_s))*1000):03d}"
                    t2 = f"{int(en_s//3600):02d}:{int((en_s%3600)//60):02d}:{int(en_s%60):02d},{int((en_s-int(en_s))*1000):03d}"
                    raw_s += f"{i+1}\n{t1} --> {t2}\n{seg['text'].strip()}\n\n"
            subs = pysrt.from_string(raw_s)
            bar = st.progress(0)
            for i, s in enumerate(subs):
                if s.text.strip(): s.text = smart_translate(s.text, 'auto', tc_srt)
                bar.progress((i + 1) / len(subs))
            vtt_o, srt_o = generate_subtitles(subs)
            st.success("🎉 Hoàn tất!")
            st.video(mp4_f, subtitles={tgt_srt_l: vtt_o})
            mux = hardsub_video(mp4_f.getvalue(), srt_o)
            if mux: st.download_button("📽️ TẢI VIDEO ĐÃ ÉP PHỤ ĐỀ", mux, f"AI_{mp4_f.name}", "video/mp4")
            st.download_button("📥 Tải tệp Vietsub (.srt)", srt_o, "AI_Dich.srt", "text/plain")
# === KẾT THÚC FILE CHUẨN 100% ===
