"""
cover/mix.py — Mix các bài cover THEO ĐÚNG THỨ TỰ người dùng xếp.

Khác tool cũ (nhạc thiền): không xáo trộn, không cần tối thiểu 15 bài, tên
đoạn = tên bài hát (không nhờ AI đặt tên). Tuỳ chọn lặp lại cả danh sách cho
đủ thời lượng video đã chọn.

Bài nào đã có bản master (ban_cover/mastered/<tên>_master.wav) → Mix dùng bản
đó, chưa có thì dùng file gốc. «source_file» trong mix_info vẫn là tên file gốc
(khớp Track.filename); «audio_file» = file thật sự đem ghép.

Ngoài mix.wav + mix.cue/tracklist/info như cũ, mix_info.json có thêm cho mỗi
đoạn: song_id, track_id, artist, src_offset_seconds = vị trí trong FILE bài tại
đúng thời điểm đoạn bắt đầu trong mix. Nhờ đó lời bài hát (tính theo giây của
file bài) đổi được sang giây của video:
    t_video = start_seconds + (t_bai - src_offset_seconds)
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import soundfile as sf

from backend import core_bridge  # noqa: F401 — thêm thư mục gốc vào sys.path
from backend.cover import mastering
from analyzer import find_transition_points
from audio_loader import load_audio
from crossfade_engine import (BIT_DEPTH_SUBTYPES, _resample, _StreamWriter,
                              _to_stereo, make_crossfade_curves)
from metadata_writer import write_metadata

MIN_SAMPLE_RATE = 44100


def ordered_songs(db_path: str, project_id: int) -> list[dict]:
    """Bài của project theo thứ tự phát (CoverSong.position, rồi track id)."""
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(coversong)")}
        gain = "COALESCE(cs.mix_gain_db, 0)" if "mix_gain_db" in cols else "0"
        rows = conn.execute(f"""
            SELECT t.id, t.filepath, t.filename, cs.id, cs.title, cs.artist,
                   COALESCE(cs.position, 999999), {gain}
            FROM track t LEFT JOIN coversong cs ON cs.track_id = t.id
            WHERE t.project_id = ?
            ORDER BY COALESCE(cs.position, 999999), t.id
        """, (project_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for tid, path, fname, sid, title, artist, _, gain_db in rows:
        master = mastering.find_master(Path(path)) if path else None
        out.append({"track_id": tid, "path": str(master) if master else path,
                    "source_file": fname or Path(path).name, "mastered": master is not None,
                    "song_id": sid, "title": (title or "").strip() or Path(fname).stem,
                    "artist": (artist or "").strip(), "mix_gain_db": float(gain_db or 0.0)})
    return out


def mix_target(songs: list[dict]) -> float:
    """Độ to mục tiêu của Mix: có bài dùng bản master → theo preset master
    (không đè mất công master), không thì -16 LUFS như trước."""
    from backend.loudness_service import TARGET_LUFS
    return (mastering.mix_target_lufs() if any(s["mastered"] for s in songs)
            else TARGET_LUFS)


def _cover_settings(db_path: str, project_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT repeat_to_fill FROM coversettings "
                           "WHERE project_id = ?", (project_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    return {"repeat_to_fill": bool(row[0]) if row else False}


def _load_stereo(track, target_sr: int) -> np.ndarray:
    audio, sr = load_audio(track)
    audio = np.asarray(audio, dtype=np.float32)
    audio = _to_stereo(_resample(audio, sr, target_sr))
    return np.ascontiguousarray(audio, dtype=np.float32)


def _load_normalized(track, target_sr: int, target_lufs: float | None = None) -> np.ndarray:
    """Cách cũ (tắt cân bằng): chỉ chuẩn LUFS tích hợp từng bài."""
    from backend.loudness_service import TARGET_LUFS, normalize_audio
    return normalize_audio(_load_stereo(track, target_sr), target_sr,
                           TARGET_LUFS if target_lufs is None else target_lufs)


def analyze_project(db_path: str, project_id: int) -> dict:
    """Bảng đo độ to các bài (file Mix sẽ dùng) + gain dự kiến khi cân bằng.
    Không ghi gì — chỉ đọc file (có cache theo mtime)."""
    from backend.cover import loudness_balance as lb
    songs = ordered_songs(db_path, project_id)
    target = mix_target(songs) if songs else -16.0
    tracks = {t.path: t for t in core_bridge._load_ignore_minimum(
        [s["path"] for s in songs])} if songs else {}
    stats, rows = [], []
    for s in songs:
        track = tracks.get(s["path"])
        if track is None:
            st = {"integrated": None, "loud": None, "lra": None, "peak_db": None,
                  "duration": 0.0, "error": "Không đọc được file"}
        else:
            st = lb.analyze_cached(s["path"], lambda t=track: load_audio(t))
        stats.append(st)
    plan = lb.plan(stats, target, [s["mix_gain_db"] for s in songs])
    for s, st, pl in zip(songs, stats, plan):
        rows.append({"track_id": s["track_id"], "song_id": s["song_id"],
                     "title": s["title"], "artist": s["artist"],
                     "audio_file": Path(s["path"]).name, "mastered": s["mastered"],
                     "before": st, **pl})
    return {"project_id": project_id, "target_lufs": target,
            "ceiling_db": lb.CEILING_DB, "flag_lu": lb.FLAG_LU,
            "spread_before": _spread([st.get("loud") for st in stats]),
            "spread_after": _spread([r["after_loud"] for r in rows]),
            "songs": rows}


def _load_balanced(songs: list[dict], tracks: dict, sample_rate: int,
                   target_lufs: float, progress_cb) -> tuple[dict, dict]:
    """Đọc mọi bài → đo → tính gain chung → áp gain + limiter → đo lại.
    Trả (audio theo path, khối «loudness» ghi vào mix_info.json)."""
    from backend.cover import loudness_balance as lb
    unique = list(dict.fromkeys(s["path"] for s in songs))
    first = {p: next(s for s in songs if s["path"] == p) for p in unique}
    n = max(len(unique), 1)
    audio: dict[str, np.ndarray] = {}
    stats = []
    for i, path in enumerate(unique):
        s = first[path]
        tag = " (bản master)" if s["mastered"] else ""
        progress_cb(2, f"Đo âm lượng [{i + 1}/{len(unique)}] {s['title']}{tag}",
                    5.0 + 30.0 * i / n)
        audio[path] = _load_stereo(tracks[path], sample_rate)
        stats.append(lb.analyze(audio[path], sample_rate))
    plan = lb.plan(stats, target_lufs, [first[p]["mix_gain_db"] for p in unique])
    rows = []
    for i, (path, st, pl) in enumerate(zip(unique, stats, plan)):
        s = first[path]
        progress_cb(3, f"Cân bằng âm lượng [{i + 1}/{len(unique)}] {s['title']}",
                    35.0 + 15.0 * i / n)
        audio[path], limited = lb.apply(audio[path], sample_rate, pl["gain_db"])
        after = lb.analyze(audio[path], sample_rate)
        rows.append({"track_id": s["track_id"], "song_id": s["song_id"],
                     "title": s["title"], "audio_file": Path(path).name,
                     "before": st, "after": after,
                     "auto_gain_db": pl["auto_gain_db"],
                     "manual_gain_db": pl["manual_gain_db"],
                     "gain_db": pl["gain_db"], "limited_db": limited})
    return audio, {
        "balanced": True, "target_lufs": target_lufs, "ceiling_db": lb.CEILING_DB,
        "spread_before": _spread([st.get("loud") for st in stats]),
        "spread_after": _spread([r["after"].get("loud") for r in rows]),
        "songs": rows,
    }


def _spread(values) -> float | None:
    """Chênh lệch «đoạn to» giữa bài to nhất và nhỏ nhất (LU)."""
    vals = [v for v in values if v is not None]
    return round(max(vals) - min(vals), 2) if vals else None


def build_playlist(songs: list[dict], effective: dict[str, float],
                   target_seconds: float, repeat: bool) -> list[dict]:
    """Danh sách theo thứ tự; repeat=True → lặp lại cả danh sách tới khi đủ."""
    if not songs:
        raise ValueError("Chưa có bài hát nào trong project.")
    playlist = list(songs)
    if repeat and target_seconds > 0:
        total = sum(max(effective.get(s["path"], 0.0), 1.0) for s in songs)
        if total <= 0:
            return playlist
        i = 0
        while total < target_seconds and len(playlist) < 2000:
            s = songs[i % len(songs)]
            playlist.append(s)
            total += max(effective.get(s["path"], 0.0), 1.0)
            i += 1
    return playlist


def render_ordered(playlist: list[dict], audio: dict[str, np.ndarray],
                   output_path: str, crossfade_sec: float, target_sr: int,
                   bit_depth: int, progress_callback=None) -> list[dict]:
    """Ghép theo thứ tự với crossfade equal-power (cùng thuật toán tool cũ) và
    ghi lại src_offset_seconds cho từng đoạn."""
    subtype = BIT_DEPTH_SUBTYPES.get(int(bit_depth))
    if subtype is None:
        raise ValueError(f"bit_depth phải là 24 hoặc 32, nhận được {bit_depth}")
    xf = max(int(crossfade_sec * target_sr), 1)
    estimated = sum(len(audio[s["path"]]) for s in playlist) or 1
    stamps: list[dict] = []

    def stamp(i: int, song: dict, start: int, end: int, src_off: int) -> None:
        stamps.append({
            "index": i + 1, "name": song["title"],
            "source_file": song.get("source_file") or Path(song["path"]).name,
            "audio_file": Path(song["path"]).name,
            "start_seconds": start / target_sr, "end_seconds": end / target_sr,
            "src_offset_seconds": src_off / target_sr,
            "song_id": song["song_id"], "track_id": song["track_id"],
            "artist": song["artist"],
        })

    with sf.SoundFile(output_path, mode="w", samplerate=target_sr,
                      channels=2, subtype=subtype) as handle:
        writer = _StreamWriter(handle, target_sr, None)
        current = audio[playlist[0]["path"]]
        tail_cur, _ = find_transition_points(current, target_sr, crossfade_sec)
        body_start = 0
        track_start = 0
        src_off = 0                       # bài đầu phát từ mẫu 0 của file
        for i in range(len(playlist) - 1):
            nxt = audio[playlist[i + 1]["path"]]
            tail_next, head_next = find_transition_points(nxt, target_sr, crossfade_sec)
            overlap = max(min(xf, tail_cur - body_start, tail_next - head_next), 1)
            writer.write(current[body_start:max(tail_cur - overlap, body_start)])
            fade_start = writer.position
            fade_out, fade_in = make_crossfade_curves(overlap)
            blended = (current[tail_cur - overlap:tail_cur] * fade_out[:, None]
                       + nxt[head_next:head_next + overlap] * fade_in[:, None])
            writer.write(blended)
            stamp(i, playlist[i], track_start, fade_start + overlap, src_off)
            track_start = fade_start
            src_off = head_next           # mẫu head_next của bài sau nằm ở fade_start
            current, tail_cur, body_start = nxt, tail_next, head_next + overlap
            if progress_callback:
                progress_callback(writer.position, estimated)
        writer.write(current[body_start:])
        stamp(len(playlist) - 1, playlist[-1], track_start, writer.position, src_off)
        if progress_callback:
            progress_callback(writer.position, writer.position)
    return stamps


def run_cover_mix_job(mix_id: int, progress_cb, project_id: int, output_dir: str,
                      duration_minutes: float, crossfade_seconds: float,
                      sample_rate: int, bit_depth: int, db_path: str,
                      loudness_balance: bool = True) -> dict:
    """Job mix cho job_manager: progress_cb(step, step_name, percent, message).
    loudness_balance=True (mặc định): đo + cân bằng độ to «đoạn to» giữa các bài
    (cover/loudness_balance.py); False: chỉ chuẩn LUFS tích hợp như cũ."""
    os.makedirs(output_dir, exist_ok=True)
    progress_cb(1, "Đọc danh sách bài", 3.0)
    songs = ordered_songs(db_path, project_id)
    if not songs:
        raise ValueError("Project chưa có bài hát nào.")
    tracks = {t.path: t for t in core_bridge._load_ignore_minimum(
        [s["path"] for s in songs])}
    missing = [Path(s["path"]).name for s in songs if s["path"] not in tracks]
    if missing:
        raise ValueError("Không đọc được file: " + ", ".join(missing))
    if not sample_rate or sample_rate <= 0:
        sample_rate = max([t.sample_rate for t in tracks.values()] + [MIN_SAMPLE_RATE])

    # Có bài dùng bản master → chuẩn mọi bài theo độ to của preset master (thay vì
    # -16 LUFS mặc định) để không đè mất công master và các bài to đều nhau.
    target_lufs = (mastering.mix_target_lufs() if any(s["mastered"] for s in songs)
                   else None)
    audio: dict[str, np.ndarray] = {}
    effective: dict[str, float] = {}
    loudness: dict | None = None
    if loudness_balance:
        audio, loudness = _load_balanced(songs, tracks, sample_rate,
                                         mix_target(songs), progress_cb)
    for i, s in enumerate(songs):
        if s["path"] in effective:
            continue
        if not loudness_balance:
            tag = " (bản master)" if s["mastered"] else ""
            progress_cb(2, f"[{i + 1}/{len(songs)}] {s['title']}{tag}",
                        5.0 + 45.0 * i / max(len(songs), 1))
            audio[s["path"]] = _load_normalized(tracks[s["path"]], sample_rate,
                                                target_lufs)
        tail, head = find_transition_points(audio[s["path"]], sample_rate,
                                            crossfade_seconds)
        effective[s["path"]] = (tail - head) / sample_rate - crossfade_seconds

    progress_cb(3, "Xếp danh sách phát", 52.0)
    # Mixset (2026-10-29): mỗi bài phát đúng 1 lần theo thứ tự tab ③ — độ dài
    # video = tổng độ dài các bài. Không còn ô Duration / «lặp cho đủ thời lượng».
    repeat = False
    playlist = build_playlist(songs, effective, duration_minutes * 60.0, repeat)

    out_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        pct = (written / total) if total else 0.0
        progress_cb(5, "Ghép nhạc", 55.0 + min(pct, 1.0) * 35.0)

    stamps = render_ordered(playlist, audio, out_wav, crossfade_seconds,
                            sample_rate, bit_depth, render_progress)

    progress_cb(6, "Ghi danh sách bài", 92.0)
    info = sf.info(out_wav)
    files = write_metadata(timestamps=stamps, output_wav_path=out_wav,
                           total_duration_sec=info.duration, sample_rate=sample_rate,
                           bit_depth=bit_depth, crossfade_sec=crossfade_seconds,
                           channels=info.channels, title="Cover Mix")
    # metadata_writer chỉ giữ các trường chuẩn → bổ sung trường của tool cover.
    p = Path(files["json"])
    data = json.loads(p.read_text(encoding="utf-8"))
    for entry, st in zip(data.get("tracks") or [], stamps):
        for k in ("src_offset_seconds", "song_id", "track_id", "artist", "source_file",
                  "audio_file"):
            entry[k] = st[k]
    data["cover"] = {"project_id": project_id, "repeat_to_fill": repeat}
    data["loudness"] = loudness or {
        "balanced": False,
        "target_lufs": target_lufs if target_lufs is not None else mix_target(songs)}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    progress_cb(6, "Hoàn tất", 100.0)
    return {"total_duration_seconds": info.duration, "track_count": len(playlist),
            "output_dir": output_dir, "sample_rate": sample_rate, "bit_depth": bit_depth}
