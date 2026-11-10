from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from sqlmodel import Session, select
from pydantic import BaseModel, Field
from typing import Optional
import json, shutil
from pathlib import Path

from backend.database import get_session
from backend.models import Track, TrackStem
from backend.stem_job_manager import stem_job_manager
from backend.stem_service import STEMS_ROOT

router = APIRouter(prefix="/api", tags=["stems"])

# ── Schemas ────────────────────────────────────────────────────

class VolumeUpdate(BaseModel):
    vol_other:  Optional[float] = Field(None, ge=0.0, le=2.0)
    vol_bass:   Optional[float] = Field(None, ge=0.0, le=2.0)
    vol_drums:  Optional[float] = Field(None, ge=0.0, le=2.0)
    vol_vocals: Optional[float] = Field(None, ge=0.0, le=2.0)
    # Độ mạnh khử noise thủ công mỗi stem (0..1). None = không đổi.
    den_other:  Optional[float] = Field(None, ge=0.0, le=1.0)
    den_bass:   Optional[float] = Field(None, ge=0.0, le=1.0)
    den_drums:  Optional[float] = Field(None, ge=0.0, le=1.0)
    den_vocals: Optional[float] = Field(None, ge=0.0, le=1.0)

class BatchVolumeUpdate(BaseModel):
    vol_other:  float = Field(ge=0.0, le=2.0)
    vol_bass:   float = Field(ge=0.0, le=2.0)
    vol_drums:  float = Field(ge=0.0, le=2.0)
    vol_vocals: float = Field(ge=0.0, le=2.0)
    # Denoise là optional để tương thích client cũ; None = giữ nguyên.
    den_other:  Optional[float] = Field(None, ge=0.0, le=1.0)
    den_bass:   Optional[float] = Field(None, ge=0.0, le=1.0)
    den_drums:  Optional[float] = Field(None, ge=0.0, le=1.0)
    den_vocals: Optional[float] = Field(None, ge=0.0, le=1.0)

# ── Helpers ────────────────────────────────────────────────────

def _get_or_create_stem_record(
    track_id: int, project_id: int, db: Session
) -> TrackStem:
    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    if not stem:
        stem = TrackStem(track_id=track_id, project_id=project_id)
        db.add(stem)
        db.commit()
        db.refresh(stem)
    return stem

def _stem_to_response(stem: Optional[TrackStem]) -> dict:
    if not stem:
        return {
            "status": "not_started",
            "volumes": {"other":1.0, "bass":1.0,
                        "drums":1.0, "vocals":1.0},
            "denoise": {"other":0.0, "bass":0.0,
                        "drums":0.0, "vocals":0.0},
            "waveform_data": None,
            "error_message": None,
        }
    return {
        "track_stem_id": stem.id,
        "status":        stem.status,
        "error_message": stem.error_message,
        "volumes": {
            "other":  stem.vol_other,
            "bass":   stem.vol_bass,
            "drums":  stem.vol_drums,
            "vocals": stem.vol_vocals,
        },
        "denoise": {
            "other":  stem.den_other,
            "bass":   stem.den_bass,
            "drums":  stem.den_drums,
            "vocals": stem.den_vocals,
        },
        "waveform_data": (
            json.loads(stem.waveform_data) if stem.waveform_data else None
        ),
        "lufs_gain_applied": stem.lufs_gain_applied,
    }

# ── Endpoints ──────────────────────────────────────────────────

@router.get("/tracks/{track_id}/stems")
def get_stem_info(track_id: int, db: Session = Depends(get_session)):
    """Trả về trạng thái, volumes, denoise, waveform data."""
    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    return _stem_to_response(stem)

@router.get("/tracks/{track_id}/stems/audio/{stem_name}")
async def get_stem_audio(
    track_id: int,
    stem_name: str,     # "other" | "bass" | "drums" | "vocals"
    db: Session = Depends(get_session),
):
    """
    Stream 1 stem WAV file cho browser audio preview.
    Browser sẽ fetch 4 lần (1 per stem) khi user click Play.
    """
    valid_stems = {"other", "bass", "drums", "vocals"}
    if stem_name not in valid_stems:
        raise HTTPException(
            400, f"Invalid stem: {stem_name}. Must be one of {valid_stems}")

    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "Track not found")

    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    if not stem or stem.status != "completed":
        raise HTTPException(404, "Stems not ready for this track")

    wav_path = STEMS_ROOT / str(track.project_id) / str(track_id) / f"{stem_name}.wav"
    if not wav_path.exists():
        raise HTTPException(404, f"Stem file not found: {stem_name}")

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=f"{track.filename}_{stem_name}.wav",
        headers={"Cache-Control": "private, max-age=3600"},
    )

@router.post("/tracks/{track_id}/stems/analyze")
def analyze_single(track_id: int, db: Session = Depends(get_session)):
    """Submit 1 track vào Demucs queue."""
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "Track not found")

    stem = _get_or_create_stem_record(track_id, track.project_id, db)

    if stem.status == "completed":
        return {"status": "already_completed", "stem_id": stem.id}

    # Reset nếu failed trước đó
    stem.status        = "pending"
    stem.error_message = None
    db.add(stem)
    db.commit()
    db.refresh(stem)

    stem_job_manager.submit(
        stem.id, track_id, track.project_id, track.filepath
    )
    return {"status": "submitted", "stem_id": stem.id,
            "queue_position": stem_job_manager.get_pending_count()}

