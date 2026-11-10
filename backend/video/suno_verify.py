"""
video/suno_verify.py — Xác thực KỸ THUẬT file WAV tải từ Suno (STEP 0, mục H).

Tách bạch với thẩm định NỘI DUNG (âm nhạc hay/dở): ở đây chỉ kiểm tra file có
đúng là WAV thật hay không, có audio stream, đủ dài, không im lặng, và tính
SHA-256 để chống trùng. KHÔNG có analyzer nội dung ⇒ content_qc_status luôn để
'not_verified' ở tầng gọi — KHÔNG bịa "đã nghe và chọn bài hay nhất".

Không tiêu credit Suno → an toàn để unit-test. Chỉ shell ra ffprobe/ffmpeg
(đã là phụ thuộc sẵn có của project, xem backend/video/assembler.py).

QUY TẮC CỨNG (từ yêu cầu người dùng):
  • Không thay WAV bằng MP3. Không đổi đuôi MP3 thành WAV.
  • File .wav mà thực chất là MP3 (đổi đuôi) → verify PHẢI trượt (invalid).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

# Container hợp lệ cho "WAV thật": ffprobe format_name của WAV thường là
# 'wav' (đôi khi kèm alias). MP3 đổi đuôi sẽ ra 'mp3' → bị loại.
_WAV_FORMAT_TOKENS = {"wav", "wave", "waveform"}
# Codec audio hợp lệ trong WAV: PCM (không nén) hoặc float PCM. Nếu codec là
# 'mp3' nghĩa là file MP3 nhét trong .wav → loại.
_PCM_CODEC_PREFIXES = ("pcm_", "adpcm_")


@dataclass
class VerifyResult:
    """Kết quả xác thực 1 file. valid=True ⇔ WAV thật, đủ dài, không im lặng."""
    path: str
    valid: bool = False
    reason: str = ""                      # lý do trượt (nếu invalid)
    sha256: Optional[str] = None
    format_name: Optional[str] = None
    codec_name: Optional[str] = None
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    is_silent: Optional[bool] = None
    is_duplicate: bool = False            # trùng SHA-256 với file đã biết

    def as_dict(self) -> dict:
        return asdict(self)


def _ffprobe_bin() -> Optional[str]:
    return shutil.which("ffprobe")


def _ffmpeg_bin() -> Optional[str]:
    return shutil.which("ffmpeg")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """SHA-256 của toàn bộ nội dung file (để chống trùng)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def probe_audio(path: str | Path) -> dict:
    """
    Đọc metadata bằng ffprobe (JSON). Trả {format_name, codec_name, duration,
    sample_rate, channels}. Raise RuntimeError nếu ffprobe thiếu/lỗi.
    """
    ffprobe = _ffprobe_bin()
    if not ffprobe:
        raise RuntimeError(
            "Không tìm thấy ffprobe trên PATH — cần để xác thực WAV. "
            "Cài ffmpeg và đảm bảo ffprobe nằm trong PATH.")
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=format_name,duration:stream=codec_type,codec_name,"
         "sample_rate,channels",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout or "{}")
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out = {
        "format_name": fmt.get("format_name"),
        "duration": None,
        "codec_name": None,
        "sample_rate": None,
        "channels": None,
        "has_audio": audio is not None,
    }
    try:
        out["duration"] = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        out["duration"] = None
    if audio:
        out["codec_name"] = audio.get("codec_name")
        try:
            out["sample_rate"] = int(audio.get("sample_rate")) if audio.get("sample_rate") else None
        except (TypeError, ValueError):
            out["sample_rate"] = None
        try:
            out["channels"] = int(audio.get("channels")) if audio.get("channels") else None
        except (TypeError, ValueError):
            out["channels"] = None
    return out


def _is_wav_container(format_name: Optional[str]) -> bool:
    if not format_name:
        return False
    tokens = {t.strip().lower() for t in format_name.split(",")}
    return bool(tokens & _WAV_FORMAT_TOKENS)


def _is_pcm_codec(codec_name: Optional[str]) -> bool:
    if not codec_name:
        return False
    return codec_name.lower().startswith(_PCM_CODEC_PREFIXES)


