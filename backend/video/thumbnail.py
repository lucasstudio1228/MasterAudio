"""
video/thumbnail.py — Vẽ tiêu đề lên ảnh thumbnail (ảnh 0) bằng Pillow.

LÝ DO: model sinh ảnh (Gemini) KHÔNG đánh vần được chữ → tiêu đề "vẽ" vào ảnh
ra gibberish ("Bubaıng cou ın" thay vì "Bamboo Flute"). Cách xử lý triệt để:
bảo Gemini vẽ ảnh nền KHÔNG chữ, rồi overlay tiêu đề ở đây bằng font thật →
chính tả + dấu tiếng Việt luôn đúng, nét chữ sắc, tương phản tốt.

Kiểu chữ không còn cố định 1 kiểu: `style` (từ thumbnail_recipe.render_style)
chọn bố cục (left/right/top/bottom), kiểu chữ (TREATMENTS), bảng màu, font. Không
truyền `style` → kiểu cũ (chữ trái, vàng 3D).

Best-effort: nếu thiếu Pillow/font hoặc lỗi → giữ nguyên ảnh, KHÔNG làm vỡ
pipeline (chỉ log cảnh báo).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

# Font ưu tiên (đều hỗ trợ dấu tiếng Việt trên Windows). Đặt bold trước cho
# tiêu đề, semibold/regular cho dòng phụ. Có thể override qua overrides.json.
_TITLE_FONTS = [
    "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",    # Arial Bold
    "C:/Windows/Fonts/ariblk.ttf",     # Arial Black
]
_SUBTITLE_FONTS = [
    "C:/Windows/Fonts/seguisb.ttf",    # Segoe UI Semibold
    "C:/Windows/Fonts/segoeui.ttf",    # Segoe UI
    "C:/Windows/Fonts/arial.ttf",      # Arial
]

# Danh mục font TIÊU ĐỀ (chữ tiếng Anh). Mỗi phong cách kênh dùng 1 nhóm riêng
# (channel_looks.LOOKS[*]["fonts"]); bộ chọn ở tab Video khoá font cho từng project.
_WF = "C:/Windows/Fonts/"
FONT_CATALOG: list[dict] = [
    {"key": "segoe_script_bold", "label": "Segoe Script Bold",
     "note": "Viết tay tròn, dễ đọc", "file": _WF + "segoescb.ttf"},
    {"key": "mistral", "label": "Mistral",
     "note": "Nét cọ phóng, thư pháp mạnh", "file": _WF + "MISTRAL.TTF"},
    {"key": "brush_script", "label": "Brush Script",
     "note": "Bút lông nghiêng, mềm", "file": _WF + "BRUSHSCI.TTF"},
    {"key": "lucida_calligraphy", "label": "Lucida Calligraphy",
     "note": "Thư pháp cổ điển, sang", "file": _WF + "LCALLIG.TTF"},
    {"key": "segoe_print_bold", "label": "Segoe Print Bold",
     "note": "Bút dạ viết tay, mộc", "file": _WF + "segoeprb.ttf"},
    {"key": "vivaldi", "label": "Vivaldi",
     "note": "Thư pháp hoa mỹ", "file": _WF + "VIVALDII.TTF"},
    {"key": "papyrus", "label": "Papyrus",
     "note": "Cổ, thô ráp", "file": _WF + "PAPYRUS.TTF"},
    {"key": "ink_free", "label": "Ink Free",
     "note": "Bút mực tự do", "file": _WF + "Inkfree.ttf"},
    {"key": "rage_italic", "label": "Rage Italic",
     "note": "Bút sắt nghiêng, gân guốc", "file": _WF + "RAGE.TTF"},
    {"key": "kunstler_script", "label": "Kunstler Script",
     "note": "Thư pháp Âu, rất mảnh", "file": _WF + "KUNSTLER.TTF"},
    {"key": "old_english", "label": "Old English",
     "note": "Gothic cổ, kiểu trung cổ", "file": _WF + "OLDENGL.TTF"},
    {"key": "cooper_black", "label": "Cooper Black",
     "note": "Tròn mập, ấm áp kiểu lofi", "file": _WF + "COOPBL.TTF"},
    {"key": "berlin_sans_demi", "label": "Berlin Sans Demi",
     "note": "Tròn gọn, thân thiện", "file": _WF + "BRLNSDB.TTF"},
    {"key": "perpetua_titling", "label": "Perpetua Titling",
     "note": "Chữ hoa khắc đá, điện ảnh", "file": _WF + "PERTIBD.TTF"},
    {"key": "castellar", "label": "Castellar",
     "note": "Chữ hoa viền khắc, trang trọng", "file": _WF + "CASTELAR.TTF"},
    {"key": "bodoni_bold", "label": "Bodoni Bold",
     "note": "Serif tương phản cao, sang", "file": _WF + "BOD_B.TTF"},
    {"key": "palatino_bold", "label": "Palatino Bold",
     "note": "Serif cổ điển, dễ đọc", "file": _WF + "palab.ttf"},
    {"key": "copperplate", "label": "Copperplate Gothic",
     "note": "Chữ hoa rộng, trang trọng", "file": _WF + "COPRGTB.TTF"},
    {"key": "harrington", "label": "Harrington",
     "note": "Cổ tích, hoa văn nhẹ", "file": _WF + "HARNGTON.TTF"},
    {"key": "bell_mt_bold", "label": "Bell MT Bold",
     "note": "Serif mềm, kể chuyện", "file": _WF + "BELLB.TTF"},
    {"key": "high_tower", "label": "High Tower",
     "note": "Serif cổ, sách truyện", "file": _WF + "HTOWERT.TTF"},
    {"key": "segoe_ui_black", "label": "Segoe UI Black",
     "note": "Không chân, rất đậm", "file": _WF + "seguibl.ttf"},
    {"key": "gill_sans_ultra", "label": "Gill Sans Ultra",
     "note": "Khối tròn cực đậm, vui", "file": _WF + "GILSANUB.TTF"},
    {"key": "showcard", "label": "Showcard Gothic",
     "note": "Biển hiệu, năng động", "file": _WF + "SHOWG.TTF"},
    {"key": "tw_cen_cond_xb", "label": "Tw Cen Condensed",
     "note": "Hẹp cao, hiện đại", "file": _WF + "TCCEB.TTF"},
    {"key": "segoe_ui_bold", "label": "Segoe UI Bold",
     "note": "Không chân, đậm, hiện đại", "file": _WF + "segoeuib.ttf"},
    # ── Mở rộng kho (2026-10-15) ────────────────────────────────────────
    # Thêm 28 font ĐỀU CÓ SẴN trên Windows để mỗi look kênh có ~8-13 font
    # thay vì 3-4 → font tiêu đề không lặp sau vài video. Font nào thiếu dấu
    # tiếng Việt sẽ tự rơi về _WIDE_FONTS (xem _usable), nên thêm font trang
    # trí là an toàn.
    # Nhóm thư pháp / nét cọ (ink_zen)
    {"key": "freestyle_script", "label": "Freestyle Script",
     "note": "Nét cọ nhanh, bay bổng", "file": _WF + "FREESCPT.TTF"},
    {"key": "monotype_corsiva", "label": "Monotype Corsiva",
     "note": "Thư pháp Ý, mảnh nghiêng", "file": _WF + "MTCORSVA.TTF"},
    {"key": "pristina", "label": "Pristina",
     "note": "Bút lông mảnh, nhẹ nhàng", "file": _WF + "PRISTINA.TTF"},
    {"key": "edwardian_script", "label": "Edwardian Script",
     "note": "Thư pháp Anh rất mảnh, sang", "file": _WF + "ITCEDSCR.TTF"},
    # Nhóm tròn mập / viết tay ấm (cozy_lofi)
    {"key": "eras_bold", "label": "Eras Bold",
     "note": "Tròn mềm, hiện đại ấm", "file": _WF + "ERASBD.TTF"},
    {"key": "bauhaus_93", "label": "Bauhaus 93",
     "note": "Khối tròn hình học, vui", "file": _WF + "BAUHS93.TTF"},
    {"key": "bradley_hand", "label": "Bradley Hand",
     "note": "Viết tay bút bi, mộc mạc", "file": _WF + "BRADHITC.TTF"},
    {"key": "lucida_handwriting", "label": "Lucida Handwriting",
     "note": "Viết tay nghiêng, mềm", "file": _WF + "LHANDW.TTF"},
    {"key": "snap_itc", "label": "Snap ITC",
     "note": "Tròn mập cực đậm, tinh nghịch", "file": _WF + "SNAP____.TTF"},
    # Nhóm khắc đá / serif tương phản (cinematic)
    {"key": "engravers_mt", "label": "Engravers MT",
     "note": "Chữ hoa khắc, cổ điển", "file": _WF + "ENGR.TTF"},
    {"key": "felix_titling", "label": "Felix Titling",
     "note": "Chữ hoa khắc thanh, nhẹ", "file": _WF + "FELIXTI.TTF"},
    {"key": "modern_no20", "label": "Modern No. 20",
     "note": "Serif tương phản, cổ mà sắc", "file": _WF + "MOD20.TTF"},
    {"key": "georgia_bold", "label": "Georgia Bold",
     "note": "Serif dễ đọc, đậm", "file": _WF + "georgiab.ttf"},
    {"key": "times_bold", "label": "Times New Roman Bold",
     "note": "Serif kinh điển", "file": _WF + "timesbd.ttf"},
    # Nhóm sách truyện / cổ tích (painterly)
    {"key": "garamond_bold", "label": "Garamond Bold",
     "note": "Serif cổ mềm, thanh lịch", "file": _WF + "GARABD.TTF"},
    {"key": "goudy_stout", "label": "Goudy Stout",
     "note": "Serif mập, cổ tích đậm", "file": _WF + "GOUDYSTO.TTF"},
    {"key": "book_antiqua", "label": "Book Antiqua",
     "note": "Serif sách, ấm áp", "file": _WF + "BKANT.TTF"},
    {"key": "colonna", "label": "Colonna MT",
     "note": "Chữ hoa mảnh trang trí", "file": _WF + "COLONNA.TTF"},
    {"key": "matura_script", "label": "Matura MT Script Capitals",
     "note": "Chữ hoa cổ tích tròn", "file": _WF + "MATURASC.TTF"},
    {"key": "imprint_shadow", "label": "Imprint MT Shadow",
     "note": "Serif có bóng khắc nổi", "file": _WF + "IMPRISHA.TTF"},
    # Nhóm poster đậm / năng động (bright)
    {"key": "impact", "label": "Impact",
     "note": "Hẹp, cực đậm kiểu poster", "file": _WF + "impact.ttf"},
    {"key": "arial_black", "label": "Arial Black",
     "note": "Không chân, khối đặc", "file": _WF + "ariblk.ttf"},
    {"key": "elephant", "label": "Elephant",
     "note": "Serif cực mập, nặng", "file": _WF + "ELEPHNT.TTF"},
    {"key": "forte", "label": "Forte",
     "note": "Bút cọ đậm, năng động", "file": _WF + "FORTE.TTF"},
    {"key": "wide_latin", "label": "Wide Latin",
     "note": "Serif rộng cực đậm", "file": _WF + "LATINWD.TTF"},
    {"key": "franklin_demi", "label": "Franklin Gothic Demi",
     "note": "Không chân đậm, kiểu báo chí", "file": _WF + "FRADM.TTF"},
    {"key": "gill_sans_ultra_cond", "label": "Gill Sans Ultra Condensed",
     "note": "Hẹp cực đậm, dồn chữ", "file": _WF + "GILLUBCD.TTF"},
    {"key": "trebuchet_bold", "label": "Trebuchet Bold",
     "note": "Không chân thân thiện, đậm", "file": _WF + "trebucbd.ttf"},
]
DEFAULT_FONT_KEY = "segoe_script_bold"

# Font DÒNG PHỤ theo phong cách kênh.
SUB_FONTS: dict[str, str] = {
    "gabriola": _WF + "Gabriola.ttf",
    "palatino": _WF + "pala.ttf",
    "segoe_print": _WF + "segoepr.ttf",
    "berlin_sans": _WF + "BRLNSR.TTF",
    "ink_free_sub": _WF + "Inkfree.ttf",
    "segoe_semibold": _WF + "seguisb.ttf",
    "copperplate_light": _WF + "COPRGTL.TTF",
    "gill_sans": _WF + "GIL_____.TTF",
    "bell_mt": _WF + "BELL.TTF",
    "goudy": _WF + "GOUDOS.TTF",
    "tw_cen": _WF + "TCM_____.TTF",
    # Mở rộng (2026-10-15) → mỗi look có ~5-6 font phụ riêng, không lặp sớm.
    "garamond": _WF + "GARA.TTF",
    "century": _WF + "CENTURY.TTF",
    "book_antiqua_sub": _WF + "BKANT.TTF",
    "lucida_sans": _WF + "LSANS.TTF",
    "lucida_bright": _WF + "LBRITE.TTF",
    "candara": _WF + "candara.ttf",
    "corbel": _WF + "corbel.ttf",
    "constantia": _WF + "constan.ttf",
    "georgia": _WF + "georgia.ttf",
    "trebuchet": _WF + "trebuc.ttf",
    "verdana": _WF + "verdana.ttf",
    "rockwell": _WF + "ROCK.TTF",
    "gill_cond": _WF + "GILC____.TTF",
    "corsiva_sub": _WF + "MTCORSVA.TTF",
    "franklin_cond": _WF + "FRADMCN.TTF",
}

# Chữ ngoài Latin cơ bản (vd tiếng Việt có dấu) → font trang trí thường thiếu
# glyph → rơi về các font này (đủ dấu tiếng Việt).
_WIDE_FONTS = [_WF + "palab.ttf", _WF + "segoeuib.ttf", _WF + "segoescb.ttf"]
_SEAL_FONTS = [_WF + "msyhbd.ttc", _WF + "msyh.ttc", _WF + "simsun.ttc"]
_SAFE_PUNCT = set("·—–’‘“”…•|")


def font_entry(key: str | None) -> dict | None:
    return next((f for f in FONT_CATALOG if f["key"] == key), None)


def selected_font_key() -> str:
    """Font tiêu đề chung cũ (video_overrides.json → thumbnail_font) — chỉ còn
    dùng cho project không có công thức thumbnail."""
    try:
        from . import config
        key = config.load_overrides().get("thumbnail_font")
    except Exception:
        key = None
    f = font_entry(key)
    if f and Path(f["file"]).exists():
        return key
    return DEFAULT_FONT_KEY


def _selected_title_fonts() -> list[str]:
    f = font_entry(selected_font_key())
    return ([f["file"]] if f else []) + _TITLE_FONTS


def title_font_files(key: str | None) -> list[str]:
    f = font_entry(key)
    return ([f["file"]] if f else []) + _TITLE_FONTS


def sub_font_files(key: str | None) -> list[str]:
    p = SUB_FONTS.get(key or "")
    return ([p] if p else []) + _SUBTITLE_FONTS


@lru_cache(maxsize=64)
def _cmap(path: str) -> frozenset:
    try:
        from fontTools.ttLib import TTFont
        kw = {"fontNumber": 0} if path.lower().endswith(".ttc") else {}
        f = TTFont(path, lazy=True, **kw)
        return frozenset((f.getBestCmap() or {}).keys())
    except Exception:
        return frozenset()


def _covers(path: str, text: str) -> bool:
    """Font có đủ glyph cho `text` không. Không đọc được bảng mã → chỉ chấp nhận
    chữ Latin cơ bản."""
    cm = _cmap(path)
    chars = {c for c in text if not c.isspace()}
    if cm:
        return all(ord(c) in cm for c in chars)
    return all(ord(c) < 0x180 or c in _SAFE_PUNCT for c in chars)


def _usable(candidates: list[str], text: str) -> list[str]:
    """Đưa font đủ glyph lên đầu (font trang trí thiếu dấu → rơi về font rộng)."""
    existing = [p for p in candidates if Path(p).exists()]
    good = [p for p in existing if _covers(p, text)]
    if good:
        return good + [p for p in candidates if p not in good]
    wide = [p for p in _WIDE_FONTS if Path(p).exists() and _covers(p, text)]
    return wide + candidates


# Bảng màu chữ 3D "hấp dẫn" (kiểu cũ): mặt chữ gradient VÀNG GOLD ấm, thân 3D
# teal đậm, viền tối sắc nét, quầng sáng ấm.
_FACE_TOP = (255, 248, 214)       # kem sáng (mặt trên chữ)
_FACE_BOTTOM = (255, 190, 58)     # vàng gold (mặt dưới chữ)
_EXTRUDE = (13, 46, 37)           # teal đậm — thân khối 3D
_OUTLINE = (6, 20, 15)            # viền tối, tạo nét sắc
_GLOW = (255, 176, 32)            # quầng sáng ấm sau chữ
_SUB_FACE = (242, 249, 245)       # dòng phụ: trắng ngà
_DEFAULT_PALETTE = {"face_top": _FACE_TOP, "face_bottom": _FACE_BOTTOM,
                    "extrude": _EXTRUDE, "outline": _OUTLINE, "glow": _GLOW,
                    "sub": _SUB_FACE, "accent": _FACE_BOTTOM, "shade": (10, 20, 15)}
_PAPER = (248, 243, 230)

LAYOUTS = ("left", "right", "top", "bottom")
TREATMENTS = ("gold3d", "soft_glow", "ink_seal", "outline_pop", "banner",
              "clean_shadow", "framed", "neon")


def _vgrad_region(w: int, h: int, y0: int, y1: int, top, bottom):
    """Ảnh RGB gradient dọc: hàng y0→top color, y1→bottom color (clamp ngoài
    khoảng). Dùng để tô mặt chữ theo đúng chiều cao khối tiêu đề → hiệu ứng gold
    đậm hơn là gradient trải cả canvas."""
    from PIL import Image
    span = max(y1 - y0, 1)
    col = Image.new("RGB", (1, h))
    px = col.load()
    for yy in range(h):
        t = min(1.0, max(0.0, (yy - y0) / span))
        px[0, yy] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((w, h))


def _load_font(candidates: list[str], size: int):
    from PIL import ImageFont
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _fit_font(candidates: list[str], text: str, max_w: int, start_size: int,
              min_size: int = 14):
    """Chọn cỡ font lớn nhất để `text` vừa trong `max_w` (giảm dần)."""
    from PIL import ImageDraw, Image
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    size = start_size
    while size > min_size:
        font = _load_font(candidates, size)
        if font is None:
            return None
        w = scratch.textlength(text, font=font)
        if w <= max_w:
            return font
        size -= 2
    return _load_font(candidates, min_size)


def _wrap(text: str, font, draw, max_w: int) -> list[str]:
    """Ngắt dòng theo từ để mỗi dòng vừa max_w."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit_wrapped(candidates: list[str], text: str, draw, max_w: int,
                 start_size: int, max_lines: int = 3, min_size: int = 14):
    """Cỡ font lớn nhất để `text` ngắt thành ≤ max_lines dòng, mỗi dòng vừa
    max_w. Trả (font, lines) hoặc (None, [])."""
    size = start_size
    while size >= min_size:
        font = _load_font(candidates, size)
        if font is None:
            return None, []
        lines = _wrap(text, font, draw, max_w)
        if (len(lines) <= max_lines
                and all(draw.textlength(ln, font=font) <= max_w for ln in lines)):
            return font, lines
        size -= 2
    font = _load_font(candidates, min_size)
    return font, (_wrap(text, font, draw, max_w) if font else [])


