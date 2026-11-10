"""CRUD cho /api/projects."""
import io
import re
import zipfile
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Mix, Project, Track
from backend.schemas import (BatchCreate, BatchRunResponse,
                             ProjectBulkDelete, ProjectCreate,
                             ProjectDetailResponse, ProjectResponse,
                             ProjectRename, ProjectUpdate, MixResponse,
                             TrackResponse)

router = APIRouter(prefix="/projects", tags=["projects"])

def _counts(session: Session, project_id: int) -> tuple[int, int]:
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)).all()
    return len(tracks), len(mixes)


def _effective_channel(session: Session, project: Project) -> dict:
    """Kênh dùng khi đăng nháp: kênh đã chọn, chưa chọn thì khớp nhạc cụ + thể loại."""
    from backend.models import ChannelMapping
    from backend.video.service import match_channel
    if project.channel_id:
        m = session.get(ChannelMapping, project.channel_id)
        if m:
            return m.model_dump()
    rows = [m.model_dump() for m in session.exec(select(ChannelMapping)).all()]
    return match_channel(rows, project.instrument or "", project.music_style or "",
                         getattr(project, "music_line", "") or "") or {}


def to_response(session: Session, project: Project) -> ProjectResponse:
    track_count, mix_count = _counts(session, project.id)
    channel = _effective_channel(session, project)
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at,
        track_count=track_count, mix_count=mix_count,
        video_idea=project.video_idea, auto_video=project.auto_video,
        video_style=project.video_style,
        intro_mode=getattr(project, "intro_mode", None) or "thumbnail",
        cast_mode=getattr(project, "cast_mode", None) or "character_pet",
        footage_scene_mode=getattr(project, "footage_scene_mode", None) or "auto",
        creative_instrument=bool(getattr(project, "creative_instrument", True)),
        instrument=project.instrument, music_style=project.music_style,
        music_line=getattr(project, "music_line", "") or "",
        auto_upload=project.auto_upload,
        music_source=getattr(project, "music_source", "local"),
        suno_idea=getattr(project, "suno_idea", ""),
        batch_id=getattr(project, "batch_id", None),
        kind=getattr(project, "kind", None) or "mixset",
        mix_duration_minutes=project.mix_duration_minutes or 120.0,
        mix_crossfade_seconds=project.mix_crossfade_seconds or 5.0,
        channel_id=project.channel_id,
        effective_channel_id=channel.get("id"),
        effective_channel_name=channel.get("channel_name") or "",
    )


def _check_channel(session: Session, channel_id) -> None:
    """Kênh chọn cho project phải là 1 dòng trong ⚙️ Cài đặt → YouTube/GPMLogin."""
    from backend.models import ChannelMapping
    if channel_id is not None and session.get(ChannelMapping, channel_id) is None:
        raise HTTPException(400, f"Kênh YouTube #{channel_id} không tồn tại — chọn lại kênh.")


_IDEA_FIELDS = ("video_idea", "suno_idea")


def _ideas_to_english(values: dict) -> dict:
    """Ý tưởng người dùng nhập được lưu bằng TIẾNG ANH để mọi prompt đồng nhất.
    Dịch lỗi → giữ nguyên bản gốc (generator vẫn dịch lại lúc chạy)."""
    from backend.video.translate import needs_translation, to_english_or_none
    for key in _IDEA_FIELDS:
        text = values.get(key)
        if isinstance(text, str) and needs_translation(text):
            translated = to_english_or_none(
                text, log=lambda m: print(f"[translate] {m}", flush=True))
            if translated:
                values[key] = translated
    return values


def _canonical_instrument(name: str) -> str:
    """Nhạc cụ trong danh mục → nhãn chuẩn (vd 'piano' → 'Piano'); tên tự nhập giữ nguyên."""
    from backend.video.music_spec import find_instrument
    spec = find_instrument(name or "")
    if not spec:
        return ""
    return spec["label"] if spec["key"] != "custom" else name.strip()


def get_project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    # Project trong Thùng rác = như đã xoá với mọi thao tác thường.
    if project is None or project.deleted_at is not None:
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project)
                            .where(Project.deleted_at == None)  # noqa: E711
                            .order_by(Project.created_at.desc())).all()
    return [to_response(session, p) for p in projects]


@router.get("/progress")
def projects_progress(session: Session = Depends(get_session)):
    """Tình trạng từng project: đã xong / đang ở giai đoạn nào (Nhạc → Mix → Ảnh →
    Clip → Video → Nháp YT → Short). Chỉ đọc DB + đĩa + job trong bộ nhớ."""
    from backend.project_progress import STEPS, all_progress
    return {"steps": [{"key": k, "label": label} for k, label in STEPS],
            "projects": all_progress(session)}


