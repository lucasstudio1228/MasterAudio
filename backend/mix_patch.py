"""
mix_patch.py — Tra "phút X của bản mix là bài nào" + VÁ MIX sau khi thay bài.

• timeline(project_id): đọc mix_info.json của bản mix mới nhất → từng đoạn
  [start, end] ↔ track_id + trạng thái rà soát (track_qc). Mỗi bài thường lặp
  2–3 lần trong mix 120', nên 1 bài lỗi = nhiều đoạn lỗi.
• start_patch(project_id): render lại theo ĐÚNG thứ tự + tên đoạn của bản mix
  cũ, chỉ trỏ các đoạn của bài đã thay sang file mới (core_bridge.run_patch_mix_job).
  Xong → nếu final.mp4 được dựng từ bản mix cũ thì CHỈ thay track âm thanh của
  nó (copy hình, không dựng lại video).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, select

from backend.database import engine
from backend.models import Mix, Project, Track

_ROOT = Path(__file__).parent.parent
OUTPUTS_ROOT = _ROOT / "outputs"
DB_PATH = str(_ROOT / "data" / "app.db")
SYNC_TOLERANCE_SEC = 5.0


class PatchError(RuntimeError):
    pass


def _info_path(mix: Mix) -> Optional[Path]:
    if not mix.output_dir:
        return None
    p = Path(mix.output_dir) / "mix_info.json"
    return p if p.exists() else None


def latest_mix(session: Session, project_id: int) -> Optional[Mix]:
    """Bản mix hoàn tất mới nhất có mốc thời gian từng bài."""
    for m in session.exec(select(Mix).where(
            Mix.project_id == project_id, Mix.status == "completed")
            .order_by(Mix.created_at.desc())).all():
        if _info_path(m):
            return m
    return None


def _replacement_chain(report: dict) -> dict[str, str]:
    """old_filename → new_filename (theo lịch sử thay bài)."""
    return {r["old_filename"]: r["new_filename"]
            for r in report.get("replaced", [])
            if r.get("old_filename") and r.get("new_filename")}


def _resolve(fname: str, current: dict[str, Track], chain: dict[str, str]):
    """Theo chuỗi thay thế tới Track đang có. Trả (track, đã_bị_thay)."""
    seen, name = set(), fname
    while name not in current and name in chain and name not in seen:
        seen.add(name)
        name = chain[name]
    return current.get(name), name != fname


def timeline(project_id: int) -> Optional[dict]:
    from backend.video import track_qc

    with Session(engine) as s:
        project = s.get(Project, project_id)
        if project is None:
            return None
        mix = latest_mix(s, project_id)
        if mix is None:
            return None
        data = json.loads(_info_path(mix).read_text(encoding="utf-8"))
        tracks = s.exec(select(Track).where(Track.project_id == project_id)).all()
        current = {t.filename: t for t in tracks}
        report = track_qc.load_report(project_id, project.name)
        mix_id, title = mix.id, mix.title
    chain = _replacement_chain(report)
    qc = report.get("tracks", {})

    segs, outdated = [], 0
    for e in data.get("tracks", []):
        src = e.get("source_file", "")
        t, replaced = _resolve(src, current, chain)
        entry = qc.get(str(t.id)) if t else None
        status = "missing" if t is None else (
            "replaced" if replaced else (entry or {}).get("status", "unknown"))
        if replaced or t is None:
            outdated += 1
        segs.append({
            "index": e.get("index"),
            "name": e.get("name"),
            "start": round(float(e.get("start_seconds", 0)), 2),
            "end": round(float(e.get("end_seconds", 0)), 2),
            "source_file": src,
            "track_id": t.id if t else None,
            "track_filename": t.filename if t else None,
            "status": status,
            "issues": [i["message"] for i in (entry or {}).get("issues", [])]
                      if not replaced else [],
        })
    return {
        "mix_id": mix_id, "mix_title": title,
        "total_seconds": float(data.get("audio", {}).get("total_duration_seconds", 0)),
        "crossfade_seconds": float(data.get("audio", {}).get("crossfade_seconds", 0)),
        "segments": segs,
        "outdated_segments": outdated,
    }


def start_patch(project_id: int) -> int:
    """Submit job vá mix lên job_manager (worker mix). Trả mix_id mới."""
    from backend.job_manager import job_manager
    from backend import core_bridge
    from backend.video import track_qc

    if job_manager.is_busy():
        raise PatchError("Đang có 1 mix render khác — đợi xong rồi vá.")
    with Session(engine) as s:
        project = s.get(Project, project_id)
        if project is None:
            raise PatchError(f"Project {project_id} không tồn tại")
        base = latest_mix(s, project_id)
        if base is None:
            raise PatchError("Chưa có bản mix hoàn tất nào để vá.")
        data = json.loads(_info_path(base).read_text(encoding="utf-8"))
        current = {t.filename: t for t in s.exec(
            select(Track).where(Track.project_id == project_id)).all()}
        chain = _replacement_chain(track_qc.load_report(project_id, project.name))

        seq, names, missing, changed = [], [], [], set()
        for e in data.get("tracks", []):
            src = e.get("source_file", "")
            t, replaced = _resolve(src, current, chain)
            if t is None:
                missing.append(src)
                continue
            if replaced:
                changed.add(src)
            seq.append(t.filepath)
            names.append(e.get("name") or "")
        if missing:
            raise PatchError(
                "Bản mix có bài đã bị gỡ mà chưa có bài thay: "
                + ", ".join(sorted(set(missing))) + ". Hãy «Tạo lại Suno» trước.")
        if not changed:
            raise PatchError("Bản mix mới nhất đã dùng đúng các bài hiện tại — "
                             "không có gì để vá.")

        audio = data.get("audio", {})
        base_total = float(audio.get("total_duration_seconds")
                           or base.total_duration_seconds or 0)
        sr = int(audio.get("sample_rate") or base.sample_rate or 48000)
        bd = int(audio.get("bit_depth") or base.bit_depth or 32)
        xf = float(audio.get("crossfade_seconds") or base.crossfade_seconds or 5.0)

        mix = Mix(project_id=project_id,
                  title=f"Vá mix #{base.id} (thay {len(changed)} bài)",
                  status="pending", duration_minutes=base.duration_minutes,
                  crossfade_seconds=xf, sample_rate=sr, bit_depth=bd)
        s.add(mix); s.commit(); s.refresh(mix)
        out_dir = OUTPUTS_ROOT / str(project_id) / str(mix.id)
        mix.output_dir = str(out_dir)
        s.add(mix); s.commit()
        mix_id, base_id = mix.id, base.id

    from backend.routers.mixes import _on_failure, _on_cancel
    job_manager.submit(
        mix_id, core_bridge.run_patch_mix_job,
        seq, names, str(out_dir), xf, sr, bd, DB_PATH, base_total,
        on_success=partial(_on_patch_success, project_id=project_id,
                           base_total=base_total, base_mix_id=base_id),
        on_failure=_on_failure, on_cancel=_on_cancel,
    )
    return mix_id


def _on_patch_success(mix_id: int, result: dict, *, project_id: int,
                      base_total: float, base_mix_id: int) -> None:
    with Session(engine) as s:
        mix = s.get(Mix, mix_id)
        if mix is None:
            return
        mix.status = "completed"
        mix.completed_at = datetime.now(timezone.utc)
        mix.total_duration_seconds = result.get("total_duration_seconds")
        mix.track_count = result.get("track_count")
        mix.output_dir = result.get("output_dir")
        s.add(mix); s.commit()
        project = s.get(Project, project_id)
        pname = project.name if project else None
        wav = Path(mix.output_dir) / "mix.wav"
    from backend.cover.mix_export import export_mix_audio
    export_mix_audio(project_id, pname, wav)
    try:
        from backend.video import track_qc
        track_qc.clear_needs_remix(project_id, pname, mix_id)
    except Exception:       # noqa: BLE001
        pass
    # Video đã dựng từ bản mix cũ → chỉ thay nhạc (không chen nếu worker video bận).
    try:
        from backend.video import config as vconfig
        from backend.video.job_manager import video_job_manager
        final = vconfig.final_dir(project_id, pname) / "final.mp4"
        if final.exists() and not video_job_manager.is_busy():
            video_job_manager.submit(project_id, "remux", replace_final_audio,
                                     str(wav), pname, base_total)
    except Exception:       # noqa: BLE001
        pass


def video_in_sync(final: Path, base_total: float) -> bool:
    from backend.video.assembler import probe_duration
    try:
        return abs(probe_duration(str(final)) - base_total) <= SYNC_TOLERANCE_SEC
    except Exception:       # noqa: BLE001
        return False


def probe_or(path: Path, default: float = 0.0) -> float:
    from backend.video.assembler import probe_duration
    try:
        return float(probe_duration(str(path)))
    except Exception:       # noqa: BLE001
        return default


def replace_final_audio(project_id: int, progress_cb: Callable,
                        audio_path: str, project_name: Optional[str],
                        base_total: Optional[float] = None) -> dict:
    """Thay track âm thanh của final.mp4 bằng bản mix đã vá (hình giữ nguyên,
    -c:v copy). Chỉ làm khi video được dựng từ bản mix cũ (độ dài khớp) —
    ngược lại video đang dùng nhạc khác, không tự đè."""
    from backend.video import config as vconfig
    final = vconfig.final_dir(project_id, project_name) / "final.mp4"
    if not final.exists():
        raise PatchError("Chưa có final.mp4 — không cần thay nhạc.")
    if base_total is not None and not video_in_sync(final, base_total):
        progress_cb("final.mp4 không được dựng từ bản mix vừa vá (độ dài khác) — "
                    "bỏ qua thay nhạc. Hãy ghép lại video nếu cần.", 100)
        return {"skipped": True}
    tmp = final.with_name("_final_patched.mp4")
    # Lần trước đã render xong nhưng không thay được (final.mp4 bị khoá) →
    # dùng lại, chỉ cần đổi tên (khỏi ghép lại ~2 phút).
    reuse = (tmp.exists() and tmp.stat().st_mtime > Path(audio_path).stat().st_mtime
             and video_in_sync(tmp, probe_or(final)))
    if reuse:
        progress_cb("Đã có bản video thay nhạc từ lần trước — chỉ thay file…", 80)
    else:
        progress_cb("Thay nhạc trong final.mp4 (giữ nguyên hình, không dựng lại)…", 10)
        cmd = ["ffmpeg", "-y", "-i", str(final), "-i", str(audio_path),
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "320k", "-shortest",
               "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
            raise PatchError(f"ffmpeg thay nhạc lỗi: {r.stderr.strip()[-400:]}")
    # Windows: final.mp4 đang mở trong VLC/trình phát → WinError 5. Thử lại vài
    # lần; vẫn khoá thì GIỮ _final_patched.mp4 và báo người dùng đóng trình phát.
    for i in range(6):
        try:
            os.replace(tmp, final)
            break
        except PermissionError:
            if i == 5:
                raise PatchError(
                    "final.mp4 đang được mở bởi chương trình khác (VLC/trình phát "
                    "video/Explorer xem trước) nên không thay được. Đóng nó rồi bấm "
                    "«🎬 Thay nhạc trong final.mp4» — bản đã ghép nhạc mới được giữ "
                    "lại, lần sau chỉ đổi tên (vài giây).")
            time.sleep(5)
    progress_cb("Đã thay nhạc trong final.mp4. Bản nháp YouTube đã đăng trước "
                "đó (nếu có) vẫn là nhạc cũ — cần đăng nháp lại.", 100)
    return {"output": str(final)}
