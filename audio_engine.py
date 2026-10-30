"""Bộ xử lý audio độc lập: đổi tempo giữ cao độ và master loudnorm 2-pass."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".aiff", ".aif"}
PRESETS = {
    "natural": {"label": "Natural · -14 LUFS (YouTube)", "lufs": -14.0, "ratio": 2.0},
    "dynamic": {"label": "Dynamic · -16 LUFS", "lufs": -16.0, "ratio": 1.5},
    "loud": {"label": "Loud · -10 LUFS", "lufs": -10.0, "ratio": 3.0},
}
Log = Callable[[str], None]


class AudioToolError(RuntimeError):
    pass


def _emit(log: Log, message: str) -> None:
    """Log Unicode cho GUI; tự hạ ký tự nếu console Windows dùng CP1252."""
    try:
        log(message)
    except UnicodeEncodeError:
        log(message.encode("ascii", "replace").decode("ascii"))


def _run_kwargs() -> dict:
    kwargs: dict = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def find_ffmpeg(tool_dir: Path) -> str:
    """Ưu tiên ffmpeg đi kèm tool, sau đó PATH của Windows."""
    bundled = tool_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if not found:
        raise AudioToolError(
            "Không tìm thấy ffmpeg. Hãy cài ffmpeg vào PATH hoặc đặt ffmpeg.exe tại "
            f"{bundled}."
        )
    return found


def ffmpeg_version(exe: str) -> str:
    result = subprocess.run([exe, "-version"], timeout=20, **_run_kwargs())
    if result.returncode != 0:
        raise AudioToolError((result.stderr or "ffmpeg không chạy được").strip()[-500:])
    return (result.stdout or "").splitlines()[0]


def normalize_factor(value: float) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioToolError("Tempo không hợp lệ.") from exc
    if not math.isfinite(factor) or not 0.2 <= factor <= 5.0:
        raise AudioToolError("Tempo phải nằm trong khoảng 0.2× đến 5.0×.")
    return round(factor, 3)


def atempo_filter(value: float) -> str:
    """Tách factor thành các stage 0.5–2.0 để tương thích mọi bản ffmpeg."""
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
    return ",".join(f"atempo={v:.6g}" for v in stages)


def _safe_stem(stem: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return cleaned[:140] or "audio"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise AudioToolError(f"Có quá nhiều file trùng tên: {path.name}")


def _run(exe: str, args: list[str], cancel: threading.Event | None = None,
         timeout: float = 3600) -> subprocess.CompletedProcess:
    if cancel and cancel.is_set():
        raise AudioToolError("Đã dừng theo yêu cầu.")
    process = subprocess.Popen(
        [exe, "-hide_banner", "-nostdin", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        while process.poll() is None:
            if cancel and cancel.wait(0.2):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise AudioToolError("Đã dừng theo yêu cầu.")
            if not cancel:
                process.wait(timeout=timeout)
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()


def export_tempo(source: Path, output_root: Path, factor: float, exe: str,
                 log: Log = print, cancel: threading.Event | None = None) -> Path:
    source = Path(source)
    if not source.is_file():
        raise AudioToolError(f"Không tìm thấy file: {source}")
    factor = normalize_factor(factor)
    folder = Path(output_root) / "da_chinh_tempo"
    folder.mkdir(parents=True, exist_ok=True)
    dest = _unique(folder / f"{_safe_stem(source.stem)} - tempo {factor:g}x.wav")
    temp = dest.with_name(f".{dest.stem}.tmp.wav")
    temp.unlink(missing_ok=True)
    _emit(log, f"Tempo {factor:g}×: {source.name}")
    try:
        result = _run(exe, ["-y", "-loglevel", "error", "-i", str(source),
                            "-map", "0:a:0", "-vn", "-filter:a", atempo_filter(factor),
                            "-c:a", "pcm_s24le", "-ar", "48000", str(temp)], cancel)
        if result.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 44:
            raise AudioToolError(f"Xuất tempo lỗi: {(result.stderr or result.stdout)[-700:]}")
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    _emit(log, f"✓ Đã tạo: {dest}")
    return dest


def _parse_loudnorm(stderr: str) -> dict:
    blocks = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', stderr or "", re.S)
    if not blocks:
        raise AudioToolError("ffmpeg không trả về số đo loudness.")
    data = json.loads(blocks[-1])
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if any(not math.isfinite(float(data[k])) for k in required):
        raise AudioToolError("File im lặng hoặc không đo được loudness.")
    return data


def _ln_args(preset: dict, measured: dict | None = None, margin: float = 0.3) -> str:
    base = f"I={preset['lufs']:g}:TP={-1.0 - margin:g}:LRA=11:print_format=json"
    if measured:
        base += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                 f":measured_LRA={measured['input_lra']}"
                 f":measured_thresh={measured['input_thresh']}"
                 f":offset={measured['target_offset']}:linear=true")
    return base


def _master_filter(preset: dict, loudnorm: str) -> str:
    return (
        "[0:a]aformat=channel_layouts=stereo,highpass=f=30,"
        "equalizer=f=300:t=q:w=1:g=-1.5,"
        "equalizer=f=3500:t=q:w=1.5:g=-1.5,highshelf=f=10000:g=-1.5,"
        "acrossover=split=120[lo][hi];"
        "[lo]pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[lom];"
        f"[lom][hi]amix=inputs=2:normalize=0,acompressor=threshold=-18dB:ratio={preset['ratio']:g}:"
        f"attack=20:release=200,loudnorm={loudnorm}[out]"
    )


def _measure(exe: str, source: Path, preset: dict, cancel: threading.Event | None) -> dict:
    result = _run(exe, ["-i", str(source), "-map", "0:a:0", "-af",
                        f"loudnorm=I={preset['lufs']:g}:TP=-1:LRA=11:print_format=json",
                        "-f", "null", "-"], cancel)
    if result.returncode != 0:
        raise AudioToolError(f"Đo loudness lỗi: {result.stderr[-700:]}")
    return _parse_loudnorm(result.stderr)


def master_audio(source: Path, output_root: Path, preset_name: str, output_format: str,
                 exe: str, log: Log = print, cancel: threading.Event | None = None) -> list[Path]:
    source = Path(source)
    if not source.is_file():
        raise AudioToolError(f"Không tìm thấy file: {source}")
    if preset_name not in PRESETS:
        raise AudioToolError(f"Preset không hợp lệ: {preset_name}")
    if output_format not in {"wav", "mp3", "both"}:
        raise AudioToolError("Định dạng master phải là WAV, MP3 hoặc cả hai.")
    preset = PRESETS[preset_name]
    folder = Path(output_root) / "mastered"
    folder.mkdir(parents=True, exist_ok=True)
    exts = [".wav", ".mp3"] if output_format == "both" else [f".{output_format}"]
    targets = [_unique(folder / f"{_safe_stem(source.stem)}_master{ext}") for ext in exts]
    _emit(log, f"Master {preset['label']}: {source.name}")

    before = _measure(exe, source, preset, cancel)
    pass1 = _run(exe, ["-i", str(source), "-filter_complex",
                       _master_filter(preset, _ln_args(preset, margin=0.0)),
                       "-map", "[out]", "-f", "null", "-"], cancel)
    if pass1.returncode != 0:
        raise AudioToolError(f"Master pass 1 lỗi: {pass1.stderr[-900:]}")
    measured = _parse_loudnorm(pass1.stderr)

    done: list[Path] = []
    for dest in targets:
        temp = dest.with_name(f".{dest.stem}.tmp{dest.suffix}")
        temp.unlink(missing_ok=True)
        is_mp3 = dest.suffix.lower() == ".mp3"
        encode = (["-ar", "48000", "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]
                  if is_mp3 else ["-ar", "48000", "-c:a", "pcm_s24le", "-f", "wav"])
        try:
            result = _run(exe, ["-y", "-i", str(source), "-filter_complex",
                                _master_filter(preset, _ln_args(
                                    preset, measured, margin=0.6 if is_mp3 else 0.3)),
                                "-map", "[out]", "-map_metadata", "0", *encode, str(temp)], cancel)
            if result.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 44:
                raise AudioToolError(f"Master pass 2 ({dest.suffix}) lỗi: {result.stderr[-900:]}")
            temp.replace(dest)
            done.append(dest)
        finally:
            temp.unlink(missing_ok=True)

    after = _measure(exe, done[0], preset, cancel)
    _emit(log, f"✓ LUFS {float(before['input_i']):.1f} → {float(after['input_i']):.1f}; "
               f"TP {float(before['input_tp']):.1f} → {float(after['input_tp']):.1f} dBTP")
    for path in done:
        _emit(log, f"✓ Đã tạo: {path}")
    return done


def collect_audio(folder: Path, recursive: bool = True) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted((p for p in iterator if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
                  key=lambda p: str(p).casefold())
