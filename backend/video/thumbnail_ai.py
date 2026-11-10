"""
video/thumbnail_ai.py — Thumbnail bằng AI qua API (mặc định ShopAIKey).

Model ảnh vẽ CẢ ảnh lẫn chữ tiêu đề: gửi ảnh bìa SẠCH (images/0.png) làm ảnh
tham chiếu + prompt tiếng Anh mô tả kiểu chữ theo công thức thumbnail của video
(bố cục, kiểu chữ, bảng màu, font — thumbnail_recipe / channel_looks) → model
trả 1 ảnh 16:9 đã có chữ → cắt/thu về 1280x720 (< 2 MB) → final/thumbnail.png.

3 giao thức (tự chọn theo tên model):
  • gemini  — Gemini native  POST {root}/v1beta/models/{model}:generateContent
              (Nano Banana Pro / Nano Banana 2 …): ảnh tham chiếu inlineData,
              imageConfig aspectRatio 16:9 + imageSize 2K.
  • images  — OpenAI Images  POST {root}/v1/images/edits (gpt-image-1 / 1.5).
  • chat    — OpenAI Chat    POST {root}/v1/chat/completions (gpt-image-*-all,
              *-x …): model trả link/ảnh base64 trong nội dung trả lời.

Kiểm tra chữ: model vision của cấu hình API chính đọc lại chữ trên ảnh; tiêu đề
sai chính tả → thử lại 1 lần, vẫn sai → báo lỗi để service rơi về chữ PIL.
Tối đa 2 lượt gọi model ảnh (tốn phí) cho mỗi lần tạo.

Bộ nhớ đệm: final/_thumbnail_ai.png + final/thumbnail_ai.json (khoá = tiêu đề,
dòng phụ, model, công thức, ảnh bìa) → ghép lại video KHÔNG gọi lại API (không
tốn thêm phí) nếu không có gì đổi. Nút «🎨 Tạo thumbnail bằng AI» = force (bản mới).

Máy vẽ (2026-10-23): `DrawEngine` — API ở file này, hoặc ChatGPT web
(chatgpt_thumbnail.py, MẶC ĐỊNH) dùng chung prompt / cắt ảnh / kiểm tra chữ / cache.
Cài đặt `thumb_engine`: chatgpt | api | pil.

KHÔNG bao giờ ghi API key / header xác thực vào log hay file.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import io
import json
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

DEFAULT_BASE = "https://api.shopaikey.com"
DEFAULT_MODEL = "gemini-3-pro-image-preview"
PROMPT_VERSION = 2              # 2 = model tự thiết kế chữ + style (2026-10-25)
TIMEOUT = 300.0
OUT_SIZE = (1280, 720)
MAX_BYTES = 1_950_000          # YouTube giới hạn thumbnail 2 MB
MAX_CALLS = 2                  # tối đa 2 lượt gọi model ảnh / lần tạo
META_NAME = "thumbnail_ai.json"
CACHE_NAME = "_thumbnail_ai.png"
TITLE_MIN_RATIO = 0.9
SUB_MIN_RATIO = 0.8

# Máy vẽ thumbnail mặc định khi ghép video (Cài đặt → Thumbnail).
ENGINES = ("chatgpt", "api", "pil")
DEFAULT_ENGINE = "chatgpt"
ENGINE_CHOICES: list[dict] = [
    {"key": "chatgpt", "label": "ChatGPT web (tài khoản ChatGPT của bạn)",
     "note": "Mặc định — ChatGPT vẽ ảnh + chữ trong cửa sổ trình duyệt riêng của tool; "
             "không tốn phí API (dùng lượt tạo ảnh của gói ChatGPT)."},
    {"key": "api", "label": "API model ảnh (ShopAIKey…)",
     "note": "Tốn phí theo model, cần API key bên dưới."},
    {"key": "pil", "label": "Chữ font của tool (PIL)",
     "note": "Miễn phí, không dùng AI — chữ vẽ bằng font máy lên ảnh bìa."},
]

# Model gợi ý (giá ShopAIKey 2026-10-21, USD/ảnh, đã gồm hệ số nhóm). Nhóm của
# key quyết định model nào gọi được — "Image Only" KHÔNG có model Gemini.
MODEL_CHOICES: list[dict] = [
    {"key": "gemini-3-pro-image-preview", "label": "Nano Banana Pro (Gemini 3 Pro Image)",
     "group": "Gemini API", "price_usd": 0.6405, "protocol": "gemini",
     "note": "Chữ đẹp + đúng chính tả nhất, 16:9 2K, giữ ảnh bìa — khuyên dùng"},
    {"key": "gemini-3.1-flash-image-preview", "label": "Nano Banana 2 (Gemini 3.1 Flash Image)",
     "group": "Gemini API", "price_usd": 0.321, "protocol": "gemini",
     "note": "Rẻ bằng nửa, chữ khá tốt"},
    {"key": "gemini-2.5-flash-image", "label": "Nano Banana (Gemini 2.5 Flash Image)",
     "group": "Gemini API", "price_usd": 0.15, "protocol": "gemini",
     "note": "Rẻ nhất nhóm Gemini, chữ dài dễ sai"},
    {"key": "gpt-image-2-all", "label": "GPT Image 2 (all)",
     "group": "Image Only / Cheap", "price_usd": 0.36, "protocol": "chat",
     "note": "Tốt nhất trong nhóm Image Only ($0.144 ở nhóm Cheap)"},
    {"key": "gpt-image-1.5-all", "label": "GPT Image 1.5 (all)",
     "group": "Image Only / Cheap", "price_usd": 0.45, "protocol": "chat",
     "note": "Chữ tốt, nhóm Image Only"},
    {"key": "gpt-image-1-all", "label": "GPT Image 1 (all)",
     "group": "Image Only / Cheap", "price_usd": 0.30, "protocol": "chat",
     "note": "Đời cũ, chữ kém hơn"},
]

_IMAGES_API_MODELS = {"gpt-image-1", "gpt-image-1.5", "gpt-image-1-mini"}


class ThumbAIError(RuntimeError):
    """Lỗi tạo thumbnail AI (thông điệp tiếng Việt, KHÔNG chứa key)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class ThumbAISettings:
    api_key: str = field(default="", repr=False)   # KHÔNG in key ra log
    base_url: str = DEFAULT_BASE
    model: str = DEFAULT_MODEL
    enabled: bool = True
    verify: bool = True
    engine: str = DEFAULT_ENGINE      # chatgpt | api | pil — máy vẽ khi ghép video

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.model)

    @property
    def active(self) -> bool:
        """Dùng AI khi ghép video: đã bật + đã có key."""
        return bool(self.enabled and self.ready)


