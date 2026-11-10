"""
Router STEP 0 — tạo nhạc nền Suno cho 1 project.

Tích hợp cùng video_job_manager (kind="suno") nên được serialize với các job
video/audio khác. GIỮ NGUYÊN khả năng import nhạc thủ công (không đụng tracks.py).

An toàn (mục K của spec):
  • /dry-run: điền form + đọc trạng thái, KHÔNG tạo/tải → KHÔNG tiêu credit.
  • /start: LIVE — chỉ chạy khi người dùng chủ động bấm (kèm ngân sách override).
  • /pause: dừng ở checkpoint gần nhất, giữ state để /resume.
  • /cancel: huỷ hẳn batch (terminal).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Project
from backend.video import config as vconfig
from backend.video import suno_selfcheck
from backend.video import suno_service
from backend.video.job_manager import video_job_manager

router = APIRouter(prefix="/api/projects/{project_id}/suno", tags=["suno"])


class StartBody(BaseModel):
    preset: Optional[str] = None
    # Styles/Exclusions tuỳ chọn (AI viết từ ý tưởng, hoặc người dùng tự sửa).
    # Nếu bỏ trống → dùng nguyên văn preset.
    styles: Optional[str] = None
    exclusions: Optional[str] = None
    suno_idea: Optional[str] = None
    target_tracks: Optional[int] = None
    preferred_model: Optional[str] = None
    allow_model_fallback: Optional[bool] = None
    max_mode: Optional[bool] = None
    preferred_duration_seconds_min: Optional[int] = None
    preferred_duration_seconds_max: Optional[int] = None
    minimum_duration_seconds: Optional[int] = None
    # Ngân sách — người dùng xác nhận trước khi chạy live.
    max_create_actions: Optional[int] = None
    max_generation_credits: Optional[int] = None
    max_new_song_downloads: Optional[int] = None
    auto_continue_workflow: Optional[bool] = None

    def overrides(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def _project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p or getattr(p, "deleted_at", None):
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return p


_KIND_VI = {"upload": "đăng nháp YouTube", "suno": "Suno", "suno-fix": "tạo lại bài Suno",
            "suno-login": "đăng nhập Suno", "remux": "thay nhạc final.mp4",
            "full": "dựng video", "images": "tạo ảnh", "clips": "tạo clip",
            "assemble": "ghép video"}


def _describe_job(db: Session, job) -> str:
    p = db.get(Project, job.project_id)
    name = f" «{p.name}»" if p else ""
    pct = f" {job.percent:.0f}%" if job.status == "running" else ""
    state = {"paused": " (đang TẠM DỪNG)", "pending": " (đang xếp hàng)"}.get(job.status, "")
    return (f"project #{job.project_id}{name} — "
            f"{_KIND_VI.get(job.kind, job.kind)}{pct}{state}")


def _guard_busy(db: Session, project_id: int, allow_queue: bool = False) -> str:
    """Worker video/Suno chỉ có 1 luồng. Trả "" nếu rảnh; nếu đang có job của
    project KHÁC chạy (không tạm dừng) và chưa ai xếp hàng → cho xếp hàng
    (allow_queue) và trả lời nhắn "đang chờ"; ngược lại 409 nói rõ ai đang bận."""
    jobs = video_job_manager.active_jobs()
    if not jobs:
        return ""
    head = jobs[0]
    who = _describe_job(db, head)
    if (allow_queue and len(jobs) == 1 and head.status == "running"
            and head.project_id != project_id):
        return (f"⏳ Đang xếp hàng — chờ {who} xong rồi TỰ chạy "
                "(không cần bấm lại, đừng đóng tool).")
    extra = (f"; đã có {_describe_job(db, jobs[1])} chờ sau" if len(jobs) > 1 else "")
    raise HTTPException(409, f"Worker đang bận: {who}{extra}. Đợi xong rồi bấm lại.")


@router.get("/config")
def suno_config(project_id: int, db: Session = Depends(get_session)):
    """Cấu hình mặc định + danh sách preset (kèm Styles/Exclusions để UI xem
    trước). Không tiêu credit."""
    _project_or_404(db, project_id)
    base = vconfig.SUNO
    presets = [
        {"key": k, "label": v["label"], "styles": v["styles"],
         "exclusions": v["exclusions"]}
        for k, v in vconfig.SUNO_PRESETS.items()
    ]
    return {
        "defaults": {
            "source": base.source,
            "preset": base.preset,
            "target_tracks": base.target_tracks,
            "preferred_model": base.preferred_model,
            "allow_model_fallback": base.allow_model_fallback,
            "instrumental": base.instrumental,
            "max_mode": base.max_mode,
            "preferred_duration_seconds_min": base.preferred_duration_seconds_min,
            "preferred_duration_seconds_max": base.preferred_duration_seconds_max,
            "minimum_duration_seconds": base.minimum_duration_seconds,
            "max_create_actions": base.max_create_actions,
            "max_generation_credits": base.max_generation_credits,
            "max_new_song_downloads": base.max_new_song_downloads,
            "auto_continue_workflow": base.auto_continue_workflow,
        },
        "presets": presets,
    }


class GenStylesBody(BaseModel):
    idea: str = ""
    preset: Optional[str] = None
    save: bool = True   # lưu idea vào project.suno_idea để textbox nhớ


@router.post("/generate-styles")
def suno_generate_styles(project_id: int, body: GenStylesBody,
                         db: Session = Depends(get_session)):
    """AI viết prompt Styles (+ Exclude styles) từ ý tưởng/nhạc cụ người dùng.
    LUÔN nhạc không lời. KHÔNG mở trình duyệt, KHÔNG tiêu credit Suno (chỉ gọi
    LLM). Nếu không dùng được AI (thiếu key/lỗi) → báo lỗi, không đổi ý tưởng.

    Đồng thời PHÂN LOẠI project vào đúng kênh YouTube đã cấu hình (bám sát
    settings), lưu project.instrument/music_style, và BẬT auto_video +
    auto_upload để luồng chạy tự động tới bước lưu nháp YouTube."""
    project = _project_or_404(db, project_id)
    idea = (body.idea or "").strip()
    # Prompt 100% tiếng Anh ⇒ ý tưởng được dịch trước khi lưu/dùng
    # (dịch lỗi → giữ bản gốc; generate_suno_styles vẫn tự dịch lại).
    from backend.video.translate import needs_translation, to_english_or_none
    if needs_translation(idea):
        idea = to_english_or_none(idea) or idea

    # Lưu ý tưởng vào project (textbox nhớ giữa các phiên) — giống video_idea.
    if body.save:
        project.suno_idea = idea
        db.add(project)
        db.commit()

    from backend.database import DB_PATH
    from backend.core_bridge import get_api_config_from_db
    from backend.video.suno_prompt import generate_suno_styles

    title = (project.name or "").strip()
    from backend.video.prompt_catalog import project_brief
    from backend.video.prompt_workflow import project_context
    import json
    context = project_context(project_id, title)
    context["music_idea"] = idea
    context["music_styles"] = ""
    brief = project_brief(project_id, title, json.dumps(context, ensure_ascii=False))
    api_config = get_api_config_from_db(str(DB_PATH))

    # AI đoán nhạc cụ/không khí từ Project Title + ý tưởng. Chạy khi có ít nhất
    # một trong hai (thường luôn có Project Title).
    result = None
    if idea or title:
        result = generate_suno_styles(json.dumps(context, ensure_ascii=False), api_config,
                                      project_title=title, creative_brief=brief["music"])

    if result is None:
        raise HTTPException(422, "AI chưa viết được Styles đúng hồ sơ project. "
                            "Kiểm tra API/model hoặc rút gọn ý tưởng; không thay bằng preset khác nội dung.")
    else:
        styles = result["styles"]
        exclusions = result["exclusions"]
        source = "ai"
        note = None

    # ── Phân loại kênh YouTube + bật chuỗi tự động ──
    channel = _classify_and_prepare(db, project, title, idea, styles, api_config)
    return {"source": source, "styles": styles, "exclusions": exclusions,
            "note": note, "channel": channel}


def _classify_and_prepare(db: Session, project: Project, title: str, idea: str,
                          styles: str, api_config) -> dict:
    """Kênh YouTube của project: người dùng ĐÃ CHỌN kênh (project.channel_id) →
    dùng đúng kênh đó, không phân loại, không đổi nhạc cụ/thể loại (kênh nào
    cũng nhận nhạc cụ nào — 2026-10-25). Chưa chọn → AI chọn kênh khớp nhất
    trong các ChannelMapping rồi GHI channel_id. Bật auto_video + auto_upload để
    luồng một-chạm đi hết tới lưu nháp YouTube. Trả dict cho UI hiển thị (kèm
    cảnh báo nếu chưa cấu hình / không khớp)."""
    from backend.models import ChannelMapping
    from backend.video.channel_match import classify_channel
    from backend.video.music_spec import same_instrument

    mappings = [
        {"id": getattr(m, "id", None), "instrument": m.instrument or "",
         "music_style": m.music_style or "",
         "channel_name": m.channel_name, "gpm_profile_id": m.gpm_profile_id}
        for m in db.exec(select(ChannelMapping).order_by(ChannelMapping.id)).all()
    ]

    # Luôn bật auto_upload + auto_video: luồng một-chạm phải đi hết tới YouTube.
    project.auto_upload = True
    project.auto_video = True

    matched: Optional[dict] = None
    warning: Optional[str] = None
    chosen_id = getattr(project, "channel_id", None)
    chosen = next((m for m in mappings if chosen_id and m["id"] == chosen_id), None)
    if chosen:
        matched = {**chosen, "match_confidence": "chosen"}
    elif chosen_id:
        warning = ("Kênh YouTube đã chọn cho project này không còn trong ⚙️ Settings → "
                   "YouTube/GPMLogin. Chọn lại kênh (tab Video hoặc ✏️ Sửa project); "
                   "Suno/Mix/Video vẫn chạy, tool dừng ở bước đăng nháp.")
    elif not mappings:
        warning = ("Chưa cấu hình kênh YouTube nào. Vào ⚙️ Settings → "
                   "YouTube/GPMLogin thêm kênh (tên kênh → profile GPMLogin) rồi "
                   "chọn kênh cho project thì tool mới đăng nháp. Suno/Mix/Video vẫn chạy.")
    else:
        user_inst = (project.instrument or "").strip()
        pool = mappings
        if user_inst:
            # Người dùng đã chọn nhạc cụ → chỉ xét kênh CÙNG nhạc cụ; không bao
            # giờ đổi sang nhạc cụ khác (piano không bị đổi thành guitar).
            pool = [m for m in mappings if same_instrument(m["instrument"], user_inst)]
            style = (project.music_style or "").strip().lower()
            exact = [m for m in pool if style and m["music_style"].strip().lower() == style]
            if len(exact) == 1:
                pool = exact
        matched = classify_channel(title, idea, styles, pool, api_config) if pool else None
        if matched:
            # Ghi luôn kênh đã chọn → bước đăng nháp/thumbnail dùng đúng kênh này.
            if matched.get("id"):
                project.channel_id = matched["id"]
        elif user_inst and not pool:
            warning = (f"Chưa chọn kênh YouTube và không kênh nào gợi ý nhạc cụ «{user_inst}». "
                       "Chọn kênh ở tab Video (mục «Đăng nháp YouTube») hoặc ✏️ Sửa project — "
                       "kênh nào cũng nhận nhạc cụ nào. Suno/Mix/Video vẫn chạy đúng nhạc cụ; "
                       "tool dừng ở bước đăng nháp để không đăng nhầm kênh.")
        else:
            warning = ("Không xác định chắc chắn kênh YouTube cho project này. "
                       "Chọn kênh ở tab Video (mục «Đăng nháp YouTube») hoặc ✏️ Sửa "
                       "project. Tool sẽ dừng ở bước đăng nháp để không đăng nhầm kênh.")

    if matched:
        # Nhạc cụ/thể loại là của NGƯỜI DÙNG — chỉ điền khi đang trống.
        if not (project.instrument or "").strip():
            project.instrument = matched.get("instrument", "")
        if not (project.music_style or "").strip():
            project.music_style = matched.get("music_style", "")
        # Mỗi kênh 1 phong cách riêng: chưa có ảnh mà phong cách ảnh đang là
        # của kênh khác → chuyển sang phong cách của kênh này.
        try:
            from backend.video import channel_looks
            new_style = channel_looks.realign_style(
                project.id, project.name, project.video_style,
                project.instrument, project.music_style,
                channel_id=matched.get("id"))
            if new_style and new_style != project.video_style:
                logging.getLogger(__name__).info(
                    "Project %s: phong cách ảnh %s → %s (theo phong cách kênh)",
                    project.id, project.video_style, new_style)
                project.video_style = new_style
        except Exception as exc:
            logging.getLogger(__name__).warning("realign_style lỗi: %s", exc)

    db.add(project)
    db.commit()

    return {
        "matched": bool(matched),
        "instrument": project.instrument,
        "music_style": project.music_style,
        "channel_name": (matched or {}).get("channel_name", ""),
        "confidence": (matched or {}).get("match_confidence", ""),
        "warning": warning,
    }


@router.get("/status")
def suno_status(project_id: int, db: Session = Depends(get_session)):
    """Trạng thái batch gần nhất + job hiện tại. KHÔNG báo '15/15 hoàn thành' khi
    mới chỉ submit — các counter phản ánh đúng generated/selected/downloaded/
    validated/imported."""
    _project_or_404(db, project_id)
    batch = suno_service.batch_status(project_id)
    job = video_job_manager.get(project_id)
    job_info = None
    if job and job.kind in ("suno", "suno-login"):
        job_info = {"kind": job.kind, "status": job.status,
                    "percent": job.percent, "message": job.message,
                    "error": job.error}
    return {"batch": batch, "job": job_info,
            "selector_check": suno_selfcheck.get_last_check(project_id)}


@router.post("/dry-run")
def suno_dry_run(project_id: int, body: StartBody = StartBody(),
                 db: Session = Depends(get_session)):
    """Điền form + đọc credits/plan, KHÔNG Create/tải. An toàn, không tốn credit."""
    _project_or_404(db, project_id)
    queued = _guard_busy(db, project_id, allow_queue=True)
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=True, overrides=body.overrides(),
                             queued_message=queued)
    return {"status": "queued" if queued else "submitted", "kind": "suno",
            "mode": "dry-run", "message": queued}


@router.post("/start")
def suno_start(project_id: int, body: StartBody = StartBody(),
               db: Session = Depends(get_session)):
    """LIVE — tạo đủ 15 bài, tải WAV, xác thực, import. Người dùng chủ động bấm."""
    _project_or_404(db, project_id)
    queued = _guard_busy(db, project_id, allow_queue=True)
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=False, overrides=body.overrides(),
                             queued_message=queued)
    return {"status": "queued" if queued else "submitted", "kind": "suno",
            "mode": "live", "message": queued}


@router.post("/resume")
def suno_resume(project_id: int, db: Session = Depends(get_session)):
    """Tiếp tục batch đang dở (PAUSED/blocking). KHÔNG tạo lại/tải lại/import lại
    những gì đã xong — resume dựa trên manifest bền vững."""
    _project_or_404(db, project_id)
    queued = _guard_busy(db, project_id, allow_queue=True)
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=False, resume=True, queued_message=queued)
    return {"status": "queued" if queued else "submitted", "kind": "suno",
            "mode": "resume", "message": queued}


@router.post("/pause")
def suno_pause(project_id: int, db: Session = Depends(get_session)):
    """Tạm dừng: dừng ở checkpoint gần nhất, giữ state để resume."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)   # → JobCancelled → PAUSED
    return {"status": "pausing" if ok else "no-active-job"}


