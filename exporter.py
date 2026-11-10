"""Kiểm tra dung lượng đĩa và format thông tin file output."""
import shutil
from pathlib import Path

DISK_BUFFER_RATIO = 1.15
WAV_HEADER_BYTES = 44


def estimate_output_bytes(target_sec: float, sample_rate: int,
                          channels: int = 2, bit_depth: int = 24) -> int:
    bytes_per_sample = bit_depth // 8
    return int(target_sec * sample_rate * channels * bytes_per_sample) + WAV_HEADER_BYTES


def check_disk_space(output_path: str, target_sec: float,
                     sample_rate: int, channels: int = 2, bit_depth: int = 24):
    bytes_per_sample = bit_depth // 8
    estimated = target_sec * sample_rate * channels * bytes_per_sample
    free = shutil.disk_usage(Path(output_path).parent).free
    if free < estimated * DISK_BUFFER_RATIO:   # 15% buffer
        raise RuntimeError(
            f"Disk space không đủ: cần ~{estimated / 1e9:.2f} GB, "
            f"còn {free / 1e9:.2f} GB"
        )
    return estimated


def format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024


def format_duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60} min {total % 60:02d} sec"


def wav_size_on_disk(path: str) -> str:
    return format_size(Path(path).stat().st_size)