def _db_path() -> Path:
    from backend.database import DB_PATH
    return Path(DB_PATH)


def load_settings(db_path: str | Path | None = None) -> ThumbAISettings:
    """Đọc appsettings id=1 bằng sqlite trực tiếp (chạy được trong worker
    thread). Chưa có cột/bảng → mặc định (chưa có key = không hoạt động)."""
    cols = ("thumb_api_key, thumb_api_base_url, thumb_api_model, "
            "thumb_ai_enabled, thumb_ai_verify")
    row = None
    try:
        con = sqlite3.connect(str(db_path or _db_path()))
        try:
            try:
                row = con.execute(f"SELECT {cols}, thumb_engine FROM appsettings "
                                  "WHERE id=1").fetchone()
            except sqlite3.OperationalError:     # DB chưa chạy M014
                old = con.execute(f"SELECT {cols} FROM appsettings WHERE id=1").fetchone()
                row = (*old, None) if old else None
        finally:
            con.close()
    except sqlite3.Error:
        return ThumbAISettings()
    if not row:
        return ThumbAISettings()
    key, base, model, enabled, verify, engine = row
    engine = (engine or DEFAULT_ENGINE).strip().lower()
    return ThumbAISettings(
        api_key=(key or "").strip(), base_url=(base or DEFAULT_BASE).strip(),
        model=(model or DEFAULT_MODEL).strip(),
        enabled=True if enabled is None else bool(enabled),
        verify=True if verify is None else bool(verify),
        engine=engine if engine in ENGINES else DEFAULT_ENGINE)


def api_root(base_url: str | None) -> str:
    """'https://api.shopaikey.com/v1/' → 'https://api.shopaikey.com'."""
    root = (base_url or DEFAULT_BASE).strip().rstrip("/")
    for suffix in ("/v1beta", "/v1"):
        if root.lower().endswith(suffix):
            root = root[: -len(suffix)]
    return root.rstrip("/") or DEFAULT_BASE


def protocol_for(model: str) -> str:
    m = (model or "").strip().lower()
    if m.startswith(("gemini", "nano-banana")):
        return "gemini"
    if m in _IMAGES_API_MODELS:
        return "images"
    return "chat"


def model_info(model: str) -> dict:
    return next((c for c in MODEL_CHOICES if c["key"] == model),
                {"key": model, "label": model, "group": "?", "price_usd": None,
                 "protocol": protocol_for(model), "note": ""})


# ── Prompt (100% tiếng Anh) ──────────────────────────────────────

LAYOUT_PLACEMENT = {
    "left": ("Place the text block in the LEFT part of the frame over the calm empty "
             "area, left-aligned and vertically centred"),
    "right": ("Place the text block in the RIGHT part of the frame over the calm empty "
              "area, right-aligned and vertically centred"),
    "top": ("Place the text block across the TOP of the frame over the calm empty sky "
            "area, centred horizontally"),
    "bottom": ("Place the text block across the BOTTOM of the frame over the calm, "
               "slightly darker area, centred horizontally"),
}

TREATMENT_TEXT = {
    "gold3d": ("bold 3D extruded lettering: the letter face has a vertical gradient "
               "from {face_top} to {face_bottom}, a deep {extrude} extrusion, a crisp "
               "{outline} outline and a soft {glow} glow behind the letters"),
    "soft_glow": ("luminous lettering in {face_top} fading to {face_bottom}, wrapped in "
                  "a soft {glow} halo glow, with a thin {outline} edge for legibility"),
    "ink_seal": ("expressive brush-calligraphy lettering in {face_top} with dark "
                 "{outline} ink edges, like ink on rice paper"),
    "outline_pop": ("chunky sticker-style lettering, face {face_top} to {face_bottom}, "
                    "with a thick {outline} outline and a small offset drop shadow"),
    "banner": ("lettering in {face_top} set on a soft rounded, semi-transparent "
               "{shade} banner card with a thin {accent} edge"),
    "clean_shadow": ("clean, elegant lettering in {face_top} with a soft {shade} drop "
                     "shadow, minimal and refined"),
    "framed": ("lettering in {face_top} inside a thin, elegant {accent} rectangular "
               "frame with small corner ornaments"),
    "neon": ("glowing neon-tube lettering: bright {face_top} core with an intense "
             "{glow} neon glow and a faint reflection"),
}