@router.post("/cancel")
def suno_cancel(project_id: int, db: Session = Depends(get_session)):
    """Huỷ hẳn batch (terminal). Nếu job đang chạy → yêu cầu huỷ; nếu không →
    đánh dấu batch CANCELLED trực tiếp."""
    _project_or_404(db, project_id)
    suno_service.request_cancel(project_id)
    if video_job_manager.cancel(project_id):
        return {"status": "cancelling"}
    result = suno_service.mark_cancelled(project_id)
    return {"status": "cancelled" if result else "no-active-batch",
            "batch": result}


@router.post("/open-browser")
def suno_open_browser(project_id: int, db: Session = Depends(get_session)):
    """Mở trình duyệt Suno (profile riêng) để người dùng ĐĂNG NHẬP, rồi tự KIỂM
    TRA + tự sửa/lưu selector khi giao diện Suno đổi. KHÔNG tạo/tải gì → không
    tốn credit. Giữ cửa sổ mở tới khi bấm 'Đóng trình duyệt'."""
    _project_or_404(db, project_id)
    _guard_busy(db, project_id)
    video_job_manager.submit(project_id, "suno-login",
                             suno_selfcheck.run_login_and_check)
    return {"status": "submitted", "kind": "suno-login"}


@router.post("/close-browser")
def suno_close_browser(project_id: int, db: Session = Depends(get_session)):
    """Đóng trình duyệt đăng nhập/kiểm tra selector (huỷ job giữ cửa sổ)."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)
    return {"status": "closing" if ok else "no-active-job"}


@router.get("/progress")
async def suno_progress(project_id: int):
    async def gen():
        async for event in video_job_manager.stream(project_id):
            yield event
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