@router.post("/projects/{project_id}/stems/analyze-all")
def analyze_all(project_id: int, db: Session = Depends(get_session)):
    """Submit tất cả tracks chưa completed vào queue."""
    tracks = db.exec(
        select(Track).where(Track.project_id == project_id)
    ).all()
    if not tracks:
        raise HTTPException(400, "No tracks in project")

    submitted = skipped = 0
    for track in tracks:
        stem = _get_or_create_stem_record(track.id, project_id, db)
        if stem.status == "completed":
            skipped += 1
            continue
        stem.status = "pending"
        stem.error_message = None
        db.add(stem)
        db.commit()
        db.refresh(stem)
        stem_job_manager.submit(
            stem.id, track.id, project_id, track.filepath
        )
        submitted += 1

    return {
        "submitted": submitted,
        "skipped_already_done": skipped,
        "pending_in_queue": stem_job_manager.get_pending_count(),
    }

@router.patch("/tracks/{track_id}/stems/volumes")
def update_volumes(
    track_id: int,
    body: VolumeUpdate,
    db: Session = Depends(get_session),
):
    """
    Cập nhật volume sliders + độ mạnh khử noise (denoise) mỗi stem.
    Validation (vol 0.0–2.0, den 0.0–1.0) đã được Pydantic handle.
    Chỉ update field nào được gửi (khác None).
    """
    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    if not stem:
        raise HTTPException(404, "Stem record not found. Run analyze first.")

    if body.vol_other  is not None: stem.vol_other  = body.vol_other
    if body.vol_bass   is not None: stem.vol_bass   = body.vol_bass
    if body.vol_drums  is not None: stem.vol_drums  = body.vol_drums
    if body.vol_vocals is not None: stem.vol_vocals = body.vol_vocals

    if body.den_other  is not None: stem.den_other  = body.den_other
    if body.den_bass   is not None: stem.den_bass   = body.den_bass
    if body.den_drums  is not None: stem.den_drums  = body.den_drums
    if body.den_vocals is not None: stem.den_vocals = body.den_vocals

    db.add(stem)
    db.commit()
    return {
        "ok": True,
        "volumes": {
            "other":  stem.vol_other, "bass":  stem.vol_bass,
            "drums":  stem.vol_drums, "vocals": stem.vol_vocals,
        },
        "denoise": {
            "other":  stem.den_other, "bass":  stem.den_bass,
            "drums":  stem.den_drums, "vocals": stem.den_vocals,
        },
    }

@router.patch("/projects/{project_id}/stems/volumes-all")
def update_all_volumes(
    project_id: int,
    body: BatchVolumeUpdate,
    db: Session = Depends(get_session),
):
    """
    Apply cùng volumes (+ denoise nếu gửi) cho TẤT CẢ tracks trong project.
    Chỉ update tracks đã completed stems.
    """
    stems = db.exec(
        select(TrackStem).where(
            TrackStem.project_id == project_id,
            TrackStem.status == "completed",
        )
    ).all()

    if not stems:
        raise HTTPException(404, "No completed stems in project")

    updated = 0
    for stem in stems:
        stem.vol_other  = body.vol_other
        stem.vol_bass   = body.vol_bass
        stem.vol_drums  = body.vol_drums
        stem.vol_vocals = body.vol_vocals
        if body.den_other  is not None: stem.den_other  = body.den_other
        if body.den_bass   is not None: stem.den_bass   = body.den_bass
        if body.den_drums  is not None: stem.den_drums  = body.den_drums
        if body.den_vocals is not None: stem.den_vocals = body.den_vocals
        db.add(stem)
        updated += 1

    db.commit()
    return {
        "ok": True,
        "updated": updated,
        "volumes": {
            "other":  body.vol_other,
            "bass":   body.vol_bass,
            "drums":  body.vol_drums,
            "vocals": body.vol_vocals,
        },
        "denoise": {
            "other":  body.den_other,
            "bass":   body.den_bass,
            "drums":  body.den_drums,
            "vocals": body.den_vocals,
        },
    }

@router.get("/tracks/{track_id}/stems/progress")
async def stem_progress_sse(track_id: int,
                             db: Session = Depends(get_session)):
    """SSE stream cho separation progress."""
    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    if not stem:
        raise HTTPException(404)

    async def generator():
        async for event in stem_job_manager.stream(stem.id):
            import json as _j
            yield f"event: {event['type']}\ndata: {_j.dumps(event)}\n\n"

    return StreamingResponse(generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",
            "Connection":       "keep-alive",
        })

@router.delete("/tracks/{track_id}/stems")
def delete_stems(track_id: int, db: Session = Depends(get_session)):
    """Xóa stem files + DB record. Track vẫn giữ nguyên."""
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404)

    stem_dir = STEMS_ROOT / str(track.project_id) / str(track_id)
    if stem_dir.exists():
        shutil.rmtree(stem_dir)

    stem = db.exec(
        select(TrackStem).where(TrackStem.track_id == track_id)
    ).first()
    if stem:
        db.delete(stem)
        db.commit()
    return {"ok": True}

@router.get("/projects/{project_id}/stems/status")
def project_stems_status(project_id: int,
                          db: Session = Depends(get_session)):
    """Tổng quan trạng thái stems của toàn project."""
    stems = db.exec(
        select(TrackStem).where(TrackStem.project_id == project_id)
    ).all()
    counts = {"pending":0, "running":0, "completed":0, "failed":0}
    for s in stems:
        if s.status in counts:
            counts[s.status] += 1
    return {
        "total": len(stems),
        "counts": counts,
        "is_processing": stem_job_manager.is_processing(),
        "pending_in_queue": stem_job_manager.get_pending_count(),
    }
