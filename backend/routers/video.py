"""Router phần Video: tạo ảnh, tạo clip, ghép video, theo dõi tiến độ."""
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from backend.database import get_session
from backend.models import Project
from backend.video import config, service
from backend.video.job_manager import video_job_manager

router = APIRouter(prefix="/api/projects/{project_id}/video", tags=["video"])

FOOTAGE_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}


class RunImagesBody(BaseModel):
    topic: Optional[str] = None
    idea: Optional[str] = None      # ý tưởng người dùng → Claude viết prompt
    mode: str = "restart"          # "restart" = làm lại từ đầu | "resume" = tạo tiếp

class RunClipsBody(BaseModel):
    mode: str = "restart"          # "restart" | "resume"

class ManualClipsBody(BaseModel):
    only: Optional[list[int]] = None   # None = mọi clip còn thiếu; [k] = làm lại clip k
    assemble: bool = True              # đủ clip + có nhạc → ghép video luôn

class AssembleBody(BaseModel):
    audio_path: Optional[str] = None
    seed: Optional[int] = None

class RunAllBody(BaseModel):
    topic: Optional[str] = None
    idea: Optional[str] = None      # ý tưởng người dùng → Claude viết prompt
    audio_path: Optional[str] = None
    seed: Optional[int] = None

class RebuildBody(BaseModel):
    audio_path: Optional[str] = None
    seed: Optional[int] = None
    mode: str = "restart"          # clip mode: "restart" (tạo lại clip) | "resume"


def _project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p or getattr(p, "deleted_at", None):
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return p

def _guard_busy():
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ video chạy. Vui lòng đợi.")


def _footage_name(name: str) -> str:
    """Tên file an toàn, không cho đường dẫn từ tên upload đi ra ngoài project."""
    p = Path(name or "footage.mp4")
    stem = re.sub(r"[^\w .()-]+", "_", p.stem, flags=re.UNICODE).strip(" .")
    return f"{(stem or 'footage')[:100]}{p.suffix.lower()}"


@router.get("/styles")
def list_styles(project_id: int):
    """Danh sách phong cách hình ảnh/video cho bộ chọn ở UI (cuộn được)."""
    return {
        "default": config.DEFAULT_STYLE,
        "styles": [
            {
                "key": key,
                "icon": s.get("icon", ""),
                "label": s.get("label", key),
                "desc": s.get("desc", ""),
            }
            for key, s in config.STYLES.items()
        ],
    }


class ThumbnailFontBody(BaseModel):
    key: str


def _project_recipe(project_id: int, project_name: str | None):
    """Công thức thumbnail riêng của project (None nếu lỗi → kiểu cũ)."""
    try:
        from backend.video import thumbnail_recipe
        return thumbnail_recipe.recipe_for_project(project_id, project_name)
    except Exception:
        return None


@router.get("/thumbnail-fonts")
def list_thumbnail_fonts(project_id: int, db: Session = Depends(get_session)):
    """Danh mục font tiêu đề (thumbnail + intro) + font của RIÊNG project này
    (công thức thumbnail: tự động theo phong cách kênh, hoặc khoá tay)."""
    from backend.video import channel_looks
    from backend.video import thumbnail as th
    from backend.video import thumbnail_recipe as tr
    project = _project_or_404(db, project_id)
    fonts = [{"key": f["key"], "label": f["label"], "note": f["note"]}
             for f in th.FONT_CATALOG if Path(f["file"]).exists()]
    have = {f["key"] for f in fonts}
    recipe = _project_recipe(project_id, project.name)
    if not recipe:
        return {"selected": th.selected_font_key(), "default": th.DEFAULT_FONT_KEY,
                "auto": False, "auto_font": "", "suggested": [], "recipe": None,
                "fonts": fonts}
    lk = channel_looks.look_info(recipe.get("look"))
    lock = recipe.get("font_lock") or ""
    return {
        "selected": lock or recipe.get("font") or th.DEFAULT_FONT_KEY,
        "default": th.DEFAULT_FONT_KEY,
        # "Tự động" nghĩa là tool tự chọn rồi KHOÁ một font nhận diện theo kênh;
        # font_lock vì vậy vẫn luôn có giá trị.
        "auto": not bool(recipe.get("font_manual")),
        "auto_font": recipe.get("font") or "",
        "suggested": [k for k in lk["fonts"] if k in have],
        "recipe": {**tr.describe(recipe), "look_icon": lk["icon"],
                   "look_styles": list(lk["styles"]),
                   "subtitle": tr.project_subtitle(project_id, recipe)},
        "fonts": fonts,
    }


