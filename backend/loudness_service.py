"""
loudness_service.py
Phân tích và normalize LUFS theo ITU-R BS.1770-4.

Nguyên tắc:
  - Đo LUFS trên track gốc (mixed stereo)
  - Tính linear gain để đạt target LUFS
  - Apply CÙNG gain cho tất cả 4 stems
  → Giữ nguyên balance tự nhiên giữa stems
  → Không có artifact hay méo tiếng
"""
import numpy as np
import soundfile as sf
import pyloudnorm as pyln
from pathlib import Path
from typing import Optional

TARGET_LUFS   = -16.0   # Phù hợp nhạc thiền/ambient
SILENCE_LUFS  = -70.0   # Threshold coi là im lặng hoàn toàn
MAX_GAIN_DB   =  12.0   # Giới hạn boost tối đa để tránh noise pump
MIN_GAIN_DB   = -24.0   # Giới hạn cut tối đa


def measure_lufs(audio: np.ndarray, sr: int) -> Optional[float]:
    """
    Đo integrated loudness (LUFS) của audio array.
    Return None nếu audio quá ngắn hoặc quá im lặng.
    """
    try:
        meter    = pyln.Meter(sr)   # ITU-R BS.1770-4
        loudness = meter.integrated_loudness(audio)
        if loudness < SILENCE_LUFS or np.isinf(loudness) or np.isnan(loudness):
            return None
        return float(loudness)
    except Exception:
        return None


def compute_lufs_gain(measured_lufs: float,
                      target_lufs: float = TARGET_LUFS) -> float:
    """
    Tính linear gain để đưa track từ measured_lufs → target_lufs.
    Clamp trong khoảng [MIN_GAIN_DB, MAX_GAIN_DB] để tránh artifact.
    """
    gain_db     = target_lufs - measured_lufs
    gain_db     = max(MIN_GAIN_DB, min(MAX_GAIN_DB, gain_db))
    linear_gain = 10.0 ** (gain_db / 20.0)
    return linear_gain


def apply_gain_safe(audio: np.ndarray, gain: float) -> np.ndarray:
    """
    Apply gain với soft limiter sau đó.
    KHÔNG hard-clip. Nếu sau khi gain peak > 0.99:
      → Scale toàn bộ xuống (giữ dynamic shape, không méo)
    """
    result = audio * gain
    peak   = np.max(np.abs(result))
    if peak > 0.99:
        result = result * (0.99 / peak)
    return result


def normalize_stems_to_target(
    stems: dict[str, np.ndarray],   # {"other": ndarray, "bass":..., ...}
    original_audio: np.ndarray,     # Track gốc để đo LUFS
    sr: int,
    target_lufs: float = TARGET_LUFS,
) -> tuple[dict[str, np.ndarray], float]:
    """
    Normalize tất cả stems bằng cách đo LUFS trên track gốc.

    Flow:
      1. Đo LUFS(original_audio)
      2. gain = compute_lufs_gain(measured, target)
      3. Với mỗi stem: stem_normalized = apply_gain_safe(stem, gain)

    Return: (normalized_stems_dict, gain_applied)
    """
    measured = measure_lufs(original_audio, sr)

    if measured is None:
        # Track quá im lặng — không normalize, giữ nguyên
        return stems, 1.0

    gain = compute_lufs_gain(measured, target_lufs)
    normalized = {name: apply_gain_safe(arr, gain)
                  for name, arr in stems.items()}

    return normalized, gain


def normalize_audio(audio: np.ndarray, sr: int,
                    target_lufs: float = TARGET_LUFS) -> np.ndarray:
    """
    Normalize 1 audio array về target LUFS.
    Dùng khi load track gốc (fallback, không có stems).
    """
    measured = measure_lufs(audio, sr)
    if measured is None:
        return audio
    gain = compute_lufs_gain(measured, target_lufs)
    return apply_gain_safe(audio, gain)
