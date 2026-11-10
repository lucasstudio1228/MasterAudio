"""
cover/mastering.py — Tự động master 2 bản Suno bằng ffmpeg, ngay sau khi tải xong.

Chuỗi xử lý (đúng thứ tự, mọi con số lấy từ data/master_config.json):
  highpass → EQ giảm đục (300 Hz) → EQ giảm gắt (3.5 kHz) → highshelf 10 kHz
  → (tuỳ chọn) gom bass < 120 Hz về mono → compressor → loudnorm 2 pass.

File gốc KHÔNG bao giờ bị ghi đè/xoá. Kết quả ở thư mục con ``mastered/`` cạnh
file gốc, tên «<tên gốc>_master.wav» (WAV 24-bit 48 kHz) và/hoặc «.mp3» 320k.
File đã master rồi → bỏ qua (chạy lại không xử lý trùng).

Chạy thử bằng tay:
    .venv\\Scripts\\python.exe -m backend.cover.mastering bai1.wav bai2.wav [--preset loud]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from backend.database import DATA_DIR

CONFIG_FILE = DATA_DIR / "master_config.json"
MASTER_DIR = "mastered"
SUFFIX = "_master"
PARTIAL_EXTS = (".part", ".tmp", ".crdownload", ".download")
LOG_NAME = "_master.log"

DEFAULT_CONFIG: dict = {
    "enabled": True,
    # "ffmpeg" = tìm trong PATH; hoặc đường dẫn đầy đủ, vd "C:/ffmpeg/bin/ffmpeg.exe"
    "ffmpeg_path": "ffmpeg",
    "preset": "natural",            # natural | dynamic | loud
    "output_format": "wav",         # wav | mp3 | both
    "max_parallel": 2,
    "stable_seconds": 2,            # kích thước file phải đứng yên bấy nhiêu giây
    "wait_ready_timeout_sec": 120,
    "highpass_hz": 30,
    "eq_mud": {"freq_hz": 300, "width_q": 1.0, "gain_db": -1.5},
    "eq_harsh": {"freq_hz": 3500, "width_q": 1.5, "gain_db": -1.5},
    "high_shelf": {"freq_hz": 10000, "gain_db": -1.5},
    "mono_bass": {"enabled": True, "split_hz": 120},
    "compressor": {"threshold_db": -18, "attack_ms": 20, "release_ms": 200},
    "loudnorm": {"true_peak_db": -1.0, "lra": 11},
    # Bộ hạn đỉnh của loudnorm + đổi về 48 kHz hay lố ~0.1 dB → nhắm thấp hơn chút cho chắc
    # ≤ true_peak_db. Nén MP3 lố thêm ~0.2-0.4 dB → bản MP3 chừa thêm.
    "true_peak_margin_db": 0.3,
    "mp3_true_peak_margin_db": 0.3,
    "presets": {
        "natural": {"lufs": -14, "ratio": 2, "note": "Cân bằng, chuẩn YouTube"},
        "dynamic": {"lufs": -16, "ratio": 1.5, "note": "Giữ dynamic, ít nén"},
        "loud": {"lufs": -10, "ratio": 3, "note": "To hơn, TP vẫn -1"},
    },
}

INSTALL_HINT = (
    "Cài ffmpeg: tải bản «ffmpeg-release-essentials» ở https://www.gyan.dev/ffmpeg/builds/ "
    "→ giải nén (vd C:\\ffmpeg) → thêm C:\\ffmpeg\\bin vào PATH (hoặc ghi đường dẫn "
    "ffmpeg.exe vào \"ffmpeg_path\" trong data\\master_config.json) → mở lại Tool.")

Log = Callable[[str], None]

_executor: Optional[ThreadPoolExecutor] = None
_executor_size = 0
_lock = threading.Lock()
_ffmpeg_state: dict = {"checked": False, "ok": False, "path": "", "version": "", "error": ""}
_active: dict[str, str] = {}          # đường dẫn file gốc → "queued" | "running"


class MasterError(RuntimeError):
    pass


# ── config ────────────────────────────────────────────────────────────────
def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    """Mặc định + ghi đè từ data/master_config.json (file hỏng → dùng mặc định)."""
    path = path or CONFIG_FILE
    try:
        if path.is_file():
            return _merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        print(f"[master] master_config.json lỗi ({e}) — dùng mặc định.", flush=True)
    return copy.deepcopy(DEFAULT_CONFIG)


def ensure_config_file(path: Path | None = None) -> Path:
    """Tạo file config mặc định cho người dùng sửa (không ghi đè file đã có)."""
    path = path or CONFIG_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return path


OUTPUT_FORMATS = ("wav", "mp3", "both")


def save_settings(updates: dict, path: Path | None = None) -> dict:
    """Giao diện đổi preset / định dạng / bật-tắt → ghi vào master_config.json
    (giữ nguyên các số khác người dùng đã sửa tay). Trả config mới."""
    path = path or CONFIG_FILE
    cfg = load_config(path)
    if "preset" in updates and updates["preset"] is not None:
        cfg["preset"] = preset_values(cfg, str(updates["preset"]))[0]
    if "output_format" in updates and updates["output_format"] is not None:
        fmt = str(updates["output_format"]).lower()
        if fmt not in OUTPUT_FORMATS:
            raise MasterError(f"Định dạng «{fmt}» không hợp lệ — chọn wav, mp3 hoặc both.")
        cfg["output_format"] = fmt
    if "enabled" in updates and updates["enabled"] is not None:
        cfg["enabled"] = bool(updates["enabled"])
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.update({k: cfg[k] for k in ("preset", "output_format", "enabled")})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_merge(DEFAULT_CONFIG, raw), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)
    return cfg


def preset_values(cfg: dict, name: str | None = None) -> tuple[str, dict]:
    presets = cfg.get("presets") or DEFAULT_CONFIG["presets"]
    name = (name or cfg.get("preset") or "natural").strip().lower()
    if name not in presets:
        raise MasterError(f"Preset «{name}» không có — chọn: {', '.join(presets)}.")
    return name, presets[name]


# ── ffmpeg ────────────────────────────────────────────────────────────────
def _run_kwargs() -> dict:
    kw: dict = {"capture_output": True, "text": True, "encoding": "utf-8",
                "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def resolve_ffmpeg(cfg: dict) -> str:
    raw = str(cfg.get("ffmpeg_path") or "ffmpeg").strip().strip('"')
    if Path(raw).is_file():
        return str(Path(raw))
    found = shutil.which(raw)
    if not found:
        raise MasterError(f"Không tìm thấy ffmpeg («{raw}»). {INSTALL_HINT}")
    return found


def check_ffmpeg(cfg: dict | None = None, log: Log = print) -> dict:
    """Chạy ``ffmpeg -version``. Lỗi → báo rõ + hướng dẫn cài; tool vẫn chạy."""
    cfg = cfg or load_config()
    state = {"checked": True, "ok": False, "path": "", "version": "", "error": ""}
    try:
        exe = resolve_ffmpeg(cfg)
        res = subprocess.run([exe, "-version"], timeout=20, **_run_kwargs())
        if res.returncode != 0:
            raise MasterError(f"«{exe} -version» lỗi: {(res.stderr or '').strip()[-300:]}")
        state.update(ok=True, path=exe, version=(res.stdout or "").splitlines()[0][:120])
        log(f"[master] ffmpeg OK: {state['version']}")
    except (MasterError, OSError, subprocess.SubprocessError) as e:
        state["error"] = str(e)
        log(f"[master] ⚠ {e} — tool vẫn tải nhạc bình thường, chỉ BỎ QUA bước master.")
    _ffmpeg_state.update(state)
    return dict(state)


def ffmpeg_status() -> dict:
    return dict(_ffmpeg_state)


# ── điều kiện kích hoạt ───────────────────────────────────────────────────
def master_paths(src: Path, fmt: str) -> list[Path]:
    folder = src.parent / MASTER_DIR
    exts = {"wav": [".wav"], "mp3": [".mp3"], "both": [".wav", ".mp3"]}.get(fmt)
    if exts is None:
        raise MasterError(f"output_format «{fmt}» không hợp lệ — chọn wav, mp3 hoặc both.")
    return [folder / f"{src.stem}{SUFFIX}{ext}" for ext in exts]


def find_master(src: Path) -> Path | None:
    """Bản _master đã xong của ``src`` (ưu tiên WAV, rồi MP3) — dùng cho Mix/video."""
    src = Path(src)
    for p in master_paths(src, "both"):
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None


def _key(src: Path) -> str:
    try:
        return str(Path(src).resolve())
    except OSError:
        return str(src)


def _info_path(src: Path) -> Path:
    return Path(src).parent / MASTER_DIR / f"{Path(src).stem}{SUFFIX}.json"


def _read_info(src: Path) -> dict:
    try:
        data = json.loads(_info_path(src).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_info(src: Path, data: dict) -> None:
    path = _info_path(src)
    try:
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def is_busy() -> bool:
    with _lock:
        return bool(_active)


def status_of(src: Path) -> dict:
    """Trạng thái master của 1 bản cover cho giao diện:
    state = queued | running | done | failed | none."""
    src = Path(src)
    with _lock:
        active = _active.get(_key(src))
    info = _read_info(src)
    master = find_master(src)
    out = {"state": active or ("done" if master else "failed" if info.get("last_error")
                               else "none"),
           "file": master.name if master else "",
           "preset": info.get("preset", ""), "before": info.get("before"),
           "after": info.get("after"), "seconds": info.get("seconds"),
           "at": info.get("at", ""), "error": info.get("last_error", "")}
    return out


def mix_target_lufs(cfg: dict | None = None) -> float | None:
    """Độ to của preset đang chọn — Mix chuẩn các bài theo mức này khi dùng bản master."""
    cfg = cfg or load_config()
    try:
        return float(preset_values(cfg)[1]["lufs"])
    except (MasterError, KeyError, TypeError, ValueError):
        return None


def _master_fresh(dest: Path, src: Path) -> bool:
    """Bản _master còn dùng được: có dữ liệu và KHÔNG cũ hơn file gốc. File gốc bị
    ghi đè (vd. Suno tải lại «cover 3.wav») → bản _master cũ là bài khác, phải master lại."""
    if not (dest.is_file() and dest.stat().st_size > 0):
        return False
    try:
        return dest.stat().st_mtime >= Path(src).stat().st_mtime
    except OSError:
        return True


def already_mastered(src: Path, fmt: str) -> bool:
    return all(_master_fresh(p, src) for p in master_paths(src, fmt))


def wait_ready(path: Path, stable_seconds: float = 2, timeout_sec: float = 120,
               poll: float = 0.5) -> bool:
    """File tải xong hẳn: tồn tại, > 0 byte, không còn đuôi tải dở, kích thước
    đứng yên ``stable_seconds`` giây (và không còn file .part cùng tên)."""
    path = Path(path)
    if path.suffix.lower() in PARTIAL_EXTS:
        return False
    deadline = time.monotonic() + timeout_sec
    last, since = -1, time.monotonic()
    while time.monotonic() < deadline:
        partial = any(path.with_name(path.name + ext).exists() for ext in PARTIAL_EXTS)
        size = path.stat().st_size if path.is_file() else -1
        if size != last or partial or size <= 0:
            last, since = size, time.monotonic()
        elif time.monotonic() - since >= stable_seconds:
            return True
        time.sleep(poll)
    return False


# ── chuỗi lọc ─────────────────────────────────────────────────────────────
def _num(v) -> str:
    return f"{float(v):g}"


def build_filter(cfg: dict, preset: dict, loudnorm: str) -> str:
    """filter_complex: [0:a] → … → [out]. ``loudnorm`` = tham số sau «loudnorm=»."""
    mud, harsh, shelf = cfg["eq_mud"], cfg["eq_harsh"], cfg["high_shelf"]
    comp = cfg["compressor"]
    pre = ",".join([
        "aformat=channel_layouts=stereo",          # file mono → stereo cho pan bên dưới
        f"highpass=f={_num(cfg['highpass_hz'])}",
        f"equalizer=f={_num(mud['freq_hz'])}:t=q:w={_num(mud['width_q'])}:g={_num(mud['gain_db'])}",
        f"equalizer=f={_num(harsh['freq_hz'])}:t=q:w={_num(harsh['width_q'])}"
        f":g={_num(harsh['gain_db'])}",
        f"highshelf=f={_num(shelf['freq_hz'])}:g={_num(shelf['gain_db'])}",
    ])
    post = (f"acompressor=threshold={_num(comp['threshold_db'])}dB:ratio={_num(preset['ratio'])}"
            f":attack={_num(comp['attack_ms'])}:release={_num(comp['release_ms'])}"
            f",loudnorm={loudnorm}")
    mono = cfg.get("mono_bass") or {}
    if not mono.get("enabled", True):
        return f"[0:a]{pre},{post}[out]"
    return (f"[0:a]{pre},acrossover=split={_num(mono.get('split_hz', 120))}[lo][hi];"
            "[lo]pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[lom];"
            f"[lom][hi]amix=inputs=2:normalize=0,{post}[out]")


def _loudnorm_args(cfg: dict, preset: dict, measured: dict | None = None,
                   tp_margin: float = 0.0) -> str:
    ln = cfg["loudnorm"]
    tp = float(ln["true_peak_db"]) - abs(float(tp_margin or 0))
    base = (f"I={_num(preset['lufs'])}:TP={_num(tp)}:LRA={_num(ln['lra'])}"
            ":print_format=json")
    if measured:
        base += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                 f":measured_LRA={measured['input_lra']}"
                 f":measured_thresh={measured['input_thresh']}"
                 f":offset={measured['target_offset']}:linear=true")
    return base


def parse_loudnorm_json(stderr: str) -> dict:
    """Lấy khối JSON cuối cùng mà loudnorm in ra stderr."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr or "", re.S)
    if not blocks:
        raise MasterError("ffmpeg không in số đo loudnorm (JSON).")
    data = json.loads(blocks[-1])
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if not math.isfinite(float(data[k])):
            raise MasterError(f"file im lặng / không đo được loudness ({k}={data[k]}).")
    return data