@router.put("/thumbnail-fonts")
def set_thumbnail_font(project_id: int, body: ThumbnailFontBody,
                       db: Session = Depends(get_session)):
    """Khoá font cho RIÊNG project này; key "auto"/rỗng = tự động theo phong cách kênh."""
    from backend.video import thumbnail as th
    from backend.video import thumbnail_recipe as tr
    project = _project_or_404(db, project_id)
    key = (body.key or "").strip()
    if key.lower() == "auto":
        key = ""
    if key:
        f = th.font_entry(key)
        if not f or not Path(f["file"]).exists():
            raise HTTPException(400, f"Font '{body.key}' không có trên máy.")
    try:
        rec = tr.set_font_lock(project_id, key, project.name)
    except Exception as exc:
        raise HTTPException(500, f"Không lưu được font cho project: {exc}")
    return {"selected": key or rec.get("font") or th.DEFAULT_FONT_KEY, "auto": not key}


class ClipEngineBody(BaseModel):
    engine: str


@router.put("/clip-engine")
def set_clip_engine(project_id: int, body: ClipEngineBody):
    """Chọn máy tạo clip (chung mọi project): flow | gemini | muse. Không đổi
    giữa lúc job video đang chạy (job đang chạy giữ máy đã chọn lúc bắt đầu)."""
    key = (body.engine or "").strip().lower()
    if key not in config.CLIP_ENGINES:
        raise HTTPException(400, f"Máy tạo clip '{body.engine}' không hợp lệ.")
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có job video chạy — đợi xong rồi hãy đổi máy tạo clip.")
    config.save_override("clip_engine", key)
    return {"clip_engine": key, "clip_engine_label": config.CLIP_ENGINE_LABELS[key]}


@router.get("/thumbnail-fonts/{key}/preview")
def thumbnail_font_preview(project_id: int, key: str,
                           db: Session = Depends(get_session)):
    """Ảnh mẫu: tên project viết bằng font `key` trên ảnh bìa của project, theo
    kiểu chữ (bố cục/hiệu ứng/màu/dòng phụ) của công thức thumbnail project."""
    import hashlib
    import json as _json
    from backend.video import thumbnail as th
    from backend.video import thumbnail_recipe as tr
    project = _project_or_404(db, project_id)
    if not th.font_entry(key):
        raise HTTPException(404, f"Không có font '{key}'.")
    title = (project.name or "Healing Music").strip()
    bg = config.images_dir(project_id, project.name) / "0.png"
    recipe = _project_recipe(project_id, project.name)
    style = tr.render_style(recipe) if recipe else None
    subtitle = (tr.project_subtitle(project_id, recipe) if recipe
                else service._thumbnail_keywords())
    look_sig = _json.dumps({k: (recipe or {}).get(k) for k in
                            ("look", "layout", "treatment", "palette", "sub_font", "upper")},
                           sort_keys=True)
    stamp = f"{title}|{bg.stat().st_mtime if bg.exists() else 0}|{subtitle}|{look_sig}"
    h = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:10]
    out = config.OVERRIDES.parent / "thumb_font_previews" / f"{project_id}_{key}_{h}.jpg"
    if not out.exists() and not th.render_font_preview(
            key, out, title, subtitle, bg if bg.exists() else None, style=style):
        raise HTTPException(500, "Không vẽ được ảnh mẫu cho font này.")
    return FileResponse(str(out), media_type="image/jpeg")


