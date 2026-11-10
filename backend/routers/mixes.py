"""Mix lifecycle: tạo job, theo dõi SSE, tải file, xóa."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend import core_bridge
from backend.database import engine, get_session
from backend.job_manager import job_manager
from backend.models import Mix, Project, Track
from backend.routers.projects import get_project_or_404
from backend.schemas import MixCreate, MixResponse

router = APIRouter(tags=["mixes"])

_OUTPUTS_ROOT = Path(__file__).parent.parent.parent / "outputs"
DB_PATH = str(Path(__file__).parent.parent.parent / "data" / "app.db")
# Tool Cover: 1 bài cũng mix được (mix theo thứ tự, xem backend/cover/mix.py).
MIN_TRACKS = 1
# Chất lượng tối đa, không nén: luôn xuất 32-bit float.
FORCED_BIT_DEPTH = 32
MIN_SAMPLE_RATE = 44100
DOWNLOAD_FILES = {
    "wav": ("mix.wav", "audio/wav"),
    "cue": ("mix.cue", "text/plain"),
    "tracklist": ("mix_tracklist.txt", "text/plain"),
    "json": ("mix_info.json", "application/json"),
}


def to_mix_response(mix: Mix) -> MixResponse:
    """Merge DB record với progress realtime từ job_manager (nếu đang chạy)."""
    job = job_manager.get_status(mix.id)
    percent = job.percent if job else (100.0 if mix.status == "completed" else 0.0)
    return MixResponse(
        id=mix.id, project_id=mix.project_id, title=mix.title,
        status=(job.status if job else mix.status),
        created_at=mix.created_at, completed_at=mix.completed_at,
        duration_minutes=mix.duration_minutes,
        crossfade_seconds=mix.crossfade_seconds, sample_rate=mix.sample_rate,
        bit_depth=mix.bit_depth, output_dir=mix.output_dir,
        total_duration_seconds=mix.total_duration_seconds,
        track_count=mix.track_count, error_message=mix.error_message,
        progress_percent=percent,
        progress_step=job.step if job else 0,
        progress_step_name=job.step_name if job else "",
    )


def _get_mix_or_404(session: Session, mix_id: int) -> Mix:
    mix = session.get(Mix, mix_id)
    if mix is None:
        raise HTTPException(404, f"Mix {mix_id} không tồn tại")
    return mix


# ---------- Job callbacks (chạy trong worker thread → session riêng) ----------
def _maybe_trigger_auto_video(project_id: int, project_name: str,
                              idea: str, audio_path: str,
                              style_key: str | None = None) -> None:
    """
    Sau khi mix Audio xong: nếu project bật auto_video thì tự động submit job
    dựng video (ảnh → clip → ghép → MP4 vào media/<project>/final/). Import cục
    bộ để tránh vòng import và để lỗi ở nhánh video không làm hỏng việc lưu mix.
    """
    from backend.edition import AUDIO_ONLY
    if AUDIO_ONLY:
        return      # Cover Audio Tool: mix xong là xong, không dựng video.
    try:
        from backend.video.job_manager import video_job_manager
        from backend.video import service as video_service

        if video_job_manager.is_busy():
            # Đang có tác vụ video chạy — bỏ qua auto để không chen ngang.
            return
        topic = (project_name or "meditation").strip()
        video_job_manager.submit(
            project_id, "full", video_service.run_full_video,
            topic, audio_path, None, project_name, (idea or None), style_key,
        )
    except Exception:
        # Auto-video là tính năng phụ — không được làm sập callback mix.
        pass


def _on_success(mix_id: int, result: dict, force_video: bool = False) -> None:
    """force_video=True: luôn auto-dựng video sau mix (dùng cho chuỗi Suno →
    Mix → Video), bỏ qua công tắc auto_video của project."""
    auto_ctx: dict | None = None
    qc_pid: int | None = None
    with Session(engine) as session:
        mix = session.get(Mix, mix_id)
        if mix:
            qc_pid = mix.project_id
            mix.status = "completed"
            mix.completed_at = datetime.now(timezone.utc)
            mix.total_duration_seconds = result.get("total_duration_seconds")
            mix.track_count = result.get("track_count")
            mix.output_dir = result.get("output_dir")
            # SR thực tế đã resolve trong worker (auto) — lưu lại cho chính xác.
            if result.get("sample_rate"):
                mix.sample_rate = result["sample_rate"]
            if result.get("bit_depth"):
                mix.bit_depth = result["bit_depth"]
            session.add(mix)
            session.commit()

            # Chuẩn bị context để auto-dựng video (đọc trong session còn mở).
            project = session.get(Project, mix.project_id)
            out_dir = mix.output_dir
            if project and out_dir:
                # Thư mục nhac_mix: 1 file nhạc đã mix, không cần video.
                from backend.cover.mix_export import export_mix_audio
                export_mix_audio(project.id, project.name, Path(out_dir) / "mix.wav")
            if project and (project.auto_video or force_video) and out_dir:
                wav = Path(out_dir) / "mix.wav"
                if wav.exists():
                    auto_ctx = {
                        "project_id": project.id,
                        "project_name": project.name,
                        "idea": project.video_idea or "",
                        "audio_path": str(wav),
                        "style_key": project.video_style,
                    }

    # Rà soát từng track lẻ (nền, ~0.6 s/bài) TRƯỚC khi dựng video: có bài
    # Suno lỗi → tự tạo lại + vá mix rồi mới dựng video bằng bản đã vá
    # (qc_autofix lo luôn phần nối chuỗi video).
    # Tool Cover: KHÔNG chạy qc_autofix (rà soát nhạc thiền bằng PANNs — với
    # bài có lời sẽ báo lỗi sai và tự "tạo lại" bài). Dựng video thẳng.

    # Submit NGOÀI session (job chạy trong worker thread khác của video manager).
    if auto_ctx:
        _maybe_trigger_auto_video(
            auto_ctx["project_id"], auto_ctx["project_name"],
            auto_ctx["idea"], auto_ctx["audio_path"], auto_ctx["style_key"],
        )


def _on_failure(mix_id: int, error: str) -> None:
    with Session(engine) as session:
        mix = session.get(Mix, mix_id)
        if mix:
            mix.status = "failed"
            mix.error_message = error
            mix.completed_at = datetime.now(timezone.utc)
            session.add(mix)
            session.commit()


def _on_cancel(mix_id: int) -> None:
    with Session(engine) as session:
        mix = session.get(Mix, mix_id)
        if mix:
            mix.status = "cancelled"
            mix.error_message = "Đã huỷ theo yêu cầu"
            mix.completed_at = datetime.now(timezone.utc)
            session.add(mix)
            session.commit()


# ---------- Endpoints ----------
@router.get("/projects/{project_id}/mixes", response_model=list[MixResponse])
def list_mixes(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)
        .order_by(Mix.created_at.desc())).all()
    return [to_mix_response(m) for m in mixes]


def _submit_mix_job(session: Session, project_id: int, data: MixCreate,
                    tracks: list[Track], *, force_video: bool) -> Mix:
    """Tạo record Mix + submit job render (dùng chung cho endpoint và auto-continue
    từ Suno). Giả định caller đã validate (đủ track, manager rảnh)."""
    from functools import partial

    # ── Chốt chất lượng output ──
    # Sample rate: 0/âm → auto = max native của library (sàn 44100), KHÔNG
    # upsample giả. Nếu người dùng chỉ định thì tôn trọng nhưng không dưới sàn.
    if not data.sample_rate or data.sample_rate <= 0:
        sample_rate = max([t.sample_rate for t in tracks] + [MIN_SAMPLE_RATE])
    else:
        sample_rate = max(int(data.sample_rate), MIN_SAMPLE_RATE)
    # Bit depth: luôn 32-bit float (không nén) để giữ headroom tối đa.
    bit_depth = FORCED_BIT_DEPTH

    title = data.title or f"Mix {datetime.now():%Y-%m-%d %H:%M}"
    mix = Mix(
        project_id=project_id, title=title, status="pending",
        duration_minutes=data.duration_minutes,
        crossfade_seconds=data.crossfade_seconds,
        sample_rate=sample_rate, bit_depth=bit_depth,
    )
    session.add(mix)
    session.commit()
    session.refresh(mix)

    output_dir = _OUTPUTS_ROOT / str(project_id) / str(mix.id)
    mix.output_dir = str(output_dir)
    session.add(mix)
    session.commit()
    session.refresh(mix)

    from backend.cover.mix import run_cover_mix_job

    job_manager.submit(
        mix.id, run_cover_mix_job,
        project_id, str(output_dir), data.duration_minutes,
        data.crossfade_seconds, sample_rate, bit_depth, DB_PATH,
        loudness_balance=data.loudness_balance,
        on_success=partial(_on_success, force_video=force_video),
        on_failure=_on_failure, on_cancel=_on_cancel,
    )
    return mix


def start_mix_for_project(project_id: int, *, force_video: bool = False,
                          data: MixCreate | None = None) -> int:
    """Khởi động 1 mix cho project theo cách lập trình (auto-continue từ Suno).
    Mở session riêng, validate, submit. Trả mix_id. Raise RuntimeError nếu không
    đủ điều kiện (để caller log, không làm sập job Suno)."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"Project {project_id} không tồn tại")
        # Thời lượng/crossfade theo cài đặt đã lưu của project (tab Audio) —
        # KHÔNG dùng mặc định cứng của MixCreate.
        data = data or MixCreate(
            duration_minutes=project.mix_duration_minutes or 120.0,
            crossfade_seconds=project.mix_crossfade_seconds or 5.0,
        )
        tracks = session.exec(
            select(Track).where(Track.project_id == project_id)).all()
        if len(tracks) < MIN_TRACKS:
            raise RuntimeError(
                f"Cần tối thiểu {MIN_TRACKS} track, hiện có {len(tracks)}")
        if job_manager.is_busy():
            raise RuntimeError("Đang có 1 mix render khác — bỏ qua auto-continue.")
        mix = _submit_mix_job(session, project_id, data, tracks,
                              force_video=force_video)
        return mix.id


