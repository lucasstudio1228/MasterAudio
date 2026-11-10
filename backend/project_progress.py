"""Tình trạng từng project trên trang danh sách: đã xong / đang ở giai đoạn nào.

Chuỗi 1 sản phẩm: Nhạc (Suno hoặc import) → Mix → Ảnh → Clip → Video final →
Nháp YouTube → Short. Mỗi bước được chấm từ dữ liệu THẬT (DB + file trên đĩa +
job đang chạy trong bộ nhớ), không tin cờ "đã chạy". Chỉ đọc — không tạo thư
mục, không ffprobe (gọi mỗi vài giây cho mọi project).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from backend.models import (BatchRun, Mix, Project, SunoBatch, Track,
                            YoutubeUpload)

STEPS = (
    ("music", "Nhạc"),
    ("mix", "Mix"),
    ("images", "Ảnh"),
    ("clips", "Clip"),
    ("final", "Video"),
    ("youtube", "Nháp YT"),
    ("short", "Short"),
)
_LABEL = dict(STEPS)
# Tên đầy đủ cho dòng trạng thái.
_DOING = {
    "music": "tạo nhạc", "mix": "render mix", "images": "tạo ảnh",
    "clips": "tạo clip", "final": "ghép video", "youtube": "lưu nháp YouTube",
    "short": "tạo & lưu nháp Short",
}
# Job video (kind) → các bước nó bao trùm, theo thứ tự chạy.
_KIND_STEPS = {
    "suno": ("music",), "suno-fix": ("music",), "suno-login": (),
    "images": ("images",), "clips": ("clips",), "assemble": ("final",),
    "rebuild": ("final",), "remux": ("final",), "retitle": ("final",),
    "full": ("images", "clips", "final", "youtube", "short"),
    "continue": ("images", "clips", "final", "youtube", "short"),
    "upload": ("youtube", "short"), "upload_short": ("short",),
}
_ACTIVE = ("pending", "running", "paused")
_SUNO_DONE = {"IMPORTED", "DOWNSTREAM_RUNNING", "COMPLETED"}
_SUNO_BLOCKED = {
    "WAITING_FOR_LOGIN": "chờ đăng nhập Suno",
    "WAITING_FOR_HUMAN": "chờ người xử lý trên Suno",
    "SUBMISSION_UNCERTAIN": "không chắc Suno đã nhận lệnh — cần kiểm tra",
    "INSUFFICIENT_GENERATION_CREDITS": "hết credit Suno",
    "INSUFFICIENT_DOWNLOAD_ALLOWANCE": "hết lượt tải Suno",
    "UI_CHANGED": "giao diện Suno đổi",
    "PAUSED": "tạm dừng",
}


def _count(folder: Path, pattern: str) -> int:
    try:
        return sum(1 for _ in folder.glob(pattern)) if folder.is_dir() else 0
    except OSError:
        return 0


def _short_text(text: Optional[str], limit: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _latest_batch(session: Session, pid: int) -> Optional[SunoBatch]:
    return session.exec(select(SunoBatch).where(SunoBatch.project_id == pid)
                        .order_by(SunoBatch.id.desc())).first()


def project_progress(session: Session, project: Project, *, video_job=None,
                     mix_jobs=None, params=None, auto_short: bool = True,
                     queued_in_batch: bool = False) -> dict:
    from backend.video import config as vconfig

    pid, name = project.id, project.name
    params = params or vconfig.PARAMS
    steps = {key: {"key": key, "label": label, "state": "pending", "detail": ""}
             for key, label in STEPS}

    # ── Nhạc ──
    tracks = len(session.exec(select(Track.id).where(Track.project_id == pid)).all())
    batch = _latest_batch(session, pid) if project.music_source == "suno" else None
    music = steps["music"]
    if batch is not None:
        target = batch.target_tracks or 15
        got = max(batch.imported_tracks, batch.downloaded_tracks)
        if batch.phase in _SUNO_DONE and batch.imported_tracks > 0:
            music.update(state="done", detail=f"{batch.imported_tracks} bài Suno")
        elif tracks >= target:
            # Đã import đủ bài; phase cũ kẹt (vd DOWNLOADING khi server tắt giữa lúc
            # tạo lại bài lẻ) không làm project "dừng ở bước Nhạc".
            music.update(state="done", detail=f"{tracks} bài Suno")
        elif batch.phase in _SUNO_BLOCKED:
            music.update(state="blocked",
                         detail=f"Suno: {_SUNO_BLOCKED[batch.phase]} ({got}/{target} bài)")
        elif batch.phase == "FAILED":
            music.update(state="error", detail=_short_text(
                batch.error_message or batch.message or "Suno lỗi"))
        elif batch.phase == "CANCELLED":
            music.update(state="done" if tracks else "stopped",
                         detail=f"{tracks} bài" if tracks else "đã huỷ batch Suno")
        else:
            music.update(state="waiting", detail=f"Suno {got}/{target} bài")
    elif tracks:
        music.update(state="done", detail=f"{tracks} bài")

    # ── Mix ──
    mixes = session.exec(select(Mix).where(Mix.project_id == pid)
                         .order_by(Mix.id.desc())).all()
    mix_step = steps["mix"]
    running_mix = None
    for m in mixes:
        job = (mix_jobs or {}).get(m.id)
        status = job.status if job else m.status
        if status in _ACTIVE:
            running_mix = (m, job)
            break
    done_mix = next((m for m in mixes if m.status == "completed" and m.output_dir
                     and (Path(m.output_dir) / "mix.wav").exists()), None)
    if running_mix is not None:
        m, job = running_mix
        pct = job.percent if job else 0.0
        mix_step.update(state="paused" if job and job.status == "paused" else "running",
                        percent=pct,
                        detail=(f"Bước {job.step}/6 · {job.step_name}"
                                if job and job.step_name else "đang xếp hàng"))
    elif done_mix is not None:
        mins = (done_mix.total_duration_seconds or 0) / 60
        mix_step.update(state="done", detail=f"{mins:.0f} phút" if mins else "")
    elif mixes and mixes[0].status == "failed":
        mix_step.update(state="error", detail=_short_text(mixes[0].error_message or "Mix lỗi"))

    # ── Ảnh / clip / final ──
    n_img = _count(vconfig.images_dir(pid, name), "*.png")
    n_clip = _count(vconfig.clips_dir(pid, name), "clip_*.mp4")
    final_dir = vconfig.final_dir(pid, name)
    need_img, need_clip = params.image_count, params.total_clips
    steps["images"]["detail"] = f"{n_img}/{need_img}"
    steps["clips"]["detail"] = f"{n_clip}/{need_clip}"
    if n_img >= need_img:
        steps["images"]["state"] = "done"
    if n_clip >= need_clip:
        steps["clips"]["state"] = "done"
    if (final_dir / "final.mp4").exists():
        steps["final"].update(state="done", detail="final.mp4")

    # ── YouTube ──
    ups = session.exec(select(YoutubeUpload).where(YoutubeUpload.project_id == pid)
                       .order_by(YoutubeUpload.id.desc())).all()
    long_ups = [u for u in ups if not (u.video_path or "").endswith("short.mp4")]
    short_ups = [u for u in ups if (u.video_path or "").endswith("short.mp4")]
    for key, rows in (("youtube", long_ups), ("short", short_ups)):
        draft = next((u for u in rows if (u.status or "") == "draft"), None)
        if draft is not None:
            steps[key].update(state="done", detail=draft.channel_name or "nháp")
        elif rows and rows[0].error_message:
            steps[key].update(state="error", detail=_short_text(rows[0].error_message))
    if not auto_short and steps["short"]["state"] == "pending":
        steps["short"].update(state="skipped", detail="tắt tự tạo Short")

    # ── Job video đang chạy / vừa lỗi → gắn vào bước nó đang làm ──
    vjob = video_job
    if vjob is not None:
        covered = _KIND_STEPS.get(vjob.kind, ())
        target_key = next((k for k in covered if steps[k]["state"] != "done"),
                          covered[-1] if covered else None)
        if target_key and vjob.status in _ACTIVE:
            steps[target_key].update(
                state="paused" if vjob.status == "paused"
                else ("queued" if vjob.status == "pending" else "running"),
                percent=float(vjob.percent or 0.0),
                detail=_short_text(vjob.message) or steps[target_key]["detail"])
        elif target_key and vjob.status == "failed" \
                and steps[target_key]["state"] != "done":
            steps[target_key].update(state="error", detail=_short_text(vjob.error))

    # ── Tổng hợp 1 dòng ──
    order = [steps[k] for k, _ in STEPS]
    live = next((s for s in order if s["state"] in ("running", "paused", "queued")), None)
    todo = [s for s in order if s["state"] not in ("done", "skipped")]
    done_n = sum(1 for s in order if s["state"] in ("done", "skipped"))
    if live is not None:
        verb = {"running": "Đang", "paused": "Tạm dừng", "queued": "Xếp hàng"}[live["state"]]
        state = "running" if live["state"] == "running" else live["state"]
        summary = f"{verb} {_DOING[live['key']]}"
        if live.get("detail"):
            summary += f" — {live['detail']}"
        stage = live["key"]
    elif not todo:
        state, stage = "done", "short"
        summary = "Hoàn tất — đã lưu nháp YouTube" + (" + Short" if auto_short else "")
    else:
        first = todo[0]
        stage = first["key"]
        if first["state"] == "error":
            state = "error"
            summary = f"Lỗi ở bước {_LABEL[stage]}: {first['detail']}"
        elif first["state"] == "blocked":
            state = "blocked"
            summary = f"Chờ xử lý — {first['detail']}"
        elif first["state"] == "waiting":
            state = "stopped"
            summary = f"Dừng giữa chừng ở bước {_LABEL[stage]} ({first['detail']})"
        elif queued_in_batch and done_n == 0:
            state = "queued"
            summary = "Chờ tới lượt trong lô sản xuất hàng loạt"
        elif done_n == 0:
            state = "new"
            summary = "Chưa bắt đầu"
        else:
            last = max((s for s in order if s["state"] in ("done", "skipped")),
                       key=lambda s: [k for k, _ in STEPS].index(s["key"]))
            state = "stopped"
            summary = f"Đã xong {_LABEL[last['key']]} · tiếp theo: {_LABEL[stage]}"
    return {"project_id": pid, "state": state, "stage": stage,
            "stage_label": _LABEL[stage], "summary": summary,
            "percent": live.get("percent") if live else None,
            "done_steps": done_n, "total_steps": len(STEPS), "steps": order}


def all_progress(session: Session) -> list[dict]:
    """Tình trạng mọi project (trừ project trong Thùng rác)."""
    from backend.job_manager import job_manager
    from backend.video import config as vconfig
    from backend.video.job_manager import video_job_manager
    try:
        from backend.video import shorts
        auto_short = bool(shorts.settings()["auto_short"])
    except Exception:            # noqa: BLE001
        auto_short = True
    with job_manager._lock:
        mix_jobs = dict(job_manager._jobs)
    with video_job_manager._lock:
        video_jobs = dict(video_job_manager._jobs)
    queued: set[int] = set()
    try:
        from backend import batch_service
        alive = {k for k, t in dict(batch_service._threads).items() if t.is_alive()}
    except Exception:            # noqa: BLE001
        alive = set()
    # Chỉ lô có thread orchestrator còn sống: DB có thể còn "running" cũ sau khi
    # server tắt giữa chừng (lô đó thực tế đã dừng).
    for run in session.exec(select(BatchRun).where(BatchRun.status == "running")).all():
        if run.batch_key not in alive:
            continue
        for p in session.exec(select(Project).where(Project.batch_id == run.batch_key)).all():
            if p.id != run.current_project_id:
                queued.add(p.id)
    out = []
    for project in session.exec(select(Project)).all():
        if getattr(project, "deleted_at", None) is not None:
            continue
        try:
            out.append(project_progress(
                session, project, video_job=video_jobs.get(project.id),
                mix_jobs=mix_jobs, params=vconfig.PARAMS, auto_short=auto_short,
                queued_in_batch=project.id in queued))
        except Exception as exc:     # noqa: BLE001 — 1 project lỗi không làm hỏng cả trang
            out.append({"project_id": project.id, "state": "unknown", "stage": "music",
                        "stage_label": "?", "summary": f"Không đọc được tình trạng: {exc}",
                        "percent": None, "done_steps": 0, "total_steps": len(STEPS),
                        "steps": []})
    return out
