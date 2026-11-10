"""
core_bridge.py – Adapter giữa backend và existing core modules.
Tất cả gọi mixer đi qua đây để cô lập việc quản lý import path.
KHÔNG sửa core modules — chỉ wrap chúng.
"""
import os
import sys
from pathlib import Path

# Add meditation_mixer root vào path để import được core modules ở parent dir.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from analyzer import find_transition_points  # noqa: E402
from api_config import APIConfig  # noqa: E402
from audio_loader import (SUPPORTED_EXTENSIONS, Track, load_library)  # noqa: E402
from crossfade_engine import render_mix  # noqa: E402
from metadata_writer import write_metadata  # noqa: E402
from playlist_generator import generate_playlist  # noqa: E402
from track_namer import generate_names  # noqa: E402

# Sàn sample rate: không upsample giả xuống dưới mức này, cũng không ép lên cao
# hơn nguồn. 44100 = sr gốc của stem Demucs.
MIN_SAMPLE_RATE = 44100


def scan_folder(folder_path: str, recursive: bool = False) -> tuple[list[Track], list[str]]:
    """
    Scan 1 folder → (tracks hợp lệ, danh sách filename lỗi).
    Không dùng load_library trực tiếp vì nó raise khi < 15 track;
    ở tầng scan ta muốn lấy tất cả những gì đọc được rồi để router quyết định.
    """
    folder = Path(folder_path)
    pattern = "**/*" if recursive else "*"
    candidates = [p for p in folder.glob(pattern)
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]

    tracks: list[Track] = []
    error_files: list[str] = []
    for path in sorted(candidates):
        loaded = _probe_single(str(path))
        if loaded is None:
            error_files.append(path.name)
        else:
            tracks.append(loaded)
    return tracks, error_files


def probe_file(file_path: str) -> Track | None:
    """Đọc metadata 1 file đơn lẻ. None nếu lỗi."""
    return _probe_single(file_path)


def _probe_single(file_path: str) -> Track | None:
    """Tận dụng load_library (đọc đúng metadata) trên đúng 1 file."""
    try:
        tracks = _load_ignore_minimum([file_path])
        return tracks[0] if tracks else None
    except Exception:
        return None


def _load_ignore_minimum(paths: list[str]) -> list[Track]:
    """
    load_library raise ValueError khi < 15 track. Ở tầng scan/probe ta chỉ cần
    metadata nên tạm nâng ngưỡng lên 0 quanh lời gọi (không sửa file core).
    """
    import audio_loader
    original = audio_loader.MIN_TRACKS
    audio_loader.MIN_TRACKS = 0
    try:
        return load_library(paths)
    finally:
        audio_loader.MIN_TRACKS = original


def resolve_target_sample_rate(track_sample_rates: list[int]) -> int:
    """
    Auto sample rate = max native trong library (sàn 44100), KHÔNG upsample giả.
    Dùng khi người dùng chọn 'Auto (gốc cao nhất)'.
    """
    return max(list(track_sample_rates) + [MIN_SAMPLE_RATE])


def get_api_config_from_db(db_path: str) -> APIConfig:
    """
    Đọc AppSettings từ SQLite trực tiếp (không qua FastAPI session)
    vì hàm này chạy trong ThreadPoolExecutor thread.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT api_key, api_base_url, api_model "
                "FROM appsettings WHERE id=1"
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # Bảng chưa tồn tại — fallback về env vars.
        return APIConfig()

    if not row:
        # Fallback về env vars nếu chưa có settings trong DB.
        return APIConfig()

    api_key, base_url, model = row
    cfg = APIConfig()
    # DB override env, nhưng env là fallback nếu DB trống.
    cfg.api_key = api_key or cfg.api_key
    cfg.base_url = base_url or cfg.base_url
    cfg.model = model or cfg.model
    return cfg


def _load_track_audio_with_stems(
    filepath: str,
    target_sr: int,
    db_path: str,
) -> np.ndarray:
    """
    Load audio cho 1 track:
      Path A (stems ready): mix 4 stems với volume settings → đã LUFS normalized
                            + đã khử noise/bleed thông minh theo volume
      Path B (fallback):    load file gốc → apply LUFS normalization

    Không bao giờ raise → luôn trả về audio array hợp lệ.
    """
    import sqlite3
    import librosa
    from backend.loudness_service import normalize_audio

    # ── Kiểm tra stems có sẵn không ──
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT t.id, t.project_id,
                   ts.status,
                   ts.vol_other, ts.vol_bass,
                   ts.vol_drums, ts.vol_vocals,
                   ts.den_other, ts.den_bass,
                   ts.den_drums, ts.den_vocals
            FROM track t
            LEFT JOIN trackstem ts ON ts.track_id = t.id
            WHERE t.filepath = ?
        """, (filepath,)).fetchone()
    finally:
        conn.close()

    if row and row[2] == "completed":
        (track_id, project_id, _, vo, vb, vd, vv,
         do, db_, dd, dv) = row
        from backend.stem_service import load_mixed_stems
        mixed = load_mixed_stems(
            project_id=project_id,
            track_id=track_id,
            vol_other=vo,  vol_bass=vb,
            vol_drums=vd,  vol_vocals=vv,
            den_other=do,  den_bass=db_,
            den_drums=dd,  den_vocals=dv,
            target_sr=target_sr,
        )
        if mixed is not None:
            # Stems đã được LUFS normalized + smart-clean trong stem_service
            return mixed
        # Nếu load_mixed_stems trả None (file bị xóa) → fallback

    # ── Fallback: load file gốc + LUFS normalize ──
    audio, sr = sf.read(filepath, dtype="float32")
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    if sr != target_sr:
        audio = librosa.resample(
            audio.T, orig_sr=sr, target_sr=target_sr,
            res_type="kaiser_best"
        ).T
    # Normalize về -16 LUFS để đồng bộ với stems
    audio = normalize_audio(audio, target_sr)
    return audio


