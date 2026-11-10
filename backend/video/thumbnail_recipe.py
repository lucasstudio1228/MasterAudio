"""
video/thumbnail_recipe.py — "Công thức" thumbnail RIÊNG cho từng video.

Mỗi project nhận 1 công thức: bố cục (chữ trái/phải/trên/dưới), kiểu chữ, bảng
màu, font, góc máy, ánh sáng, tông màu môi trường, dòng phụ đề, kiểu chuyển cảnh.
Nguyên liệu lấy từ LOOK của kênh (channel_looks.py) → kênh khác nhau luôn khác
nhau; trong cùng kênh, tổ hợp được chấm điểm để KHÁC các video gần nhất (không
lặp nguyên tổ hợp đã dùng).

Sổ ghi: data/thumbnail_recipes.json  {"projects": {pid: recipe},
                                       "channels": {key: [pid, ...]}}
ID project không bao giờ tái dùng nên sổ không bị lẫn.

Project CŨ (ảnh bìa đã vẽ theo luật "chừa nửa TRÁI") → công thức legacy: bố cục
"left" cố định, chỉ đổi kiểu chữ/màu/font theo look kênh.

Từ 2026-10-25 thumbnail AI (ChatGPT/API) TỰ thiết kế chữ theo 1 hướng chữ
(ai_lettering) + 1 hướng style (ai_design) riêng mỗi video; font/kiểu chữ/màu
trong công thức chỉ còn cho chữ PIL (dự phòng), intro và hook Short — trừ khi
người dùng khoá font tay (khi đó model vẽ đúng font đó).
"""
from __future__ import annotations

import json
import hashlib
import os
import random
import tempfile
import threading
from pathlib import Path
from typing import Optional

from . import config
from . import channel_looks as looks

LEDGER = config._ROOT / "data" / "thumbnail_recipes.json"
RECIPE_VERSION = 1
COMPOSITION_MARK = "THUMBNAIL COMPOSITION"
_LOCK = threading.Lock()

LAYOUT_TEXT = {
    "left": ("place the characters in the RIGHT third of the frame and keep the LEFT "
             "HALF as calm, uncluttered negative space (sky, mist, water or soft "
             "foliage) where a title will be added later"),
    "right": ("place the characters in the LEFT third of the frame and keep the RIGHT "
              "HALF as calm, uncluttered negative space (sky, mist, water or soft "
              "foliage) where a title will be added later"),
    "top": ("place the characters in the LOWER HALF of the frame (centred or slightly "
            "off-centre) and keep the UPPER THIRD as calm, uncluttered negative space "
            "(open sky, mist or soft glow) where a title will be added later"),
    "bottom": ("place the characters in the UPPER TWO-THIRDS of the frame and keep the "
               "BOTTOM THIRD as calm, uncluttered, slightly darker negative space (still "
               "water, ground mist or soft shadow) where a title will be added later"),
}

# Góc máy + bố cục chữ tương thích.
SHOTS = [
    {"key": "wide", "layouts": ["left", "right", "top", "bottom"],
     "text": "wide establishing shot: the characters are small within a vast, breathtaking landscape"},
    {"key": "medium", "layouts": ["left", "right"],
     "text": "medium shot: the characters seen from the knees up, close enough to read their calm expressions"},
    {"key": "low_angle", "layouts": ["left", "right", "top"],
     "text": "low-angle shot looking up at the characters against a wide open sky"},
    {"key": "high_angle", "layouts": ["top", "bottom"],
     "text": "gentle high-angle shot looking down on the characters and the scenery around them"},
    {"key": "silhouette", "layouts": ["left", "right", "top", "bottom"],
     "text": "backlit shot: the characters as soft rim-lit near-silhouettes against a glowing horizon"},
    {"key": "over_shoulder", "layouts": ["left", "right"],
     "text": "over-the-shoulder view from slightly behind the characters, gazing out at the scenery"},
    {"key": "framed", "layouts": ["left", "right", "bottom"],
     "text": "framed shot: the scene seen through soft out-of-focus foreground elements (branches, a window frame or hanging lanterns)"},
    {"key": "reflection", "layouts": ["left", "right", "bottom"],
     "text": "mirror-like still water in front of the characters doubling the scene in a soft reflection"},
]

# Chế độ CHỈ CẢNH VẬT (cast_mode=scenery): không có nhân vật → góc máy xoay
# quanh điểm nhấn của khung cảnh (theo "Ý tưởng của bạn").
SCENERY_SHOT_TEXT = {
    "wide": "wide establishing shot: one focal landmark of the place small within a vast, breathtaking landscape",
    "medium": "medium shot of the focal landmark of the place, close enough to read its textures and details",
    "low_angle": "low-angle shot looking up at the focal landmark of the place against a wide open sky",
    "high_angle": "gentle high-angle shot looking down on the place and its focal landmark",
    "silhouette": "backlit shot: the focal landmark of the place as a soft rim-lit near-silhouette against a glowing horizon",
    "over_shoulder": "view from slightly behind a foreground element of the place (a railing, a stone or a branch), gazing out at the scenery",
    "framed": "framed shot: the place seen through soft out-of-focus foreground elements (branches, a window frame or hanging lanterns)",
    "reflection": "mirror-like still water in front of the focal landmark doubling the scene in a soft reflection",
}
SCENERY_ONLY_NOTE = "the place is completely empty: no people, no animals, no creatures"

