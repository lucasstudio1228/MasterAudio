"""Global API settings (singleton row id=1)."""
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

# api_config.py nằm ở gốc dự án (cạnh backend/) — bảo đảm có trên sys.path để
# import được kể cả khi router này load trước core_bridge.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api_config import DEFAULT_MODEL, APIConfig  # noqa: E402
from backend.database import get_session
from backend.models import AppSettings
from backend.schemas import (
    ApiTestRequest,
    ApiTestResult,
    AppSettingsRead,
    AppSettingsUpdate,
    ThumbAISettingsRead,
    ThumbAISettingsUpdate,
    ThumbAITestRequest,
    ThumbAITestResult,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_or_create_settings(db: Session) -> AppSettings:
    """Singleton: luôn trả về row id=1, tạo nếu chưa có."""
    settings = db.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("", response_model=AppSettingsRead)
def get_settings(db: Session = Depends(get_session)):
    s = _get_or_create_settings(db)
    return AppSettingsRead(
        api_base_url=s.api_base_url,
        api_model=s.api_model,
        has_api_key=bool(s.api_key),
    )


@router.post("/test", response_model=ApiTestResult)
def test_api(
    data: ApiTestRequest,
    db: Session = Depends(get_session),
):
    """Gọi thử OpenAI bằng 1 request cực nhỏ để kiểm tra key/base_url/model.

    Ưu tiên giá trị gửi lên (đang gõ trong form); trống thì dùng cấu hình đã
    lưu trong DB. Không lưu gì, chỉ trả kết quả để hiển thị.
    """
    s = _get_or_create_settings(db)

    # Nền tảng là cấu hình đã lưu; override bằng field không rỗng gửi lên.
    cfg = APIConfig()
    cfg.api_key = (data.api_key or "").strip() or s.api_key or cfg.api_key
    base = (data.api_base_url or "").strip() or s.api_base_url or cfg.base_url
    cfg.base_url = base.rstrip("/")
    model = (data.api_model or "").strip() or s.api_model or cfg.model
    # Model cũ để tên Claude → không hợp lệ với endpoint GPT.
    if not model or model.lower().startswith("claude"):
        model = DEFAULT_MODEL
    cfg.model = model

    if not cfg.api_key:
        return ApiTestResult(
            ok=False, model=model, base_url=cfg.base_url,
            error_type="NoApiKey",
            error="Chưa có API key. Nhập key ở ô trên rồi bấm Test lại.",
        )

    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        return ApiTestResult(
            ok=False, model=model, base_url=cfg.base_url,
            error_type=type(e).__name__,
            error=f"Không import được thư viện openai: {e}",
        )

    # timeout ngắn + không auto-retry để Test phản hồi nhanh, thấy lỗi thật.
    try:
        client = OpenAI(timeout=60.0, max_retries=0, **cfg.to_client_kwargs())
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            max_tokens=16,
            messages=[
                {"role": "system", "content": "You are a health check."},
                {"role": "user", "content": "Reply with exactly: OK"},
            ],
        )
        latency_ms = int((time.time() - t0) * 1000)
        reply = (resp.choices[0].message.content or "").strip()
        return ApiTestResult(
            ok=True, model=model, base_url=cfg.base_url,
            latency_ms=latency_ms, reply=reply[:200],
        )
    except Exception as e:
        return ApiTestResult(
            ok=False, model=model, base_url=cfg.base_url,
            error_type=type(e).__name__,
            error=_friendly_api_error(e),
            retryable=_is_retryable(e),
        )


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    return None


def _is_retryable(exc: Exception) -> bool:
    code = _status_code(exc)
    if code in (408, 429, 500, 502, 503, 504, 520, 522, 524):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def _friendly_api_error(exc: Exception) -> str:
    code = _status_code(exc)
    name = type(exc).__name__
    if code == 524 or (code and code in (502, 503, 504, 520, 522)):
        return (
            f"Máy chủ API/proxy quá tải hoặc phản hồi chậm (HTTP {code}). "
            "Đây là lỗi TẠM THỜI phía proxy — hãy chờ ~120 giây rồi Test lại. "
            "Nếu lặp lại nhiều lần, kiểm tra lại proxy/base URL."
        )
    if code == 401:
        return "API key sai hoặc đã hết hạn (HTTP 401). Kiểm tra lại key."
    if code == 404:
        return (
            f"Không tìm thấy model/endpoint (HTTP 404). Kiểm tra tên model "
            "và Base URL (phải có '/v1' ở cuối)."
        )
    if code == 429:
        return "Bị giới hạn tần suất/hết hạn mức (HTTP 429). Chờ rồi thử lại."
    if "timeout" in name.lower():
        return "Hết thời gian chờ (timeout 60s) — proxy phản hồi quá chậm. Thử lại."
    if "connection" in name.lower():
        return "Không kết nối được tới Base URL. Kiểm tra mạng và Base URL."
    return f"{name}: {exc}"


