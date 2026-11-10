"""
cover/music_line.py — Hồ sơ «Dòng nhạc» (music_line) dùng chung cho MỌI prompt.

Project chọn Dòng nhạc (VN / US-UK / Chinese / Japanese / Korean / Thai / tự nhập)
+ Thể loại (music_style). Mọi prompt (Styles Suno, ảnh bìa, clip, thumbnail) đọc
qua đây để luôn bám đúng lựa chọn — không còn ghi cứng «Vietnamese …».

Ngôn ngữ HÁT là chuyện khác: bản cover giữ nguyên lời gốc, nên ngôn ngữ lời được
nhận từ chính lời bài (``lyrics_language``); dòng nhạc chỉ quyết định chất âm
thanh / phong cách sản xuất và hình ảnh.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"

# key → (nhãn, chất âm thanh cho Suno, người trong ảnh, phong cách kênh/playlist,
#        ngôn ngữ lời mặc định)
_PROFILES = {
    "vn": ("VN", "Vietnamese-market", "Vietnamese",
           "popular Vietnamese mixtape / playlist YouTube channels", "Vietnamese",
           "Vietnamese-market polish and natural emotionally direct phrasing"),
    "us-uk": ("US-UK", "contemporary Western-market", "",
              "popular US-UK English music playlist YouTube channels", "English",
              "contemporary US-UK radio polish and natural hook-led phrasing"),
    "chinese": ("Chinese", "Greater-China-market", "Chinese",
                "popular Chinese music playlist YouTube channels", "Mandarin Chinese",
                "Greater-China-market polish and tonal-language-friendly phrasing"),
    "japanese": ("Japanese", "Japanese-market", "Japanese",
                 "popular Japanese music playlist YouTube channels", "Japanese",
                 "Japanese-market polish and articulate syllabic phrasing"),
    "korean": ("Korean", "Korean-market", "Korean",
               "popular Korean music playlist YouTube channels", "Korean",
               "Korean-market polish, precise vocal layers and section contrast"),
    "thai": ("Thai", "Thai-market", "Thai",
             "popular Thai music playlist YouTube channels", "Thai",
             "Thai-market polish and tonal-language-friendly phrasing"),
}
_ALIASES = {
    "vn": "vn", "viet": "vn", "vietnam": "vn", "vietnamese": "vn", "nhac viet": "vn",
    "vpop": "vn", "v-pop": "vn",
    "us-uk": "us-uk", "us uk": "us-uk", "usuk": "us-uk", "us": "us-uk", "uk": "us-uk",
    "english": "us-uk", "en": "us-uk",
    "chinese": "chinese", "china": "chinese", "c-pop": "chinese", "cpop": "chinese",
    "nhac hoa": "chinese", "mandopop": "chinese",
    "japanese": "japanese", "japan": "japanese", "j-pop": "japanese", "jpop": "japanese",
    "nhac nhat": "japanese",
    "korean": "korean", "korea": "korean", "k-pop": "korean", "kpop": "korean",
    "nhac han": "korean",
    "thai": "thai", "thailand": "thai", "t-pop": "thai", "nhac thai": "thai",
}

# Thể loại/khu vực KHÔNG được tự thêm vào Styles nếu người dùng không chọn
# (vd chọn Deep House mà AI viết «Latin», «reggaeton»).
FOREIGN_GENRE_WORDS = (
    "latin", "reggaeton", "salsa", "bachata", "bossa", "samba", "tango", "cumbia",
    "flamenco", "afrobeat", "amapiano", "dancehall", "k-pop", "kpop", "j-pop", "jpop",
    "c-pop", "cpop", "mandopop", "v-pop", "vpop", "t-pop", "bolero", "country",
    "bluegrass", "reggae", "polka",
)
# Từ trong FOREIGN_GENRE_WORDS vẫn hợp lệ khi đúng là dòng nhạc đã chọn.
LINE_ALLOWED_WORDS = {
    # Dòng nhạc là thị trường/khu vực, không cấp quyền tự thêm V-pop/K-pop/...
    # như một genre thứ hai. Chỉ music_style do người dùng chọn mới quyết định genre.
    "vn": (), "chinese": (), "japanese": (), "korean": (), "thai": (),
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").strip().casefold())
    text = text.replace("đ", "d")
    return "".join(c for c in text if not unicodedata.combining(c))


def line_key(music_line: str) -> str:
    """'' (chưa chọn) | vn | us-uk | chinese | japanese | korean | thai | custom."""
    raw = _fold(music_line)
    if not raw:
        return ""
    if raw in _ALIASES:
        return _ALIASES[raw]
    for word, key in _ALIASES.items():
        if len(word) > 3 and word in raw:
            return key
    return "custom"


def line_profile(music_line: str) -> dict:
    """→ {key, label, sound, people, channels, language}. Chưa chọn = VN (mặc
    định của tool); dòng nhạc tự nhập (vd «Latin», «Indonesia») dùng nguyên chữ."""
    key = line_key(music_line)
    if key == "custom":
        name = " ".join((music_line or "").split())[:40]
        return {"key": key, "label": name, "sound": f"{name}-market", "people": "",
                "channels": f"popular {name} music playlist YouTube channels",
                "language": "", "prompt_modifier":
                f"{name}-market production polish without changing the selected genre"}
    label, sound, people, channels, language, modifier = _PROFILES[key or "vn"]
    return {"key": key or "vn", "label": label, "sound": sound, "people": people,
            "channels": channels, "language": language, "prompt_modifier": modifier}


def lyrics_language(lyrics: str, music_line: str = "") -> str:
    """Ngôn ngữ của LỜI bài (bản cover giữ nguyên lời). Không đoán được → ngôn
    ngữ mặc định của dòng nhạc ('' nếu dòng nhạc tự nhập)."""
    text = lyrics or ""
    counts = {"vi": 0, "ko": 0, "ja": 0, "zh": 0, "th": 0, "latin": 0}
    for ch in text:
        o = ord(ch)
        if ch in "ăĂđĐơƠưƯ" or 0x1EA0 <= o <= 0x1EF9:
            counts["vi"] += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            counts["ko"] += 1
        elif 0x3040 <= o <= 0x30FF:
            counts["ja"] += 1
        elif 0x4E00 <= o <= 0x9FFF:
            counts["zh"] += 1
        elif 0x0E00 <= o <= 0x0E7F:
            counts["th"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["latin"] += 1
    letters = sum(counts.values())
    if letters >= 20:
        if counts["vi"] >= max(3, letters * 0.02):
            return "Vietnamese"
        if counts["ko"] > letters * 0.2:
            return "Korean"
        if counts["ja"] > letters * 0.05:
            return "Japanese"
        if counts["zh"] > letters * 0.2:
            return "Mandarin Chinese"
        if counts["th"] > letters * 0.2:
            return "Thai"
        if counts["latin"] > letters * 0.9:
            return "English"
    return line_profile(music_line)["language"]


def project_line(project_id: int, db_path: Path | str | None = None) -> str:
    """Đọc music_line của project thẳng từ SQLite (dùng được trong luồng nền)."""
    try:
        conn = sqlite3.connect(str(db_path or DB_PATH))
        try:
            row = conn.execute("SELECT music_line FROM project WHERE id = ?",
                               (int(project_id),)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError):
        return ""
    return (row[0] or "") if row else ""