@router.post("/projects/{project_id}/mixes", response_model=MixResponse)
def create_mix(project_id: int, data: MixCreate,
               session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)

    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    if len(tracks) < MIN_TRACKS:
        raise HTTPException(
            400, f"Cần tối thiểu {MIN_TRACKS} track, hiện có {len(tracks)}")
    if job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 mix render. Vui lòng đợi hoàn tất.")

    # Endpoint thủ công: video sau mix vẫn theo công tắc auto_video của project.
    mix = _submit_mix_job(session, project_id, data, tracks, force_video=False)
    return to_mix_response(mix)


class MixGainUpdate(BaseModel):
    gain_db: float = Field(ge=-12.0, le=12.0)


@router.get("/projects/{project_id}/loudness")
def analyze_loudness(project_id: int, session: Session = Depends(get_session)):
    """Đo độ to từng bài (file Mix sẽ dùng) + gain dự kiến khi cân bằng. Chỉ đọc."""
    get_project_or_404(session, project_id)
    from backend.cover.mix import analyze_project
    try:
        return analyze_project(DB_PATH, project_id)
    except Exception as e:  # noqa: BLE001 — file hỏng/không đọc được
        raise HTTPException(400, f"Không đo được âm lượng: {e}") from e


@router.get("/mixes/{mix_id}/loudness")
def mix_loudness(mix_id: int, session: Session = Depends(get_session)):
    """Kết quả cân bằng âm lượng đã ghi trong mix_info.json của 1 bản mix."""
    mix = _get_mix_or_404(session, mix_id)
    info = Path(mix.output_dir or "") / "mix_info.json"
    if not mix.output_dir or not info.is_file():
        raise HTTPException(404, "Bản mix này chưa có mix_info.json")
    data = json.loads(info.read_text(encoding="utf-8"))
    return data.get("loudness") or {"balanced": False}