@router.patch("", response_model=AppSettingsRead)
def update_settings(
    data: AppSettingsUpdate,
    db: Session = Depends(get_session),
):
    s = _get_or_create_settings(db)

    if data.clear_api_key:
        s.api_key = None
    elif data.api_key is not None:
        stripped = data.api_key.strip()
        if stripped:                       # Bỏ qua nếu gửi lên empty string
            s.api_key = stripped

    if data.api_base_url is not None:
        url = data.api_base_url.strip().rstrip("/")
        if url:
            s.api_base_url = url

    if data.api_model is not None:
        model = data.api_model.strip()
        if model:
            s.api_model = model

    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)

    return AppSettingsRead(
        api_base_url=s.api_base_url,
        api_model=s.api_model,
        has_api_key=bool(s.api_key),
    )


# ── Thumbnail AI (model ảnh vẽ cả ảnh + chữ qua API, mặc định ShopAIKey) ──

def _thumb_read(s: AppSettings) -> ThumbAISettingsRead:
    from backend.video import chatgpt_thumbnail as C
    from backend.video import thumbnail_ai as T
    model = s.thumb_api_model or T.DEFAULT_MODEL
    engine = (s.thumb_engine or T.DEFAULT_ENGINE).strip().lower()
    login = C.login_status()
    return ThumbAISettingsRead(
        base_url=s.thumb_api_base_url or T.DEFAULT_BASE, model=model,
        enabled=bool(s.thumb_ai_enabled), verify=bool(s.thumb_ai_verify),
        has_api_key=bool(s.thumb_api_key), protocol=T.protocol_for(model),
        models=T.MODEL_CHOICES,
        engine=engine if engine in T.ENGINES else T.DEFAULT_ENGINE,
        engines=T.ENGINE_CHOICES, chatgpt_logged_in=login.get("logged_in"),
        chatgpt_checked_at=login.get("checked_at"))


@router.get("/thumbnail-ai", response_model=ThumbAISettingsRead)
def get_thumbnail_ai(db: Session = Depends(get_session)):
    return _thumb_read(_get_or_create_settings(db))


@router.patch("/thumbnail-ai", response_model=ThumbAISettingsRead)
def update_thumbnail_ai(data: ThumbAISettingsUpdate,
                        db: Session = Depends(get_session)):
    s = _get_or_create_settings(db)
    if data.clear_api_key:
        s.thumb_api_key = None
    elif data.api_key is not None and data.api_key.strip():
        s.thumb_api_key = data.api_key.strip()
    if data.base_url is not None and data.base_url.strip():
        s.thumb_api_base_url = data.base_url.strip().rstrip("/")
    if data.model is not None and data.model.strip():
        s.thumb_api_model = data.model.strip()
    if data.enabled is not None:
        s.thumb_ai_enabled = bool(data.enabled)
    if data.verify is not None:
        s.thumb_ai_verify = bool(data.verify)
    if data.engine is not None:
        from backend.video import thumbnail_ai as T
        eng = data.engine.strip().lower()
        if eng not in T.ENGINES:
            raise HTTPException(400, f"engine phải là: {', '.join(T.ENGINES)}")
        s.thumb_engine = eng
    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _thumb_read(s)


@router.post("/thumbnail-ai/test", response_model=ThumbAITestResult)
def test_thumbnail_ai(data: ThumbAITestRequest,
                      db: Session = Depends(get_session)):
    """Kiểm tra key MIỄN PHÍ: GET /v1/models (không tạo ảnh, không tốn phí) →
    key hợp lệ? model đã chọn có trong nhóm của key không?"""
    from backend.video import thumbnail_ai as T
    s = _get_or_create_settings(db)
    key = (data.api_key or "").strip() or s.thumb_api_key or ""
    base = (data.base_url or "").strip() or s.thumb_api_base_url or T.DEFAULT_BASE
    model = (data.model or "").strip() or s.thumb_api_model or T.DEFAULT_MODEL
    if not key:
        return ThumbAITestResult(ok=False, base_url=base, model=model,
                                 error="Chưa có API key. Dán key rồi bấm Kiểm tra lại.")
    t0 = time.time()
    try:
        names = T.list_remote_models(key, base)
    except T.ThumbAIError as e:
        return ThumbAITestResult(ok=False, base_url=base, model=model, error=str(e))
    images = [n for n in names if T.is_image_model(n)]
    available = model in names if names else None
    return ThumbAITestResult(
        ok=True, base_url=T.api_root(base), model=model, model_available=available,
        image_models=images, model_count=len(names),
        latency_ms=int((time.time() - t0) * 1000))


# ── ChatGPT web (máy vẽ thumbnail mặc định) — cửa sổ để NGƯỜI DÙNG tự đăng nhập ──
# Chạy như job video id 0 (worker video duy nhất → không tranh profile với tác vụ
# đang chạy). Tool KHÔNG nhập email/mật khẩu, không bấm xác minh — chỉ mở trang
# và đọc trạng thái đăng nhập (có/không) để hiện trên UI.
CHATGPT_JOB_ID = 0