LIGHTS = [
    {"key": "golden", "text": "warm golden-hour sunlight with long soft shadows"},
    {"key": "blue_hour", "text": "blue-hour twilight with the first faint stars"},
    {"key": "misty_morning", "text": "misty early-morning light, pale and diffused"},
    {"key": "moonlit", "text": "moonlit night with a few warm lantern accents"},
    {"key": "overcast", "text": "soft overcast light, gentle and shadowless"},
    {"key": "lantern", "text": "dusk lit by warm lantern or candle glow"},
    {"key": "rain", "text": "light rain with glistening reflections and soft haze"},
    {"key": "clear_day", "text": "clear bright daylight with crisp gentle shadows"},
    {"key": "snow", "text": "quiet snowfall in cool winter light"},
    {"key": "autumn_back", "text": "autumn afternoon backlight glowing through leaves"},
]

# ── Chữ + style thumbnail do ChatGPT/model ảnh TỰ thiết kế (2026-10-25) ──
# Người dùng: "chữ trên thumbnail để cho chatgpt tạo luôn, không để chữ mặc
# định, mỗi video 1 kiểu chữ và 1 style thumbnail khác nhau". Mỗi video bốc 1
# HƯỚNG chữ + 1 HƯỚNG style (không lặp trong kênh, tránh cả các video gần nhất
# của mọi kênh); model tự chọn nét, màu, hiệu ứng. looks=None → hợp mọi look.
# Font khoá tay (font_lock) vẫn được tôn trọng: khi đó prompt dặn đúng font đó.
_ALL = None
LETTERINGS = [
    {"key": "watercolor_brush", "vi": "cọ màu nước", "looks": _ALL,
     "text": "hand-painted watercolour brush lettering with soft pigment blooms and gently uneven wet edges"},
    {"key": "gold_leaf_serif", "vi": "serif dát vàng", "looks": ("ink_zen", "cinematic", "painterly"),
     "text": "elegant gold-leaf serif capitals with fine engraved highlights and a soft warm sheen"},
    {"key": "copperplate_swash", "vi": "bút chấm mực uốn lượn", "looks": ("cinematic", "painterly", "cozy_lofi"),
     "text": "flowing copperplate script drawn with a dip pen, delicate hairlines and graceful swashes"},
    {"key": "crayon_round", "vi": "chữ tròn sáp màu", "looks": ("cozy_lofi", "bright", "painterly"),
     "text": "chunky rounded hand-drawn letters with a soft pastel fill and a cosy crayon texture"},
    {"key": "carved_wood", "vi": "biển gỗ khắc", "looks": ("cozy_lofi", "painterly", "cinematic"),
     "text": "carved wooden sign lettering with visible wood grain and softly bevelled edges"},
    {"key": "stardust", "vi": "bụi sao lấp lánh", "looks": _ALL,
     "text": "letters formed from tiny soft glowing light particles, like drifting stardust"},
    {"key": "leafy_vines", "vi": "dây lá và hoa nhỏ", "looks": ("painterly", "ink_zen", "bright"),
     "text": "letters woven from leafy vines with tiny blossoms growing along the strokes"},
    {"key": "sumi_brush", "vi": "nét mực tàu khô", "looks": ("ink_zen",),
     "text": "bold sumi-ink brush strokes with expressive dry-brush texture, like calligraphy on rice paper"},
    {"key": "airy_sans_glow", "vi": "sans mảnh phát sáng", "looks": _ALL,
     "text": "a thin, airy, widely spaced modern sans-serif with a soft luminous glow"},
    {"key": "retro_bubble", "vi": "bong bóng retro 70s", "looks": ("cozy_lofi", "bright"),
     "text": "retro 1970s rounded bubble lettering with layered offset outlines in warm tones"},
    {"key": "papercut", "vi": "giấy cắt nhiều lớp", "looks": _ALL,
     "text": "embossed paper-cut letters in several layers with soft shadows, like a papercraft diorama"},
    {"key": "chalk", "vi": "phấn bảng", "looks": ("cozy_lofi",),
     "text": "hand-drawn chalk lettering with dusty texture and small doodled accents"},
    {"key": "stained_glass", "vi": "kính màu vitrail", "looks": ("painterly", "cinematic"),
     "text": "stained-glass letters with luminous coloured panes and thin dark leading lines"},
    {"key": "frosted_glass", "vi": "kính mờ khúc xạ", "looks": ("cinematic", "bright", "cozy_lofi"),
     "text": "frosted glass letters with soft refraction and a gentle inner light"},
    {"key": "embroidery", "vi": "chỉ thêu", "looks": ("cozy_lofi", "painterly"),
     "text": "embroidered thread lettering with visible stitches on soft fabric"},
    {"key": "cloud_letters", "vi": "chữ mây bồng", "looks": ("painterly", "bright", "ink_zen"),
     "text": "soft fluffy cloud-like letters floating in the sky, lit by gentle light"},
    {"key": "moon_silver", "vi": "bạc ánh trăng", "looks": ("cinematic", "ink_zen", "painterly"),
     "text": "moonlit silver serif lettering with a subtle shimmer and a few fine star sparkles"},
    {"key": "neon_script", "vi": "neon viết tay", "looks": ("cozy_lofi", "bright"),
     "text": "a hand-written neon sign script with a soft pink and blue tube glow"},
    {"key": "marker_bounce", "vi": "bút dạ nảy nhịp", "looks": ("cozy_lofi", "bright"),
     "text": "casual hand-lettered brush-marker script with a relaxed bouncy rhythm"},
    {"key": "stone_roman", "vi": "đá khắc La Mã", "looks": ("cinematic", "ink_zen", "painterly"),
     "text": "classical Roman inscription capitals carved in pale stone with a touch of soft moss"},
    {"key": "storybook_initial", "vi": "sách truyện chữ hoa trang trí", "looks": ("painterly", "cozy_lofi"),
     "text": "a storybook decorative serif with an ornate illuminated first capital and tiny flourishes"},
    {"key": "silk_ribbon", "vi": "dải lụa uốn", "looks": ("painterly", "bright", "ink_zen"),
     "text": "letters formed from one flowing silk ribbon with soft folds and sheen"},
    {"key": "editorial_didone", "vi": "didone tạp chí", "looks": ("cinematic", "bright"),
     "text": "a minimal high-contrast didone serif in a refined fashion-magazine style"},
    {"key": "paper_lantern", "vi": "đèn lồng giấy", "looks": ("ink_zen", "cozy_lofi", "painterly"),
     "text": "translucent paper-lantern letters with a warm gentle inner glow"},
    {"key": "frost_crystal", "vi": "pha lê băng giá", "looks": ("cinematic", "bright"),
     "text": "ice-blue crystalline letters with delicate frost texture and cold sparkle"},
    {"key": "clay_3d", "vi": "đất sét 3D pastel", "looks": ("bright", "cozy_lofi"),
     "text": "soft rounded 3D letters like smooth pastel ceramic or clay, with gentle studio light"},
    {"key": "seal_script", "vi": "triện thư + dấu son", "looks": ("ink_zen",),
     "text": ("calligraphic brush letters inspired by seal script, with one small square vermilion "
              "seal stamp beside the title (that seal is the only allowed extra mark)")},
    {"key": "sunset_gradient", "vi": "chuyển màu hoàng hôn", "looks": ("bright", "cinematic"),
     "text": "a bold sans-serif filled with a golden sunset gradient and a long soft shadow"},
    {"key": "pencil_sketch", "vi": "phác chì gạch bóng", "looks": ("cozy_lofi", "painterly"),
     "text": "hand-drawn pencil sketch lettering with light cross-hatching and paper grain"},
    {"key": "water_ripple", "vi": "nước gợn sóng", "looks": _ALL,
     "text": "fluid letters shaped like calm water, with gentle ripples and soft reflections"},
    {"key": "letterpress", "vi": "in letterpress cổ", "looks": ("cinematic", "cozy_lofi"),
     "text": "vintage letterpress print lettering with slightly worn ink texture"},
    {"key": "bamboo_strokes", "vi": "đốt tre", "looks": ("ink_zen",),
     "text": "letters built from natural bamboo stalks with green segments and small leaves"},
    {"key": "lofi_pixel", "vi": "pixel lofi", "looks": ("cozy_lofi",),
     "text": "soft retro pixel lettering with a faint scanline glow, cosy lofi computer style"},
    {"key": "petal_script", "vi": "cánh hoa rơi", "looks": ("painterly", "ink_zen", "bright"),
     "text": "a graceful script whose strokes dissolve into a few drifting flower petals"},
]

