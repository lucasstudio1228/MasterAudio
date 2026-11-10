"""
video/track_qc.py — Rà soát CHẤT LƯỢNG từng track lẻ (sau khi mix xong).

suno_verify chỉ kiểm tra KỸ THUẬT lúc tải (WAV thật, ≥120 s, không im lặng).
Bài lọt qua đó vẫn có thể hỏng khi nghe: rè/xì/tạp âm cao tần, méo (clipping),
to bất thường so với các bài khác, hụt tiếng giữa bài, kết thúc bị cắt cụt.
Đo thật trên 45 bài (project 6/7/8, 2026-09-29): bài bình thường có tỷ lệ năng
lượng >6 kHz ~0.2–1 %, 2 bài rè của project 8 lên 5–9 % (gấp ~10 lần) và 1 bài
clip 313 mẫu — nên so với CHÍNH các bài cùng project thay vì 1 ngưỡng cứng.

KHÔNG tốn credit, không mở trình duyệt. Kết quả lưu ở
media/<project>/track_qc.json (cache theo path + mtime + size).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

QC_FILE = "track_qc.json"

# Ngưỡng (đo trên dữ liệu thật — xem docstring module).
SILENCE_DB = -50.0            # cửa sổ 0.5 s dưới mức này = im lặng
DROPOUT_SEC = 3.0             # hụt tiếng giữa bài ≥ 3 s → lỗi
CLIP_LEVEL = 0.999
CLIP_SAMPLES_ERR = 200        # ≥ 200 mẫu chạm trần → méo nghe được
HF_CUTOFF_HZ = 6000.0
HF_ABS_MIN = 3.0              # % năng lượng >6 kHz: dưới mức này luôn coi là sạch
HF_REL_FACTOR = 4.0           # … và phải gấp ≥4 lần trung vị các bài cùng project
LOUD_REL_DB = 4.0             # to hơn trung vị project ≥ 4 dB
QUIET_REL_DB = -6.0           # nhỏ hơn trung vị project ≥ 6 dB
ABRUPT_END_REL_DB = -12.0     # 1 s cuối vẫn gần mức trung bình → bị cắt cụt
MIN_DURATION = 120.0

_lock = threading.Lock()      # ghi file QC từ nhiều luồng (mix callback / job thay bài)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_t(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


# ══════════════════════════════════════════════════════════════════
# Đo 1 file
# ══════════════════════════════════════════════════════════════════
def measure(path: str | Path) -> dict:
    """Số đo thô của 1 file (không phán xét). Raise nếu không đọc được."""
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return measure_array(x, sr)


def measure_array(x: np.ndarray, sr: int) -> dict:
    """Như measure() nhưng nhận sẵn mẫu [n, kênh] float32 (file đã giải mã)."""
    if x.size == 0:
        raise ValueError("File rỗng")
    mono = x.mean(axis=1)
    dur = len(mono) / sr

    peak_abs = np.abs(x).max(axis=1)
    clip_samples = int((peak_abs >= CLIP_LEVEL).sum())
    clip_first = (float(np.argmax(peak_abs >= CLIP_LEVEL)) / sr) if clip_samples else None

    rms_db = float(20 * np.log10(np.sqrt(np.mean(mono ** 2)) + 1e-9))

    # Đường bao 0.5 s → im lặng đầu/cuối/giữa bài.
    w = max(1, int(sr * 0.5))
    n = len(mono) // w
    env = 20 * np.log10(np.sqrt((mono[: n * w].reshape(n, w) ** 2).mean(axis=1)) + 1e-9) \
        if n else np.array([rms_db])
    loud = env >= SILENCE_DB
    if loud.any():
        lead = int(np.argmax(loud))
        tail = int(np.argmax(loud[::-1]))
    else:
        lead, tail = len(env), 0
    gap_max, gap_at, run, start = 0, None, 0, 0
    for i in range(lead, len(env) - tail):
        if not loud[i]:
            if run == 0:
                start = i
            run += 1
            if run > gap_max:
                gap_max, gap_at = run, start * 0.5
        else:
            run = 0

    end_db = float(20 * np.log10(np.sqrt(np.mean(mono[-sr:] ** 2)) + 1e-9))

    # Năng lượng cao tần (>6 kHz) theo khung — chỉ tính khung có tín hiệu.
    nfft = 4096
    frames = len(mono) // nfft
    hf_med = hf_p90 = 0.0
    hf_first = None
    if frames:
        fr = mono[: frames * nfft].reshape(frames, nfft) * np.hanning(nfft)
        spec = np.abs(np.fft.rfft(fr, axis=1))
        cut = int(HF_CUTOFF_HZ / sr * nfft)
        tot = spec.sum(axis=1) + 1e-9
        hf = spec[:, cut:].sum(axis=1) / tot * 100.0
        active = tot > np.percentile(tot, 20)
        if active.any():
            hf_med = float(np.median(hf[active]))
            hf_p90 = float(np.percentile(hf[active], 90))
            hf_series = hf
        else:
            hf_series = hf
        # Khối 10 s: dùng để chỉ ra THỜI ĐIỂM rè cho người nghe lại.
        per = max(1, int(10 * sr / nfft))
        nb = frames // per
        blocks = hf_series[: nb * per].reshape(nb, per).mean(axis=1) if nb else np.array([])
    else:
        blocks = np.array([])

    return {
        "duration": round(dur, 2),
        "sample_rate": int(sr),
        "rms_db": round(rms_db, 2),
        "peak": round(float(peak_abs.max()), 4),
        "clip_samples": clip_samples,
        "clip_first": round(clip_first, 1) if clip_first is not None else None,
        "gap_max": round(gap_max * 0.5, 1),
        "gap_at": gap_at,
        "end_db": round(end_db, 2),
        "end_rel_db": round(end_db - rms_db, 2),
        "hf_median": round(hf_med, 3),
        "hf_p90": round(hf_p90, 3),
        "hf_blocks": [round(float(v), 2) for v in blocks],
    }


# ══════════════════════════════════════════════════════════════════
# Phán xét (so với các bài cùng project)
# ══════════════════════════════════════════════════════════════════
def judge(m: dict, ctx: dict) -> list[dict]:
    """Trả danh sách vấn đề {code, level: error|warning, message}."""
    out: list[dict] = []

    def add(code, level, msg):
        out.append({"code": code, "level": level, "message": msg})

    if m["duration"] < MIN_DURATION:
        add("too_short", "error", f"Quá ngắn ({m['duration']:.0f}s < {MIN_DURATION:.0f}s).")

    hf_ref = ctx.get("hf_median") or 0.0
    hf_thr = max(HF_ABS_MIN, HF_REL_FACTOR * hf_ref)
    if m["hf_median"] >= hf_thr:
        where = ""
        blocks = m.get("hf_blocks") or []
        hot = [i for i, v in enumerate(blocks) if v >= hf_thr]
        if hot:
            peak = int(np.argmax(blocks))
            where = (f", xuất hiện từ {_fmt_t(hot[0] * 10)}"
                     f", nặng nhất quanh {_fmt_t(peak * 10)}–{_fmt_t(peak * 10 + 10)}")
        add("noise", "error",
            f"Rè/xì — tạp âm cao tần {m['hf_median']:.1f}% (các bài khác ~{hf_ref:.1f}%)"
            f"{where}.")

    if m["clip_samples"] >= CLIP_SAMPLES_ERR:
        add("clipping", "error",
            f"Méo tiếng (clipping {m['clip_samples']} mẫu, từ {_fmt_t(m['clip_first'] or 0)}).")

    if m["gap_max"] >= DROPOUT_SEC:
        add("dropout", "error",
            f"Mất tiếng giữa bài {m['gap_max']:.0f}s tại {_fmt_t(m['gap_at'] or 0)}.")

    ref_db = ctx.get("rms_db")
    if ref_db is not None:
        d = m["rms_db"] - ref_db
        if d >= LOUD_REL_DB:
            add("loud", "warning", f"To bất thường (+{d:.1f} dB so với các bài khác).")
        elif d <= QUIET_REL_DB:
            add("quiet", "warning", f"Nhỏ bất thường ({d:.1f} dB so với các bài khác).")

    if m["end_rel_db"] > ABRUPT_END_REL_DB:
        add("abrupt_end", "warning",
            "Kết thúc đột ngột (giây cuối vẫn to) — crossfade che bớt, nên nghe lại.")
    return out


def _status(issues: list[dict], manual: bool) -> str:
    if manual or any(i["level"] == "error" for i in issues):
        return "error"
    if issues:
        return "warning"
    return "ok"


# ══════════════════════════════════════════════════════════════════
# Báo cáo theo project (file JSON)
# ══════════════════════════════════════════════════════════════════
def _qc_path(project_id: int, project_name: Optional[str]) -> Path:
    from . import config
    return config.project_dir(project_id, project_name) / QC_FILE


def load_report(project_id: int, project_name: Optional[str]) -> dict:
    p = _qc_path(project_id, project_name)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tracks", {})
            data.setdefault("manual_flags", [])
            data.setdefault("replaced", [])
            return data
    except Exception:
        pass
    return {"tracks": {}, "manual_flags": [], "replaced": []}


def save_report(project_id: int, project_name: Optional[str], data: dict) -> None:
    p = _qc_path(project_id, project_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _file_key(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def review_tracks(project_id: int, project_name: Optional[str],
                  tracks: list[tuple[int, str, str]],
                  *, log: Optional[Callable[[str], None]] = None,
                  mix_id: Optional[int] = None,
                  _measure: Callable[[str], dict] = measure) -> dict:
    """Đo + phán xét mọi track [(track_id, filename, filepath)] của project.
    Số đo cache theo (size, mtime) → chạy lại nhanh. Trả báo cáo đã lưu."""
    with _lock:
        data = load_report(project_id, project_name)
    old = data.get("tracks", {})
    measured: dict[str, dict] = {}
    for tid, fname, fpath in tracks:
        p = Path(fpath)
        entry = {"track_id": tid, "filename": fname}
        if not p.exists():
            entry.update(metrics=None, error="Không thấy file.")
            measured[str(tid)] = entry
            continue
        key = _file_key(p)
        prev = old.get(str(tid)) or {}
        if prev.get("file_key") == key and prev.get("metrics"):
            entry.update(metrics=prev["metrics"], file_key=key)
        else:
            try:
                if log:
                    log(f"Rà soát {fname}…")
                entry.update(metrics=_measure(str(p)), file_key=key)
            except Exception as e:      # noqa: BLE001 — 1 file hỏng không chặn cả lượt
                entry.update(metrics=None, error=f"Không đọc được: {e}")
        measured[str(tid)] = entry

    ms = [e["metrics"] for e in measured.values() if e.get("metrics")]
    ctx = {
        "hf_median": float(np.median([m["hf_median"] for m in ms])) if ms else 0.0,
        "rms_db": float(np.median([m["rms_db"] for m in ms])) if ms else None,
    }
    with _lock:
        data = load_report(project_id, project_name)   # đọc lại: có thể vừa đổi cờ tay
        manual = {int(x) for x in data.get("manual_flags", [])}
        for tid, e in measured.items():
            if e.get("metrics"):
                e["issues"] = judge(e["metrics"], ctx)
            else:
                e["issues"] = [{"code": "unreadable", "level": "error",
                                "message": e.get("error") or "Không đọc được file."}]
            e["manual_flag"] = int(tid) in manual
            e["status"] = _status(e["issues"], e["manual_flag"])
        data["tracks"] = measured
        data["context"] = {k: (round(v, 3) if isinstance(v, float) else v)
                           for k, v in ctx.items()}
        data["analyzed_at"] = _now()
        if mix_id is not None:
            data["mix_id"] = mix_id
        data["manual_flags"] = sorted(m for m in manual if str(m) in measured)
        save_report(project_id, project_name, data)
    return data


def set_manual_flag(project_id: int, project_name: Optional[str],
                    track_id: int, flagged: bool) -> dict:
    """Người dùng tự đánh dấu bài lỗi (nghe thấy lỗi mà máy đo không bắt được)."""
    with _lock:
        data = load_report(project_id, project_name)
        flags = {int(x) for x in data.get("manual_flags", [])}
        (flags.add if flagged else flags.discard)(int(track_id))
        data["manual_flags"] = sorted(flags)
        e = data["tracks"].get(str(track_id))
        if e is not None:
            e["manual_flag"] = flagged
            e["status"] = _status(e.get("issues") or [], flagged)
        save_report(project_id, project_name, data)
    return data


def record_replacement(project_id: int, project_name: Optional[str],
                       old_track_id: int, new_track_id: int, info: dict) -> None:
    with _lock:
        data = load_report(project_id, project_name)
        data["replaced"].append({"old_track_id": old_track_id,
                                 "new_track_id": new_track_id, "at": _now(), **info})
        data["tracks"].pop(str(old_track_id), None)
        data["manual_flags"] = [x for x in data.get("manual_flags", [])
                                if int(x) != int(old_track_id)]
        data["needs_remix"] = True
        save_report(project_id, project_name, data)


def clear_needs_remix(project_id: int, project_name: Optional[str], mix_id: int) -> None:
    with _lock:
        data = load_report(project_id, project_name)
        data["needs_remix"] = False
        data["mix_id"] = mix_id
        save_report(project_id, project_name, data)


def passes(path: str | Path, ctx: dict) -> tuple[bool, list[dict], dict]:
    """Kiểm 1 bài MỚI (bài thay thế) theo cùng tiêu chí với các bài đang có."""
    m = measure(path)
    issues = judge(m, ctx)
    return (not any(i["level"] == "error" for i in issues)), issues, m


def review_project(project_id: int, *, log=None, mix_id: Optional[int] = None) -> dict:
    """Rà soát toàn bộ track hiện có của project (mở session riêng)."""
    from sqlmodel import Session, select
    from backend.database import engine
    from backend.models import Project, Track

    with Session(engine) as s:
        project = s.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} không tồn tại")
        rows = s.exec(select(Track).where(Track.project_id == project_id)
                      .order_by(Track.added_at)).all()
        tracks = [(t.id, t.filename, t.filepath) for t in rows]
        name = project.name
    return review_tracks(project_id, name, tracks, log=log, mix_id=mix_id)


def review_project_async(project_id: int, mix_id: Optional[int] = None) -> None:
    """Chạy nền sau khi mix xong — không chặn callback mix / chuỗi auto-video."""
    def _run():
        try:
            review_project(project_id, mix_id=mix_id)
        except Exception:       # noqa: BLE001 — rà soát là bước phụ
            pass
    threading.Thread(target=_run, name=f"track-qc-{project_id}", daemon=True).start()