def _ffmpeg(exe: str, args: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([exe, "-hide_banner", "-nostdin", *args], timeout=timeout,
                          **_run_kwargs())


def measure(exe: str, path: Path, lufs: float = -14, tp: float = -1, lra: float = 11) -> dict:
    """Đo Integrated LUFS / True Peak của 1 file (loudnorm print_format=json)."""
    res = _ffmpeg(exe, ["-i", str(path), "-map", "0:a:0", "-af",
                        f"loudnorm=I={lufs:g}:TP={tp:g}:LRA={lra:g}:print_format=json",
                        "-f", "null", "-"])
    if res.returncode != 0:
        raise MasterError(f"Đo loudness lỗi: {(res.stderr or '').strip()[-400:]}")
    return parse_loudnorm_json(res.stderr)


def _encode_args(fmt: str) -> list[str]:
    if fmt == "mp3":
        return ["-map", "0:v?", "-c:v", "copy", "-id3v2_version", "3",
                "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]
    return ["-ar", "48000", "-c:a", "pcm_s24le", "-f", "wav"]


# ── master 1 file ─────────────────────────────────────────────────────────
def master_file(src: Path, cfg: dict | None = None, preset_name: str | None = None,
                log: Log = print, log_file: Path | None = None, force: bool = False) -> dict:
    """Master 1 file. Trả {"status": "done"|"skipped"|"failed", ...}. Không raise.
    ``force`` = master lại dù đã có bản _master (bản cũ chỉ bị thay khi bản mới xong)."""
    src = Path(src)
    key = _key(src)
    with _lock:
        _active[key] = "running"
    try:
        return _master_file(src, cfg or load_config(), preset_name, log, log_file, force)
    finally:
        with _lock:
            _active.pop(key, None)


def _master_file(src: Path, cfg: dict, preset_name: str | None, log: Log,
                 log_file: Path | None, force: bool) -> dict:
    t0 = time.monotonic()
    out: dict = {"file": src.name, "status": "failed", "outputs": []}
    try:
        name, preset = preset_values(cfg, preset_name)
        out["preset"] = name
        fmt = str(cfg.get("output_format") or "wav").lower()
        targets = master_paths(src, fmt)
        if not force and already_mastered(src, fmt):
            out.update(status="skipped", outputs=[str(p) for p in targets])
            log(f"[master] {src.name}: đã có bản _master — bỏ qua.")
            return out
        if not src.is_file():
            raise MasterError("không thấy file (tải lỗi hoặc đã bị chuyển đi).")
        if not wait_ready(src, float(cfg.get("stable_seconds", 2)),
                          float(cfg.get("wait_ready_timeout_sec", 120))):
            raise MasterError("file chưa tải xong (không tồn tại / 0 byte / còn đang ghi).")
        exe = resolve_ffmpeg(cfg)
        ln = cfg["loudnorm"]
        before = measure(exe, src, preset["lufs"], ln["true_peak_db"], ln["lra"])

        # Pass 1: đo cả chuỗi (đã EQ/nén) để loudnorm pass 2 chỉnh tuyến tính chính xác.
        res = _ffmpeg(exe, ["-i", str(src), "-filter_complex",
                            build_filter(cfg, preset, _loudnorm_args(cfg, preset)),
                            "-map", "[out]", "-f", "null", "-"])
        if res.returncode != 0:
            raise MasterError(f"pass 1 lỗi:\n{res.stderr}")
        measured = parse_loudnorm_json(res.stderr)

        # Pass 2: ghi file (ra file tạm rồi đổi tên → không bao giờ để file _master dở).
        targets[0].parent.mkdir(parents=True, exist_ok=True)
        for dest in targets:
            if not force and _master_fresh(dest, src):
                continue                      # «both»: định dạng này đã có từ trước
            tmp = dest.with_name(f".{dest.stem}.tmp{dest.suffix}")
            tmp.unlink(missing_ok=True)
            fmt_one = dest.suffix.lstrip(".")
            margin = abs(float(cfg.get("true_peak_margin_db", 0.3)))
            if fmt_one == "mp3":
                margin += abs(float(cfg.get("mp3_true_peak_margin_db", 0.3)))
            graph = build_filter(cfg, preset, _loudnorm_args(cfg, preset, measured, margin))
            try:
                res = _ffmpeg(exe, ["-y", "-i", str(src), "-filter_complex", graph,
                                    "-map", "[out]", "-map_metadata", "0",
                                    *_encode_args(fmt_one), str(tmp)])
                if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 44:
                    raise MasterError(f"pass 2 ({fmt_one}) lỗi:\n{res.stderr}")
                tmp.replace(dest)
            finally:
                tmp.unlink(missing_ok=True)
        after = measure(exe, targets[0], preset["lufs"], ln["true_peak_db"], ln["lra"])
        out.update(status="done", outputs=[str(p) for p in targets],
                   before={"lufs": float(before["input_i"]), "tp": float(before["input_tp"])},
                   after={"lufs": float(after["input_i"]), "tp": float(after["input_tp"])})
        line = (f"✓ {src.name} | preset {name} ({preset['lufs']} LUFS) | "
                f"LUFS {out['before']['lufs']:.1f} → {out['after']['lufs']:.1f} | "
                f"TP {out['before']['tp']:.1f} → {out['after']['tp']:.1f} dBTP | "
                f"{time.monotonic() - t0:.1f}s")
        log(f"[master] {line}")
        _write_log(log_file or targets[0].parent / LOG_NAME, line)
        _write_info(src, {"preset": name, "before": out["before"], "after": out["after"],
                          "seconds": round(time.monotonic() - t0, 1),
                          "outputs": [p.name for p in targets],
                          "at": f"{datetime.now():%Y-%m-%d %H:%M}"})
    except Exception as e:  # noqa: BLE001 — master lỗi KHÔNG được làm crash tool
        detail = str(e)
        out["error"] = detail.splitlines()[0][:300] if detail else type(e).__name__
        log(f"[master] ✗ {src.name}: {out['error']} | {time.monotonic() - t0:.1f}s "
            "(file gốc giữ nguyên)")
        _write_log(log_file or src.parent / MASTER_DIR / LOG_NAME,
                   f"LỖI {src.name}\n{detail}")
        if src.parent.is_dir():
            info = _read_info(src)
            info.update(last_error=out["error"], failed_at=f"{datetime.now():%Y-%m-%d %H:%M}")
            _write_info(src, info)
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def _write_log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(exist_ok=True)   # không dựng lại thư mục cha đã bị xoá
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


# ── chạy nền sau khi tải xong 1 lượt Suno ────────────────────────────────
def _pool(size: int) -> ThreadPoolExecutor:
    global _executor, _executor_size
    with _lock:
        if _executor is None or _executor_size != size:
            _executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="master")
            _executor_size = size
        return _executor


def master_batch(files: list[Path], expected: int | None = None, log: Log = print,
                 preset_name: str | None = None, cfg: dict | None = None,
                 force: bool = False) -> list[dict]:
    """Master các file của CÙNG 1 lượt tạo, song song tối đa ``max_parallel``.
    Thiếu bản nào (tải lỗi) → vẫn master bản còn lại + ghi log."""
    files = [Path(f) for f in files]
    try:
        return _master_batch(files, expected, log, preset_name, cfg or load_config(), force)
    finally:
        with _lock:                       # bỏ dấu «chờ master» của file không được chạy
            for f in files:
                if _active.get(_key(f)) == "queued":
                    _active.pop(_key(f), None)


def _master_batch(files: list[Path], expected: int | None, log: Log,
                  preset_name: str | None, cfg: dict, force: bool) -> list[dict]:
    if not cfg.get("enabled", True):     # «enabled» = tự master sau khi tải Suno
        log("[master] đang tắt trong master_config.json — bỏ qua.")
        return []
    if expected and len(files) < expected:
        msg = (f"chỉ có {len(files)}/{expected} bản tải xong — master bản còn lại, "
               f"{expected - len(files)} bản tải lỗi bị bỏ qua.")
        log(f"[master] ⚠ {msg}")
        if files:
            _write_log(files[0].parent / MASTER_DIR / LOG_NAME, msg)
    if not files:
        return []
    try:
        resolve_ffmpeg(cfg)
    except MasterError as e:
        log(f"[master] ⚠ {e} — bỏ qua bước master, file tải về vẫn dùng bình thường.")
        return []
    size = max(1, min(2, int(cfg.get("max_parallel", 2) or 1)))
    pool = _pool(size)
    with _lock:
        for f in files:
            _active.setdefault(_key(f), "queued")
    futs = [pool.submit(master_file, f, cfg, preset_name, log, None, force) for f in files]
    return [f.result() for f in futs]


def submit(files: list[Path], expected: int | None = None, log: Log = print,
           force: bool = False, cfg: dict | None = None) -> threading.Thread | None:
    """Chạy ``master_batch`` ở luồng nền — trả ngay, không chặn luồng tải Suno.
    File đang chờ/đang master rồi thì không xếp thêm lần nữa."""
    with _lock:
        files = [Path(f) for f in files if _key(Path(f)) not in _active]
        for f in files:
            _active[_key(f)] = "queued"   # giao diện thấy «chờ master» ngay
    if not files and not expected:
        return None
    t = threading.Thread(target=master_batch, args=(files, expected, log),
                         kwargs={"force": force, "cfg": cfg}, name="master-batch",
                         daemon=True)
    t.start()
    return t


# ── chạy tay ──────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Master file nhạc bằng ffmpeg (loudnorm 2 pass).")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--preset", choices=sorted(DEFAULT_CONFIG["presets"]))
    ap.add_argument("--format", choices=["wav", "mp3", "both"])
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.format:
        cfg["output_format"] = args.format
    if not check_ffmpeg(cfg)["ok"]:
        return 2
    results = master_batch(args.files, cfg=cfg, preset_name=args.preset)
    exe = resolve_ffmpeg(cfg)
    ok = True
    for r in results:
        if r["status"] != "done":
            ok = ok and r["status"] == "skipped"
            continue
        _, preset = preset_values(cfg, r["preset"])
        for o in r["outputs"]:
            m = measure(exe, Path(o), preset["lufs"])
            i, tp = float(m["input_i"]), float(m["input_tp"])
            good = abs(i - preset["lufs"]) <= 0.5 and tp <= -1.0 + 1e-6
            ok = ok and good
            print(f"  kiểm tra {Path(o).name}: {i:.2f} LUFS (mục tiêu {preset['lufs']}), "
                  f"TP {tp:.2f} dBTP → {'ĐẠT' if good else 'KHÔNG ĐẠT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