# ---------- Thùng rác: xoá = giữ 7 ngày rồi mới xoá vĩnh viễn ----------
def _trashed_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.deleted_at is None:
        raise HTTPException(404, f"Project {project_id} không có trong Thùng rác")
    return project


@router.get("/trash")
def list_trash(session: Session = Depends(get_session)):
    from backend import project_trash
    items = project_trash.list_trash(session)
    return {"retention_days": project_trash.RETENTION_DAYS, "items": items,
            "total_bytes": sum(i["size_bytes"] for i in items)}


@router.post("/trash/empty")
def empty_trash():
    """Xoá vĩnh viễn MỌI project trong Thùng rác (không chờ đủ 7 ngày)."""
    from sqlmodel import Session as DbSession
    from backend.database import engine
    from backend import project_trash
    with DbSession(engine) as session:
        ids = [p.id for p in session.exec(
            select(Project).where(Project.deleted_at != None)).all()]  # noqa: E711
    results = [_purge_one(pid) for pid in ids]
    return {"results": results, "deleted": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "freed_bytes": sum(r.get("freed_bytes", 0) for r in results),
            "retention_days": project_trash.RETENTION_DAYS}


def _purge_one(project_id: int) -> dict:
    from sqlmodel import Session as DbSession
    from backend.database import engine
    from backend.project_cleanup import ProjectBusyError, ProjectCleanupError
    from backend import project_trash
    with DbSession(engine) as session:
        project = session.get(Project, project_id)
        if project is None or project.deleted_at is None:
            return {"id": project_id, "name": f"#{project_id}", "ok": False,
                    "error": "Không có trong Thùng rác."}
        name = project.name
        try:
            info = project_trash.purge_trashed(session, project)
            return {"id": project_id, "name": name, "ok": True, **info}
        except ProjectBusyError as exc:
            return {"id": project_id, "name": name, "ok": False, "busy": True,
                    "error": str(exc)}
        except (ProjectCleanupError, OSError) as exc:
            session.rollback()
            return {"id": project_id, "name": name, "ok": False, "error": str(exc)}


@router.post("/trash/{project_id}/restore", response_model=ProjectResponse)
def restore_project(project_id: int, session: Session = Depends(get_session)):
    from backend.project_cleanup import ProjectCleanupError
    from backend import project_trash
    project = _trashed_or_404(session, project_id)
    try:
        project_trash.restore_from_trash(session, project)
    except (ProjectCleanupError, OSError) as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    session.refresh(project)
    return to_response(session, project)


@router.delete("/trash/{project_id}")
def purge_project(project_id: int, session: Session = Depends(get_session)):
    """Xoá vĩnh viễn ngay 1 project đang ở Thùng rác."""
    _trashed_or_404(session, project_id)
    result = _purge_one(project_id)
    if not result["ok"]:
        raise HTTPException(409 if result.get("busy") else 500, result["error"])
    return result


@router.post("", response_model=ProjectResponse)
def create_project(data: ProjectCreate, session: Session = Depends(get_session)):
    values = _ideas_to_english(data.model_dump())
    values["instrument"] = _canonical_instrument(values.get("instrument", ""))
    values["music_style"] = (values.get("music_style") or "").strip()
    values["music_line"] = (values.get("music_line") or "").strip()
    _check_channel(session, values.get("channel_id"))
    from backend.video import channel_looks
    values["video_style"] = channel_looks.resolve_video_style(
        values.get("video_style"), values["instrument"], values["music_style"],
        channel_id=values.get("channel_id"), music_line=values["music_line"])
    # «Tạo 1 project thủ công» chỉ chuẩn bị đầy đủ bộ công cụ; không tự nối
    # Mix → Video → YouTube. Người dùng vẫn có thể bấm Tự động chạy ở taskbar.
    values["auto_video"] = False
    values["auto_upload"] = False
    project = Project(**values)
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