@router.put("/projects/{project_id}/loudness/{track_id}")
def set_mix_gain(project_id: int, track_id: int, data: MixGainUpdate,
                 session: Session = Depends(get_session)):
    """Chỉnh tay độ to 1 bài khi Mix (dB cộng thêm sau cân bằng tự động)."""
    from backend.models import CoverSong
    get_project_or_404(session, project_id)
    song = session.exec(select(CoverSong).where(
        CoverSong.track_id == track_id, CoverSong.project_id == project_id)).first()
    if song is None:
        raise HTTPException(404, "Bài này chưa có trong danh sách bài cover của project.")
    song.mix_gain_db = round(float(data.gain_db), 1)
    session.add(song)
    session.commit()
    return {"track_id": track_id, "mix_gain_db": song.mix_gain_db}


@router.get("/mixes/{mix_id}", response_model=MixResponse)
def get_mix(mix_id: int, session: Session = Depends(get_session)):
    return to_mix_response(_get_mix_or_404(session, mix_id))


@router.delete("/mixes/{mix_id}")
def delete_mix(mix_id: int, session: Session = Depends(get_session)):
    mix = _get_mix_or_404(session, mix_id)
    if job_manager.is_busy() and mix.status in ("pending", "running"):
        job = job_manager.get_status(mix_id)
        if job and job.status in ("pending", "running"):
            raise HTTPException(409, "Mix đang render, không thể xóa.")
    if mix.output_dir and Path(mix.output_dir).exists():
        shutil.rmtree(mix.output_dir, ignore_errors=True)
    session.delete(mix)
    session.commit()
    return {"ok": True}