def detect_silence(path: str | Path, threshold_db: float = -60.0) -> Optional[bool]:
    """
    True nếu file gần như im lặng (mean_volume dưới ngưỡng), False nếu có tín
    hiệu, None nếu không đo được (thiếu ffmpeg). Dùng ffmpeg volumedetect.
    """
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # volumedetect ghi ra stderr: "mean_volume: -23.4 dB", "max_volume: -3.1 dB"
    text = r.stderr or ""
    mean_db = None
    max_db = None
    for line in text.splitlines():
        low = line.lower()
        if "mean_volume:" in low:
            try:
                mean_db = float(low.split("mean_volume:")[1].split("db")[0].strip())
            except (ValueError, IndexError):
                pass
        elif "max_volume:" in low:
            try:
                max_db = float(low.split("max_volume:")[1].split("db")[0].strip())
            except (ValueError, IndexError):
                pass
    if mean_db is None and max_db is None:
        return None
    # Im lặng nếu CẢ mean lẫn max đều dưới ngưỡng (max rất thấp = không có đỉnh).
    ref = max_db if max_db is not None else mean_db
    return ref <= threshold_db


def verify_wav(
    path: str | Path,
    minimum_duration_seconds: float = 120.0,
    known_hashes: Optional[Iterable[str]] = None,
    check_silence: bool = True,
) -> VerifyResult:
    """
    Xác thực 1 file WAV tải từ Suno. valid=True chỉ khi:
      • file tồn tại và đọc được;
      • là WAV container thật (KHÔNG phải MP3 đổi đuôi);
      • codec audio là PCM (không nén);
      • có audio stream;
      • duration ≥ minimum_duration_seconds;
      • không im lặng (nếu đo được);
      • không trùng SHA-256 với known_hashes.
    Luôn tính sha256 (kể cả khi invalid) để tầng gọi lưu manifest.
    """
    p = Path(path)
    res = VerifyResult(path=str(p))

    if not p.exists() or not p.is_file():
        res.reason = "Không thấy file WAV (download có thể chưa hoàn tất)."
        return res
    if p.stat().st_size == 0:
        res.reason = "File WAV rỗng (0 byte)."
        return res

    # SHA-256 trước — cần cho cả chống trùng lẫn manifest.
    try:
        res.sha256 = sha256_file(p)
    except Exception as e:
        res.reason = f"Không đọc được file để tính SHA-256: {e}"
        return res

    known = {h.lower() for h in (known_hashes or []) if h}
    if res.sha256.lower() in known:
        res.is_duplicate = True
        res.reason = "File trùng nội dung (SHA-256) với bài đã tải."
        return res

    # ffprobe metadata.
    try:
        meta = probe_audio(p)
    except Exception as e:
        res.reason = str(e)
        return res

    res.format_name = meta["format_name"]
    res.codec_name = meta["codec_name"]
    res.duration_seconds = meta["duration"]
    res.sample_rate = meta["sample_rate"]
    res.channels = meta["channels"]

    if not meta["has_audio"]:
        res.reason = "File không có audio stream."
        return res
    if not _is_wav_container(meta["format_name"]):
        res.reason = (
            f"Không phải WAV container thật (format={meta['format_name']!r}). "
            f"Có thể là MP3/định dạng khác đổi đuôi .wav — bị từ chối.")
        return res
    if not _is_pcm_codec(meta["codec_name"]):
        res.reason = (
            f"Codec audio không phải PCM ({meta['codec_name']!r}). "
            f"WAV thật từ Suno phải là PCM không nén.")
        return res
    if meta["duration"] is None:
        res.reason = "Không đọc được duration."
        return res
    if meta["duration"] < minimum_duration_seconds:
        res.reason = (
            f"Quá ngắn: {meta['duration']:.1f}s < tối thiểu "
            f"{minimum_duration_seconds:.0f}s.")
        return res

    if check_silence:
        silent = detect_silence(p)
        res.is_silent = silent
        if silent is True:
            res.reason = "File gần như im lặng (không có tín hiệu âm thanh)."
            return res

    res.valid = True
    res.reason = "OK"
    return res