# ---------- Batch: sản xuất hàng loạt N sản phẩm YouTube ----------
def _read_mixset_lines(filename: str, payload: bytes) -> list[str]:
    """Đọc mỗi dòng/đoạn văn trong TXT hoặc DOCX thành một truy vấn bài hát."""
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("File danh sách vượt quá 2 MB.")
    suffix = (filename or "").lower().rsplit(".", 1)[-1]
    if suffix == "txt":
        text = None
        for encoding in ("utf-8-sig", "utf-16", "cp1258"):
            try:
                text = payload.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if text is None:
            raise ValueError("Không đọc được file TXT; hãy lưu file ở UTF-8.")
        raw = text.splitlines()
    elif suffix == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 8 * 1024 * 1024:
                    raise ValueError("Nội dung DOCX quá lớn.")
                xml = archive.read(info)
            root = ElementTree.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            raw = []
            for paragraph in root.iter(ns + "p"):
                part = ""
                for node in paragraph.iter():
                    if node.tag == ns + "t":
                        part += node.text or ""
                    elif node.tag in (ns + "br", ns + "cr"):
                        raw.append(part)
                        part = ""
                raw.append(part)
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise ValueError("File DOCX không hợp lệ hoặc đã hỏng.") from exc
    else:
        raise ValueError("Chỉ hỗ trợ file .txt hoặc .docx.")
    lines = []
    for value in raw:
        value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", value).strip()
        if value:
            lines.append(value)
    if not lines:
        raise ValueError("File chưa có dòng bài hát nào.")
    return lines


@router.post("/mixset", response_model=BatchRunResponse)
async def create_mixset(
    file: UploadFile = File(...), name: str = Form(...),
    music_style: str = Form(...), music_line: str = Form(""),
    channel_id: int | None = Form(None), video_style: str = Form("channel"),
    theme: str = Form(""), mix_duration_minutes: float = Form(120),
    intro_mode: str = Form("thumbnail"), cast_mode: str = Form("character_pet"),
    footage_scene_mode: str = Form("auto"),
    creative_instrument: bool = Form(True),
):
    """Một danh sách bài → một project Mix Set → cover/mix/video/YouTube draft."""
    from backend import batch_service
    try:
        lines = _read_mixset_lines(file.filename or "", await file.read())
        return batch_service.create_cover_mixset(
            name=name, song_lines=lines, music_style=music_style,
            music_line=music_line, channel_id=channel_id,
            video_style=video_style, theme=theme,
            mix_duration_minutes=mix_duration_minutes,
            intro_mode=intro_mode, cast_mode=cast_mode,
            footage_scene_mode=footage_scene_mode,
            creative_instrument=creative_instrument)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/singles", response_model=BatchRunResponse)
async def create_singles(
    file: UploadFile = File(...),
    music_style: str = Form(...), music_line: str = Form(""),
    channel_id: int | None = Form(None), video_style: str = Form("channel"),
    theme: str = Form(""),
    intro_mode: str = Form("thumbnail"), cast_mode: str = Form("character_pet"),
    footage_scene_mode: str = Form("auto"),
    creative_instrument: bool = Form(True),
):
    """Một danh sách bài → MỖI BÀI một project Track lẻ (ảnh bìa + video riêng)."""
    from backend import batch_service
    try:
        lines = _read_mixset_lines(file.filename or "", await file.read())
        return batch_service.create_cover_singles(
            song_lines=lines, music_style=music_style, music_line=music_line,
            channel_id=channel_id, video_style=video_style, theme=theme,
            intro_mode=intro_mode, cast_mode=cast_mode,
            footage_scene_mode=footage_scene_mode,
            creative_instrument=creative_instrument)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/batch", response_model=BatchRunResponse)
def create_batch(data: BatchCreate):
    """Sinh N ý tưởng phân biệt (AI, KHÔNG tốn credit Suno) → tạo N project cùng
    batch_id → chạy TUẦN TỰ trọn pipeline (Suno→Mix→Video→đăng nháp) cho từng cái.
    Cảnh báo ngân sách + xác nhận đã thực hiện ở phía UI trước khi gọi."""
    from backend import batch_service
    try:
        return batch_service.create_batch(
            count=data.count, instrument=data.instrument,
            music_style=data.music_style, video_style=data.video_style,
            music_line=data.music_line, theme=data.theme, target_tracks=data.target_tracks,
            intro_mode=data.intro_mode, cast_mode=data.cast_mode,
            max_create_actions=data.max_create_actions,
            channel_name=data.channel_name, channel_id=data.channel_id,
            mix_duration_minutes=data.mix_duration_minutes,
        )
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/instruments")
def instrument_catalog():
    """Danh mục nhạc cụ + thể loại cho form New Project (đơn lẻ và hàng loạt)."""
    from backend.video import music_spec
    return {
        "instruments": music_spec.instrument_options(),
        "genres": [{"key": g["key"], "label": g["label"],
                    "bpm": list(g["bpm"]), "beatless": g["beatless"]}
                   for g in music_spec.GENRES],
    }