def _balance(text: str, font, draw, n: int) -> list[str]:
    """Chia `text` thành đúng `n` dòng sao cho dòng dài nhất ngắn nhất (tránh
    dòng cuối lẻ 1 chữ). Dùng cho kiểu chữ mới; kiểu cũ giữ ngắt tham lam."""
    words = text.split()
    if n <= 1 or len(words) <= n or len(words) > 14:
        return None
    from itertools import combinations
    best, best_w = None, None
    for cuts in combinations(range(1, len(words)), n - 1):
        idx = (0,) + cuts + (len(words),)
        lines = [" ".join(words[idx[i]:idx[i + 1]]) for i in range(n)]
        w = max(draw.textlength(ln, font=font) for ln in lines)
        if best_w is None or w < best_w - 0.5:
            best, best_w = lines, w
    return best


def _region_lum(img, box) -> Optional[float]:
    """Độ sáng trung bình (0–255) của vùng `box` (toạ độ trên khung W×H của box[4:])
    trong ảnh nền. None nếu không đo được (vd nền trong suốt)."""
    from PIL import Image, ImageStat
    try:
        if isinstance(img, (str, Path)):
            if not Path(img).exists():
                return None
            img = Image.open(img)
        x0, y0, x1, y1, W, H = box
        sx, sy = img.size[0] / W, img.size[1] / H
        crop = img.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
        if crop.mode in ("RGBA", "LA") and crop.getchannel("A").getextrema()[1] == 0:
            return None
        return float(ImageStat.Stat(crop.convert("L")).mean[0])
    except Exception:
        return None