FONT_FEEL = {
    "segoe_script_bold": "a rounded, friendly handwritten script",
    "mistral": "a bold, energetic brush-pen script",
    "brush_script": "a soft slanted brush script",
    "lucida_calligraphy": "a classic, refined calligraphy script",
    "segoe_print_bold": "a casual hand-printed marker style",
    "vivaldi": "an ornate flourished calligraphy script",
    "papyrus": "an ancient, rough-edged textured serif",
    "ink_free": "a free-flowing ink pen handwriting",
    "rage_italic": "a sharp, sinewy steel-pen italic script",
    "kunstler_script": "a very fine, elegant copperplate script",
    "old_english": "an old blackletter gothic style",
    "cooper_black": "a soft, rounded, chunky retro serif",
    "berlin_sans_demi": "a rounded, compact, friendly sans-serif",
    "perpetua_titling": "elegant chiselled stone-carved capitals",
    "castellar": "engraved decorative inline capitals",
    "bodoni_bold": "a high-contrast luxury didone serif",
    "palatino_bold": "a graceful humanist bold serif",
    "copperplate": "wide engraved copperplate gothic capitals",
    "harrington": "a decorative Victorian-era display serif",
    "bell_mt_bold": "a classic bookish bold serif",
    "high_tower": "a calligraphic Venetian old-style serif",
    "segoe_ui_black": "a heavy, modern geometric sans-serif",
    "gill_sans_ultra": "an ultra-bold, playful humanist sans-serif",
    "showcard": "a bold vintage poster display face",
    "tw_cen_cond_xb": "a tall condensed extra-bold geometric sans-serif",
    "segoe_ui_bold": "a clean, modern bold sans-serif",
    "freestyle_script": "a quick, casual connected script",
    "monotype_corsiva": "a classic chancery italic script",
    "pristina": "a light, airy handwritten script",
    "edwardian_script": "a formal, elegant Edwardian copperplate script",
    "eras_bold": "a soft, slightly slanted modern sans-serif",
    "bauhaus_93": "a bold, rounded Bauhaus-style display face",
    "bradley_hand": "a relaxed, natural handwriting",
    "lucida_handwriting": "a flowing connected handwriting",
    "snap_itc": "a playful, bubbly cartoon display face",
    "engravers_mt": "wide, engraved formal capitals",
    "felix_titling": "classical Roman inscription capitals",
    "modern_no20": "a sharp, high-contrast editorial serif",
    "georgia_bold": "a sturdy, readable bold serif",
    "times_bold": "a timeless bold newspaper serif",
    "garamond_bold": "an elegant Renaissance bold serif",
    "goudy_stout": "an ultra-heavy decorative display serif",
    "book_antiqua": "a calligraphic book serif",
    "colonna": "an inline engraved display capital style",
    "matura_script": "a bold, brushy script with capitals",
    "imprint_shadow": "a classic serif with an engraved shadow",
    "impact": "a heavy, condensed impact sans-serif",
    "arial_black": "a heavy, wide grotesque sans-serif",
    "elephant": "a heavy, high-contrast fat-face serif",
    "forte": "a bold, retro connected brush script",
    "wide_latin": "an extremely wide, heavy display serif",
    "franklin_demi": "a strong, editorial gothic sans-serif",
    "gill_sans_ultra_cond": "an ultra-condensed, heavy sans-serif",
    "trebuchet_bold": "a friendly, humanist bold sans-serif",
}


def _hex(rgb) -> str:
    try:
        r, g, b = (int(x) for x in list(rgb)[:3])
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#FFFFFF"


def _palette_hex(recipe: dict | None) -> dict:
    from . import channel_looks as looks
    from .thumbnail import _DEFAULT_PALETTE
    pal = None
    if recipe:
        lk = looks.look_info(recipe.get("look"))
        pal = lk["palettes"].get(recipe.get("palette")) or next(iter(lk["palettes"].values()))
    pal = {**_DEFAULT_PALETTE, **(pal or {})}
    return {k: _hex(v) for k, v in pal.items()}


def _font_feel(key: str | None) -> str:
    if key and key in FONT_FEEL:
        return FONT_FEEL[key]
    from .thumbnail import font_entry
    ent = font_entry(key)
    return (f"a typeface in the spirit of {ent['label']}" if ent
            else "an elegant, highly legible display typeface")