THUMB_DESIGNS = [
    {"key": "movie_poster", "vi": "poster điện ảnh", "looks": _ALL,
     "text": "cinematic movie-poster finish with a gentle vignette, soft light rays and subtle film grain"},
    {"key": "minimal_editorial", "vi": "tối giản biên tập", "looks": _ALL,
     "text": "minimal editorial layout with generous calm negative space and one fine accent line under the title"},
    {"key": "storybook_frame", "vi": "khung sách truyện", "looks": ("painterly", "cozy_lofi", "ink_zen"),
     "text": "storybook cover with delicate decorative corner ornaments framing the image"},
    {"key": "dreamy_haze", "vi": "mơ màng pastel", "looks": _ALL,
     "text": "dreamy soft-focus haze with pastel bloom and a few floating light particles"},
    {"key": "golden_glow", "vi": "nắng vàng ngược sáng", "looks": _ALL,
     "text": "golden-hour glow with warm backlight, a very subtle lens flare and a rich warm grade"},
    {"key": "night_bokeh", "vi": "đêm bokeh", "looks": ("cinematic", "cozy_lofi", "bright"),
     "text": "moody night ambience with a deep blue grade, soft bokeh lights and a gentle moon glow"},
    {"key": "travel_poster", "vi": "poster du lịch cổ", "looks": ("cinematic", "painterly", "bright"),
     "text": "vintage travel-poster look with slightly flattened colours, paper texture and bold simple shapes"},
    {"key": "watercolor_edges", "vi": "viền loang màu nước", "looks": ("painterly", "ink_zen", "cozy_lofi"),
     "text": "the artwork fades into soft watercolour washes on textured paper near the borders"},
    {"key": "frosted_card", "vi": "thẻ kính mờ", "looks": ("bright", "cozy_lofi", "cinematic"),
     "text": "the text sits on a softly frosted, translucent glass card with rounded corners"},
    {"key": "teal_peach", "vi": "tông xanh ngọc - đào", "looks": ("cinematic", "bright"),
     "text": "split-tone colour grade with teal shadows and peach highlights and crisp contrast"},
    {"key": "high_key", "vi": "sáng trong trẻo", "looks": ("bright", "painterly"),
     "text": "high-key airy finish with bright soft whites and gentle pastel tones"},
    {"key": "seasonal_drift", "vi": "lá/hoa bay theo mùa", "looks": _ALL,
     "text": "a few drifting seasonal accents (petals, leaves or snowflakes that match the scene) crossing the frame"},
    {"key": "ink_mist", "vi": "sương mực loang", "looks": ("ink_zen",),
     "text": "layered ink-wash mist with a few delicate ink splatter accents near the edges"},
    {"key": "magazine_cover", "vi": "bìa tạp chí", "looks": ("cinematic", "bright"),
     "text": "magazine-cover energy with a bold clear title hierarchy and an elegant small tagline"},
    {"key": "duotone_sky", "vi": "trời 2 màu", "looks": ("bright", "cozy_lofi", "cinematic"),
     "text": "a soft two-colour gradient wash over the sky area behind the title"},
    {"key": "vhs_soft", "vi": "VHS lofi ấm", "looks": ("cozy_lofi",),
     "text": "lofi VHS softness with light grain, a faint colour fringe and a cosy warm tint"},
    {"key": "sparkle_halo", "vi": "lấp lánh quầng sáng", "looks": ("painterly", "bright", "cozy_lofi"),
     "text": "tiny twinkling sparkles and a soft halo of light around the title"},
    {"key": "deep_focus", "vi": "chiều sâu xoá phông", "looks": ("cinematic", "painterly"),
     "text": "cinematic depth with a softly blurred foreground edge and crisp characters"},
    {"key": "paper_layers", "vi": "lớp giấy chồng", "looks": ("painterly", "cozy_lofi", "ink_zen"),
     "text": "subtle layered paper-cut shadows around the edges giving gentle depth"},
    {"key": "sunbeams", "vi": "tia nắng xuyên", "looks": _ALL,
     "text": "soft volumetric sunbeams falling across the scene"},
    {"key": "rain_glass", "vi": "kính mưa", "looks": ("cozy_lofi", "cinematic"),
     "text": "gentle raindrops on glass near the frame edges with a cosy warm contrast"},
    {"key": "pastel_clouds", "vi": "mây pastel", "looks": ("painterly", "bright", "cozy_lofi"),
     "text": "an enhanced pastel gradient sky with soft clouds behind the title"},
    {"key": "fine_art_print", "vi": "tranh in mỹ thuật", "looks": ("ink_zen", "painterly", "cinematic"),
     "text": "museum fine-art print finish with subtle canvas texture and a refined muted grade"},
    {"key": "halo_disc", "vi": "vầng sáng tròn sau chữ", "looks": ("ink_zen", "cinematic", "painterly"),
     "text": "a large soft circular glow of pale light behind the title, like a gentle moon halo"},
]
_AI_BY_KEY = {e["key"]: e for e in (*LETTERINGS, *THUMB_DESIGNS)}