def _norm_style(style: dict | None) -> dict:
    st = dict(style or {})
    st["layout"] = st.get("layout") if st.get("layout") in LAYOUTS else "left"
    st["treatment"] = st.get("treatment") if st.get("treatment") in TREATMENTS else "gold3d"
    st["palette"] = {**_DEFAULT_PALETTE, **(st.get("palette") or {})}
    return st


def _shade_mask(W: int, H: int, layout: str, max_alpha: int):
    """Lớp tối/sáng gradient từ mép có chữ vào trong."""
    from PIL import Image
    if layout in ("left", "right"):
        grad = Image.new("L", (W, 1), 0)
        px = grad.load()
        fade_to = int(W * 0.72)
        for x in range(W):
            d = x if layout == "left" else W - 1 - x
            px[x, 0] = 0 if d >= fade_to else int(max_alpha * (1 - d / fade_to))
    else:
        grad = Image.new("L", (1, H), 0)
        px = grad.load()
        fade_to = int(H * 0.55)
        for y in range(H):
            d = y if layout == "top" else H - 1 - y
            px[0, y] = 0 if d >= fade_to else int(max_alpha * (1 - d / fade_to) ** 1.3)
    return grad.resize((W, H))


def _compose_title(base, title: str, subtitle: str, tf: list[str],
                   sf: list[str], style: dict | None = None, bg_hint=None):
    """Vẽ lớp nền gradient + tiêu đề + dòng phụ lên ảnh RGBA `base` theo `style`.
    Trả về ảnh RGBA MỚI đã composite, hoặc None nếu không nạp được font.

    Dùng CHUNG cho: (1) thumbnail — `base` là ảnh nền; (2) overlay video —
    `base` trong suốt để ffmpeg chồng chữ lên clip intro (`bg_hint` = ảnh bìa để
    đo độ sáng vùng chữ). Nhờ vậy chữ trên intro video khớp y hệt thumbnail."""
    from PIL import Image, ImageDraw, ImageFilter, ImageChops

    st = _norm_style(style)
    pal = st["palette"]
    layout, treat = st["layout"], st["treatment"]
    W, H = base.size
    title = title.upper() if st.get("upper") else title
    subtitle = (subtitle or "").strip()
    if treat in ("clean_shadow", "framed") and subtitle:
        subtitle = subtitle.upper()
    tf = _usable(tf, title)
    sf = _usable(sf, subtitle) if subtitle else sf

    margin = int(W * 0.06)
    side = layout in ("left", "right")
    text_max_w = int(W * (0.62 if side else 0.84))
    title_start = int(H * (0.16 if side else 0.115))
    sub_start = int(H * (0.062 if side else 0.054))
    max_lines = 3 if side else 2
    align = layout if side else "center"
    box = {"left": (0, 0, int(W * 0.62), H), "right": (int(W * 0.38), 0, W, H),
           "top": (0, 0, W, int(H * 0.36)), "bottom": (0, int(H * 0.64), W, H)}[layout]
    lum = _region_lum(bg_hint if bg_hint is not None else base, (*box, W, H))

    # 1) Lớp nền gradient từ mép chữ: đảm bảo tương phản, alpha theo độ sáng vùng.
    dark_text = treat == "ink_seal" and lum is not None and lum > 150
    if lum is None:
        max_alpha = 150
    elif dark_text:
        max_alpha = int(min(110, max(50, 40 + (lum - 150) * 0.6)))
    else:
        max_alpha = int(min(185, max(70, 70 + (lum - 60) * 0.9)))
    if treat == "banner":
        max_alpha = int(max_alpha * 0.55)
    shade_rgb = _PAPER if dark_text else tuple(pal["shade"])
    shade = Image.new("RGBA", (W, H), shade_rgb + (0,))
    shade.putalpha(_shade_mask(W, H, layout, max_alpha))
    base = Image.alpha_composite(base, shade)

    draw = ImageDraw.Draw(base)

    # 2) Cỡ + ngắt dòng tiêu đề: cỡ LỚN NHẤT mà ngắt được ≤ max_lines dòng.
    title_font, title_lines = _fit_wrapped(tf, title, draw, text_max_w,
                                           title_start, max_lines=max_lines)
    if title_font is None:
        return None
    if style is not None and len(title_lines) > 1:
        bal = _balance(title, title_font, draw, len(title_lines))
        if bal:
            title_lines = bal

    sub_font = None
    sub_lines: list[str] = []
    if subtitle:
        sub_font = _fit_font(sf, subtitle, text_max_w, sub_start)
        if sub_font is not None:
            sub_lines = _wrap(subtitle, sub_font, draw, text_max_w)

    def _line_h(font) -> int:
        b = draw.textbbox((0, 0), "Ag", font=font)
        return b[3] - b[1]

    title_lh = _line_h(title_font)
    gap = int(title_lh * 0.22)
    sub_lh = _line_h(sub_font) if sub_font else 0
    sub_gap = int(title_lh * (0.62 if treat in ("clean_shadow", "framed") else 0.5))
    pill = treat == "outline_pop" and bool(sub_lines)
    pill_pad = int(sub_lh * 0.45) if pill else 0

    block_h = len(title_lines) * (title_lh + gap) - gap
    if sub_lines:
        block_h += sub_gap + pill_pad + len(sub_lines) * (sub_lh + gap) - gap

    if side:
        y = max(margin, (H - block_h) // 2)
    elif layout == "top":
        y = int(H * 0.07)
    else:
        y = H - int(H * 0.08) - block_h - pill_pad

    def _x(width: float) -> int:
        if align == "left":
            return margin
        if align == "right":
            return int(W - margin - width)
        return int((W - width) / 2)

    t_pos = []
    yy = y
    for ln in title_lines:
        t_pos.append((_x(draw.textlength(ln, font=title_font)), yy, ln))
        yy += title_lh + gap
    title_top, title_bot = y, yy - gap
    s_pos = []
    yy = title_bot + sub_gap + pill_pad // 2
    for ln in sub_lines:
        s_pos.append((_x(draw.textlength(ln, font=sub_font)), yy, ln))
        yy += sub_lh + gap
    blk_x0 = min(p[0] for p in t_pos + s_pos)
    blk_x1 = max(p[0] + draw.textlength(p[2], font=title_font if p in t_pos else sub_font)
                 for p in t_pos + s_pos)
    blk_y1 = (s_pos[-1][1] + sub_lh) if s_pos else title_bot

    stroke_w = max(3, int(title_lh * 0.055))
    depth = max(4, int(title_lh * 0.12))

    def layer():
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    def _draw_title(target, dx, dy, fill, stroke_fill=None, sw=0):
        for x, ty, ln in t_pos:
            target.text((x + dx, ty + dy), ln, font=title_font, fill=fill,
                        stroke_width=sw, stroke_fill=stroke_fill if stroke_fill else fill)

    def _draw_sub(target, dx, dy, fill, stroke_fill=None, sw=0):
        for x, sy, ln in s_pos:
            target.text((x + dx, sy + dy), ln, font=sub_font, fill=fill,
                        stroke_width=sw, stroke_fill=stroke_fill if stroke_fill else fill)

    def _blur_layer(painter, radius):
        lay = layer()
        painter(ImageDraw.Draw(lay))
        return lay.filter(ImageFilter.GaussianBlur(max(1, radius)))

    def _face(top, bottom):
        """Tô mặt chữ gradient qua mask glyph."""
        nonlocal base
        mask = Image.new("L", (W, H), 0)
        _draw_title(ImageDraw.Draw(mask), 0, 0, 255)
        grad = _vgrad_region(W, H, title_top, title_bot, top, bottom).convert("RGBA")
        lay = layer()
        lay.paste(grad, (0, 0), mask)
        base = Image.alpha_composite(base, lay)

    def _comp(lay):
        nonlocal base
        base = Image.alpha_composite(base, lay)

    def _a(rgb, a=255):
        return tuple(rgb) + (a,)

    # ---- Nền khối chữ (banner) ----
    if treat == "banner":
        pad_x, pad_y = int(title_lh * 0.55), int(title_lh * 0.38)
        x0, y0 = int(blk_x0 - pad_x), int(title_top - pad_y)
        x1, y1 = int(blk_x1 + pad_x), int(blk_y1 + pad_y)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        rad = int(title_lh * 0.35)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), rad, fill=255)
        if base.getchannel("A").getextrema()[0] > 0:            # có ảnh nền → kính mờ
            frosted = base.filter(ImageFilter.GaussianBlur(int(H * 0.02)))
            base.paste(frosted, (0, 0), mask)
        tint = Image.new("RGBA", (W, H), _a(pal["shade"], 0))
        tint.putalpha(mask.point(lambda v: int(v * 0.58)))
        _comp(tint)
        border = layer()
        ImageDraw.Draw(border).rounded_rectangle(
            (x0, y0, x1, y1), rad, outline=_a(pal["accent"], 235),
            width=max(2, int(H * 0.004)))
        _comp(border)

    # ---- Dựng tiêu đề theo kiểu ----
    if treat == "gold3d":
        _comp(_blur_layer(lambda d: _draw_title(d, depth + 2, depth + 5, (0, 0, 0, 175),
                                                sw=stroke_w), depth * 1.4))
        _comp(_blur_layer(lambda d: _draw_title(d, 0, 0, _a(pal["glow"]),
                                                _a(pal["glow"]), stroke_w), depth * 2.0))
        ext = layer()
        ed = ImageDraw.Draw(ext)
        for i in range(depth, 0, -1):
            _draw_title(ed, i, i, _a(pal["extrude"]))
        _comp(ext)
        out = layer()
        _draw_title(ImageDraw.Draw(out), 0, 0, _a(pal["outline"]), _a(pal["outline"]), stroke_w)
        _comp(out)
        _face(pal["face_top"], pal["face_bottom"])
        k = max(1, depth // 2)
        m_full = Image.new("L", (W, H), 0)
        _draw_title(ImageDraw.Draw(m_full), 0, 0, 255)
        m_down = Image.new("L", (W, H), 0)
        _draw_title(ImageDraw.Draw(m_down), 0, k, 255)
        edge = ImageChops.subtract(m_full, m_down).point(lambda v: int(v * 0.55))
        hi = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        hi.putalpha(edge)
        _comp(hi)
    elif treat == "soft_glow":
        _comp(_blur_layer(lambda d: _draw_title(d, 0, int(title_lh * 0.04), (0, 0, 0, 140),
                                                sw=stroke_w), title_lh * 0.10))
        g = _blur_layer(lambda d: _draw_title(d, 0, 0, _a(pal["glow"], 230),
                                              _a(pal["glow"], 230), stroke_w * 2),
                        title_lh * 0.22)
        _comp(g)
        _comp(g)
        out = layer()
        sw = max(2, stroke_w // 2)
        _draw_title(ImageDraw.Draw(out), 0, 0, _a(pal["outline"], 220), _a(pal["outline"], 220), sw)
        _comp(out)
        _face(pal["face_top"], pal["face_bottom"])
    elif treat == "ink_seal":
        if dark_text:
            _comp(_blur_layer(lambda d: _draw_title(d, 0, 0, _a(_PAPER, 235), _a(_PAPER, 235),
                                                    stroke_w * 2), title_lh * 0.16))
            ink = layer()
            _draw_title(ImageDraw.Draw(ink), 0, 0, _a(pal["outline"]))
            _comp(ink)
        else:
            _comp(_blur_layer(lambda d: _draw_title(d, 0, int(title_lh * 0.05), (0, 0, 0, 190),
                                                    (0, 0, 0, 190), stroke_w), title_lh * 0.12))
            _face(pal["face_top"], pal["face_bottom"])
        glyph = (st.get("seal") or "静")[:1]
        s = int(title_lh * 0.62)
        last_x, last_y, last_ln = t_pos[-1]
        lw = draw.textlength(last_ln, font=title_font)
        sy = int(last_y + title_lh - s)
        sx = (int(last_x - s - title_lh * 0.25) if align == "right"
              else int(last_x + lw + title_lh * 0.25))
        sx = max(4, min(W - s - 4, sx))
        seal = layer()
        sd = ImageDraw.Draw(seal)
        sd.rounded_rectangle((sx, sy, sx + s, sy + s), max(2, s // 8), fill=_a(pal["accent"], 238))
        gfont = _load_font(_SEAL_FONTS, int(s * 0.72))
        if gfont is not None and any(Path(p).exists() for p in _SEAL_FONTS):
            gb = sd.textbbox((0, 0), glyph, font=gfont)
            gx = sx + (s - (gb[2] - gb[0])) / 2 - gb[0]
            gy = sy + (s - (gb[3] - gb[1])) / 2 - gb[1]
            sd.text((gx, gy), glyph, font=gfont, fill=_a(_PAPER, 245))
        else:
            sd.rounded_rectangle((sx + s // 5, sy + s // 5, sx + s - s // 5, sy + s - s // 5),
                                 max(1, s // 10), outline=_a(_PAPER, 245), width=max(2, s // 12))
        _comp(seal)
    elif treat == "outline_pop":
        big = max(5, int(title_lh * 0.09))
        o = max(4, int(title_lh * 0.07))
        hard = layer()
        _draw_title(ImageDraw.Draw(hard), o, o, _a(pal["extrude"]), _a(pal["extrude"]), big)
        _comp(hard)
        out = layer()
        _draw_title(ImageDraw.Draw(out), 0, 0, _a(pal["outline"]), _a(pal["outline"]), big)
        _comp(out)
        _face(pal["face_top"], pal["face_bottom"])
    elif treat == "banner":
        _comp(_blur_layer(lambda d: _draw_title(d, 0, int(title_lh * 0.04), (0, 0, 0, 120)),
                          title_lh * 0.06))
        face = layer()
        _draw_title(ImageDraw.Draw(face), 0, 0, _a(pal["face_top"]))
        _comp(face)
    elif treat in ("clean_shadow", "framed"):
        off = max(2, int(title_lh * 0.04))
        _comp(_blur_layer(lambda d: _draw_title(d, off, off, (0, 0, 0, 200), (0, 0, 0, 200),
                                                max(1, stroke_w // 2)), title_lh * 0.08))
        mix = tuple(int(a * 0.7 + b * 0.3) for a, b in zip(pal["face_top"], pal["face_bottom"]))
        _face(pal["face_top"], mix)
        deco = layer()
        dd = ImageDraw.Draw(deco)
        thick = max(2, int(H * 0.004))
        if sub_lines:                              # vạch nhấn giữa tiêu đề và dòng phụ
            rule_w = int(min((blk_x1 - blk_x0) * 0.4, W * 0.22))
            ry = int(title_bot + sub_gap * 0.45)
            rx = (blk_x0 if align == "left" else
                  int(blk_x1 - rule_w) if align == "right" else int((W - rule_w) / 2))
            dd.rectangle((rx, ry, rx + rule_w, ry + thick), fill=_a(pal["accent"], 235))
        if treat == "framed":                       # góc khung mảnh quanh khối chữ
            p = int(title_lh * 0.45)
            L = int(title_lh * 0.6)
            x0, y0 = int(blk_x0 - p), int(title_top - p)
            x1, y1 = int(blk_x1 + p), int(blk_y1 + p)
            col = _a(pal["accent"], 235)
            for (cx, cy, sx_, sy_) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                                       (x0, y1, 1, -1), (x1, y1, -1, -1)):
                dd.line([(cx, cy), (cx + sx_ * L, cy)], fill=col, width=thick)
                dd.line([(cx, cy), (cx, cy + sy_ * L)], fill=col, width=thick)
        _comp(deco)
    elif treat == "neon":
        outer = _blur_layer(lambda d: _draw_title(d, 0, 0, _a(pal["glow"]), _a(pal["glow"]),
                                                  stroke_w * 2), title_lh * 0.25)
        _comp(outer)
        _comp(outer)
        _comp(_blur_layer(lambda d: _draw_title(d, 0, 0, _a(pal["glow"]), _a(pal["glow"]),
                                                stroke_w), title_lh * 0.07))
        ring = layer()
        _draw_title(ImageDraw.Draw(ring), 0, 0, _a(pal["glow"]), _a(pal["glow"]),
                    max(2, stroke_w // 2))
        _comp(ring)
        face = layer()
        _draw_title(ImageDraw.Draw(face), 0, 0, _a(pal["face_top"]))
        _comp(face)

    # ---- Dòng phụ ----
    if sub_lines:
        sub_sw = max(2, stroke_w // 2)
        if pill:
            pl = layer()
            pd = ImageDraw.Draw(pl)
            for x, sy, ln in s_pos:
                lw = draw.textlength(ln, font=sub_font)
                b = draw.textbbox((x, sy), ln, font=sub_font)
                pd.rounded_rectangle((x - pill_pad, b[1] - pill_pad // 2,
                                      x + lw + pill_pad, b[3] + pill_pad // 2),
                                     (b[3] - b[1] + pill_pad) // 2, fill=_a(pal["accent"], 240))
            _comp(pl)
            ft = layer()
            _draw_sub(ImageDraw.Draw(ft), 0, 0, _a(pal["outline"]))
            _comp(ft)
        elif treat == "ink_seal" and dark_text:
            _comp(_blur_layer(lambda d: _draw_sub(d, 0, 0, _a(_PAPER, 220), _a(_PAPER, 220),
                                                  sub_sw * 2), sub_lh * 0.2))
            ft = layer()
            _draw_sub(ImageDraw.Draw(ft), 0, 0, _a(pal["outline"]))
            _comp(ft)
        elif treat == "neon":
            _comp(_blur_layer(lambda d: _draw_sub(d, 0, 0, _a(pal["accent"]), _a(pal["accent"]),
                                                  sub_sw), sub_lh * 0.25))
            ft = layer()
            _draw_sub(ImageDraw.Draw(ft), 0, 0, _a(pal["sub"]))
            _comp(ft)
        else:
            _comp(_blur_layer(lambda d: _draw_sub(d, 2, 3, (0, 0, 0, 170), (0, 0, 0, 170),
                                                  sub_sw), sub_sw * 2))
            ft = layer()
            thin = treat in ("banner", "clean_shadow", "framed")
            _draw_sub(ImageDraw.Draw(ft), 0, 0, _a(pal["sub"]),
                      None if thin else _a(pal["outline"]), 0 if thin else sub_sw)
            _comp(ft)

    return base


def render_title(
    image_path: str | Path,
    title: str,
    subtitle: str = "",
    *,
    title_fonts: Optional[list[str]] = None,
    subtitle_fonts: Optional[list[str]] = None,
    style: Optional[dict] = None,
    log=None,
) -> bool:
    """
    Overlay `title` (lớn) + `subtitle` (nhỏ) lên ảnh, lưu đè lại chính file.
    `style` (thumbnail_recipe.render_style) chọn bố cục/kiểu chữ/màu/font; không
    có → kiểu cũ (căn trái, chữ vàng 3D). Trả True nếu vẽ được.
    """
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    title = (title or "").strip()
    if not title:
        return False
    p = Path(image_path)
    if not p.exists():
        _log(f"thumbnail: không thấy ảnh {p}")
        return False

    try:
        from PIL import Image
    except Exception as e:
        _log(f"thumbnail: thiếu Pillow ({e}) — bỏ qua overlay")
        return False

    st = style or {}
    tf = title_fonts or st.get("title_fonts") or _selected_title_fonts()
    sf = subtitle_fonts or st.get("sub_fonts") or _SUBTITLE_FONTS

    try:
        base = Image.open(p).convert("RGBA")
        out = _compose_title(base, title, subtitle or "", tf, sf, style=style)
        if out is None:
            _log("thumbnail: không nạp được font — bỏ qua overlay")
            return False
        out.convert("RGB").save(p, format="PNG")
        kind = (f" [{st.get('layout', 'left')} · {st.get('treatment', 'gold3d')}]"
                if style else "")
        _log(f"thumbnail: đã overlay tiêu đề '{title}'"
             + (f" · '{subtitle}'" if subtitle.strip() else "") + kind)
        return True
    except Exception as e:
        _log(f"thumbnail: lỗi overlay ({type(e).__name__}: {e}) — giữ ảnh gốc")
        return False


def render_font_preview(font_key: str, out_path: str | Path, title: str,
                        subtitle: str = "", background: str | Path | None = None,
                        max_w: int = 720, style: Optional[dict] = None) -> bool:
    """Ảnh mẫu JPEG cho bộ chọn font: vẽ tiêu đề bằng ĐÚNG `font_key` lên ảnh bìa
    của project (hoặc nền gradient tối nếu chưa có ảnh bìa), theo kiểu chữ của
    công thức thumbnail project (`style`)."""
    f = font_entry(font_key)
    if not f or not Path(f["file"]).exists():
        return False
    from PIL import Image
    if background and Path(background).exists():
        base = Image.open(background).convert("RGBA")
    else:
        base = _vgrad_region(1280, 720, 0, 720, (32, 58, 52), (8, 16, 14)
                             ).convert("RGBA")
    st = dict(style or {})
    out = _compose_title(base, (title or "Healing Music").strip(), subtitle,
                         [f["file"]] + _TITLE_FONTS,
                         st.get("sub_fonts") or _SUBTITLE_FONTS, style=style)
    if out is None:
        return False
    out = out.convert("RGB")
    out.thumbnail((max_w, max_w))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, format="JPEG", quality=85)
    return True


def render_title_overlay_png(
    width: int,
    height: int,
    title: str,
    subtitle: str,
    out_path: str | Path,
    *,
    title_fonts: Optional[list[str]] = None,
    subtitle_fonts: Optional[list[str]] = None,
    style: Optional[dict] = None,
    bg_hint: str | Path | None = None,
    log=None,
) -> bool:
    """Tạo PNG TRONG SUỐT (RGBA) chỉ gồm lớp nền gradient + tiêu đề, kích thước
    width×height — để ffmpeg chồng lên clip intro. Cần thiết vì Veo tạo lại cảnh
    từ ảnh 0 nên chữ nướng sẵn trong ảnh 0 KHÔNG còn ở clip_00. `bg_hint` = ảnh
    bìa (đo độ sáng vùng chữ cho khớp thumbnail). Trả True nếu tạo được file."""
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    title = (title or "").strip()
    if not title or width <= 0 or height <= 0:
        return False
    try:
        from PIL import Image
    except Exception as e:
        _log(f"thumbnail: thiếu Pillow ({e}) — bỏ qua overlay intro")
        return False

    st = style or {}
    tf = title_fonts or st.get("title_fonts") or _selected_title_fonts()
    sf = subtitle_fonts or st.get("sub_fonts") or _SUBTITLE_FONTS
    try:
        base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        out = _compose_title(base, title, subtitle or "", tf, sf, style=style,
                             bg_hint=bg_hint)
        if out is None:
            _log("thumbnail: không nạp được font — bỏ qua overlay intro")
            return False
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        out.save(out_path, format="PNG")          # GIỮ alpha
        return True
    except Exception as e:
        _log(f"thumbnail: lỗi tạo overlay intro ({type(e).__name__}: {e})")
        return False
