"""
video/service.py — Bản Cover Audio Tool (không có video): chỉ còn các hàm tra
cứu dùng chung cho Suno / Mix (tìm file audio mới nhất, đọc thông tin project).
Toàn bộ pipeline dựng video / ảnh bìa / đăng YouTube đã được gỡ bỏ.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

from . import config

_ROOT = Path(__file__).parent.parent.parent
OUTPUTS_ROOT = _ROOT / "outputs"       # nơi phần Audio lưu mix.wav


def find_latest_audio(project_id: int,
                      project_name: str | None = None) -> Optional[str]:
    """Nhạc nền mới nhất cho video: xét CẢ mix.wav (phần Audio) LẪN file upload
    trực tiếp (media/<project>/uploads/*). Trả về file có mtime mới nhất — nên
    upload 1 track dài xong là dùng ngay được, không cần render mix 15 track."""
    candidates: list[Path] = []
    kind = _project_kind(project_id)

    # Track lẻ luôn ưu tiên đúng cover/master; không được chọn nhầm mix.wav cũ.
    if kind == "single":
        con = _db()
        try:
            rows = con.execute(
                "SELECT t.filepath FROM coversong cs JOIN track t ON t.id=cs.track_id "
                "WHERE cs.project_id=? ORDER BY cs.position, cs.id", (project_id,)
            ).fetchall()
            covers = [Path(r["filepath"]) for r in rows
                      if r["filepath"] and Path(r["filepath"]).is_file()]
        finally:
            con.close()
        if covers:
            covers.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(covers[0])
    else:
        pdir = OUTPUTS_ROOT / str(project_id)
        if pdir.exists():
            candidates.extend(pdir.glob("*/mix.wav"))

    up = config.uploads_dir(project_id, project_name)
    if up.exists():
        candidates.extend(p for p in up.iterdir()
                          if p.is_file() and p.suffix.lower() in config.AUDIO_EXTS)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _project_kind(project_id: int) -> str:
    """``single`` | ``mixset``; lỗi DB thì giữ hành vi cũ là mixset."""
    con = _db()
    try:
        row = con.execute("SELECT kind FROM project WHERE id=?", (project_id,)).fetchone()
        return "single" if row and row["kind"] == "single" else "mixset"
    except Exception:
        return "mixset"
    finally:
        con.close()


def _db():
    """Kết nối SQLite app.db (worker thread) — trả sqlite3.Connection."""
    import sqlite3
    from backend.database import DB_PATH
    # Bản clone mới chưa có data/ → tạo thư mục để lỗi «no such table»
    # rơi vào except của nơi gọi (trả mặc định) thay vì văng lỗi mở file.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def match_channel(rows: list[dict], instrument: str, music_style: str,
                  music_line: str = "") -> dict | None:
    """Dòng ánh xạ khớp (instrument, music_style) — không phân biệt hoa/thường &
    khoảng trắng thừa; dòng mới nhất thắng. Dùng cho project CHƯA chọn kênh.

    Tool Cover (project không có nhạc cụ) khớp theo dòng nhạc + thể loại, thứ tự:
    cùng dòng + cùng thể loại → cùng dòng, kênh chưa ghi thể loại → cùng dòng →
    kênh chưa ghi dòng nhạc + cùng thể loại. Project chưa có dòng nhạc → chỉ
    khớp thể loại như trước."""
    def _n(r: dict, key: str) -> str:
        return (r.get(key) or "").strip().lower()

    ins = (instrument or "").strip().lower()
    sty = (music_style or "").strip().lower()
    line = (music_line or "").strip().lower()
    rows = sorted(rows, key=lambda r: r.get("id") or 0, reverse=True)
    if not ins:
        if line:
            same = [r for r in rows if _n(r, "music_line") == line]
            hit = (next((r for r in same if sty and _n(r, "music_style") == sty), None)
                   or next((r for r in same if not _n(r, "music_style")), None)
                   or (same[0] if same else None))
            if hit or not sty:
                return hit
            return next((r for r in rows if not _n(r, "music_line")
                         and _n(r, "music_style") == sty), None)
        if not sty:
            return None
        return next((r for r in rows if _n(r, "music_style") == sty), None)
    if not sty:
        return None
    hit = next((r for r in rows if (r.get("instrument") or "").strip().lower() == ins
                and (r.get("music_style") or "").strip().lower() == sty), None)
    if hit:
        return hit
    # 'Piano' (danh mục) ↔ 'Lofi Piano' (ánh xạ): cùng nhạc cụ + cùng thể loại.
    from .music_spec import same_instrument
    return next((r for r in rows if (r.get("music_style") or "").strip().lower() == sty
                 and same_instrument(r.get("instrument") or "", ins)), None)


def _lookup_channel_mapping(instrument: str, music_style: str,
                            music_line: str = "") -> dict | None:
    """Tra ChannelMapping theo (instrument, music_style, music_line). Trả dict/None."""
    con = _db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM channelmapping").fetchall()]
    except Exception:
        return None
    finally:
        con.close()
    return match_channel(rows, instrument, music_style, music_line)


def _lookup_channel_by_id(channel_id) -> dict | None:
    """ChannelMapping theo id (kênh người dùng chọn cho project)."""
    try:
        cid = int(channel_id or 0)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    con = _db()
    try:
        row = con.execute("SELECT * FROM channelmapping WHERE id=?", (cid,)).fetchone()
    except Exception:
        return None
    finally:
        con.close()
    return dict(row) if row else None


def channel_for(channel_id, instrument: str, music_style: str,
                music_line: str = "") -> dict | None:
    """Kênh YouTube của 1 project: kênh người dùng CHỌN (channel_id) — kênh nào
    cũng nhận nhạc cụ/thể loại nào; chưa chọn → tự chọn giống form project hiển thị
    (tool Cover: dòng nhạc + thể loại; project cũ: nhạc cụ + thể loại)."""
    m = _lookup_channel_by_id(channel_id)
    if m:
        return m
    if not (music_style or "").strip() and not (music_line or "").strip():
        return None
    return _lookup_channel_mapping(instrument or "", music_style or "", music_line or "")


def channel_for_project(project_id: int, instrument: str | None = None,
                        music_style: str | None = None) -> dict | None:
    fields = _read_project_fields(project_id)
    ins = (instrument if instrument is not None else fields.get("instrument")) or ""
    sty = (music_style if music_style is not None else fields.get("music_style")) or ""
    return channel_for(fields.get("channel_id"), ins, sty, fields.get("music_line") or "")


def _read_project_fields(project_id: int) -> dict:
    con = _db()
    row = None
    try:
        # DB chưa chạy migration M022 (music_line) / M015 (channel_id) → bớt cột.
        for extra in (", channel_id, music_line", ", channel_id", ""):
            try:
                row = con.execute(
                    "SELECT name, instrument, music_style, auto_upload" + extra
                    + " FROM project WHERE id=?", (project_id,)
                ).fetchone()
                break
            except sqlite3.OperationalError:
                continue
    finally:
        con.close()
    return dict(row) if row else {}
