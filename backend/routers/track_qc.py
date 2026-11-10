"""
Rà soát từng track lẻ sau khi mix + "Tạo lại Suno" cho bài lỗi + vá mix.

  GET  /api/projects/{pid}/qc                    báo cáo + dòng thời gian mix + job
  POST /api/projects/{pid}/qc/analyze            đo lại (cache theo file)
  POST /api/projects/{pid}/qc/tracks/{tid}/flag  người dùng tự đánh dấu lỗi
  GET  /api/projects/{pid}/qc/tracks/{tid}/audio nghe thử bài lẻ
  POST /api/projects/{pid}/qc/regenerate         job "suno-fix" (TỐN credit nếu Create)
  POST /api/projects/{pid}/qc/patch-mix          render lại mix theo thứ tự cũ
  POST /api/projects/{pid}/qc/remux              thay nhạc trong final.mp4
  POST /api/projects/{pid}/qc/auto               bật/tắt TỰ tạo lại Suno sau mix
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.database import get_session
from backend.job_manager import job_manager
from backend.models import Mix, Project, SunoCandidate, Track
from backend import mix_patch
from backend.video import track_qc, suno_fix, qc_autofix
from backend.video.job_manager import video_job_manager

router = APIRouter(prefix="/api/projects/{project_id}/qc", tags=["track-qc"])

_JOB_KINDS = ("suno-fix", "remux")


class FlagBody(BaseModel):
    flagged: bool = True


class AutoBody(BaseModel):
    enabled: bool = True


class RegenerateBody(BaseModel):
    track_ids: list[int]
    max_creates_per_track: int = Field(default=suno_fix.DEFAULT_MAX_CREATES_PER_TRACK,
                                       ge=0, le=3)


def _project(db: Session, pid: int) -> Project:
    p = db.get(Project, pid)
    if not p or getattr(p, "deleted_at", None):
        raise HTTPException(404, f"Project {pid} không tồn tại")
    return p


def _job(pid: int):
    j = video_job_manager.get(pid)
    if j is None or j.kind not in _JOB_KINDS:
        return None
    return {"kind": j.kind, "status": j.status, "message": j.message,
            "percent": j.percent, "error": j.error, "result": j.result}


@router.get("")
def get_qc(project_id: int, db: Session = Depends(get_session)):
    p = _project(db, project_id)
    report = track_qc.load_report(project_id, p.name)
    tracks = db.exec(select(Track).where(Track.project_id == project_id)
                     .order_by(Track.added_at)).all()
    suno = {c.track_id: c for c in db.exec(select(SunoCandidate).where(
        SunoCandidate.project_id == project_id,
        SunoCandidate.track_id != None)).all()}      # noqa: E711
    spares = {x["track_id"]: x["spares"]
              for x in suno_fix.fix_plan(project_id, [t.id for t in tracks if t.id in suno])}
    qc = report.get("tracks", {})
    rows = []
    for t in tracks:
        e = qc.get(str(t.id)) or {}
        rows.append({
            "track_id": t.id, "filename": t.filename,
            "duration": t.duration_seconds,
            "status": e.get("status", "unknown"),
            "issues": e.get("issues", []),
            "manual_flag": bool(e.get("manual_flag")),
            "is_suno": t.id in suno,
            "request_id": suno[t.id].request_id if t.id in suno else None,
            "free_spares": spares.get(t.id, 0),
        })
    tl = mix_patch.timeline(project_id)
    return {
        "analyzed_at": report.get("analyzed_at"),
        "context": report.get("context"),
        "tracks": rows,
        "replaced": report.get("replaced", []),
        "timeline": tl,
        "job": _job(project_id),
        "video_busy": video_job_manager.is_busy(),
        "mix_busy": job_manager.is_busy(),
        "credits_per_create": suno_fix.CREDITS_PER_CREATE,
        "auto_regen": qc_autofix.auto_enabled(report),
        "auto_fix": report.get("auto_fix"),
        "auto_active": qc_autofix.is_active(project_id),
        "auto_limits": {"max_tracks": qc_autofix.AUTO_MAX_TRACKS,
                        "max_creates_per_track": qc_autofix.AUTO_MAX_CREATES_PER_TRACK},
    }


@router.post("/auto")
def set_auto(project_id: int, body: AutoBody, db: Session = Depends(get_session)):
    p = _project(db, project_id)
    qc_autofix.set_auto_enabled(project_id, p.name, body.enabled)
    return {"ok": True, "auto_regen": body.enabled}


@router.post("/analyze")
def analyze(project_id: int, db: Session = Depends(get_session)):
    _project(db, project_id)
    try:
        track_qc.review_project(project_id)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"Rà soát lỗi: {e}")
    return {"ok": True}


@router.post("/tracks/{track_id}/flag")
def flag(project_id: int, track_id: int, body: FlagBody,
         db: Session = Depends(get_session)):
    p = _project(db, project_id)
    t = db.get(Track, track_id)
    if t is None or t.project_id != project_id:
        raise HTTPException(404, "Track không thuộc project")
    track_qc.set_manual_flag(project_id, p.name, track_id, body.flagged)
    return {"ok": True}


@router.get("/tracks/{track_id}/audio")
def track_audio(project_id: int, track_id: int, db: Session = Depends(get_session)):
    t = db.get(Track, track_id)
    if t is None or t.project_id != project_id or not Path(t.filepath).exists():
        raise HTTPException(404, "Không thấy file track")
    return FileResponse(t.filepath, media_type="audio/wav", filename=t.filename)


@router.post("/regenerate")
def regenerate(project_id: int, body: RegenerateBody,
               db: Session = Depends(get_session)):
    _project(db, project_id)
    if not body.track_ids:
        raise HTTPException(400, "Chưa chọn bài nào.")
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ (video/suno) chạy. Vui lòng đợi.")
    plan = suno_fix.fix_plan(project_id, body.track_ids)
    bad = [x["track_id"] for x in plan if not x["suno"]]
    if bad:
        raise HTTPException(400, f"Track {bad} không phải bài Suno của project — "
                                 "không tạo lại được.")
    video_job_manager.submit(project_id, "suno-fix", suno_fix.replace_tracks,
                             track_ids=list(dict.fromkeys(body.track_ids)),
                             max_creates_per_track=body.max_creates_per_track)
    return {"ok": True, "plan": plan}


@router.post("/patch-mix")
def patch_mix(project_id: int, db: Session = Depends(get_session)):
    _project(db, project_id)
    try:
        mix_id = mix_patch.start_patch(project_id)
    except mix_patch.PatchError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "mix_id": mix_id}


@router.post("/remux")
def remux(project_id: int, db: Session = Depends(get_session)):
    """Thay nhạc final.mp4 bằng bản vá mới nhất (khi lúc vá xong worker video bận)."""
    p = _project(db, project_id)
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ (video/suno) chạy. Vui lòng đợi.")
    patched = db.exec(select(Mix).where(
        Mix.project_id == project_id, Mix.status == "completed",
        Mix.title.like("Vá mix #%")).order_by(Mix.created_at.desc())).first()
    if patched is None or not patched.output_dir:
        raise HTTPException(409, "Chưa có bản mix đã vá.")
    try:
        base_id = int(patched.title.split("#", 1)[1].split()[0])
        base = db.get(Mix, base_id)
        base_total = base.total_duration_seconds if base else None
    except (ValueError, IndexError):
        base_total = None
    video_job_manager.submit(project_id, "remux", mix_patch.replace_final_audio,
                             str(Path(patched.output_dir) / "mix.wav"), p.name,
                             base_total)
    return {"ok": True}
