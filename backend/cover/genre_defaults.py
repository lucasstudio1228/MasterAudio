"""
cover/genre_defaults.py — Cài đặt video cover TỰ THEO THỂ LOẠI của project.

Project đã chọn thể loại (music_style) + dòng nhạc (music_line) lúc tạo, nên
ngôn ngữ / phong cách cover / dòng chữ phụ thumbnail không cần chỉnh tay nữa:
trong bảng coversettings, ``language`` hoặc ``cover_style`` để TRỐNG = «theo
thể loại»; chỉ khi người dùng tự chọn mới lưu giá trị riêng. Mọi bước tự động
(căn lời, thumbnail, tiêu đề/mô tả YouTube, chữ trên video) đọc qua
``effective_cover`` để luôn dùng cùng 1 kết quả.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"

LANGUAGES = ("vi", "en")
STYLES = ("ballad", "remix", "acoustic", "original")

# Từ khoá thể loại → phong cách cover. Xét theo thứ tự: nhóm nhạc nhảy trước
# (vd «Deep Chill House» là remix chứ không phải ballad).
_STYLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("remix", ("vietmix", "viet mix", "mix việt", "mix viet", "house", "remix", "edm",
               "dance", "nonstop", "techno", "trance", "phonk", "disco", "dj")),
    ("acoustic", ("acoustic", "unplugged", "mộc", "guitar")),
    ("ballad", ("ballad", "lofi", "lo-fi", "lo fi", "r&b", "rnb", "soul", "jazz", "blues",
                "bolero", "slow", "chill", "piano", "indie")),
    ("original", ("pop", "rock", "rap", "hip hop", "hiphop", "original", "nguyên bản")),
]

# Dòng chữ phụ thumbnail theo phong cách; {g} = tên thể loại của project.
_SUBTITLE_WITH_GENRE = {
    "vi": {"ballad": "Tuyển Tập {g} Cover Nhẹ Nhàng",
           "remix": "Nhạc {g} Cover Cực Cuốn",
           "acoustic": "{g} Cover Chill",
           "original": "Nhạc {g} Cover Hay Nhất"},
    "en": {"ballad": "{g} Cover Playlist",
           "remix": "{g} Cover Mix",
           "acoustic": "{g} Covers",
           "original": "Best {g} Covers"},
}
_SUBTITLE_PLAIN = {
    "vi": {"ballad": "Tuyển Tập Ballad Cover Nhẹ Nhàng",
           "remix": "Nhạc Remix Cover Cực Cuốn",
           "acoustic": "Acoustic Cover Chill",
           "original": "Nhạc Cover Hay Nhất"},
    "en": {"ballad": "Ballad Cover Playlist",
           "remix": "Remix Cover Mix",
           "acoustic": "Acoustic Covers",
           "original": "Best Cover Songs"},
}


def _style_from(text: str) -> str:
    key = (text or "").casefold()
    if not key.strip():
        return ""
    for style, words in _STYLE_KEYWORDS:
        if any(w in key for w in words):
            return style
    return ""


def language_for_line(music_line: str) -> str:
    """Dòng nhạc US-UK → tiếng Anh; còn lại (VN, nhạc Hoa/Hàn/Nhật lời Việt…) → tiếng Việt."""
    line = (music_line or "").strip().casefold()
    return "en" if line in ("us-uk", "us", "uk", "usuk", "english", "en") else "vi"


def genre_defaults(music_style: str = "", music_line: str = "",
                   description: str = "", name: str = "") -> dict:
    """Cài đặt gợi ý từ thể loại của project.

    → {language, cover_style, genre, reason}. Thể loại (music_style) quyết định
    trước; trống/lạ thì xem mô tả rồi tên project; không đoán được → ballad."""
    genre = (music_style or "").strip()
    style, source = "", ""
    for text, label in ((genre, "thể loại"), (description, "mô tả"), (name, "tên project")):
        style = _style_from(text)
        if style:
            source = label
            break
    if not style:
        style, source = "ballad", "mặc định"
    lang = language_for_line(music_line)
    reason = f"{genre or 'chưa chọn thể loại'}"
    if (music_line or "").strip():
        reason += f" · {music_line.strip()}"
    return {"language": lang, "cover_style": style, "genre": genre,
            "style_source": source, "reason": reason}


def default_subtitle(language: str, cover_style: str, genre: str = "") -> str:
    lang = language if language in _SUBTITLE_PLAIN else "vi"
    style = cover_style if cover_style in STYLES else "ballad"
    g = (genre or "").strip()
    # Thể loại ngắn gọn thì đưa lên thumbnail; dài/lạ thì dùng câu chung.
    if g and len(g) <= 18 and _style_from(g):
        return _SUBTITLE_WITH_GENRE[lang][style].format(g=g)
    return _SUBTITLE_PLAIN[lang][style]


def project_genre(project) -> dict:
    """Project (ORM) → dict thể loại. Đọc từng thuộc tính (không dùng model_dump)
    để object đã hết hạn sau commit vẫn tự nạp lại đúng giá trị."""
    return {k: getattr(project, k, "") or "" for k in
            ("name", "description", "music_style", "music_line")}


def resolve(settings: dict | None, project: dict | None) -> dict:
    """Ghép cài đặt đã lưu (có thể trống = theo thể loại) với thể loại project.

    → {language, cover_style, thumb_subtitle, auto: {language, cover_style,
    thumb_subtitle}, genre, reason} — đây là giá trị mọi bước tự động dùng."""
    settings = settings or {}
    project = project or {}
    d = genre_defaults(project.get("music_style", ""), project.get("music_line", ""),
                       project.get("description", ""), project.get("name", ""))
    lang = (settings.get("language") or "").strip()
    style = (settings.get("cover_style") or "").strip()
    sub = (settings.get("thumb_subtitle") or "").strip()
    auto = {"language": lang not in LANGUAGES, "cover_style": style not in STYLES,
            "thumb_subtitle": not sub}
    lang = d["language"] if auto["language"] else lang
    style = d["cover_style"] if auto["cover_style"] else style
    return {"language": lang, "cover_style": style,
            "thumb_subtitle": sub or default_subtitle(lang, style, d["genre"]),
            "auto": auto, "genre": d["genre"], "reason": d["reason"],
            "suggested": {"language": d["language"], "cover_style": d["cover_style"]}}


def effective_cover(project_id: int, db_path: Path | str | None = None) -> dict:
    """Đọc thẳng SQLite (dùng được trong luồng nền) → kết quả ``resolve``."""
    settings: dict = {}
    project: dict = {}
    try:
        conn = sqlite3.connect(str(db_path or DB_PATH))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT language, cover_style, thumb_subtitle FROM "
                               "coversettings WHERE project_id = ?", (project_id,)).fetchone()
            if row:
                settings = dict(row)
            row = conn.execute("SELECT name, description, music_style, music_line FROM "
                               "project WHERE id = ?", (project_id,)).fetchone()
            if row:
                project = dict(row)
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return resolve(settings, project)