def _chatgpt_window() -> dict:
    from backend.video import chatgpt_thumbnail as C
    from backend.video.job_manager import video_job_manager
    job = video_job_manager.get(CHATGPT_JOB_ID)
    is_open = bool(job and job.kind == "browser-chatgpt"
                   and job.status in ("pending", "running", "paused"))
    login = C.login_status()
    return {"open": is_open,
            "status": job.status if job and job.kind == "browser-chatgpt" else None,
            "message": (job.message if job and job.kind == "browser-chatgpt" else "") or "",
            "logged_in": login.get("logged_in"), "checked_at": login.get("checked_at")}


@router.get("/thumbnail-chatgpt")
def chatgpt_window_status():
    """Cửa sổ ChatGPT có đang mở không + lần kiểm tra đăng nhập gần nhất."""
    return _chatgpt_window()


@router.post("/thumbnail-chatgpt/open")
def open_chatgpt_window():
    """Mở chatgpt.com bằng profile RIÊNG của tool để bạn TỰ đăng nhập."""
    from backend.video import monitor_browser
    from backend.video.job_manager import video_job_manager
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có tác vụ video chạy (hoặc cửa sổ trình duyệt đang "
                                 "mở) — đợi xong rồi mở ChatGPT, tránh 2 cửa sổ tranh 1 "
                                 "profile.")
    video_job_manager.submit(CHATGPT_JOB_ID, "browser-chatgpt",
                             monitor_browser.run_monitor_browser, site="chatgpt",
                             queued_message="Đang mở ChatGPT…")
    return {"status": "opening", **_chatgpt_window()}


@router.post("/thumbnail-chatgpt/close")
def close_chatgpt_window():
    from backend.video.job_manager import video_job_manager
    job = video_job_manager.get(CHATGPT_JOB_ID)
    if (not job or job.kind != "browser-chatgpt"
            or not video_job_manager.cancel(CHATGPT_JOB_ID)):
        raise HTTPException(409, "Cửa sổ ChatGPT không mở.")
    return {"status": "closing", **_chatgpt_window()}


# ── Máy tạo video/ảnh qua GPMLogin (lưu trong data/video_overrides.json) ──
_GPM_SITES = ("muse", "flow")


def _video_engines() -> dict:
    from backend.video import config as vconfig
    ov = vconfig.load_overrides()
    return {
        "clip_engine": vconfig.clip_engine(),
        "image_engine": vconfig.image_engine(),
        "image_fallback": vconfig.image_fallback(),
        "muse_gpm_profile_id": str(ov.get("muse_gpm_profile_id") or ""),
        "flow_gpm_profile_id": str(ov.get("flow_gpm_profile_id") or ""),
        "clip_engines": [{"key": k, "label": vconfig.CLIP_ENGINE_LABELS.get(k, k)}
                         for k in vconfig.CLIP_ENGINES],
        "image_engines": [{"key": k, "label": vconfig.IMAGE_ENGINE_LABELS.get(k, k)}
                          for k in vconfig.IMAGE_ENGINES],
    }


class VideoEnginesUpdate(BaseModel):
    clip_engine: Optional[str] = None
    image_engine: Optional[str] = None
    image_fallback: Optional[bool] = None
    muse_gpm_profile_id: Optional[str] = None
    flow_gpm_profile_id: Optional[str] = None


@router.get("/video-engines")
def get_video_engines():
    """Máy tạo clip/ảnh đang chọn + profile GPMLogin của Muse và Flow."""
    return _video_engines()


@router.put("/video-engines")
def update_video_engines(body: VideoEnginesUpdate):
    from backend.video import config as vconfig
    from backend.video.job_manager import video_job_manager
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có tác vụ video chạy — đợi xong rồi hãy đổi cài đặt "
                                 "máy tạo video.")
    if body.clip_engine is not None:
        key = body.clip_engine.strip().lower()
        if key not in vconfig.CLIP_ENGINES:
            raise HTTPException(400, f"Máy tạo clip '{body.clip_engine}' không hợp lệ.")
        vconfig.save_override("clip_engine", key)
    if body.image_engine is not None:
        key = body.image_engine.strip().lower()
        if key not in vconfig.IMAGE_ENGINES:
            raise HTTPException(400, f"Máy tạo ảnh '{body.image_engine}' không hợp lệ.")
        vconfig.save_override("image_engine", key)
    if body.image_fallback is not None:
        vconfig.save_override("image_fallback", bool(body.image_fallback))
    for site in _GPM_SITES:
        v = getattr(body, f"{site}_gpm_profile_id")
        if v is not None:
            vconfig.save_override(f"{site}_gpm_profile_id", v.strip())
    return _video_engines()