@router.get("/batch/styles")
def batch_styles():
    """Danh sách phong cách ảnh cho form tạo lô (chưa có project nên không dùng
    được /api/projects/{id}/video/styles). Cùng nguồn video/config.STYLES."""
    from backend.video import config as video_config
    return {
        "default": video_config.DEFAULT_STYLE,
        "styles": [
            {"key": key, "icon": s.get("icon", ""),
             "label": s.get("label", key), "desc": s.get("desc", "")}
            for key, s in video_config.STYLES.items()
        ],
    }


@router.get("/batch/active")
def active_batch():
    """Lô đang chạy (thread orchestrator còn sống) — để UI theo dõi đúng lô kể cả
    khi request tạo lô bị timeout phía trình duyệt nên chưa nhận được batch_key."""
    from backend import batch_service
    return {"batch_key": batch_service._active_batch_key()}


@router.get("/batch/{batch_key}", response_model=BatchRunResponse)
def get_batch(batch_key: str):
    from backend import batch_service
    run = batch_service.get_batch(batch_key)
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.post("/batch/{batch_key}/cancel", response_model=BatchRunResponse)
def cancel_batch(batch_key: str):
    from backend import batch_service
    run = batch_service.cancel_batch(batch_key)
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.post("/batch/{batch_key}/resume", response_model=BatchRunResponse)
def resume_batch(batch_key: str):
    """Chạy tiếp lô đã dừng: bỏ qua sản phẩm đã xong, nối lại chỗ dừng."""
    from backend import batch_service
    try:
        run = batch_service.resume_batch(batch_key)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.post("/batch/{batch_key}/skip-missing-lyrics", response_model=BatchRunResponse)
def skip_missing_lyrics(batch_key: str):
    """Bỏ qua các bài thiếu lời và tiếp tục Mix Set bằng các bài đã có lời."""
    from backend import batch_service
    try:
        run = batch_service.skip_missing_lyrics(batch_key)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run is None:
        raise HTTPException(404, f"Không tìm thấy Mix Set {batch_key}")
    return run


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: int, session: Session = Depends(get_session)):
    from backend.routers.mixes import to_mix_response
    project = get_project_or_404(session, project_id)
    base = to_response(session, project)
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)
        .order_by(Track.added_at)).all()
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)
        .order_by(Mix.created_at.desc())).all()
    return ProjectDetailResponse(
        **base.model_dump(),
        tracks=[TrackResponse.model_validate(t, from_attributes=True) for t in tracks],
        mixes=[to_mix_response(m) for m in mixes],
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, data: ProjectUpdate,
                   session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    values = data.model_dump(exclude_unset=True)
    if "channel_id" in values:
        _check_channel(session, values["channel_id"])
    for key in ("intro_mode", "cast_mode", "footage_scene_mode", "creative_instrument"):
        if key in values and values[key] is None:
            values.pop(key)                    # null = giữ nguyên lựa chọn cũ
    new_name = values.pop("name", None)
    if new_name is not None and new_name.strip() != (project.name or ""):
        # Tên gắn với thư mục media + chữ ký prompt → đi qua luồng đổi tên an toàn.
        _do_rename(session, project, new_name)
    new_kind = values.pop("kind", None)
    if new_kind and new_kind != (getattr(project, "kind", None) or "mixset"):
        _do_change_kind(session, project, new_kind)
    for key, value in _ideas_to_english(values).items():
        if key == "video_style":
            from backend.video import config as video_config
            value = video_config.normalize_style(value)
        elif key in ("music_style", "music_line"):
            value = (value or "").strip()
        elif key == "instrument" and (value or "").strip() != (project.instrument or ""):
            # Chỉ chuẩn hoá khi THẬT SỰ đổi — giữ nguyên chuỗi cũ (vd lấy từ ánh xạ
            # kênh) để chữ ký hồ sơ prompt của project đã có ảnh không bị lệch.
            value = _canonical_instrument(value or "")
        setattr(project, key, value)
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


def _do_rename(session: Session, project: Project, new_name: str) -> dict:
    from backend.project_cleanup import ProjectBusyError
    from backend.project_rename import RenameError, rename_project
    try:
        return rename_project(session, project, new_name)
    except ProjectBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RenameError as exc:
        raise HTTPException(400, str(exc)) from exc


def _do_change_kind(session: Session, project: Project, kind: str) -> dict:
    from backend.project_cleanup import ProjectBusyError
    from backend.project_rename import RenameError, change_kind
    try:
        return change_kind(session, project, kind)
    except ProjectBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RenameError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{project_id}/rename")
def rename(project_id: int, data: ProjectRename,
           session: Session = Depends(get_session)):
    """Đổi tên project (kèm thư mục media, đường dẫn DB, tiêu đề prompt), rồi
    tuỳ `refresh` cập nhật CHỮ tiêu đề trên thumbnail + intro video:
    none = chỉ đổi tên · text = vẽ lại chữ + ghép lại video ·
    regen = tạo lại ảnh bìa (máy ảnh mặc định Muse.ai) + clip 00 rồi như text."""
    from backend.video import config as vconfig
    from backend.video import service as vservice
    from backend.video.job_manager import video_job_manager
    project = get_project_or_404(session, project_id)
    refresh = data.refresh or "none"
    if refresh not in ("none", "text", "regen"):
        raise HTTPException(400, "refresh phải là none | text | regen.")
    if refresh != "none" and video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ video chạy — chờ xong rồi "
                                 "đổi tên kèm cập nhật thumbnail.")
    if refresh == "regen" and not (
            vconfig.images_dir(project_id, project.name) / "prompts.json").exists():
        raise HTTPException(400, "Project chưa có bộ prompt/ảnh — chưa thể tạo "
                                 "lại ảnh bìa. Chọn «Chỉ đổi tên».")
    info = _do_rename(session, project, data.name)
    job = None
    if refresh != "none":
        video_job_manager.submit(project_id, "retitle", vservice.retitle_video,
                                 project.name, project.video_style,
                                 refresh == "regen")
        job = "retitle"
    return {**info, "job": job, "project": to_response(session, project)}


