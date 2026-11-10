"""
routers/youtube.py — Cấu hình cho tính năng auto-upload YouTube qua GPMLogin:
  • GPMLogin Local API: xem/sửa base URL + exe path; liệt kê profile (test kết nối).
  • Bảng kênh (profile GPMLogin + tên kênh + hashtag + ngôn ngữ; music_line +
    music_style chỉ để gợi ý kênh cho project chưa chọn kênh).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import AppSettings, ChannelMapping
from backend.video import channel_looks

router = APIRouter(prefix="/api/youtube", tags=["youtube"])


def _settings(db: Session) -> AppSettings:
    s = db.get(AppSettings, 1)
    if not s:
        s = AppSettings(id=1)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


# ── GPMLogin config ─────────────────────────────────────────────
class GpmConfigRead(BaseModel):
    gpm_api_base_url: str
    gpm_exe_path: Optional[str] = None


class GpmConfigUpdate(BaseModel):
    gpm_api_base_url: Optional[str] = None
    gpm_exe_path: Optional[str] = None


@router.get("/gpm/config", response_model=GpmConfigRead)
def get_gpm_config(db: Session = Depends(get_session)):
    s = _settings(db)
    return GpmConfigRead(gpm_api_base_url=s.gpm_api_base_url,
                        gpm_exe_path=s.gpm_exe_path)


@router.patch("/gpm/config", response_model=GpmConfigRead)
def update_gpm_config(data: GpmConfigUpdate, db: Session = Depends(get_session)):
    s = _settings(db)
    if data.gpm_api_base_url is not None:
        url = data.gpm_api_base_url.strip().rstrip("/")
        if url:
            s.gpm_api_base_url = url
    if data.gpm_exe_path is not None:
        s.gpm_exe_path = data.gpm_exe_path.strip() or None
    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)
    return GpmConfigRead(gpm_api_base_url=s.gpm_api_base_url,
                        gpm_exe_path=s.gpm_exe_path)


@router.get("/gpm/profiles")
def list_gpm_profiles(db: Session = Depends(get_session)):
    """Liệt kê profile GPMLogin (đổ dropdown mapping + test kết nối API)."""
    from backend.video import gpm_client as gpmc
    s = _settings(db)
    client = gpmc.GPMClient(s.gpm_api_base_url or None)
    try:
        profiles = client.list_profiles()
        return {"ok": True, "base_url": client.base_url, "profiles": profiles}
    except gpmc.GPMError as e:
        # Fallback đọc DB đã nằm trong list_profiles; tới đây là hỏng hẳn.
        return {"ok": False, "base_url": client.base_url,
                "profiles": [], "error": str(e)}


# ── Channel mapping CRUD ────────────────────────────────────────
class MappingBody(BaseModel):
    # Dòng nhạc + thể loại của kênh chỉ để GỢI Ý kênh cho project chưa chọn kênh
    # (project chọn kênh nào cũng được, không cần khớp) → được để trống.
    music_line: str = ""
    music_style: str = ""
    # Nhạc cụ (tool cũ) — Settings tool Cover không gửi nữa. None = giữ nguyên.
    instrument: Optional[str] = None
    gpm_profile_id: str
    channel_name: str = ""
    default_hashtags: str = ""
    language: str = "en-US"
    # Phong cách hình ảnh của kênh (có sẵn hoặc tự nhập; kênh khác dùng rồi vẫn
    # chọn được). None = giữ nguyên (PATCH) / tự gán phong cách còn trống (POST).
    visual_look: Optional[str] = None


def _mapping_out(m: ChannelMapping) -> dict:
    lk = channel_looks.look_info(m.visual_look) if channel_looks.valid_look(m.visual_look) else None
    return {
        "id": m.id, "instrument": m.instrument, "music_style": m.music_style,
        "music_line": m.music_line or "",
        "gpm_profile_id": m.gpm_profile_id, "channel_name": m.channel_name,
        "default_hashtags": m.default_hashtags, "language": m.language,
        "visual_look": m.visual_look or "",
        "look_label": f"{lk['icon']} {lk['label']}" if lk else "",
    }


def _check_look(look: str) -> str:
    """Phong cách hợp lệ (có sẵn hoặc tự nhập). Từ 2026-10-26 chọn TỰ DO — nhiều
    kênh dùng chung 1 phong cách được."""
    look = (look or "").strip()
    if not channel_looks.valid_look(look):
        raise HTTPException(400, f"Phong cách '{look}' không tồn tại.")
    return look


@router.get("/looks")
def list_looks(db: Session = Depends(get_session)):
    """Danh mục phong cách kênh (có sẵn + tự nhập) + các kênh đang dùng."""
    channel_looks.ensure_channel_looks()
    db.expire_all()
    used: dict[str, list[ChannelMapping]] = {}
    for m in db.exec(select(ChannelMapping).order_by(ChannelMapping.id)).all():
        if m.visual_look:
            used.setdefault(m.visual_look, []).append(m)
    out = []
    for c in channel_looks.catalog():
        ms = used.get(c["key"], [])
        out.append({**c, "used_by": ", ".join(m.channel_name or m.music_line or m.instrument
                                              for m in ms),
                    "used_by_ids": [m.id for m in ms]})
    return out


class CustomLookBody(BaseModel):
    label: str
    description: str = ""
    base: str = channel_looks.DEFAULT_LOOK
    styles: list[str] = []
    icon: str = "🎨"


def _english_signature(description: str) -> str:
    """Mô tả người dùng (thường tiếng Việt) → câu thẩm mỹ tiếng Anh gửi model ảnh
    (mọi prompt 100% tiếng Anh). Dịch lỗi → 502, không lưu nửa vời."""
    text = (description or "").strip()
    if not text:
        return ""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from backend.video.translate import needs_translation, to_english_or_none
    if not needs_translation(text):
        return text
    out = to_english_or_none(text, get_api_config_from_db(str(DB_PATH)))
    if not out or needs_translation(out):
        raise HTTPException(502, "Không dịch được mô tả sang tiếng Anh — kiểm tra API "
                                 "(Settings) rồi thử lại, hoặc gõ mô tả bằng tiếng Anh.")
    return out.strip()


def _look_out(key: str) -> dict:
    return next(c for c in channel_looks.catalog() if c["key"] == key)


@router.post("/looks")
def create_custom_look(body: CustomLookBody):
    """Thêm phong cách kênh tự nhập."""
    try:
        # Kiểm trước khi dịch → nhập sai không tốn lượt gọi API dịch.
        channel_looks.clean_custom(body.label, body.description, body.base,
                                   body.styles, body.icon)
        key = channel_looks.create_custom_look(
            body.label, body.description, body.base, body.styles, body.icon,
            signature=_english_signature(body.description))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _look_out(key)


@router.patch("/looks/{key}")
def update_custom_look(key: str, body: CustomLookBody):
    cur = channel_looks.custom_looks().get(key)
    if not cur:
        raise HTTPException(404, "Chỉ sửa được phong cách tự nhập.")
    sig = None                                     # mô tả không đổi → giữ bản dịch cũ
    try:
        channel_looks.clean_custom(body.label, body.description, body.base,
                                   body.styles, body.icon)
        if body.description.strip() != cur["user_desc"]:
            sig = _english_signature(body.description)
        channel_looks.update_custom_look(key, body.label, body.description, body.base,
                                         body.styles, body.icon, signature=sig)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _look_out(key)


@router.delete("/looks/{key}")
def delete_custom_look(key: str):
    """Xoá phong cách tự nhập; kênh đang dùng về phong cách gốc của nó."""
    try:
        moved = channel_looks.delete_custom_look(key)
    except KeyError:
        raise HTTPException(404, "Chỉ xoá được phong cách tự nhập.") from None
    return {"ok": True, "moved_channels": moved}


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_session)):
    channel_looks.ensure_channel_looks()
    db.expire_all()
    rows = db.exec(select(ChannelMapping).order_by(ChannelMapping.id)).all()
    return [_mapping_out(m) for m in rows]


@router.post("/mappings")
def create_mapping(body: MappingBody, db: Session = Depends(get_session)):
    if not (body.channel_name.strip() or body.music_line.strip()
            or (body.instrument or "").strip()):
        raise HTTPException(400, "Nhập tên kênh (hoặc dòng nhạc) để nhận ra kênh khi chọn.")
    if not body.gpm_profile_id.strip():
        raise HTTPException(400, "Phải chọn 1 profile GPMLogin.")
    m = ChannelMapping(
        instrument=(body.instrument or "").strip(),
        music_line=body.music_line.strip(),
        music_style=body.music_style.strip(),
        gpm_profile_id=body.gpm_profile_id.strip(),
        channel_name=body.channel_name.strip(),
        default_hashtags=body.default_hashtags.strip(),
        language=(body.language or "en-US").strip(),
    )
    if body.visual_look:
        m.visual_look = _check_look(body.visual_look)
    else:
        taken = {o.visual_look for o in db.exec(select(ChannelMapping)).all() if o.visual_look}
        m.visual_look = channel_looks.pick_free_look(
            {"instrument": m.instrument, "music_style": m.music_style,
             "channel_name": m.channel_name}, taken)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mapping_out(m)


@router.patch("/mappings/{mapping_id}")
def update_mapping(mapping_id: int, body: MappingBody,
                   db: Session = Depends(get_session)):
    m = db.get(ChannelMapping, mapping_id)
    if not m:
        raise HTTPException(404, "Không tìm thấy dòng ánh xạ.")
    if body.instrument is not None:
        m.instrument = body.instrument.strip()
    m.music_line = body.music_line.strip()
    m.music_style = body.music_style.strip()
    m.gpm_profile_id = body.gpm_profile_id.strip()
    m.channel_name = body.channel_name.strip()
    m.default_hashtags = body.default_hashtags.strip()
    m.language = (body.language or "en-US").strip()
    if body.visual_look and body.visual_look != m.visual_look:
        m.visual_look = _check_look(body.visual_look)
    m.updated_at = datetime.now(timezone.utc)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mapping_out(m)


@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int, db: Session = Depends(get_session)):
    m = db.get(ChannelMapping, mapping_id)
    if not m:
        raise HTTPException(404, "Không tìm thấy dòng ánh xạ.")
    # Project đã chọn kênh này → bỏ chọn (về tự khớp), không trỏ vào kênh đã xoá.
    from backend.models import Project
    for p in db.exec(select(Project).where(Project.channel_id == mapping_id)).all():
        p.channel_id = None
        db.add(p)
    db.delete(m)
    db.commit()
    return {"ok": True}
