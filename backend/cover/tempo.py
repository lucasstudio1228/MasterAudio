"""Xuất bản nhạc gốc đã đổi tempo nhưng giữ nguyên cao độ bằng ffmpeg."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

from backend.video import config as vconfig

from . import nct


TEMPO_STEM = "da_chinh_tempo"
MIN_FACTOR = 0.2                 # chậm tối đa 5 lần
MAX_FACTOR = 5.0                 # nhanh tối đa 5 lần


class TempoError(RuntimeError):
    pass


def normalize_factor(value: float) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError) as exc:
        raise TempoError("Tempo không hợp lệ.") from exc
    if not math.isfinite(factor) or not MIN_FACTOR <= factor <= MAX_FACTOR:
        raise TempoError(f"Tempo phải nằm trong khoảng {MIN_FACTOR}× đến {MAX_FACTOR}×.")
    return round(factor, 3)


def atempo_filter(value: float) -> str:
    """Tách factor thành các stage 0.5–2.0 để chạy được trên mọi ffmpeg hỗ trợ atempo."""
    factor = normalize_factor(value)
    stages: list[float] = []
    while factor < 0.5 - 1e-9:
        stages.append(0.5)
        factor /= 0.5
    while factor > 2.0 + 1e-9:
        stages.append(2.0)
        factor /= 2.0
    if not stages or abs(factor - 1.0) > 1e-9:
        stages.append(factor)
    return ",".join(f"atempo={x:.6g}" for x in stages)


def tempo_dir(project_id: int, project_name: str | None) -> Path:
    return vconfig.project_dir(project_id, project_name) / TEMPO_STEM


def export_file(source: Path, source_id: int, project_id: int, project_name: str | None,
                title: str, artist: str, query: str, factor: float) -> Path:
    """Xuất WAV PCM16 nguyên tử. File gốc không bao giờ bị sửa/ghi đè."""
    factor = normalize_factor(factor)
    source = Path(source)
    if not source.is_file():
        raise TempoError("Không tìm thấy file nhạc gốc để chỉnh tempo.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TempoError("Không tìm thấy ffmpeg — hãy cài ffmpeg rồi mở lại Tool.")

    folder = tempo_dir(project_id, project_name)
    folder.mkdir(parents=True, exist_ok=True)
    base = nct.safe_name(" - ".join(x for x in (title, artist) if x) or query)
    # ID tránh hai dòng cùng tên/ca sĩ ghi đè file tempo của nhau.
    dest = folder / f"{base} - tempo {factor:g}x (#{source_id}).wav"
    temporary = dest.with_name(f".{dest.stem}.tmp.wav")
    temporary.unlink(missing_ok=True)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-map", "0:a:0", "-vn",
        "-filter:a", atempo_filter(factor),
        "-c:a", "pcm_s16le", str(temporary),
    ]
    kwargs = {"capture_output": True, "text": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(cmd, **kwargs)
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 44:
            detail = (result.stderr or result.stdout or "ffmpeg không tạo được file").strip()
            raise TempoError(f"Xuất file chỉnh tempo thất bại: {detail[-500:]}")
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    return dest.resolve()