@router.get("/status")
def status(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    media = service.list_media(project_id, project.name)
    job = video_job_manager.get(project_id)
    p = config.PARAMS
    # Bộ ảnh đã chuẩn bị theo hồ sơ nào thì nhãn theo hồ sơ đó; chưa có → theo project.
    img_dir = config.images_dir(project_id, project.name)
    cast_mode = (config.images_cast_mode(img_dir) if (img_dir / "prompts.json").exists()
                 else config.normalize_cast_mode(getattr(project, "cast_mode", None)))
    media["job"] = None if not job else {
        "kind": job.kind, "status": job.status,
        "percent": job.percent, "message": job.message, "error": job.error,
    }
    media["params"] = {
        "image_count": p.image_count, "clip_seconds": p.clip_seconds,
        "rest_clips": p.rest_clips, "total_clips": p.total_clips,
        "t_window": p.t_window, "model": p.flow_model,
        # Máy tạo clip: flow (mặc định) | gemini | muse (override clip_engine).
        "clip_engine": config.clip_engine(), "clip_engine_label": service.clip_engine_label(),
        # Máy tạo ảnh: chatgpt (mặc định, dự phòng muse) | muse | gemini (override image_engine).
        "image_engine": config.image_engine(),
        "image_engine_label": config.image_engine_label(),
        "image_fallback_label": (config.image_engine_label(config.image_fallback_engine())
                                 if config.image_fallback() else None),
        "clip_engines": [{"key": k, "label": config.CLIP_ENGINE_LABELS.get(k, k)}
                         for k in config.CLIP_ENGINES],
        "aspect_ratio": p.aspect_ratio, "flow_mode": p.flow_mode,
        "blend_seconds": p.blend_seconds,
        # Chỉ cảnh vật ghép ở tốc độ thật (không làm chậm như đứng hình).
        "slow_speed": (config.SCENERY_PLAYBACK_SPEED if cast_mode == "scenery"
                       else p.slow_speed),
        # Nhãn vai trò 3 ảnh (0 bìa, 1 nhân vật chính, 2 linh thú) cho UI —
        # chế độ chỉ cảnh vật: bảng bối cảnh + bảng ánh sáng.
        "image_roles": {str(k): v for k, v in config.image_roles(cast_mode).items()
                        if k < p.image_count},
        # Tool Cover: ảnh bìa → 2 bảng (vẽ từ bìa) → clip 00 intro, 01 loop, 02–05 góc quay.
        "single_image": config.single_image(p),
        "cast_mode": cast_mode,
        "intro_mode": config.normalize_intro_mode(getattr(project, "intro_mode", None)),
    }
    from backend.video.flow_manual import manual_state
    media["manual"] = manual_state(project_id)
    # Thumbnail: engine đang dùng (ai | pil) + cấu hình Thumbnail AI + mtime
    # (cache-buster để UI tải ảnh mới sau khi vẽ lại).
    from backend.video import thumbnail_ai
    final_d = config.final_dir(project_id, project.name)
    media["thumbnail_ai"] = thumbnail_ai.status(final_d)
    try:
        media["thumbnail_mtime"] = int((final_d / "thumbnail.png").stat().st_mtime)
    except OSError:
        media["thumbnail_mtime"] = None
    return media


@router.post("/footage")
def upload_footage(project_id: int, file: UploadFile = File(...),
                   db: Session = Depends(get_session)):
    """Upload thêm một cảnh quay đời thật (project có thể có nhiều footage). Khi ghép,
    clip AI chạy 1 lần rồi các footage lặp tới hết video."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    safe_name = _footage_name(file.filename or "footage.mp4")
    ext = Path(safe_name).suffix.lower()
    if ext not in FOOTAGE_EXTS:
        raise HTTPException(400, "Chỉ nhận video: MP4, MOV, M4V, MKV hoặc WEBM.")
    root = config.footage_dir(project_id, project.name)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".uploading-{uuid.uuid4().hex}{ext}"
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        from backend.video.assembler import probe_duration, probe_fps
        duration = probe_duration(str(tmp))
        if duration <= 0 or probe_fps(str(tmp)) <= 0:
            raise ValueError("file không có video stream hợp lệ")
        # Nhiều footage/project: thêm vào danh sách, trùng tên thì đánh số.
        dest = root / safe_name
        n = 2
        while dest.exists():
            dest = root / f"{Path(safe_name).stem} ({n}){ext}"
            n += 1
        tmp.replace(dest)
    except (ValueError, RuntimeError) as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"Không đọc được footage: {exc}") from exc
    finally:
        file.file.close()
        tmp.unlink(missing_ok=True)
    return {"status": "uploaded", "name": dest.name,
            "duration": round(float(duration), 3),
            "count": len(config.footage_files(project_id, project.name))}


@router.post("/footage/render")
def render_with_footage(project_id: int, db: Session = Depends(get_session)):
    """Nút «Tạo video có footage»: ghép lại final.mp4 — clip AI chạy 1 lần,
    sau đó footage lặp tới hết video (nhạc giữ nguyên)."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    if not config.footage_file(project_id, project.name):
        raise HTTPException(400, "Project chưa có footage — hãy upload footage trước.")
    video_job_manager.submit(project_id, "assemble", service.step_assemble,
                             None, None, project.name, footage_at_end=True)
    return {"status": "submitted", "kind": "assemble", "footage_position": "loop_to_end"}


def _footage_by_name(project_id: int, project_name: str, name: str | None):
    files = config.footage_files(project_id, project_name)
    if name is None:
        return files[0] if files else None
    return next((f for f in files if f.name == name), None)


@router.get("/footage")
def preview_footage(project_id: int, name: str | None = None,
                    db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    path = _footage_by_name(project_id, project.name, name)
    if not path:
        raise HTTPException(404, "Project chưa có footage.")
    return FileResponse(str(path), filename=path.name, media_type="video/mp4")


@router.delete("/footage")
def delete_footage(project_id: int, name: str | None = None,
                   db: Session = Depends(get_session)):
    """name = xoá 1 footage; không có name = xoá tất cả footage của project."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    targets = ([f for f in [_footage_by_name(project_id, project.name, name)] if f]
               if name is not None else config.footage_files(project_id, project.name))
    for path in targets:
        path.unlink(missing_ok=True)
    return {"ok": True, "deleted": bool(targets), "count": len(targets)}


@router.get("/prompts")
def prompt_manifest(project_id: int, db: Session = Depends(get_session)):
    from backend.video.prompt_workflow import read_manifest
    project = _project_or_404(db, project_id)
    try:
        return {"manifest": read_manifest(project_id, project.name)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/prompts/prepare")
def prepare_prompts(project_id: int, body: RunImagesBody = RunImagesBody(),
                    db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    idea = body.idea if body.idea is not None else project.video_idea
    video_job_manager.submit(project_id, "prompts", service.step_prepare_prompts,
                             project.name, idea, project.video_style)
    return {"status": "submitted", "kind": "prompts"}


@router.post("/images")
def gen_images(project_id: int, body: RunImagesBody,
               db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    topic = (body.topic or project.name or "meditation").strip()
    mode = "resume" if body.mode == "resume" else "restart"
    idea = (body.idea if body.idea is not None else project.video_idea).strip() or None
    if mode == "resume":
        # «Tạo tiếp ảnh thiếu» = chạy tiếp CẢ chu trình: ảnh → clip → ghép →
        # nháp YouTube → nháp Short (bước nào xong rồi thì bỏ qua).
        video_job_manager.submit(project_id, "continue", service.continue_from_images,
                                 topic, project.name, idea, project.video_style)
        return {"status": "submitted", "kind": "continue", "topic": topic,
                "mode": mode, "has_idea": bool(idea)}
    video_job_manager.submit(project_id, "images", service.step_images,
                             topic, mode, project.name, idea, project.video_style)
    return {"status": "submitted", "kind": "images", "topic": topic,
            "mode": mode, "has_idea": bool(idea)}


@router.post("/clips")
def gen_clips(project_id: int, body: RunClipsBody = RunClipsBody(),
              db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    mode = "resume" if body.mode == "resume" else "restart"
    if mode == "resume":
        # «Tạo tiếp clip thiếu» = clip thiếu → ghép → nháp YouTube → nháp Short.
        video_job_manager.submit(project_id, "continue", service.continue_from_clips,
                                 project.name, project.video_style)
        return {"status": "submitted", "kind": "continue", "mode": mode}
    video_job_manager.submit(project_id, "clips", service.step_clips,
                             mode, project.name, project.video_style)
    return {"status": "submitted", "kind": "clips", "mode": mode}


@router.get("/clips/manual")
def manual_clip_prompts(project_id: int, db: Session = Depends(get_session)):
    """Chế độ thủ công: đủ N prompt clip (đúng chữ tool sẽ gửi Flow) + nguyên
    liệu + clip đã có chưa + trạng thái phiên thủ công đang chạy."""
    from backend.video import flow_driver
    from backend.video.flow_manual import manual_state
    project = _project_or_404(db, project_id)
    try:
        service._validate_saved_workflow(project_id, project.name)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    clips = flow_driver.clip_prompt_list(project_id, project.name,
                                         config.normalize_style(project.video_style))
    return {"total": config.PARAMS.total_clips, "clips": clips,
            "state": manual_state(project_id)}


@router.post("/clips/manual")
def start_manual_clips(project_id: int, body: ManualClipsBody = ManualClipsBody(),
                       db: Session = Depends(get_session)):
    """"Tạo video thủ công": tool mở Flow + đưa 2 bảng nhân vật vào ô soạn,
    người dùng dán prompt + bấm Tạo, tool tải về + đặt tên clip_NN.mp4."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    media = service.list_media(project_id, project.name)
    if media["image_count"] < config.PARAMS.image_count:
        raise HTTPException(400, f"Chưa đủ {config.PARAMS.image_count} ảnh — hãy tạo ảnh trước.")
    only = None
    if body.only:
        only = sorted({int(k) for k in body.only})
        bad = [k for k in only if not (0 <= k < config.PARAMS.total_clips)]
        if bad:
            raise HTTPException(400, f"Chỉ số clip không hợp lệ: {bad} "
                                     f"(0..{config.PARAMS.total_clips - 1}).")
    video_job_manager.submit(project_id, "clips-manual", service.run_manual_clips,
                             project.name, project.video_style, only, body.assemble)
    return {"status": "submitted", "kind": "clips-manual", "only": only}


@router.post("/clips/{index}/import")
async def import_clip(project_id: int, index: int, file: UploadFile = File(...),
                      db: Session = Depends(get_session)):
    """Nhập 1 file mp4 người dùng tự tạo (vd ở trình duyệt riêng khi cửa sổ của
    tool bị gắn cờ) → clips/clip_NN.mp4. Cho phép cả khi phiên thủ công đang
    chờ — phiên sẽ nhận file này và chuyển sang clip kế."""
    import tempfile
    from backend.video.flow_manual import import_clip_file
    project = _project_or_404(db, project_id)
    job = video_job_manager.get(project_id)
    if video_job_manager.is_busy() and not (
            job and job.kind == "clips-manual"
            and job.status in ("pending", "running", "paused")):
        raise HTTPException(409, "Đang có tác vụ video tự động chạy — đợi xong hoặc huỷ "
                                 "rồi mới nhập file clip.")
    if not (file.filename or "").lower().endswith(".mp4"):
        raise HTTPException(400, "Chỉ nhận file .mp4.")
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    try:
        with open(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        try:
            dest = import_clip_file(project_id, project.name, index, Path(tmp))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"status": "imported", "index": index, "path": str(dest)}


@router.post("/assemble")
def assemble(project_id: int, body: AssembleBody,
             db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "assemble", service.step_assemble,
                             body.audio_path, body.seed, project.name)
    return {"status": "submitted", "kind": "assemble"}


@router.post("/run-all")
def run_all(project_id: int, body: RunAllBody,
            db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    topic = (body.topic or project.name or "meditation").strip()
    idea = (body.idea if body.idea is not None else project.video_idea).strip() or None
    video_job_manager.submit(project_id, "full", service.run_full_video,
                             topic, body.audio_path, body.seed, project.name,
                             idea, project.video_style)
    return {"status": "submitted", "kind": "full", "topic": topic,
            "has_idea": bool(idea)}


@router.post("/rebuild")
def rebuild(project_id: int, body: RebuildBody = RebuildBody(),
            db: Session = Depends(get_session)):
    """Tạo lại VIDEO từ ảnh ĐÃ CÓ (không tạo lại ảnh): clip → ghép."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    media = service.list_media(project_id, project.name)
    if media["image_count"] < config.PARAMS.image_count:
        raise HTTPException(
            400, f"Chưa đủ {config.PARAMS.image_count} ảnh để tạo lại video "
                 f"(hiện có {media['image_count']}). Hãy tạo ảnh trước.")
    mode = "resume" if body.mode == "resume" else "restart"
    video_job_manager.submit(project_id, "rebuild", service.run_video_from_images,
                             body.audio_path, body.seed, project.name,
                             project.video_style, mode)
    return {"status": "submitted", "kind": "rebuild", "mode": mode}


@router.post("/images/{index}/regenerate")
def regen_image(project_id: int, index: int,
                db: Session = Depends(get_session)):
    """Tạo lại ĐÚNG 1 ảnh (ghi đè), giữ nguyên ảnh khác + dùng lại prompt đã lưu."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    if not (0 <= index < config.PARAMS.image_count):
        raise HTTPException(400, f"Chỉ số ảnh {index} không hợp lệ "
                                 f"(0..{config.PARAMS.image_count - 1}).")
    video_job_manager.submit(project_id, "images", service.regen_image,
                             index, project.name, project.video_style)
    return {"status": "submitted", "kind": "images", "index": index,
            "single": True}


@router.post("/clips/{index}/regenerate")
def regen_clip(project_id: int, index: int,
               db: Session = Depends(get_session)):
    """Tạo lại ĐÚNG 1 clip (ghi đè), giữ nguyên clip khác."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    # Clip nhiều hơn ảnh (3 ảnh → N clip) nên bound theo total_clips.
    if not (0 <= index < config.PARAMS.total_clips):
        raise HTTPException(400, f"Chỉ số clip {index} không hợp lệ "
                                 f"(0..{config.PARAMS.total_clips - 1}).")
    video_job_manager.submit(project_id, "clips", service.regen_clip,
                             index, project.name, project.video_style)
    return {"status": "submitted", "kind": "clips", "index": index,
            "single": True}


@router.post("/motions/regenerate")
def regen_motions(project_id: int, db: Session = Depends(get_session)):
    """Sinh lại CHỈ bộ prompt cảnh (motions) từ hai bảng nhân vật đã khoá."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "motions", service.regen_motions,
                             project.name, project.video_style)
    return {"status": "submitted", "kind": "motions"}


class ThumbnailBody(BaseModel):
    # "chatgpt" = ChatGPT web vẽ ảnh + chữ (tài khoản ChatGPT của bạn, mặc định)
    # | "ai" = model ảnh qua API (tốn phí) | "pil" = chữ font
    engine: str = "chatgpt"


@router.post("/thumbnail")
def make_thumbnail(project_id: int, body: ThumbnailBody = ThumbnailBody(),
                   db: Session = Depends(get_session)):
    """Vẽ lại final/thumbnail.png từ ảnh bìa. engine="chatgpt" mở ChatGPT web
    (chưa đăng nhập → kéo cửa sổ lên chờ bạn TỰ đăng nhập); "ai" gọi model ảnh
    qua API (tốn phí, người dùng bấm). Lỗi/chữ sai → tự dùng chữ font."""
    from backend.video import thumbnail_ai
    project = _project_or_404(db, project_id)
    _guard_busy()
    if not (config.images_dir(project_id, project.name) / "0.png").exists():
        raise HTTPException(400, "Chưa có ảnh bìa (ảnh 0) — hãy tạo ảnh trước.")
    engine = body.engine if body.engine in ("chatgpt", "ai", "pil") else "chatgpt"
    if engine == "ai" and not thumbnail_ai.load_settings().ready:
        raise HTTPException(400, "Chưa nhập API key Thumbnail AI — mở ⚙️ Cài đặt → "
                                 "Thumbnail AI để dán key.")
    video_job_manager.submit(project_id, "thumbnail", service.make_thumbnail,
                             project.name, engine)
    return {"status": "submitted", "kind": "thumbnail", "engine": engine}


@router.post("/upload-youtube")
def upload_youtube(project_id: int, db: Session = Depends(get_session)):
    """Đăng NHÁP video final.mp4 lên YouTube qua GPMLogin (thủ công). Kênh = kênh
    đã chọn cho project (chưa chọn → khớp instrument + music_style). KHÔNG publish."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    final = config.final_dir(project_id, project.name) / "final.mp4"
    if not final.exists():
        raise HTTPException(400, "Chưa có final.mp4 — hãy render video trước.")
    if not service.channel_for(project.channel_id, project.instrument or "",
                               project.music_style or "", project.music_line or ""):
        raise HTTPException(400, service._no_channel_error(
            project.instrument or "", project.music_style or "",
            music_line=project.music_line or ""))
    video_job_manager.submit(project_id, "upload", service.step_upload_youtube_and_short,
                             project.name, project.instrument, project.music_style)
    return {"status": "submitted", "kind": "upload"}


@router.post("/upload-short")
def upload_short(project_id: int, db: Session = Depends(get_session)):
    """Cắt đoạn dọc 9:16 ở GIỮA final.mp4 → YouTube Short, AI viết nội dung ngắn,
    đăng NHÁP lên cùng kênh với video dài. KHÔNG publish."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    final = config.final_dir(project_id, project.name) / "final.mp4"
    if not final.exists():
        raise HTTPException(400, "Chưa có final.mp4 — hãy render video trước.")
    if not service.channel_for(project.channel_id, project.instrument or "",
                               project.music_style or "", project.music_line or ""):
        raise HTTPException(400, service._no_channel_error(
            project.instrument or "", project.music_style or "",
            music_line=project.music_line or ""))
    video_job_manager.submit(project_id, "upload_short", service.step_upload_short,
                             project.name, project.instrument, project.music_style)
    return {"status": "submitted", "kind": "upload_short"}


class ShortSettingsBody(BaseModel):
    auto_short: Optional[bool] = None
    short_seconds: Optional[float] = None


@router.get("/short-settings")
def get_short_settings(project_id: int):
    """Cài đặt YouTube Short (toàn cục): tự tạo sau nháp video dài, độ dài đoạn."""
    from backend.video import shorts
    return shorts.settings()


@router.post("/short-settings")
def set_short_settings(project_id: int, body: ShortSettingsBody):
    from backend.video import shorts
    if body.auto_short is not None:
        config.save_override("auto_short", bool(body.auto_short))
    if body.short_seconds is not None:
        config.save_override("short_seconds", float(
            min(max(body.short_seconds, shorts.MIN_SECONDS), shorts.MAX_SECONDS)))
    return shorts.settings()


@router.get("/youtube-history")
def youtube_history(project_id: int, db: Session = Depends(get_session)):
    """Lịch sử các lần đăng nháp của project (title/description/hashtag/thời gian)."""
    from backend.models import YoutubeUpload
    from sqlmodel import select
    _project_or_404(db, project_id)
    rows = db.exec(
        select(YoutubeUpload).where(YoutubeUpload.project_id == project_id)
        .order_by(YoutubeUpload.id.desc())
    ).all()
    return [{
        "id": r.id, "channel_name": r.channel_name, "title": r.title,
        "kind": "short" if (r.video_path or "").endswith("short.mp4") else "long",
        "hashtags": r.hashtags, "status": r.status,
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.post("/cancel")
def cancel(project_id: int, db: Session = Depends(get_session)):
    """Huỷ tác vụ video đang chạy (hợp tác — dừng ở ranh giới ảnh/clip kế)."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)
    if not ok:
        raise HTTPException(409, "Không có tác vụ video nào đang chạy để huỷ.")
    return {"status": "cancelling", "project_id": project_id}


@router.post("/pause")
def pause(project_id: int, db: Session = Depends(get_session)):
    """Tạm dừng tác vụ video đang chạy (dừng ở checkpoint kế, giữ nguyên state)."""
    _project_or_404(db, project_id)
    ok = video_job_manager.pause(project_id)
    if not ok:
        raise HTTPException(409, "Không có tác vụ video nào đang chạy để tạm dừng.")
    return {"status": "pausing", "project_id": project_id}


@router.post("/resume")
def resume(project_id: int, db: Session = Depends(get_session)):
    """Tiếp tục tác vụ video đang tạm dừng từ đúng checkpoint đang dở."""
    _project_or_404(db, project_id)
    ok = video_job_manager.resume(project_id)
    if not ok:
        raise HTTPException(409, "Không có tác vụ video nào đang tạm dừng để tiếp tục.")
    return {"status": "resuming", "project_id": project_id}


@router.post("/open-browser")
def open_browser(project_id: int, site: str = "flow", db: Session = Depends(get_session)):
    """Mở Muse/Flow/Gemini/ChatGPT bằng đúng profile tool dùng để theo dõi/đăng nhập/kiểm tra.
    Chạy trên worker video duy nhất → bị khoá khi đang có tác vụ video."""
    from backend.video import monitor_browser
    _project_or_404(db, project_id)
    if site not in monitor_browser.SITES:
        raise HTTPException(400, f"site phải là: {', '.join(monitor_browser.SITES)}")
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có tác vụ video chạy (hoặc cửa sổ trình duyệt đang mở) "
                                 "— không mở thêm để tránh 2 cửa sổ tranh cùng 1 profile.")
    video_job_manager.submit(project_id, f"browser-{site}",
                             monitor_browser.run_monitor_browser, site=site)
    return {"status": "opening", "site": site, "project_id": project_id}


@router.post("/close-browser")
def close_browser(project_id: int, db: Session = Depends(get_session)):
    _project_or_404(db, project_id)
    job = video_job_manager.get(project_id)
    if not job or not job.kind.startswith("browser-") or not video_job_manager.cancel(project_id):
        raise HTTPException(409, "Không có cửa sổ trình duyệt nào đang mở.")
    return {"status": "closing", "project_id": project_id}


@router.get("/progress")
async def progress(project_id: int):
    async def gen():
        async for event in video_job_manager.stream(project_id):
            yield f"event: {event.get('type','message')}\ndata: {json.dumps(event)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/download")
def download(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    final = config.final_dir(project_id, project.name) / "final.mp4"
    if not final.exists():
        raise HTTPException(404, "Chưa có video final. Hãy ghép video trước.")
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in (project.name or "video")).strip()
    return FileResponse(str(final), media_type="video/mp4",
                        filename=f"{safe or 'video'}_final.mp4")


@router.get("/image/{index}")
def get_image(project_id: int, index: int,
              db: Session = Depends(get_session)):
    """Trả ảnh {index}.png để UI hiển thị gallery (xem trước)."""
    project = _project_or_404(db, project_id)
    img = config.images_dir(project_id, project.name) / f"{index}.png"
    if not img.exists():
        raise HTTPException(404, f"Chưa có ảnh {index}.")
    return FileResponse(str(img), media_type="image/png")


@router.get("/clip/{index}")
def get_clip(project_id: int, index: int,
             db: Session = Depends(get_session)):
    """Trả clip_{index}.mp4 để UI xem trước từng clip."""
    project = _project_or_404(db, project_id)
    clip = config.clips_dir(project_id, project.name) / f"clip_{index:02d}.mp4"
    if not clip.exists():
        raise HTTPException(404, f"Chưa có clip {index}.")
    return FileResponse(str(clip), media_type="video/mp4")


@router.get("/thumbnail")
def thumbnail(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    thumb = config.final_dir(project_id, project.name) / "thumbnail.png"
    if not thumb.exists():
        raise HTTPException(404, "Chưa có thumbnail. Hãy ghép video (assemble) "
                                 "để tạo thumbnail kèm chữ.")
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in (project.name or "video")).strip()
    return FileResponse(str(thumb), media_type="image/png",
                        filename=f"{safe or 'video'}_thumbnail.png")
