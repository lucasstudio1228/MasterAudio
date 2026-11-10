"""Scan library, đọc metadata và decode audio về float32."""
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

import console  # noqa: F401 - bật UTF-8 output khi import

SUPPORTED_EXTENSIONS = {".wav", ".flac", ".mp3"}
LOSSY_EXTENSIONS = {".mp3"}
MIN_TRACKS = 15
MIN_SAMPLE_RATE = 48000


@dataclass(frozen=True)   # frozen ⇒ hashable, dùng được trong set của T+3 rule
class Track:
    path: str
    name: str            # filename không có extension
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str          # 'WAV', 'FLAC', 'MP3'
    is_lossy: bool
    subtype: str | None  # e.g. 'PCM_24', 'FLOAT', None cho MP3

    @property
    def filename(self) -> str:
        return Path(self.path).name


def _warn(message: str) -> None:
    print(f"  ⚠️  {message}", file=sys.stderr)


def _collect_paths(folder_or_files) -> list[str]:
    """Chấp nhận: folder path, 1 file path, hoặc list of paths."""
    if isinstance(folder_or_files, (str, Path)):
        p = Path(folder_or_files)
        if p.is_dir():
            found = [c for c in sorted(p.iterdir())
                     if c.is_file() and c.suffix.lower() in SUPPORTED_EXTENSIONS]
            return [str(c) for c in found]
        return [str(p)]
    return [str(p) for p in folder_or_files]


def _probe_mp3(path: str) -> tuple[float, int, int]:
    """Dùng ffprobe lấy (duration, sample_rate, channels) cho MP3."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    return duration, int(stream["sample_rate"]), int(stream["channels"])


def load_library(folder_or_files) -> list[Track]:
    """Scan library, skip file lỗi (chỉ WARN), raise nếu còn < 15 track hợp lệ."""
    paths = _collect_paths(folder_or_files)
    tracks: list[Track] = []
    skipped: list[str] = []
    has_lossy = False

    for path in paths:
        p = Path(path)
        ext = p.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        try:
            if ext == ".mp3":
                duration, sample_rate, channels = _probe_mp3(str(p))
                fmt, subtype = "MP3", None
            else:
                info = sf.info(str(p))
                duration = float(info.duration)
                sample_rate = int(info.samplerate)
                channels = int(info.channels)
                fmt, subtype = info.format, info.subtype
            if duration <= 0 or sample_rate <= 0 or channels <= 0:
                raise ValueError("metadata không hợp lệ")
        except Exception as exc:                      # noqa: BLE001 - skip mọi file lỗi
            skipped.append(f"{p.name}: {exc}")
            continue

        is_lossy = ext in LOSSY_EXTENSIONS
        has_lossy = has_lossy or is_lossy
        tracks.append(Track(
            path=str(p), name=p.stem, duration_seconds=duration,
            sample_rate=sample_rate, channels=channels,
            format=fmt, is_lossy=is_lossy, subtype=subtype,
        ))

    for entry in skipped:
        _warn(f"Bỏ qua file lỗi – {entry}")
    if has_lossy:
        _warn("Library có file MP3 (lossy). Output vẫn là WAV lossless "
              "nhưng chất lượng nguồn đã mất — nên dùng WAV/FLAC.")

    if len(tracks) < MIN_TRACKS:
        raise ValueError(
            f"Cần tối thiểu {MIN_TRACKS} track hợp lệ để đảm bảo T+3 shuffle, "
            f"chỉ tìm thấy {len(tracks)} (đã bỏ qua {len(skipped)} file lỗi)."
        )
    return tracks


def determine_target_sample_rate(tracks: list[Track]) -> int:
    """Giữ chất lượng cao nhất trong library, sàn 48000 Hz."""
    return max([t.sample_rate for t in tracks] + [MIN_SAMPLE_RATE])


def _decode_mp3(path: str) -> tuple[np.ndarray, int]:
    """Decode MP3 → float32 qua ffmpeg pipe (không ghi file tạm)."""
    _, sample_rate, channels = _probe_mp3(path)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-f", "f32le", "-acodec", "pcm_f32le", "-"],
        capture_output=True, check=True,
    )
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels)
    return np.ascontiguousarray(audio), sample_rate


def load_audio(track: Track) -> tuple[np.ndarray, int]:
    """Đọc audio về float32, shape (n,) mono hoặc (n, channels)."""
    if track.is_lossy:
        return _decode_mp3(track.path)
    audio, sample_rate = sf.read(track.path, dtype="float32", always_2d=False)
    return audio, sample_rate