def build_prompt(title: str, subtitle: str, recipe: dict | None, *,
                 native_wide: bool = True, retry_note: str = "") -> str:
    """Prompt tiếng Anh cho model ảnh: giữ ảnh bìa, thêm chữ.

    Mặc định (2026-10-25) model TỰ thiết kế chữ theo hướng chữ + style riêng của
    video (recipe ai_lettering/ai_design) — không ép font/kiểu chữ/màu PIL. Chỉ
    khi người dùng KHOÁ font tay mới dặn đúng font + kiểu chữ + màu của công thức."""
    from . import channel_looks as looks
    from . import thumbnail_recipe as tr
    rec = recipe or {}
    ab_variant = max(1, min(3, int(rec.get("ab_variant") or 1)))
    lk = looks.look_info(rec.get("look"))
    layout = rec.get("layout") or "left"
    title_txt = title.upper() if rec.get("upper") else title
    channel_name = (rec.get("channel_name") or "MUSIC CHANNEL").strip()
    locked = rec.get("font_lock") or ""
    lettering = tr.ai_text(rec.get("ai_lettering")) or (
        "elegant custom display lettering that suits the mood of the artwork")
    design = tr.ai_text(rec.get("ai_design"))
    # Chế độ chỉ cảnh vật: ảnh bìa không có nhân vật → không nhắc nhân vật/khuôn
    # mặt, cấm thêm người/con vật.
    scenery = bool(rec.get("scenery"))
    keep = ("scene, composition and art style" if scenery
            else "scene, characters, composition and art style")
    no_add = ("Do not add any people, animals or new objects" if scenery
              else "Do not add new characters or objects")
    if rec.get("mixtape"):
        return _mixtape_prompt(title_txt, subtitle, rec, lk, layout, lettering, design,
                               scenery=scenery, keep=keep, native_wide=native_wide,
                               retry_note=retry_note)

    lines = [
        "Design a finished YouTube thumbnail (wide 16:9 landscape) for a music "
        "playlist video of cover songs (a singer's new versions of popular songs).",
        f"Use the attached image as the base artwork: keep its {keep}. You may boost "
        "contrast, light and colour so it reads well at small mobile size. " + no_add
        + ("." if locked or not design else
           "; small graphic accents asked for by the THUMBNAIL STYLE are fine."),
        f"Overall look: {lk['signature']}.",
        "Add exactly these text elements and nothing else:",
        f'1. TITLE (large, the dominant element): "{title_txt}"',
    ]
    ab_directions = {
        1: ("A/B VARIANT 1 - cinematic hero style: preserve the locked composition "
            "and palette, with premium film lighting and strong emotional focus."),
        2: ("A/B VARIANT 2 - editorial poster style: keep the EXACT SAME text zones, "
            "layout grid and colour palette as variant 1, but use cleaner negative "
            "space, bolder depth layering and a refined magazine-poster finish."),
        3: ("A/B VARIANT 3 - atmospheric spatial style: keep the EXACT SAME text zones, "
            "layout grid and colour palette as variants 1-2, but emphasise cinematic "
            "environmental depth, foreground/midground/background separation and light haze."),
    }
    lines.append(ab_directions[ab_variant])
    if subtitle.strip():
        lines.append(f'2. SUBTITLE (much smaller, a single line under the title): '
                     f'"{subtitle.strip()}"')
    lines.append(
        f'3. CHANNEL SIGNATURE (small but clearly readable, decorative brand mark, '
        f'using the SAME locked typeface family as the title): "{channel_name}"')
    lines += [
        "Spell every word exactly as written, letter for letter, with the same "
        "capitalisation and punctuation. No other words, letters, numbers, logos, "
        "watermarks or signatures anywhere in the image except the required CHANNEL SIGNATURE.",
        f"{LAYOUT_PLACEMENT.get(layout, LAYOUT_PLACEMENT['left'])}; do not cover the "
        + ("focal landmark of the scene." if scenery else "characters' faces."),
    ]
    if locked:
        lines += _locked_typography(rec)
    else:
        lines.append(
            "TITLE LETTERING: you are the typographer. Design ORIGINAL custom lettering "
            f"for this title in this direction: {lettering}. Choose the letter shapes, "
            "weight, colours and effects yourself so they harmonise with the colours and "
            "mood of the artwork; it must look hand-crafted for this video by a "
            "professional designer, never a plain default computer font.")
        if subtitle.strip():
            lines.append("Subtitle lettering: a smaller, lighter companion style that "
                         "matches the title lettering, still very easy to read.")
        if design:
            lines.append(f"THUMBNAIL STYLE for this video: {design}. Apply it as the "
                         "finishing touch (light, colour grade, subtle graphic accents) "
                         "while the scene"
                         + (" stays" if scenery else " and characters stay")
                         + " clearly recognisable.")
    lines.append("The title must be crisp, sharp and highly legible at thumbnail size, "
                 "with strong contrast against the background. Keep all text inside the "
                 "safe area (at least 5% margin from every edge) and away from the "
                 "bottom-right corner where YouTube shows the video length.")
    if not native_wide:
        lines.append("The final image will be cropped to 16:9: keep all text and "
                     + ("the focal landmark" if scenery else "faces")
                     + " within the middle 75% of the image height.")
    if retry_note:
        lines.append(retry_note)
    return "\n".join(lines)


def _mixtape_prompt(title_txt: str, subtitle: str, rec: dict, lk: dict, layout: str,
                    lettering: str, design: str, *, scenery: bool, keep: str,
                    native_wide: bool, retry_note: str) -> str:
    """Thumbnail kiểu mixtape/playlist Việt (tool Cover 2026-10-29, theo ảnh mẫu
    người dùng): tiêu đề 3D thật to ánh kim/neon, dòng thể loại, danh sách bài
    đánh số, nốt nhạc + sóng âm trang trí, ánh đèn bokeh."""
    from backend.cover.music_line import line_profile
    tracks = [str(t).strip() for t in (rec.get("tracklist") or []) if str(t).strip()]
    channel_name = (rec.get("channel_name") or "MUSIC CHANNEL").strip()
    face = "focal landmark of the scene" if scenery else "person's face"
    line = line_profile(rec.get("music_line", ""))
    ab_variant = max(1, min(3, int(rec.get("ab_variant") or 1)))
    region = line["people"] or line["label"]
    lines = [
        "Design a finished, eye-catching YouTube thumbnail (wide 16:9 landscape) for a "
        f"{region} music mixtape / playlist video of cover songs, in the bold, glossy "
        f"style of {line['channels']}.",
        f"Use the attached image as the base artwork: keep its {keep}"
        + ("" if scenery else "; the person must stay exactly the same (face, hair, "
           "outfit, pose)")
        + ". Make it pop at small mobile size: boost contrast, add warm glow, "
        "bokeh lights and a light lens flare. "
        + ("Do not add any people or animals." if scenery else "Do not add new people."),
        f"Overall look: {lk['signature']}.",
        "Add exactly these text elements and nothing else:",
        f'1. TITLE (huge, the dominant element, may break onto two lines): "{title_txt}"',
    ]
    lines.append({
        1: "A/B VARIANT 1: premium cinematic hero-poster treatment.",
        2: ("A/B VARIANT 2: refined editorial music-poster treatment; keep the EXACT "
            "same palette, text zones and composition grid as variant 1."),
        3: ("A/B VARIANT 3: atmospheric spatial treatment with stronger foreground, "
            "midground and background depth; keep the EXACT same palette, text zones "
            "and composition grid as variants 1–2."),
    }[ab_variant])
    n = 2
    if subtitle.strip():
        lines.append(f'{n}. GENRE TAG (small bold line or pill-shaped badge right above '
                     f'or below the title): "{subtitle.strip()}"')
        n += 1
    lines.append(
        f'{n}. CHANNEL SIGNATURE (small, elegant branded wordmark using the SAME '
        f'locked typeface family as the title): "{channel_name}"')
    n += 1
    if len(tracks) >= 2:            # 1 bài → danh sách 1 dòng trông lạc lõng, bỏ
        items = " / ".join(f'"{i}. {t}"' for i, t in enumerate(tracks, 1))
        lines.append(
            f"{n}. TRACKLIST (small, clean, highly readable sans-serif, one song per "
            "line, numbered, inside a semi-transparent dark rounded panel with a thin "
            "glowing border; put it under the title or in the free area beside it, "
            f"never over the {face}): {items}")
    lines += [
        "Spell every word exactly as written, letter for letter, keeping all "
        "accents, capitalisation and punctuation. No other words, letters, "
        "numbers, logos, watermarks or signatures anywhere except the required CHANNEL SIGNATURE. Decoration without letters "
        "is welcome: a few small music-note icons and a thin glowing sound-wave / "
        "equaliser line near the title.",
        f"{LAYOUT_PLACEMENT.get(layout, LAYOUT_PLACEMENT['left'])}; do not cover the "
        f"{face}.",
    ]
    if rec.get("font_lock"):
        lines += _locked_typography(rec)
    else:
        lines.append(
            "TITLE LETTERING: huge custom 3D display lettering in this direction: "
            f"{lettering} — glossy metallic gold or bright neon glow chosen to match the "
            "colours of the artwork, with depth, a crisp outline and a soft drop shadow, "
            "like a premium mixtape cover by a professional designer; never a plain "
            "default computer font.")
    if design:
        lines.append(f"THUMBNAIL STYLE for this video: {design}.")
    lines.append("The title must be crisp, sharp and highly legible at thumbnail size, "
                 "with strong contrast against the background. Keep all text inside the "
                 "safe area (at least 5% margin from every edge) and away from the "
                 "bottom-right corner where YouTube shows the video length.")
    if not native_wide:
        lines.append("The final image will be cropped to 16:9: keep all text and the "
                     f"{face} within the middle 75% of the image height.")
    if retry_note:
        lines.append(retry_note)
    return "\n".join(lines)


def _locked_typography(rec: dict) -> list[str]:
    """Người dùng khoá font tay → dặn đúng font + kiểu chữ + màu của công thức."""
    pal = _palette_hex(rec)
    treatment = rec.get("treatment") or "gold3d"
    style_txt = TREATMENT_TEXT.get(treatment, TREATMENT_TEXT["clean_shadow"]).format(**pal)
    lines = [
        f"Title typography: {_font_feel(rec.get('font_lock'))}, rendered as {style_txt}.",
        f"Subtitle typography: a clean, lighter companion typeface in {pal['sub']}, "
        "with a subtle dark shadow for contrast.",
    ]
    if treatment == "ink_seal" and (rec.get("seal") or "").strip():
        lines.append("Beside the title add one small square vermilion artist's seal "
                     "stamp with a single carved seal-script character (this seal is "
                     "the only allowed extra mark).")
    return lines


# ── Ảnh vào / ra ─────────────────────────────────────────────────

def _reference_jpeg(cover: Path, max_side: int = 1536) -> bytes:
    from PIL import Image
    im = Image.open(cover).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _to_thumbnail(data: bytes):
    """Ảnh model trả về → PIL RGB 1280x720 (cắt giữa theo tỉ lệ 16:9)."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise ThumbAIError(f"Model trả dữ liệu không phải ảnh ({type(e).__name__}).")
    im = im.convert("RGB")
    w, h = im.size
    tw, th = OUT_SIZE
    target = tw / th
    if w / h > target:            # quá rộng → cắt hai bên
        nw = int(round(h * target))
        x0 = (w - nw) // 2
        im = im.crop((x0, 0, x0 + nw, h))
    elif w / h < target:          # quá cao → cắt trên/dưới
        nh = int(round(w / target))
        y0 = (h - nh) // 2
        im = im.crop((0, y0, w, y0 + nh))
    return im.resize(OUT_SIZE, Image.LANCZOS)


def _save_png(im, path: Path) -> int:
    """Lưu PNG ≤ MAX_BYTES (quá → giảm còn 256 màu). Trả kích thước byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    if buf.tell() > MAX_BYTES:
        buf = io.BytesIO()
        im.quantize(colors=256, method=2).save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    tmp = path.with_suffix(".tmp.png")
    tmp.write_bytes(data)
    tmp.replace(path)
    return len(data)


# ── Gọi API ──────────────────────────────────────────────────────

def _headers(key: str, *, gemini: bool = False) -> dict:
    h = {"Authorization": f"Bearer {key}"}
    if gemini:
        h["x-goog-api-key"] = key
    return h