@router.post("/mixes/{mix_id}/cancel")
def cancel_mix(mix_id: int, session: Session = Depends(get_session)):
    """Huỷ mix đang render (hợp tác — dừng ở bước kế tiếp)."""
    _get_mix_or_404(session, mix_id)
    ok = job_manager.cancel(mix_id)
    if not ok:
        raise HTTPException(409, "Mix này không đang render để huỷ.")
    return {"status": "cancelling", "mix_id": mix_id}


@router.post("/mixes/{mix_id}/pause")
def pause_mix(mix_id: int, session: Session = Depends(get_session)):
    """Tạm dừng mix đang render (dừng ở bước kế, giữ nguyên state để tiếp tục)."""
    _get_mix_or_404(session, mix_id)
    ok = job_manager.pause(mix_id)
    if not ok:
        raise HTTPException(409, "Mix này không đang render để tạm dừng.")
    return {"status": "pausing", "mix_id": mix_id}


@router.post("/mixes/{mix_id}/resume")
def resume_mix(mix_id: int, session: Session = Depends(get_session)):
    """Tiếp tục mix đang tạm dừng từ đúng bước đang dở."""
    _get_mix_or_404(session, mix_id)
    ok = job_manager.resume(mix_id)
    if not ok:
        raise HTTPException(409, "Mix này không đang tạm dừng để tiếp tục.")
    return {"status": "resuming", "mix_id": mix_id}


@router.get("/mixes/{mix_id}/progress")
async def mix_progress(mix_id: int):
    async def event_generator():
        async for event in job_manager.stream(mix_id):
            data = json.dumps(event["data"])
            yield f"event: {event['type']}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/mixes/{mix_id}/download/{file_type}")
def download_mix_file(mix_id: int, file_type: str,
                      session: Session = Depends(get_session)):
    mix = _get_mix_or_404(session, mix_id)
    if file_type not in DOWNLOAD_FILES:
        raise HTTPException(400, f"file_type không hợp lệ: {file_type}")
    if not mix.output_dir:
        raise HTTPException(404, "Mix chưa có output")

    filename, media_type = DOWNLOAD_FILES[file_type]
    filepath = Path(mix.output_dir) / filename
    if not filepath.exists():
        raise HTTPException(404, f"File không tồn tại: {filename}")

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_"
                         for c in mix.title).strip() or "mix"
    return FileResponse(
        path=str(filepath),
        filename=f"{safe_title}_{filepath.name}",
        media_type=media_type,
    )