@router.delete("/{project_id}")
def delete_project(project_id: int, permanent: bool = False,
                   session: Session = Depends(get_session)):
    """Mặc định: chuyển vào Thùng rác (giữ 7 ngày, khôi phục được).
    `?permanent=true` = xoá vĩnh viễn ngay như trước."""
    project = get_project_or_404(session, project_id)
    from backend.project_cleanup import (ProjectBusyError, ProjectCleanupError,
                                         delete_project_data)
    from backend import project_trash
    try:
        if permanent:
            return delete_project_data(session, project)
        return project_trash.move_to_trash(session, project)
    except ProjectBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ProjectCleanupError, OSError) as exc:
        session.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/bulk-delete")
def bulk_delete_projects(body: ProjectBulkDelete):
    """Chuyển nhiều project vào Thùng rác (giữ 7 ngày) — LẦN LƯỢT từng cái, mỗi
    cái 1 session riêng: 1 project lỗi/bận không chặn các project còn lại.
    `permanent=true` trong body = xoá vĩnh viễn ngay (hành vi cũ)."""
    from sqlmodel import Session as DbSession
    from backend.database import engine
    from backend.project_cleanup import (ProjectBusyError, ProjectCleanupError,
                                         delete_project_data, sweep_pending)
    from backend import project_trash
    permanent = bool(getattr(body, "permanent", False))
    results = []
    for project_id in dict.fromkeys(body.ids):      # bỏ trùng, giữ thứ tự
        with DbSession(engine) as session:
            project = session.get(Project, project_id)
            if project is None or project.deleted_at is not None:
                results.append({"id": project_id, "name": f"#{project_id}",
                                "ok": False, "error": "Project không tồn tại."})
                continue
            name = project.name
            try:
                if permanent:
                    info = delete_project_data(session, project)
                    results.append({"id": project_id, "name": name, "ok": True,
                                    "warnings": info["warnings"],
                                    "cleanup_pending": info["cleanup_pending"],
                                    "freed_bytes": info["freed_bytes"]})
                else:
                    info = project_trash.move_to_trash(session, project)
                    results.append({"id": project_id, "name": name, "ok": True,
                                    "trashed": True, "warnings": info["warnings"],
                                    "freed_bytes": 0})
            except ProjectBusyError as exc:
                results.append({"id": project_id, "name": name, "ok": False,
                                "busy": True, "error": str(exc)})
            except (ProjectCleanupError, OSError) as exc:
                session.rollback()
                results.append({"id": project_id, "name": name, "ok": False,
                                "error": str(exc)})
    # Nhân tiện dọn lại phần còn sót (file bị khoá) của các lần xoá trước.
    swept = {"freed_bytes": 0}
    try:
        with DbSession(engine) as session:
            swept = sweep_pending(session)
    except Exception as exc:        # noqa: BLE001 — dọn phụ, không làm hỏng kết quả xoá
        print(f"[bulk-delete] sweep lỗi: {exc}", flush=True)
    return {
        "results": results,
        "deleted": sum(1 for r in results if r["ok"]),
        "trashed": not permanent,
        "retention_days": project_trash.RETENTION_DAYS,
        "failed": sum(1 for r in results if not r["ok"]),
        "freed_bytes": sum(r.get("freed_bytes", 0) for r in results)
        + swept.get("freed_bytes", 0),
    }