# Trọng số "giống nhau" khi so với các video trước của kênh.
_WEIGHTS = {"layout": 3.0, "treatment": 2.5, "palette": 2.0, "font": 2.0,
            "shot": 2.0, "light": 1.5, "env": 1.0, "subtitle_tpl": 1.2,
            "hook_font": 1.5, "hook_palette": 1.2,
            "transition": 0.4, "sub_font": 0.5, "upper": 0.3, "seal": 0.3}
# Kho nguyên liệu mỗi look đã mở rộng (8-13 font, 8 bảng màu, 4 kiểu chữ →
# 256-416 tổ hợp) nên nhìn lại XA hơn và bốc NHIỀU ứng viên hơn thì font/màu
# mới thực sự đổi liên tục chứ không quay vòng sau vài video.
_HISTORY = 12
_CANDIDATES = 600


# ── Sổ ghi ──────────────────────────────────────────────────────
def _load() -> dict:
    try:
        d = json.loads(LEDGER.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("projects", {})
            d.setdefault("channels", {})
            d.setdefault("channel_fonts", {})
            return d
    except Exception:
        pass
    return {"projects": {}, "channels": {}, "channel_fonts": {}}


def _save(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LEDGER.parent, prefix=".thumbrec-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, LEDGER)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_recipe(project_id: int) -> Optional[dict]:
    r = _load()["projects"].get(str(project_id))
    return dict(r) if isinstance(r, dict) else None


def save_recipe(recipe: dict) -> dict:
    pid = str(recipe["project_id"])
    with _LOCK:
        d = _load()
        d["projects"][pid] = recipe
        for key, pids in d["channels"].items():
            if pid in pids and key != recipe["channel"]:
                pids.remove(pid)
        lst = d["channels"].setdefault(recipe["channel"], [])
        if pid not in lst:
            lst.append(pid)
        _save(d)
    return recipe


def channel_history(channel: str, exclude: int | None = None,
                    limit: int = _HISTORY) -> list[dict]:
    """Công thức các video trước của kênh, MỚI → CŨ."""
    d = _load()
    pids = [p for p in d["channels"].get(channel, []) if p != str(exclude)]
    pids.sort(key=lambda p: int(p) if p.isdigit() else 0, reverse=True)
    out = [d["projects"][p] for p in pids if isinstance(d["projects"].get(p), dict)]
    return out[:limit] if limit else out


def channel_font_lock(channel: str, look_key: str) -> str:
    """Một kênh = một font nhận diện cố định, dùng xuyên suốt mọi video."""
    lk = looks.look_info(look_key)
    fonts = list(lk.get("fonts") or [])
    if not fonts:
        from .thumbnail import DEFAULT_FONT_KEY
        fonts = [DEFAULT_FONT_KEY]
    with _LOCK:
        d = _load()
        saved = str((d.get("channel_fonts") or {}).get(channel) or "")
        if saved:
            return saved
        digest = hashlib.sha1(str(channel).encode("utf-8")).digest()
        chosen = fonts[int.from_bytes(digest[:4], "big") % len(fonts)]
        d.setdefault("channel_fonts", {})[channel] = chosen
        _save(d)
        return chosen


# ── Chọn công thức ───────────────────────────────────────────────
def _combo(r: dict) -> tuple:
    return (r.get("layout"), r.get("treatment"), r.get("palette"), r.get("font"))


def _penalty(cand: dict, history: list[dict]) -> float:
    pen = 0.0
    for i, r in enumerate(history):
        w = 1.0 / (1.0 + 0.6 * i)
        pen += w * sum(wt for f, wt in _WEIGHTS.items()
                       if cand.get(f) and cand.get(f) == r.get(f))
        pen += w * 0.5 * len(set(cand.get("kws") or []) & set(r.get("kws") or []))
    return pen


def _candidate(lk: dict, rng: random.Random, legacy: bool) -> dict:
    layout = "left" if legacy else rng.choice(lk["layouts"])
    shots = [s for s in SHOTS if layout in s["layouts"]]
    tpl = rng.choice(lk["subtitles"])
    return {
        "layout": layout,
        "treatment": rng.choice(lk["treatments"]),
        "palette": rng.choice(sorted(lk["palettes"])),
        "font": rng.choice(lk["fonts"]),
        "sub_font": rng.choice(lk["sub_fonts"]),
        "shot": "" if legacy else rng.choice(shots)["key"],
        "light": "" if legacy else rng.choice(LIGHTS)["key"],
        "env": "" if legacy else rng.choice(lk["env_moods"]),
        "subtitle_tpl": tpl,
        "kws": rng.sample(lk["keywords"], 3),
        # Chữ hook của Short bốc RIÊNG (vẫn trong kho của look) để Short không
        # dùng lại y nguyên font/màu của thumbnail cùng project.
        "hook_font": rng.choice(lk["fonts"]),
        "hook_palette": rng.choice(sorted(lk["palettes"])),
        "transition": rng.choice(lk["transitions"]),
        "upper": bool(lk.get("uppercase")) and rng.random() < 0.5,
        "seal": rng.choice(lk["seal_glyphs"]) if lk.get("seal_glyphs") else "",
    }


def _pick_ai(pool: list[dict], look_key: str, rng: random.Random,
             chan_recent: list[str], all_recent: list[str]) -> str:
    """1 hướng hợp look: chưa dùng trong kênh + không trùng ~10 video gần nhất của
    mọi kênh → chưa dùng trong kênh → kiểu đã lâu nhất không dùng trong kênh."""
    family = looks.base_look(look_key)          # phong cách tự nhập → look gốc
    fits = [e["key"] for e in pool if not e["looks"] or family in e["looks"]]
    fits = fits or [e["key"] for e in pool]
    for avoid in (set(chan_recent) | set(all_recent[:10]), set(chan_recent)):
        free = [k for k in fits if k not in avoid]
        if free:
            return rng.choice(free)
    newest = {k: i for i, k in reversed(list(enumerate(chan_recent)))}
    return max(fits, key=lambda k: newest.get(k, 10**6))


def _ai_style(project_id: int, look_key: str, channel: str, rng: random.Random,
              history: list[dict] | None = None) -> dict:
    """Hướng CHỮ + STYLE thumbnail cho model ảnh tự thiết kế (mỗi video 1 kiểu)."""
    if history is not None:
        chan, every = history, []
    else:
        chan = channel_history(channel, exclude=project_id, limit=0)
        d = _load()
        every = [d["projects"][p] for p in sorted(
            (p for p in d["projects"] if p.isdigit() and p != str(project_id)),
            key=int, reverse=True) if isinstance(d["projects"][p], dict)]
    out = {}
    for field, pool in (("ai_lettering", LETTERINGS), ("ai_design", THUMB_DESIGNS)):
        out[field] = _pick_ai(pool, look_key, rng, [r.get(field) for r in chan if r.get(field)],
                              [r.get(field) for r in every if r.get(field)])
    return out


def ensure_ai_style(recipe: dict | None) -> dict | None:
    """Công thức cũ (trước 2026-10-25) chưa có hướng chữ/style AI → bốc + lưu."""
    if not recipe or (recipe.get("ai_lettering") in _AI_BY_KEY
                      and recipe.get("ai_design") in _AI_BY_KEY):
        return recipe
    recipe.update(_ai_style(recipe.get("project_id", 0), recipe.get("look"),
                            recipe.get("channel", ""), random.Random()))
    return save_recipe(recipe) if "project_id" in recipe and "channel" in recipe else recipe


def ai_text(key: str | None) -> str:
    """Câu tiếng Anh của 1 hướng chữ/style ("" nếu không có)."""
    ent = _AI_BY_KEY.get(key or "")
    return ent["text"] if ent else ""


def ai_label(key: str | None) -> str:
    ent = _AI_BY_KEY.get(key or "")
    return ent["vi"] if ent else ""


def choose_recipe(project_id: int, *, look_key: str, channel: str,
                  style_key: str | None = None, legacy: bool = False,
                  font_lock: str | None = None, rng: random.Random | None = None,
                  history: list[dict] | None = None,
                  channel_name: str = "") -> dict:
    """Bốc tổ hợp KHÁC nhất so với ~8 video gần nhất của kênh; không lặp nguyên
    tổ hợp (bố cục, kiểu chữ, màu, font) đã dùng trong kênh nếu còn tổ hợp mới."""
    rng = rng or random.Random()
    lk = looks.look_info(look_key)
    hist = history if history is not None else channel_history(channel, exclude=project_id)
    used = {_combo(r) for r in (history if history is not None
                                 else channel_history(channel, exclude=project_id, limit=0))}
    cands = [_candidate(lk, rng, legacy) for _ in range(_CANDIDATES)]
    fresh = [c for c in cands if _combo(c) not in used] or cands
    best = min(_penalty(c, hist) for c in fresh)
    pick = rng.choice([c for c in fresh if _penalty(c, hist) <= best + 1e-9])
    look_key = look_key if looks.valid_look(look_key) else looks.DEFAULT_LOOK
    locked_font = font_lock or channel_font_lock(channel, look_key)
    pick["font"] = locked_font
    pick["hook_font"] = locked_font
    return {
        "v": RECIPE_VERSION, "project_id": int(project_id), "channel": channel,
        "channel_name": (channel_name or "").strip(),
        "look": look_key,
        "style": (style_key or "").strip().lower(), "legacy": bool(legacy),
        "font_lock": locked_font, "font_manual": bool(font_lock), **pick,
        **_ai_style(project_id, look_key, channel, rng, history),
    }


def _manifest(project_id: int, project_name: str | None) -> Optional[dict]:
    try:
        from .prompt_workflow import read_manifest
        return read_manifest(project_id, project_name)
    except Exception:
        return None


def _project_row(project_id: int) -> dict:
    con = looks._db()
    try:
        row = con.execute("SELECT name, instrument, music_style, video_style, "
                          "mix_duration_minutes FROM project WHERE id=?",
                          (project_id,)).fetchone()
    except Exception:
        row = None
    finally:
        con.close()
    return dict(row) if row else {}


def is_legacy_cover(project_id: int, project_name: str | None) -> bool:
    """Ảnh bìa đã/ sẽ vẽ theo luật cũ (chừa nửa TRÁI, không có khối bố cục)."""
    saved = _manifest(project_id, project_name)
    if saved:
        p0 = ((saved.get("prompts") or {}).get("0")) or ""
        return COMPOSITION_MARK not in p0
    return (config.images_dir(project_id, project_name) / "0.png").exists()


def recipe_for_project(project_id: int, project_name: str | None = None, *,
                       create: bool = True) -> Optional[dict]:
    """Công thức đã lưu (sổ → prompts.json). Chưa có và create=True → tạo + lưu."""
    rec = get_recipe(project_id)
    if rec:
        mapping, look_key = looks.look_for_project(project_id)
        locked = channel_font_lock(looks.channel_key(mapping), look_key)
        channel_name = looks.channel_name_for_project(project_id)
        if rec.get("font_lock") != locked or rec.get("channel_name", "") != channel_name:
            rec["font_lock"] = locked
            rec["font"] = locked
            rec["hook_font"] = locked
            rec["channel_name"] = channel_name
            save_recipe(rec)
        return ensure_ai_style(rec)
    saved = _manifest(project_id, project_name)
    if saved and isinstance(saved.get("thumbnail_recipe"), dict):
        return ensure_ai_style(save_recipe(dict(saved["thumbnail_recipe"])))
    if not create:
        return None
    mapping, look_key = looks.look_for_project(project_id)
    style = _project_row(project_id).get("video_style")
    rec = choose_recipe(project_id, look_key=look_key,
                        channel=looks.channel_key(mapping), style_key=style,
                        legacy=is_legacy_cover(project_id, project_name),
                        channel_name=looks.channel_name_for_project(project_id))
    return save_recipe(rec)


def new_recipe_for_prompts(project_id: int, style_key: str | None) -> dict:
    """Gọi khi SINH MỚI bộ prompt: dùng lại công thức chưa-legacy đã có (ổn định
    qua các lần thử lại), nếu không thì bốc công thức mới (giữ font khoá tay)."""
    old = get_recipe(project_id)
    mapping, look_key = looks.look_for_project(project_id)
    channel = looks.channel_key(mapping)
    if (old and not old.get("legacy") and old.get("look") == look_key
            and old.get("channel") == channel):
        if style_key and old.get("style") != style_key:
            old["style"] = style_key
            save_recipe(old)
        return ensure_ai_style(old)
    rec = choose_recipe(project_id, look_key=look_key, channel=channel,
                        style_key=style_key, legacy=False,
                        font_lock=(old or {}).get("font_lock") or None,
                        channel_name=looks.channel_name_for_project(project_id))
    return save_recipe(rec)


def set_font_lock(project_id: int, font_key: str | None,
                  project_name: str | None = None) -> dict:
    rec = recipe_for_project(project_id, project_name) or {}
    mapping, look_key = looks.look_for_project(project_id)
    channel = looks.channel_key(mapping)
    chosen = font_key or channel_font_lock(channel, look_key)
    with _LOCK:
        d = _load()
        d.setdefault("channel_fonts", {})[channel] = chosen
        for pid in d.get("channels", {}).get(channel, []):
            item = d.get("projects", {}).get(pid)
            if isinstance(item, dict):
                item["font_lock"] = chosen
                item["font_manual"] = bool(font_key)
                item["font"] = chosen
                item["hook_font"] = chosen
        _save(d)
    rec["font_lock"] = chosen
    rec["font_manual"] = bool(font_key)
    rec["font"] = chosen
    rec["hook_font"] = chosen
    return save_recipe(rec)


# ── Văn bản gửi model ảnh ────────────────────────────────────────
def composition_directive(recipe: dict, scenery: bool = False) -> str:
    """Khối bố cục ảnh bìa (tiếng Anh) — rỗng với công thức legacy.

    scenery=True (chế độ chỉ cảnh vật): góc máy/bố cục nói về điểm nhấn của khung
    cảnh thay cho nhân vật + dặn không người, không con vật."""
    if not recipe or recipe.get("legacy"):
        return ""
    lk = looks.look_info(recipe.get("look"))
    shot = next((s["text"] for s in SHOTS if s["key"] == recipe.get("shot")), SHOTS[0]["text"])
    layout = LAYOUT_TEXT.get(recipe.get("layout"), LAYOUT_TEXT["left"])
    if scenery:
        shot = SCENERY_SHOT_TEXT.get(recipe.get("shot") or "", SCENERY_SHOT_TEXT["wide"])
        layout = layout.replace("the characters", "the focal landmark of the place")
    light = next((x["text"] for x in LIGHTS if x["key"] == recipe.get("light")), "")
    parts = [shot, layout]
    if scenery:
        parts.append(SCENERY_ONLY_NOTE)
    if light:
        parts.append(f"lighting: {light} (unless the idea already specifies the time "
                     "of day or weather)")
    if recipe.get("env"):
        parts.append(f"colour mood: {recipe['env']}")
    if (recipe.get("style") or "") in lk["styles"]:
        parts.append(f"overall look: {lk['signature']}")
    return (f"{COMPOSITION_MARK} (mandatory): " + "; ".join(parts)
            + ". Keep that title area empty: no objects, no text, no letters.")


# ── Phụ đề + kiểu chữ cho thumbnail.py ───────────────────────────
def hours_label(minutes: float | None) -> str:
    try:
        m = int(round(float(minutes or 0)))
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    if m < 60:
        return f"{m} Minutes"
    h, rest = divmod(m, 60)
    unit = "Hour" if h == 1 else "Hours"
    return f"{h} {unit}" if rest == 0 else f"{h}+ {unit}"


def resolve_subtitle(recipe: dict, instrument: str = "",
                     minutes: float | None = None) -> str:
    lk = looks.look_info(recipe.get("look"))
    kws = list(recipe.get("kws") or [])
    while len(kws) < 3:
        kws.append(lk["keywords"][len(kws) % len(lk["keywords"])])
    inst = " ".join(w[:1].upper() + w[1:] for w in (instrument or "").split()) or "Healing"
    hours = hours_label(minutes)
    tpl = recipe.get("subtitle_tpl") or "{kw1} · {kw2} · {kw3}"
    if "{hours}" in tpl and not hours:
        tpl = "{kw1} · {kw2} · {kw3}"
    try:
        return tpl.format(kw1=kws[0], kw2=kws[1], kw3=kws[2], instrument=inst, hours=hours)
    except (KeyError, IndexError, ValueError):
        return " · ".join(kws[:3])


def project_subtitle(project_id: int, recipe: dict) -> str:
    row = _project_row(project_id)
    return resolve_subtitle(recipe, row.get("instrument") or "",
                            row.get("mix_duration_minutes"))


def render_style(recipe: dict | None) -> Optional[dict]:
    """Tham số kiểu chữ cho thumbnail._compose_title (None → kiểu cũ)."""
    if not recipe:
        return None
    from . import thumbnail
    lk = looks.look_info(recipe.get("look"))
    pal_key = recipe.get("palette")
    palette = lk["palettes"].get(pal_key) or next(iter(lk["palettes"].values()))
    font_key = recipe.get("font_lock") or recipe.get("font")
    return {
        "layout": recipe.get("layout") or "left",
        "treatment": recipe.get("treatment") or "gold3d",
        "palette": palette,
        "title_fonts": thumbnail.title_font_files(font_key),
        "sub_fonts": thumbnail.sub_font_files(recipe.get("sub_font")),
        "upper": bool(recipe.get("upper")),
        "seal": recipe.get("seal") or "",
    }


def hook_style(recipe: dict | None) -> Optional[dict]:
    """Font + màu cho chữ hook của Short (shorts.render_hook_png). None → kiểu
    cũ (chữ trắng, viền tối).

    Mặt chữ dùng `face_top` của bảng màu — mọi bảng màu đều có face_top rất
    sáng, viền dùng `outline` rất tối → chữ vẫn nổi trên nền video tuỳ ý.
    Công thức CŨ chưa có hook_font/hook_palette → rơi về font/màu tiêu đề."""
    if not recipe:
        return None
    from . import thumbnail
    lk = looks.look_info(recipe.get("look"))
    pal_key = recipe.get("hook_palette") or recipe.get("palette")
    palette = lk["palettes"].get(pal_key) or next(iter(lk["palettes"].values()))
    font_key = (recipe.get("hook_font") or recipe.get("font_lock")
                or recipe.get("font"))
    return {"fonts": thumbnail.title_font_files(font_key),
            "fill": tuple(palette.get("face_top") or (255, 255, 255)),
            "stroke": tuple(palette.get("outline") or (10, 18, 15))}


def describe(recipe: dict | None) -> dict:
    """Tóm tắt cho UI / log."""
    if not recipe:
        return {}
    lk = looks.look_info(recipe.get("look"))
    return {"look": recipe.get("look"), "look_label": lk["label"],
            "layout": recipe.get("layout"), "treatment": recipe.get("treatment"),
            "palette": recipe.get("palette"),
            "font": recipe.get("font_lock") or recipe.get("font"),
            "font_auto": not recipe.get("font_lock"), "shot": recipe.get("shot"),
            "light": recipe.get("light"),
            # Kiểu THỰC dùng khi ghép (recipe cũ ghi hblur… → softfade).
            "transition": looks.finish_for(recipe.get("look"), recipe)["transition"],
            "hook_font": recipe.get("hook_font") or recipe.get("font"),
            "hook_palette": recipe.get("hook_palette") or recipe.get("palette"),
            "legacy": bool(recipe.get("legacy")),
            # Chữ + style thumbnail do ChatGPT/model ảnh tự thiết kế (trừ khi khoá font).
            "ai_text": not recipe.get("font_lock"),
            "ai_lettering": recipe.get("ai_lettering") or "",
            "ai_lettering_label": ai_label(recipe.get("ai_lettering")),
            "ai_design": recipe.get("ai_design") or "",
            "ai_design_label": ai_label(recipe.get("ai_design"))}


__all__ = ["LEDGER", "COMPOSITION_MARK", "LAYOUT_TEXT", "SHOTS", "LIGHTS",
           "get_recipe", "save_recipe", "channel_history", "choose_recipe",
           "recipe_for_project", "new_recipe_for_prompts", "set_font_lock",
           "is_legacy_cover", "composition_directive", "hours_label",
           "resolve_subtitle", "project_subtitle", "render_style", "hook_style",
           "describe", "LETTERINGS", "THUMB_DESIGNS", "ensure_ai_style", "ai_text",
           "ai_label"]