def _get_track_stem_status(filepath: str, db_path: str) -> str:
    """Trả về stem status của track ('completed', 'pending', ...)."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT ts.status FROM track t
            LEFT JOIN trackstem ts ON ts.track_id = t.id
            WHERE t.filepath = ?
        """, (filepath,)).fetchone()
        return row[0] if row and row[0] else "not_started"
    finally:
        conn.close()


def run_mix_job(
    mix_id: int,
    progress_cb,           # callback(step, step_name, percent, message="")
    track_paths: list[str],
    output_dir: str,
    duration_minutes: float,
    crossfade_seconds: float,
    sample_rate: int,
    bit_depth: int,
    db_path: str,          # đọc APIConfig từ DB trong worker thread
) -> dict:
    """
    Chạy full mix pipeline trong ThreadPoolExecutor worker.
    Return metadata dict để router cập nhật Mix record.

    sample_rate <= 0 → auto: max native của library (sàn 44100), không upsample giả.
    bit_depth mặc định 32 (float, không nén) để giữ headroom tối đa.
    """
    api_config = get_api_config_from_db(db_path)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1/6 – Load & validate tracks
    progress_cb(1, "Loading tracks", 5.0)
    tracks = load_library(track_paths)

    # Auto sample rate nếu chưa chỉ định (hoặc <= 0).
    if not sample_rate or sample_rate <= 0:
        sample_rate = resolve_target_sample_rate([t.sample_rate for t in tracks])

    # Step 2/6 – Pre-load audio (stems nếu có, fallback nếu không). Nạp TRƯỚC khi
    # xếp playlist để đo độ dài THỰC mỗi bài đóng góp vào mix (render bỏ dead-air
    # đầu/đuôi + overlap crossfade) — ước lượng theo duration file làm mix hụt
    # so với yêu cầu (vd đặt 60' ra 58'35").
    progress_cb(2, "Loading stems", 8.0)
    preloaded: dict[str, np.ndarray] = {}
    effective: dict[str, float] = {}
    for i, track in enumerate(tracks):
        if track.path in preloaded:
            continue
        pct = 8.0 + (i / max(len(tracks), 1)) * 42.0
        stem_status = _get_track_stem_status(track.path, db_path)
        msg = (f"[{i+1}/{len(tracks)}] "
               f"{'[STEMS] ' if stem_status == 'completed' else '[ORIGINAL] '}"
               f"{Path(track.path).name}")
        progress_cb(2, msg, pct)
        audio = _load_track_audio_with_stems(track.path, sample_rate, db_path)
        preloaded[track.path] = audio
        tail, head = find_transition_points(audio, sample_rate, crossfade_seconds)
        effective[track.path] = (tail - head) / sample_rate - crossfade_seconds

    # Step 3/6 – Generate playlist (shuffle theo vòng + T+5)
    progress_cb(3, "Generating playlist", 52.0)
    target_seconds = duration_minutes * 60
    playlist = generate_playlist(tracks, target_seconds, crossfade_seconds,
                                 effective=effective)

    # Step 4/6 – Name tracks
    progress_cb(4, "Naming tracks", 55.0)
    names = generate_names(len(playlist), api_config)

    # Step 5/6 – Render mix (dùng preloaded audio)
    output_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        # render_mix gọi (written_samples, total_samples). Map → overall 58-88%.
        pct = (written / total) if total else 0.0
        progress_cb(5, "Rendering audio", 58.0 + pct * 30.0)

    # Duration là SOFT target: playlist đã dừng thêm bài ngay khi vượt target,
    # nên KHÔNG cắt cứng — để bài nhạc cuối cùng phát trọn vẹn (hard_cut=False).
    timestamps = render_mix(
        playlist=playlist,
        output_path=output_wav,
        crossfade_sec=crossfade_seconds,
        target_sr=sample_rate,
        bit_depth=bit_depth,
        progress_callback=render_progress,
        preloaded_audio=preloaded,    # ← key mới
    )

    for i, ts in enumerate(timestamps):
        ts["name"] = names[i] if i < len(names) else f"Track {i + 1}"

    # Step 6/6 – Write metadata
    progress_cb(6, "Writing metadata", 92.0)
    info = sf.info(output_wav)
    write_metadata(
        timestamps=timestamps,
        output_wav_path=output_wav,
        total_duration_sec=info.duration,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        crossfade_sec=crossfade_seconds,
        channels=info.channels,
    )

    # Done
    progress_cb(6, "Completed", 100.0)

    return {
        "total_duration_seconds": info.duration,
        "track_count": len(playlist),
        "output_dir": output_dir,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }


def run_patch_mix_job(
    mix_id: int,
    progress_cb,           # callback(step, step_name, percent, message="")
    sequence: list[str],   # đường dẫn file theo ĐÚNG thứ tự bản mix cũ
    names: list[str],      # tên từng đoạn của bản mix cũ (giữ nguyên)
    output_dir: str,
    crossfade_seconds: float,
    sample_rate: int,
    bit_depth: int,
    db_path: str,
    target_seconds: float,
) -> dict:
    """
    "Vá mix": render lại theo ĐÚNG playlist + tên của bản mix cũ, chỉ khác ở các
    bài đã được thay (sequence đã trỏ sang file mới). KHÔNG shuffle lại, KHÔNG
    đặt tên lại ⇒ mọi đoạn trước lần xuất hiện đầu tiên của bài thay giống hệt
    bản cũ; đoạn sau chỉ dịch đi đúng phần chênh độ dài.
    Cắt cứng tại target_seconds (= độ dài bản cũ, có fade-out) để video đã dựng
    vẫn khớp — chỉ cần thay track âm thanh trong final.mp4.
    """
    os.makedirs(output_dir, exist_ok=True)
    progress_cb(1, "Loading tracks", 5.0)
    seq = [str(Path(p)) for p in sequence]
    uniq = list(dict.fromkeys(seq))
    lib = {t.path: t for t in _load_ignore_minimum(uniq)}
    missing = [p for p in uniq if p not in lib]
    if missing:
        raise ValueError("Không đọc được: " + ", ".join(Path(p).name for p in missing))

    progress_cb(2, "Loading stems", 8.0)
    preloaded: dict[str, np.ndarray] = {}
    for i, p in enumerate(uniq):
        progress_cb(2, f"[{i+1}/{len(uniq)}] {Path(p).name}",
                    8.0 + (i / max(len(uniq), 1)) * 42.0)
        preloaded[p] = _load_track_audio_with_stems(p, sample_rate, db_path)

    output_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        pct = (written / total) if total else 0.0
        progress_cb(5, "Rendering audio", 55.0 + min(pct, 1.0) * 33.0)

    timestamps = render_mix(
        playlist=[lib[p] for p in seq],
        output_path=output_wav,
        crossfade_sec=crossfade_seconds,
        target_sr=sample_rate,
        bit_depth=bit_depth,
        target_seconds=target_seconds,
        hard_cut=True,
        progress_callback=render_progress,
        preloaded_audio=preloaded,
    )
    for i, ts in enumerate(timestamps):
        ts["name"] = names[i] if i < len(names) and names[i] else f"Track {i + 1}"

    progress_cb(6, "Writing metadata", 92.0)
    info = sf.info(output_wav)
    write_metadata(
        timestamps=timestamps, output_wav_path=output_wav,
        total_duration_sec=info.duration, sample_rate=sample_rate,
        bit_depth=bit_depth, crossfade_sec=crossfade_seconds,
        channels=info.channels,
    )
    progress_cb(6, "Completed", 100.0)
    return {
        "total_duration_seconds": info.duration,
        "track_count": len(timestamps),
        "output_dir": output_dir,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }
