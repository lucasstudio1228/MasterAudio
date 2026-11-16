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