def _error_message(resp) -> str:
    try:
        j = resp.json()
        err = j.get("error") if isinstance(j, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or j.get("message")
        if msg:
            return str(msg)[:300]
    except Exception:
        pass
    return (resp.text or "")[:300]


def _raise_for(resp, model: str) -> None:
    code = resp.status_code
    if code < 400:
        return
    detail = _error_message(resp)
    if code == 401:
        raise ThumbAIError("API key thumbnail sai hoặc hết hạn (HTTP 401).")
    if code == 403:
        raise ThumbAIError(f"Key không được dùng model «{model}» (HTTP 403) — nhóm của "
                           f"key không chứa model này. {detail}")
    if code == 404:
        raise ThumbAIError(f"Không tìm thấy model/endpoint «{model}» (HTTP 404). {detail}")
    if code == 402 or "quota" in detail.lower() or "余额" in detail:
        raise ThumbAIError(f"Tài khoản API hết số dư/hạn mức (HTTP {code}). {detail}")
    if code == 429:
        raise ThumbAIError(f"Bị giới hạn tần suất (HTTP 429). {detail}", retryable=True)
    if code >= 500:
        raise ThumbAIError(f"Máy chủ API lỗi tạm thời (HTTP {code}). {detail}",
                           retryable=True)
    raise ThumbAIError(f"API từ chối yêu cầu (HTTP {code}): {detail}")


def _post(url: str, *, headers: dict, model: str, **kw):
    import httpx
    try:
        resp = httpx.post(url, headers=headers, timeout=TIMEOUT, **kw)
    except httpx.TimeoutException:
        raise ThumbAIError(f"Hết thời gian chờ ({int(TIMEOUT)}s) khi gọi model ảnh.",
                           retryable=True)
    except httpx.HTTPError as e:
        raise ThumbAIError(f"Không kết nối được API ({type(e).__name__}).", retryable=True)
    _raise_for(resp, model)
    return resp


def _call_gemini(s: ThumbAISettings, prompt: str, ref: bytes) -> bytes:
    url = f"{api_root(s.base_url)}/v1beta/models/{s.model}:generateContent"
    image_cfg = {"aspectRatio": "16:9"}
    if not s.model.lower().startswith("gemini-2"):
        image_cfg["imageSize"] = "2K"
    body = {
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "image/jpeg",
                            "data": base64.b64encode(ref).decode("ascii")}},
            {"text": prompt},
        ]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": image_cfg},
    }
    resp = _post(url, headers=_headers(s.api_key, gemini=True), model=s.model, json=body)
    try:
        j = resp.json()
    except Exception:
        raise ThumbAIError("API trả về không phải JSON.")
    block = (j.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ThumbAIError(f"Model từ chối prompt ({block}).")
    images, notes, finish = [], [], ""
    for cand in j.get("candidates") or []:
        finish = cand.get("finishReason") or finish
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                if not part.get("thought"):
                    images.append(inline["data"])
            elif part.get("text") and not part.get("thought"):
                notes.append(part["text"])
    if not images:
        why = finish or (" ".join(notes)[:200] if notes else "không rõ lý do")
        raise ThumbAIError(f"Model không trả ảnh ({why}).",
                           retryable=finish in ("", "STOP", "OTHER"))
    try:
        return base64.b64decode(images[-1])
    except Exception:
        raise ThumbAIError("Ảnh base64 trả về bị hỏng.", retryable=True)


def _image_from_items(items: list) -> bytes:
    for it in items or []:
        if it.get("b64_json"):
            return base64.b64decode(it["b64_json"])
        if it.get("url"):
            return _fetch_image(it["url"])
    raise ThumbAIError("API không trả ảnh nào (data rỗng).", retryable=True)


def _call_images(s: ThumbAISettings, prompt: str, ref: bytes) -> bytes:
    url = f"{api_root(s.base_url)}/v1/images/edits"
    files = {"image": ("cover.jpg", ref, "image/jpeg")}
    data = {"model": s.model, "prompt": prompt, "n": "1", "size": "1536x1024",
            "quality": "high"}
    resp = _post(url, headers=_headers(s.api_key), model=s.model, data=data, files=files)
    try:
        return _image_from_items(resp.json().get("data") or [])
    except ThumbAIError:
        raise
    except Exception:
        raise ThumbAIError("Không đọc được ảnh từ /v1/images/edits.")


_DATA_URL = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)")
_MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(https?://[^\s)\"'<>]+\.(?:png|jpe?g|webp)(?:\?[^\s)\"'<>]*)?)",
                       re.IGNORECASE)


def _image_from_chat(message: dict) -> bytes:
    for img in message.get("images") or []:          # kiểu OpenRouter
        u = ((img.get("image_url") or {}).get("url") if isinstance(img, dict) else "") or ""
        m = _DATA_URL.search(u)
        if m:
            return base64.b64decode(m.group(1))
        if u.startswith("http"):
            return _fetch_image(u)
    content = message.get("content")
    if isinstance(content, list):                    # nội dung dạng nhiều phần
        text = ""
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "image_url":
                    text += " " + ((part.get("image_url") or {}).get("url") or "")
                elif part.get("text"):
                    text += " " + part["text"]
        content = text
    content = content or ""
    m = _DATA_URL.search(content)
    if m:
        return base64.b64decode(m.group(1))
    urls = _MD_IMG.findall(content) or _BARE_URL.findall(content)
    if urls:
        return _fetch_image(urls[-1])
    reply = re.sub(r"\s+", " ", content)[:200] or "(trống)"
    raise ThumbAIError(f"Model không trả ảnh — trả lời: {reply}", retryable=True)


def _call_chat(s: ThumbAISettings, prompt: str, ref: bytes) -> bytes:
    url = f"{api_root(s.base_url)}/v1/chat/completions"
    data_url = "data:image/jpeg;base64," + base64.b64encode(ref).decode("ascii")
    body = {"model": s.model, "stream": False, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}]}
    resp = _post(url, headers=_headers(s.api_key), model=s.model, json=body)
    try:
        choices = resp.json().get("choices") or []
    except Exception:
        raise ThumbAIError("API trả về không phải JSON.")
    if not choices:
        raise ThumbAIError("API không trả lựa chọn nào.", retryable=True)
    return _image_from_chat(choices[0].get("message") or {})


