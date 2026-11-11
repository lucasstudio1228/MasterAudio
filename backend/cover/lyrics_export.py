"""Xuất lời bài hát ra file phụ đề .srt để tự chèn vào CapCut (2026-11-11).

Kiểu hát karaoke: CHỈ có lời bài hát, mỗi câu 1 phụ đề, hiện đúng lúc câu đó
được hát trong bản mix — không có tên bài, ca sĩ, tên kênh hay chữ ngoài luồng.

Mốc thời gian dùng cùng quy tắc với lớp lyrics trên video (overlay.py):
- giây của câu trong bản mix = start của bài + (giây trong file bài − src_offset);
- mỗi bài chỉ hiện lời trong khung của bài đó (đổi bài ở giữa vùng crossfade);
- câu còn hiện thêm LINE_HOLD_SEC sau khi hát xong nếu chưa tới câu sau.

File được ghi vào media/<nhóm>/<project>/nhac_mix/<tên project>.srt, cạnh file
nhạc mix (cùng tên → CapCut/người dùng dễ nhận ra cặp nhạc + lời).
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional

from .lyrics import load_lines

DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"
LINE_HOLD_SEC = 2.0     # giống overlay.LINE_HOLD_SEC
MIN_LINE_SEC = 0.3      # câu quá ngắn (bị cắt ở vùng crossfade) thì bỏ


def _segments(info: dict, total: float) -> list[dict]:
    """Đoạn phát của từng bài trong mix + mốc đổi bài (giữa vùng crossfade)."""
    tracks = info.get("tracks") or []
    segs = []
    for i, t in enumerate(tracks):
        start, end = float(t["start_seconds"]), float(t["end_seconds"])
        nxt = tracks[i + 1] if i + 1 < len(tracks) else None
        switch_out = ((float(nxt["start_seconds"]) + end) / 2) if nxt else total
        segs.append({"song_id": t.get("song_id"), "start": start, "end": end,
                     "src_off": float(t.get("src_offset_seconds") or 0.0),
                     "switch_out": switch_out})
    for i, s in enumerate(segs):
        s["switch_in"] = segs[i - 1]["switch_out"] if i else 0.0
    return segs


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return " ".join(text.split())


def lyric_timeline(info: dict, songs_lines: dict, total: Optional[float] = None
                   ) -> list[tuple[float, float, str]]:
    """→ [(giây_bắt_đầu, giây_kết_thúc, câu hát)] theo thời gian của bản mix."""
    if total is None:
        total = float((info.get("audio") or {}).get("total_duration_seconds") or 0) or None
    tracks = info.get("tracks") or []
    if total is None:
        total = max((float(t["end_seconds"]) for t in tracks), default=0.0)
    out: list[tuple[float, float, str]] = []
    for s in _segments(info, total):
        lines = songs_lines.get(s["song_id"]) or []
        limit = s["switch_out"]
        mix = []
        for l in lines:
            text = _clean(l.get("text", ""))
            if not text:
                continue
            t0 = s["start"] + (float(l["start"]) - s["src_off"])
            t1 = s["start"] + (float(l["end"]) - s["src_off"])
            if t0 < s["start"] - 0.05 or t0 >= s["end"]:
                continue
            t0 = max(t0, s["switch_in"])
            if min(t1, limit) - t0 < MIN_LINE_SEC:
                continue
            mix.append((t0, min(t1, s["end"]), text))
        for i, (t0, t1, text) in enumerate(mix):
            nxt = mix[i + 1][0] if i + 1 < len(mix) else limit
            show_end = min(max(t1, t0 + 0.4) + LINE_HOLD_SEC, nxt, limit)
            if show_end - t0 >= MIN_LINE_SEC:
                out.append((max(t0, 0.0), show_end, text))
    return out


def _srt_ts(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(timeline: list[tuple[float, float, str]]) -> str:
    blocks = []
    for n, (t0, t1, text) in enumerate(timeline, 1):
        blocks.append(f"{n}\n{_srt_ts(t0)} --> {_srt_ts(t1)}\n{text}\n")
    return "\n".join(blocks)


# ---------------- dữ liệu từ app.db / outputs ----------------
def _project_data(project_id: int) -> tuple[Optional[str], str, list[tuple[int, list, str]],
                                            Optional[str]]:
    """→ (tên project, kind, [(song_id, lines, filepath)], output_dir mix mới nhất)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        prow = conn.execute("SELECT name, kind FROM project WHERE id=?",
                            (project_id,)).fetchone()
        songs = conn.execute(
            "SELECT cs.id, cs.lyrics_json, t.filepath FROM coversong cs "
            "JOIN track t ON t.id=cs.track_id WHERE cs.project_id=? "
            "ORDER BY cs.position, cs.id", (project_id,)).fetchall()
        mrows = conn.execute(
            "SELECT output_dir FROM mix WHERE project_id=? AND status='completed' "
            "AND output_dir IS NOT NULL ORDER BY id DESC", (project_id,)).fetchall()
    finally:
        conn.close()
    mix_dir = next((d for (d,) in mrows if (Path(d) / "mix_info.json").is_file()), None)
    name = prow[0] if prow else None
    kind = (prow[1] if prow else None) or "mixset"
    return name, kind, [(sid, load_lines(j), fp) for sid, j, fp in songs], mix_dir


def _audio_duration(path: str) -> Optional[float]:
    try:
        import soundfile as sf
        return float(sf.info(path).duration)
    except Exception:
        return None


def srt_for_project(project_id: int, mix_wav: str | Path | None = None) -> str:
    """Dựng nội dung .srt. Ưu tiên mix_info.json cạnh mix_wav / mix mới nhất;
    project bài lẻ chưa mix thì dùng thẳng lời của bài (mốc 0)."""
    _, kind, songs, mix_dir = _project_data(project_id)
    lines_by_song = {sid: lines for sid, lines, _ in songs}
    info_path = None
    if mix_wav is not None:
        p = Path(mix_wav)
        for cand in (p.with_name(p.stem + "_info.json"), p.with_name("mix_info.json")):
            if cand.is_file():
                info_path = cand
                break
    if info_path is None and mix_dir:
        info_path = Path(mix_dir) / "mix_info.json"
    if info_path is not None:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        return build_srt(lyric_timeline(info, lines_by_song))
    if songs and (kind == "single" or len(songs) == 1):
        sid, lines, fp = songs[0]
        dur = _audio_duration(fp) if fp else None
        if dur is None:
            dur = max((float(l["end"]) for l in lines), default=0.0) + LINE_HOLD_SEC
        info = {"audio": {"total_duration_seconds": dur}, "tracks": [{
            "song_id": sid, "start_seconds": 0.0, "end_seconds": dur,
            "src_offset_seconds": 0.0}]}
        return build_srt(lyric_timeline(info, lines_by_song))
    return ""


def write_srt(dest: Path, content: str) -> Path:
    # UTF-8 có BOM: CapCut/Windows đọc đúng tiếng Việt có dấu.
    dest.write_text(content, encoding="utf-8-sig", newline="\r\n")
    return dest
