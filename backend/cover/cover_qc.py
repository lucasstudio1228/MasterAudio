"""
cover/cover_qc.py — Phát hiện bản cover bị LỖI THẬT (sau khi tạo trên Suno hoặc
tự nhập file) để báo đỏ + cho «Tạo lại trên Suno».

Dùng máy đo của video/track_qc nhưng so mỗi bản cover với CHÍNH bài gốc của nó
(cùng bài, cùng phối) thay vì so với các bài khác — project trộn ballad với
remix không bị báo nhầm. Chỉ những lỗi dưới đây mới là LỖI (đỏ):
  • gần như không có tiếng / không đọc được file;
  • bị cụt — ngắn hơn hẳn bài gốc;
  • rè/xì — tạp âm cao tần gấp ≥4 lần bài gốc;
  • méo dẹt đỉnh (clipping) nhiều hơn hẳn bài gốc;
  • mất tiếng giữa bài mà bài gốc không có.
To/nhỏ hơn các bản khác, kết thúc đột ngột, dài hơn bài gốc nhiều → chỉ «lưu ý».
Chưa có file bài gốc (bài tự nhập không kèm file gốc) → so với các bản cover
khác trong project như tool cũ.

Người dùng tự «🚩 Báo lỗi» (nghe thấy lỗi máy không bắt được, vd hát sai lời)
hoặc «✓ Nghe ổn» (máy báo nhầm). Số đo lưu ở media/<project>/cover_qc.json,
cache theo size+mtime; phán xét tính lại mỗi lần đọc (rẻ). KHÔNG tốn credit,
không mở trình duyệt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from backend.video import track_qc

QC_FILE = "cover_qc.json"

SILENT_DB = -45.0          # cả bài nhỏ hơn mức này = gần như im lặng
CUT_RATIO = 0.6            # bản cover < 60 % độ dài bài gốc → bị cụt
LONG_RATIO = 1.6           # > 160 % → lưu ý (Suno kéo dài/lặp đoạn cuối)
MIN_SEC_NO_REF = 90.0      # không có bài gốc để so: < 90 s coi là cụt
CLIP_RUN_SAMPLES = 4       # ≥4 mẫu liền nhau chạm trần = đỉnh bị dẹt (méo thật)
CLIP_RUNS_ERR = 100        # ≥100 đoạn dẹt đỉnh …
CLIP_REL_FACTOR = 5.0      # … và gấp ≥5 lần bài gốc → méo
DROPOUT_SLACK = 1.0        # bài gốc cũng có khoảng lặng dài tương đương → không tính

_lock = threading.Lock()   # đọc/ghi cover_qc.json
_run_lock = threading.Lock()
_running: set[int] = set()
_again: set[int] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(sec: float) -> str:
    s = int(round(sec or 0))
    return f"{s // 60}:{s % 60:02d}"


# ══════════════════════════════════════════════════════════════════
# Đo 1 file
# ══════════════════════════════════════════════════════════════════
def decode(path: str | Path) -> tuple[np.ndarray, int]:
    """Mẫu [n, kênh] float32. WAV/FLAC/MP3/OGG đọc thẳng; m4a/aac/wma… nhờ ffmpeg."""
    import soundfile as sf
    try:
        x, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return x, int(sr)
    except Exception:       # noqa: BLE001 — libsndfile không đọc được → thử ffmpeg
        pass
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Không đọc được file (máy chưa có ffmpeg).")
    sr = 44100
    r = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le",
                        "-ac", "2", "-ar", str(sr), "-"],
                       capture_output=True, timeout=600)
    if r.returncode != 0 or not r.stdout:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else "ffmpeg không giải mã được file.")
    return np.frombuffer(r.stdout, dtype="<f4").reshape(-1, 2), sr


def clip_runs(x: np.ndarray, level: float = track_qc.CLIP_LEVEL,
              min_run: int = CLIP_RUN_SAMPLES) -> int:
    """Số đoạn ≥ min_run mẫu liền nhau chạm trần (đỉnh sóng bị dẹt). Đỉnh lẻ do
    giải mã MP3 vượt trần chút ít không tính — chỉ méo thật mới tạo đoạn dẹt."""
    total = 0
    for c in range(x.shape[1]):
        hit = (np.abs(x[:, c]) >= level).astype(np.int8)
        d = np.diff(np.concatenate(([0], hit, [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        total += int(((ends - starts) >= min_run).sum())
    return total


def measure_file(path: str | Path) -> dict:
    x, sr = decode(path)
    m = track_qc.measure_array(x, sr)
    m["clip_runs"] = clip_runs(x)
    return m


# ══════════════════════════════════════════════════════════════════
# Phán xét
# ══════════════════════════════════════════════════════════════════
def judge(m: dict, ref: Optional[dict], ctx: dict) -> list[dict]:
    """m = số đo bản cover; ref = số đo bài gốc (None nếu không có);
    ctx = trung vị các bản cover trong project {hf_median, rms_db, clip_runs}.
    Trả [{code, level: error|warning, message}]."""
    out: list[dict] = []

    def add(code, level, msg):
        out.append({"code": code, "level": level, "message": msg})

    if m["rms_db"] < SILENT_DB:
        add("silent", "error", "Gần như không có tiếng.")
        return out

    if ref:
        ratio = m["duration"] / max(ref["duration"], 1.0)
        if ratio < CUT_RATIO:
            add("cut", "error", f"Bị cụt: bản cover chỉ dài {_fmt(m['duration'])}, "
                                f"bài gốc dài {_fmt(ref['duration'])}.")
        elif ratio > LONG_RATIO:
            add("long", "warning", f"Dài hơn bài gốc nhiều ({_fmt(m['duration'])} so với "
                                   f"{_fmt(ref['duration'])}) — nghe lại đoạn cuối.")
    elif m["duration"] < MIN_SEC_NO_REF:
        add("cut", "error", f"Quá ngắn ({_fmt(m['duration'])}) — có thể bị cụt.")

    who = "bài gốc" if ref else "các bản khác"
    hf_ref = (ref or ctx).get("hf_median") or 0.0
    hf_thr = max(track_qc.HF_ABS_MIN, track_qc.HF_REL_FACTOR * hf_ref)
    if m["hf_median"] >= hf_thr:
        where = ""
        blocks = m.get("hf_blocks") or []
        hot = [i for i, v in enumerate(blocks) if v >= hf_thr]
        if hot:
            peak = int(np.argmax(blocks))
            where = (f", xuất hiện từ {_fmt(hot[0] * 10)}, nặng nhất quanh "
                     f"{_fmt(peak * 10)}–{_fmt(peak * 10 + 10)}")
        add("noise", "error", f"Rè/xì — tạp âm cao tần {m['hf_median']:.1f}% "
                              f"({who} ~{hf_ref:.1f}%){where}.")

    runs = m.get("clip_runs", 0)
    base = (ref or ctx).get("clip_runs") or 0
    if runs >= CLIP_RUNS_ERR and runs >= CLIP_REL_FACTOR * max(base, 1):
        add("clipping", "error", f"Méo tiếng — {runs} chỗ đỉnh sóng bị dẹt "
                                 f"(từ {_fmt(m.get('clip_first') or 0)}; {who}: {base}).")

    if m["gap_max"] >= track_qc.DROPOUT_SEC and not (
            ref and ref["gap_max"] >= m["gap_max"] - DROPOUT_SLACK):
        add("dropout", "error", f"Mất tiếng giữa bài {m['gap_max']:.0f} giây "
                                f"tại {_fmt(m.get('gap_at') or 0)}.")

    ref_db = ctx.get("rms_db")
    if ref_db is not None:
        d = m["rms_db"] - ref_db
        if d >= track_qc.LOUD_REL_DB:
            add("loud", "warning", f"To hơn các bản khác (+{d:.1f} dB).")
        elif d <= track_qc.QUIET_REL_DB:
            add("quiet", "warning", f"Nhỏ hơn các bản khác ({d:.1f} dB).")

    if m["end_rel_db"] > track_qc.ABRUPT_END_REL_DB and not (
            ref and ref["end_rel_db"] > track_qc.ABRUPT_END_REL_DB):
        add("abrupt_end", "warning", "Kết thúc đột ngột (giây cuối vẫn to).")
    return out


def _context(metrics: list[dict]) -> dict:
    if not metrics:
        return {"hf_median": 0.0, "rms_db": None, "clip_runs": 0}
    return {"hf_median": float(np.median([m["hf_median"] for m in metrics])),
            "rms_db": float(np.median([m["rms_db"] for m in metrics])),
            "clip_runs": float(np.median([m.get("clip_runs", 0) for m in metrics]))}


# ══════════════════════════════════════════════════════════════════
# Báo cáo theo project
# ══════════════════════════════════════════════════════════════════
def _path(project_id: int, project_name: Optional[str]) -> Path:
    from backend.video import config
    return config.project_dir(project_id, project_name) / QC_FILE


def load(project_id: int, project_name: Optional[str]) -> dict:
    try:
        data = json.loads(_path(project_id, project_name).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k in ("covers", "originals"):
                data.setdefault(k, {})
            for k in ("manual_flags", "dismissed"):
                data.setdefault(k, [])
            return data
    except Exception:       # noqa: BLE001 — chưa có / hỏng → báo cáo trống
        pass
    return {"covers": {}, "originals": {}, "manual_flags": [], "dismissed": []}


def _save(project_id: int, project_name: Optional[str], data: dict) -> None:
    p = _path(project_id, project_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _key(path: str) -> Optional[str]:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return f"{st.st_size}:{int(st.st_mtime)}"


def evaluate(project_id: int, project_name: Optional[str],
             covers: list[tuple[int, str, Optional[int]]],
             originals: dict[int, str]) -> dict[int, dict]:
    """covers = [(track_id, file_path, source_id)], originals = {source_id: file_path}.
    Trả {track_id: {status: ok|warning|error|pending, issues, manual_flag, dismissed}}."""
    with _lock:
        data = load(project_id, project_name)
    cached = data["covers"]
    manual = {int(x) for x in data["manual_flags"]}
    dismissed = {int(x) for x in data["dismissed"]}
    ctx = _context([e["metrics"] for e in cached.values() if e.get("metrics")])
    out: dict[int, dict] = {}
    for tid, fpath, sid in covers:
        e = cached.get(str(tid)) or {}
        res = {"manual_flag": tid in manual, "dismissed": tid in dismissed, "issues": []}
        if e.get("file_key") != _key(fpath):
            res["status"] = "error" if res["manual_flag"] else "pending"
            out[tid] = res
            continue
        if e.get("metrics"):
            o = data["originals"].get(str(sid)) if sid is not None else None
            ref = (o["metrics"] if o and o.get("metrics") and sid in originals
                   and o.get("path") == originals[sid]
                   and o.get("file_key") == _key(originals[sid]) else None)
            res["issues"] = judge(e["metrics"], ref, ctx)
            res["compared_with"] = "original" if ref else "others"
        else:
            res["issues"] = [{"code": "unreadable", "level": "error",
                              "message": e.get("error") or "Không đọc được file."}]
        has_err = any(i["level"] == "error" for i in res["issues"])
        if res["manual_flag"] or (has_err and not res["dismissed"]):
            res["status"] = "error"
        elif res["dismissed"]:
            res["status"] = "ok"
        else:
            res["status"] = "warning" if res["issues"] else "ok"
        out[tid] = res
    return out


def _measure_all(items: list[tuple[str, str]], cache: dict, measure,
                 keep_path: bool = False) -> dict:
    """items = [(key, path)] → {key: {file_key, metrics|error[, path]}} (giữ cache)."""
    out = {}
    for k, path in items:
        fk = _key(path)
        prev = cache.get(k) or {}
        if fk is None:
            continue
        if prev.get("file_key") == fk and (prev.get("metrics") or prev.get("error")) \
                and (not keep_path or prev.get("path") == path):
            out[k] = prev
            continue
        entry = {"file_key": fk, **({"path": path} if keep_path else {})}
        try:
            entry["metrics"] = measure(path)
        except Exception as e:      # noqa: BLE001 — 1 file hỏng không chặn cả lượt
            entry["error"] = f"Không đọc được: {e}"
        out[k] = entry
    return out


def review(project_id: int, *, engine=None,
           measure: Callable[[str], dict] = measure_file) -> None:
    """Đo mọi bản cover (Track) của project + bài gốc của chúng (nếu có file)."""
    from sqlmodel import Session, select
    from backend.models import CoverSong, Project, SourceSong, Track
    if engine is None:
        from backend.database import engine
    with Session(engine) as s:
        project = s.get(Project, project_id)
        if project is None:
            return
        name = project.name
        tracks = s.exec(select(Track).where(Track.project_id == project_id)).all()
        link = {c.track_id: c.source_id for c in s.exec(select(CoverSong).where(
            CoverSong.project_id == project_id)).all()}
        sids = {sid for sid in link.values() if sid is not None}
        origs = {}
        for src in s.exec(select(SourceSong).where(SourceSong.project_id == project_id)).all():
            candidate = src.tempo_file_path or src.file_path
            if src.id in sids and candidate and Path(candidate).exists():
                origs[src.id] = candidate
        covers = [(str(t.id), t.filepath) for t in tracks]
    with _lock:
        old = load(project_id, name)
    new_covers = _measure_all(covers, old["covers"], measure)
    new_origs = _measure_all([(str(k), v) for k, v in origs.items()],
                             old["originals"], measure, keep_path=True)
    with _lock:
        data = load(project_id, name)      # đọc lại: có thể vừa bấm Báo lỗi / Nghe ổn
        alive = {int(k) for k, _ in covers}
        data["covers"] = new_covers
        data["originals"] = new_origs
        data["manual_flags"] = sorted(x for x in map(int, data["manual_flags"]) if x in alive)
        data["dismissed"] = sorted(x for x in map(int, data["dismissed"]) if x in alive)
        data["analyzed_at"] = _now()
        _save(project_id, name, data)


def set_mark(project_id: int, project_name: Optional[str], track_id: int,
             *, flagged: Optional[bool] = None, dismissed: Optional[bool] = None) -> None:
    """🚩 Báo lỗi (flagged) / ✓ Nghe ổn (dismissed) — hai cờ loại trừ nhau."""
    with _lock:
        data = load(project_id, project_name)
        flags = {int(x) for x in data["manual_flags"]}
        ok = {int(x) for x in data["dismissed"]}
        if flagged is not None:
            (flags.add if flagged else flags.discard)(track_id)
            if flagged:
                ok.discard(track_id)
        if dismissed is not None:
            (ok.add if dismissed else ok.discard)(track_id)
            if dismissed:
                flags.discard(track_id)
        data["manual_flags"], data["dismissed"] = sorted(flags), sorted(ok)
        _save(project_id, project_name, data)


# ══════════════════════════════════════════════════════════════════
# Chạy nền
# ══════════════════════════════════════════════════════════════════
def is_running(project_id: int) -> bool:
    with _run_lock:
        return project_id in _running


def review_async(project_id: int) -> None:
    """Kiểm tra nền (sau khi nhập bản cover). Đang chạy → chạy thêm 1 lượt khi xong
    để bắt cả file vừa nhập."""
    with _run_lock:
        if project_id in _running:
            _again.add(project_id)
            return
        _running.add(project_id)

    def _run():
        try:
            while True:
                try:
                    review(project_id)
                except Exception:       # noqa: BLE001 — kiểm tra là bước phụ
                    pass
                with _run_lock:
                    if project_id not in _again:
                        _running.discard(project_id)
                        return
                    _again.discard(project_id)
        except BaseException:
            with _run_lock:
                _running.discard(project_id)
            raise

    threading.Thread(target=_run, name=f"cover-qc-{project_id}", daemon=True).start()