def _fetch_image(url: str) -> bytes:
    import httpx
    try:
        r = httpx.get(url, timeout=120.0, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ThumbAIError(f"Không tải được ảnh model trả về ({type(e).__name__}).",
                           retryable=True)
    if r.status_code >= 400 or not r.content:
        raise ThumbAIError(f"Không tải được ảnh model trả về (HTTP {r.status_code}).",
                           retryable=True)
    return r.content


def call_model(s: ThumbAISettings, prompt: str, ref: bytes) -> bytes:
    proto = protocol_for(s.model)
    if proto == "gemini":
        return _call_gemini(s, prompt, ref)
    if proto == "images":
        return _call_images(s, prompt, ref)
    return _call_chat(s, prompt, ref)


@dataclass
class DrawEngine:
    """1 máy vẽ ảnh + chữ: `call(prompt, ảnh_bìa_jpeg) -> bytes ảnh`."""
    key: str                          # "ai" (API) | "chatgpt" — ghi vào meta/UI
    model: str                        # vào khoá cache + meta
    label: str
    native_wide: bool                 # máy trả đúng 16:9 (không phải cắt nhiều)
    call: Callable[[str, bytes], bytes]
    protocol: str = ""
    price_note: str = ""              # " ~$0.64/ảnh" (rỗng = không tốn phí API)


def api_engine(s: ThumbAISettings) -> DrawEngine:
    info = model_info(s.model)
    proto = protocol_for(s.model)
    return DrawEngine(
        key="ai", model=s.model, label=info["label"], native_wide=(proto == "gemini"),
        call=lambda prompt, ref: call_model(s, prompt, ref), protocol=proto,
        price_note=f" ~${info['price_usd']:.2f}/ảnh" if info.get("price_usd") else "")


# ── Kiểm tra chữ (OCR bằng model vision của API chính) ───────────

def _norm(text: str) -> str:
    t = re.sub(r"[^0-9a-zA-ZÀ-ɏḀ-ỿ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a:
        return 1.0
    if not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _read_text(im) -> Optional[dict]:
    """Model vision (cấu hình API chính) đọc chữ trên ảnh → {title, subtitle}.
    Không có API chính / lỗi → None (bỏ qua kiểm tra)."""
    try:
        from backend.core_bridge import get_api_config_from_db
        cfg = get_api_config_from_db(str(_db_path()))
        if not cfg.api_key:
            return None
        from openai import OpenAI
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        client = OpenAI(timeout=90.0, max_retries=1, **cfg.to_client_kwargs())
        resp = client.chat.completions.create(
            model=cfg.model, temperature=0, max_tokens=700,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "Read ALL text visible in this image exactly as written (keep the "
                    "spelling, even if it looks wrong). Return JSON: {\"title\": the "
                    "largest text, \"subtitle\": the smaller line or \"\", "
                    "\"other\": [any other text]}.")},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}])
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return None


def verify_text(im, title: str, subtitle: str) -> Optional[dict]:
    """None = không kiểm tra được. Ngược lại {ok, read_title, title_ratio, …}."""
    got = _read_text(im)
    if got is None:
        return None
    read_title = str(got.get("title") or "")
    read_sub = str(got.get("subtitle") or "")
    tr = _ratio(title, read_title)
    if tr < TITLE_MIN_RATIO:      # model đọc gộp tiêu đề + dòng phụ
        tr = max(tr, _ratio(title, read_title + " " + read_sub))
    sr = _ratio(subtitle, read_sub) if subtitle.strip() else 1.0
    if subtitle.strip() and sr < SUB_MIN_RATIO:
        sr = max(sr, _ratio(subtitle, " ".join(str(x) for x in got.get("other") or [])))
    return {"ok": tr >= TITLE_MIN_RATIO and sr >= SUB_MIN_RATIO,
            "read_title": read_title[:200], "read_subtitle": read_sub[:200],
            "title_ratio": round(tr, 3), "subtitle_ratio": round(sr, 3)}


# ── Bộ nhớ đệm + meta ────────────────────────────────────────────

