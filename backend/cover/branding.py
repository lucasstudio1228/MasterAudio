"""
cover/branding.py — Chữ dùng cho thumbnail/tiêu đề theo ngôn ngữ + phong cách
cover của project (thay cho từ khoá nhạc thiền của tool cũ).
"""
from __future__ import annotations

from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"

SUBTITLES = {
    "vi": {"ballad": "Tuyển Tập Ballad Cover Nhẹ Nhàng",
           "remix": "Nhạc Remix Cover Cực Cuốn",
           "acoustic": "Acoustic Cover Chill",
           "original": "Nhạc Cover Hay Nhất"},
    "en": {"ballad": "Ballad Cover Playlist",
           "remix": "Remix Cover Mix",
           "acoustic": "Acoustic Covers",
           "original": "Best Cover Songs"},
}
STYLE_WORDS = {"ballad": "slow emotional ballad", "remix": "energetic dance remix",
               "acoustic": "warm acoustic", "original": "popular"}


def project_cover(project_id: int) -> dict:
    """{language, cover_style, thumb_subtitle} ĐÃ GHÉP thể loại project: ô nào để
    «theo thể loại» thì tự suy từ music_style / music_line (xem genre_defaults)."""
    from .genre_defaults import effective_cover
    c = effective_cover(project_id, DB_PATH)
    lang = c["language"] if c["language"] in SUBTITLES else "vi"
    style = c["cover_style"] if c["cover_style"] in STYLE_WORDS else "ballad"
    return {"language": lang, "cover_style": style, "thumb_subtitle": c["thumb_subtitle"]}


def cover_subtitle(project_id: int) -> str:
    c = project_cover(project_id)
    return c["thumb_subtitle"].strip() or SUBTITLES[c["language"]][c["cover_style"]]


TRACKLIST_MAX = 6          # số bài in trên thumbnail (nhiều hơn → chữ quá nhỏ / dễ sai)


def cover_tracklist(project_id: int, limit: int = TRACKLIST_MAX) -> list[str]:
    """Tên bài theo thứ tự trong project (coversong.position) — cho danh sách bài
    đánh số trên thumbnail kiểu mixtape. Lỗi / chưa có bài → []."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            rows = conn.execute("SELECT title FROM coversong WHERE project_id = ? "
                                "ORDER BY position, id", (project_id,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    out: list[str] = []
    for (t,) in rows:
        t = " ".join(str(t or "").split())
        if len(t) > 32:
            t = t[:31].rstrip() + "…"
        if t and t not in out:
            out.append(t)
    return out[:max(0, limit)]