def cache_key(title: str, subtitle: str, recipe: dict | None, model: str,
              cover: Path) -> str:
    rec = recipe or {}
    try:
        st = cover.stat()
        cover_sig = [st.st_size, int(st.st_mtime)]
    except OSError:
        cover_sig = []
    sig = {"v": PROMPT_VERSION, "title": title, "subtitle": subtitle, "model": model,
           "cover": cover_sig,
           "recipe": {k: rec.get(k) for k in ("look", "layout", "treatment", "palette",
                                              "font", "font_lock", "sub_font", "upper",
                                              "seal", "ai_lettering", "ai_design",
                                              "channel_name")}}
    if rec.get("scenery"):          # chỉ thêm khi bật → khoá cũ không đổi
        sig["scenery"] = True
    if rec.get("mixtape"):          # kiểu mixtape (tool Cover) + danh sách bài
        sig["mixtape"] = list(rec.get("tracklist") or [])
    return hashlib.sha1(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()


def read_meta(final_dir: Path) -> dict:
    try:
        return json.loads((Path(final_dir) / META_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_meta(final_dir: Path, meta: dict) -> None:
    p = Path(final_dir) / META_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def mark_current(final_dir: Path, engine: str, note: str = "") -> None:
    """Ghi thumbnail.png hiện tại do engine nào vẽ (chatgpt | ai | pil) — cho UI."""
    meta = read_meta(final_dir)
    meta["current"] = engine
    meta["current_note"] = note[:300]
    meta["current_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _write_meta(final_dir, meta)
    except OSError:
        pass


def status(final_dir: Path) -> dict:
    from . import chatgpt_thumbnail
    s = load_settings()
    meta = read_meta(final_dir)
    login = chatgpt_thumbnail.login_status()
    return {"engine": meta.get("current") or "pil", "note": meta.get("current_note") or "",
            "default_engine": s.engine,
            "chatgpt_logged_in": login.get("logged_in"),
            "chatgpt_checked_at": login.get("checked_at"),
            "ai_ready": s.ready, "ai_active": s.active, "model": s.model,
            "model_label": model_info(s.model)["label"],
            "price_usd": model_info(s.model)["price_usd"],
            "last_ai": meta.get("ai") or None}


# ── Luồng chính ──────────────────────────────────────────────────

def generate(*, title: str, subtitle: str, recipe: dict | None, cover: Path,
             out: Path, settings: ThumbAISettings | None = None,
             log: Callable[[str], None] | None = None, force: bool = False,
             engine: DrawEngine | None = None) -> dict:
    """Tạo thumbnail AI → `out` (final/thumbnail.png). Trả meta lần tạo.
    engine=None → API theo Cài đặt (cần key); ChatGPT web truyền DrawEngine riêng.
    Ném ThumbAIError nếu không tạo được ảnh dùng được (service rơi về PIL)."""
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    s = settings or load_settings()
    if engine is None:
        if not s.ready:
            raise ThumbAIError("Chưa nhập API key thumbnail AI (Cài đặt → Thumbnail AI).")
        engine = api_engine(s)
    cover = Path(cover)
    if not cover.exists():
        raise ThumbAIError(f"Chưa có ảnh bìa {cover.name}.")
    out = Path(out)
    final_dir = out.parent
    ab_variant = max(1, min(3, int((recipe or {}).get("ab_variant") or 1)))
    cache_png = final_dir / (CACHE_NAME if ab_variant == 1
                             else f"_thumbnail_ai_ab{ab_variant}.png")
    key = cache_key(title, subtitle, recipe, engine.model, cover)
    meta = read_meta(final_dir)
    meta_key = "ai" if ab_variant == 1 else f"ai_ab_{ab_variant}"
    last = meta.get(meta_key) or {}
    # Không ép vẽ mới → dùng lại ảnh AI đã có cho CÙNG tiêu đề/công thức/ảnh bìa,
    # dù máy nào đã vẽ (đổi mặc định ChatGPT ↔ API không vẽ lại bản đã trả phí).
    same = {key}
    if last.get("model") and last.get("model") != engine.model:
        same.add(cache_key(title, subtitle, recipe, last["model"], cover))
    if not force and last.get("cache_key") in same and cache_png.exists():
        shutil.copyfile(cache_png, out)
        _log(f"Thumbnail AI: dùng lại ảnh đã tạo ({last.get('label') or engine.label})"
             " — không vẽ lại.")
        mark_current(final_dir, last.get("engine") or engine.key, "dùng lại bản đã tạo")
        return {**last, "cached": True}

    ref = _reference_jpeg(cover)
    retry_note = ""
    last_check: Optional[dict] = None
    last_err: Optional[ThumbAIError] = None
    for attempt in range(1, MAX_CALLS + 1):
        prompt = build_prompt(title, subtitle, recipe, native_wide=engine.native_wide,
                              retry_note=retry_note)
        _log(f"Thumbnail AI: {engine.label} vẽ ảnh + chữ (lượt {attempt}/{MAX_CALLS}"
             f"{engine.price_note})…")
        t0 = time.time()
        try:
            raw = engine.call(prompt, ref)
            im = _to_thumbnail(raw)
        except ThumbAIError as e:
            last_err = e
            _log(f"Thumbnail AI lỗi: {e}")
            if not e.retryable or attempt == MAX_CALLS:
                raise
            time.sleep(10)
            continue
        secs = time.time() - t0
        check = verify_text(im, title.upper() if (recipe or {}).get("upper") else title,
                            subtitle) if s.verify else None
        if check is not None and not check["ok"]:
            last_check = check
            _log(f"Thumbnail AI: chữ chưa đúng (đọc được «{check['read_title']}» · "
                 f"«{check['read_subtitle']}») — "
                 + ("thử lại…" if attempt < MAX_CALLS else "bỏ."))
            retry_note = ("IMPORTANT: a previous attempt misspelled the text. Render the "
                          "title and subtitle EXACTLY as quoted above, character by "
                          "character; prefer fewer decorative effects over wrong letters.")
            continue
        size = _save_png(im, cache_png)
        shutil.copyfile(cache_png, out)
        rec = {"cache_key": key, "engine": engine.key, "model": engine.model,
               "label": engine.label, "protocol": engine.protocol,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "attempts": attempt, "seconds": round(secs, 1), "bytes": size,
               "verified": None if check is None else True,
               "read_title": (check or {}).get("read_title"),
               "title": title, "subtitle": subtitle}
        meta[meta_key] = rec
        _write_meta(final_dir, meta)
        mark_current(final_dir, engine.key, engine.label)
        _log(f"Thumbnail AI: xong ({secs:.0f}s, {size / 1e6:.2f} MB"
             + (", chữ đã kiểm tra khớp" if check else ", chưa kiểm tra chữ") + ").")
        return rec
    if last_check is not None:
        raise ThumbAIError(f"Chữ tiêu đề trên ảnh AI không khớp sau {MAX_CALLS} lượt "
                           f"(đọc được «{last_check['read_title']}»).")
    raise last_err or ThumbAIError("Không tạo được thumbnail AI.")


def list_remote_models(api_key: str, base_url: str) -> list[str]:
    """GET {root}/v1/models (MIỄN PHÍ) → danh sách model key được dùng."""
    import httpx
    url = f"{api_root(base_url)}/v1/models"
    try:
        resp = httpx.get(url, headers=_headers(api_key), timeout=30.0)
    except httpx.HTTPError as e:
        raise ThumbAIError(f"Không kết nối được {api_root(base_url)} ({type(e).__name__}).")
    _raise_for(resp, "models")
    try:
        data = resp.json().get("data") or []
    except Exception:
        raise ThumbAIError("API /v1/models trả về không phải JSON.")
    return sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})


def is_image_model(name: str) -> bool:
    n = name.lower()
    return any(t in n for t in ("image", "banana", "imagen", "dall-e", "flux",
                                "seedream", "imagine"))


__all__ = ["DEFAULT_BASE", "DEFAULT_MODEL", "MODEL_CHOICES", "ThumbAIError",
           "ENGINES", "DEFAULT_ENGINE", "ENGINE_CHOICES", "DrawEngine", "api_engine",
           "ThumbAISettings", "load_settings", "api_root", "protocol_for", "model_info",
           "build_prompt", "call_model", "verify_text", "cache_key", "read_meta",
           "mark_current", "status", "generate", "list_remote_models", "is_image_model"]
