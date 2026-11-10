"""
video/service.py — Điều phối pipeline Video:
  0) Chuẩn bị hồ sơ chung + prompt đầy đủ và kiểm tra chống trùng.
  1) Tạo 3 ảnh (Muse.ai, dự phòng Gemini): 0 = thumbnail, 1 = bảng phân tích nhân vật chính,
     2 = bảng phân tích linh thú.
  2) Tạo thumbnail có chữ; clip 00 dùng chính thumbnail làm tham chiếu video
     intro. Clip 01..N nạp ảnh 1 + ảnh 2, chỉ khác prompt bối cảnh.
  3) Shuffle theo t_window + fade + gắn nhạc → final/final.mp4.

Chạy trong worker thread (Playwright sync API + ffmpeg).

⚠️  Quy ước tham số: job_manager gọi fn(project_id, progress_cb, *args).
    Vì vậy MỌI hàm step_* phải có chữ ký (project_id, progress_cb, ...extra).
"""
from __future__ import annotations
import json
import random
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .shuffle import build_video_sequence, build_chained_sequence
from .assembler import (assemble_video, assemble_video_blend, probe_duration,
                        overlay_title_on_clip)

_ROOT = Path(__file__).parent.parent.parent
OUTPUTS_ROOT = _ROOT / "outputs"       # nơi phần Audio lưu mix.wav


def _thumbnail_keywords(n: int = 3) -> str:
    kws = (config.load_overrides().get("thumbnail_keywords")
           or config.THUMBNAIL_KEYWORDS)
    k = min(n, len(kws))
    return " · ".join(random.sample(list(kws), k)) if k else ""


def _title_look(project_id: int, project_name: str | None):
    """Công thức thumbnail của project → (style cho thumbnail.py, dòng phụ,
    finish video theo phong cách kênh, recipe). Lỗi → kiểu cũ."""
    P = config.PARAMS
    # Tool Cover: dòng phụ = chữ theo phong cách/ngôn ngữ cover (hoặc người dùng tự gõ).
    from backend.cover.branding import cover_subtitle
    try:
        from . import channel_looks, thumbnail_recipe
        recipe = thumbnail_recipe.recipe_for_project(project_id, project_name)
        if recipe:
            return (thumbnail_recipe.render_style(recipe),
                    cover_subtitle(project_id),
                    channel_looks.finish_for(recipe.get("look"), recipe), recipe)
    except Exception:
        pass
    return (None, cover_subtitle(project_id),
            {"grade": "", "transition": "softfade", "blend_seconds": P.blend_seconds,
             "slow_speed": P.slow_speed}, None)


def _resolve_prompts(
    project_id: int,
    project_name: str | None,
    idea: str | None,
    progress_cb: Callable[[str, float], None] | None = None,
    style_key: str | None = None,
    *,
    allow_rebuild: bool = False,
) -> dict:
    from .prompt_workflow import prepare
    saved = prepare(project_id, project_name, idea, style_key,
                    log=lambda msg: progress_cb(msg, 1.0) if progress_cb else None,
                    allow_rebuild=allow_rebuild)
    return saved["prompts"]


def step_prepare_prompts(project_id: int, progress_cb, project_name=None,
                         idea=None, style_key=None) -> dict:
    prompts = _resolve_prompts(project_id, project_name, idea, progress_cb, style_key)
    progress_cb("Đã chuẩn bị và kiểm tra bộ prompt; chưa tạo ảnh/video.", 100)
    return {"count": len(prompts)}


def _validate_saved_workflow(project_id: int, project_name: str | None) -> dict:
    from .prompt_workflow import ensure_english_manifest, validate_manifest
    # Hồ sơ cũ còn tiếng Việt → tự dịch sang tiếng Anh trước khi dùng.
    saved = ensure_english_manifest(project_id, project_name)
    if saved is None:
        raise RuntimeError("Chưa có bộ prompt đã lưu. Hãy chuẩn bị prompt trước khi tạo media.")
    validate_manifest(project_id, saved)
    return saved


def find_latest_audio(project_id: int,
                      project_name: str | None = None) -> Optional[str]:
    """Nhạc nền mới nhất cho video: xét CẢ mix.wav (phần Audio) LẪN file upload
    trực tiếp (media/<project>/uploads/*). Trả về file có mtime mới nhất — nên
    upload 1 track dài xong là dùng ngay được, không cần render mix 15 track."""
    candidates: list[Path] = []
    kind = _project_kind(project_id)

    # Track lẻ luôn ưu tiên đúng cover/master; không được chọn nhầm mix.wav cũ.
    if kind == "single":
        con = _db()
        try:
            rows = con.execute(
                "SELECT t.filepath FROM coversong cs JOIN track t ON t.id=cs.track_id "
                "WHERE cs.project_id=? ORDER BY cs.position, cs.id", (project_id,)
            ).fetchall()
            covers = [Path(r["filepath"]) for r in rows
                      if r["filepath"] and Path(r["filepath"]).is_file()]
        finally:
            con.close()
        if covers:
            covers.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(covers[0])
    else:
        pdir = OUTPUTS_ROOT / str(project_id)
        if pdir.exists():
            candidates.extend(pdir.glob("*/mix.wav"))

    up = config.uploads_dir(project_id, project_name)
    if up.exists():
        candidates.extend(p for p in up.iterdir()
                          if p.is_file() and p.suffix.lower() in config.AUDIO_EXTS)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _project_kind(project_id: int) -> str:
    """``single`` | ``mixset``; lỗi DB thì giữ hành vi cũ là mixset."""
    con = _db()
    try:
        row = con.execute("SELECT kind FROM project WHERE id=?", (project_id,)).fetchone()
        return "single" if row and row["kind"] == "single" else "mixset"
    except Exception:
        return "mixset"
    finally:
        con.close()


def _video_display_title(project_id: int, project_name: str | None) -> str:
    """Track lẻ lấy đúng tên bài từ CoverSong, không kèm tên ca sĩ/query."""
    if _project_kind(project_id) != "single":
        return (project_name or "Healing").strip()
    con = _db()
    try:
        row = con.execute(
            "SELECT title FROM coversong WHERE project_id=? AND trim(title)!='' "
            "ORDER BY position,id LIMIT 1", (project_id,)).fetchone()
        if not row:
            row = con.execute(
                "SELECT title FROM sourcesong WHERE project_id=? AND trim(title)!='' "
                "ORDER BY position,id LIMIT 1", (project_id,)).fetchone()
        return ((row["title"] if row else "") or project_name or "Cover").strip()
    except Exception:
        return (project_name or "Cover").strip()
    finally:
        con.close()


def list_media(project_id: int, project_name: str | None = None) -> dict:
    """Tổng quan trạng thái media của project (để UI hiển thị)."""
    img_dir = config.images_dir(project_id, project_name)
    clip_dir = config.clips_dir(project_id, project_name)
    final_d = config.final_dir(project_id, project_name)
    final = final_d / "final.mp4"
    thumb = final_d / "thumbnail.png"
    images = sorted(str(p) for p in img_dir.glob("*.png")) if img_dir.exists() else []
    clips = sorted(str(p) for p in clip_dir.glob("clip_*.mp4")) if clip_dir.exists() else []
    # Chỉ số ảnh/clip đang có (để UI vẽ gallery + nút "Tạo lại" từng cái).
    image_indices: list[int] = []
    if img_dir.exists():
        for p in img_dir.glob("*.png"):
            try:
                image_indices.append(int(p.stem))
            except ValueError:
                pass
    clip_indices: list[int] = []
    if clip_dir.exists():
        for p in clip_dir.glob("clip_*.mp4"):
            try:
                clip_indices.append(int(p.stem.split("_")[1]))
            except (ValueError, IndexError):
                pass
    image_indices.sort()
    clip_indices.sort()
    audio = find_latest_audio(project_id, project_name)
    audio_dur = None
    if audio:
        try:
            audio_dur = probe_duration(audio)
        except Exception:
            audio_dur = None
    footage_list = []
    for f in config.footage_files(project_id, project_name):
        try:
            f_dur = probe_duration(str(f))
        except Exception:
            f_dur = None
        footage_list.append({"name": f.name, "duration": f_dur})
    footage = config.footage_file(project_id, project_name)
    footage_mode = _project_footage_scene_mode(project_id)
    footage_dur = (sum(x["duration"] or 0 for x in footage_list)
                   if footage_list else None)
    return {
        "images": images,
        "image_count": len(images),
        "image_indices": image_indices,
        "clips": clips,
        "clip_count": len(clips),
        "clip_indices": clip_indices,
        "final_exists": final.exists(),
        "final_path": str(final) if final.exists() else None,
        "thumbnail_path": str(thumb) if thumb.exists() else None,
        "audio_path": audio,
        "audio_duration": audio_dur,
        "footage_exists": bool(footage),
        "footage_path": str(footage) if footage else None,
        "footage_name": footage.name if footage else None,
        "footage_duration": footage_dur,
        "footage_list": footage_list,
        "footage_scene_mode": footage_mode,
    }


# ── Các bước riêng lẻ (progress_cb luôn ở vị trí thứ 2) ─────────

def step_images(project_id: int,
                progress_cb: Callable[[str, float], None],
                topic: str,
                mode: str = "restart",
                project_name: str | None = None,
                idea: str | None = None,
                style_key: str | None = None) -> list[str]:
    # Tạo ảnh bằng Muse.ai (mặc định); Muse hỏng → tự chuyển Gemini web vẽ lại
    # toàn bộ (_generate_images).
    # mode="resume": bỏ qua ảnh đã có, chỉ tạo ảnh còn thiếu.
    # idea: ý tưởng người dùng → Claude viết prompt bám theo (đồng bộ nhân vật).
    # style_key: "2d"/"3d" → quyết định phong cách ép vào mọi prompt ảnh.
    style_key = config.normalize_style(style_key)
    prompts_override = _resolve_prompts(project_id, project_name, idea,
                                        progress_cb, style_key,
                                        allow_rebuild=(mode == "restart"))
    # Ảnh 0 giữ SẠCH (không chữ). Tiêu đề chỉ overlay ở bước ghép video
    # (step_assemble) lên clip intro + thumbnail.png — tránh chữ nướng sẵn bị
    # Veo tạo lại thành bóng ma/chồng chéo với overlay ghép.
    return _generate_images(project_id, topic, progress_cb,
                            resume=(mode == "resume"), project_name=project_name,
                            prompts_override=prompts_override, style=style_key)


def _generate_images(project_id: int, topic: str,
                     progress_cb: Callable[[str, float], None], *,
                     resume: bool = False, project_name: str | None = None,
                     prompts_override: Optional[dict] = None,
                     style: str | None = None,
                     only: Optional[list[int]] = None) -> list[str]:
    """Tạo ảnh bằng máy chính (config.image_engine — mặc định Muse.ai). Máy chính
    hỏng (hết lượt ảnh / lỗi máy chủ / bộ lọc / mất đăng nhập) → TỰ chuyển máy dự
    phòng (Gemini) vẽ lại TOÀN BỘ ảnh TỪ ĐẦU (1 → 2 → 0, cùng bộ prompt) để cả bộ
    đồng nhất một máy, rồi chuỗi đi tiếp clip → ghép → nháp YouTube → Short
    (yêu cầu người dùng 2026-10-07, đảo Muse chính 2026-10-08). Tạo lại 1 ảnh lẻ
    (only) → máy dự phòng chỉ vẽ ảnh đó."""
    from .job_manager import JobCancelled
    kw = dict(resume=resume, project_name=project_name,
              prompts_override=prompts_override, style=style, only=only)

    def _run(engine: str) -> list[str]:
        if engine == "chatgpt":
            from .chatgpt_image import generate_images_chatgpt
            return generate_images_chatgpt(project_id, topic, config.PARAMS, progress_cb, **kw)
        if engine == "muse":
            from .muse_image import generate_images_muse
            return generate_images_muse(project_id, topic, config.PARAMS, progress_cb, **kw)
        from .gemini_driver import generate_images
        return generate_images(project_id, topic, config.PARAMS, progress_cb, **kw)

    main = config.image_engine()
    backup = config.image_fallback_engine()
    main_l, backup_l = config.image_engine_label(main), config.image_engine_label(backup)
    try:
        return _run(main)
    except JobCancelled:
        raise
    except (ValueError, FileNotFoundError):
        raise              # lỗi bộ prompt/dữ liệu — máy dự phòng cũng sẽ hỏng y hệt
    except Exception as e:
        if not config.image_fallback():
            raise
        err1 = " ".join(str(e).split())[:300]
        # Tool Cover: 2 bảng vẽ TỪ ảnh bìa (đính kèm) → bộ ảnh vẫn đồng nhất dù
        # khác máy; giữ ảnh đã có, máy dự phòng chỉ vẽ ảnh còn thiếu.
        keep = config.single_image()
        if only is None:
            # máy chính đã tự xoá ảnh cũ nếu restart → ảnh còn lại là ảnh mới vẽ
            kw["resume"] = keep            # không giữ → vẽ lại TOÀN BỘ từ đầu
        order = " → ".join(str(i) for i in config.image_order())
        what = (f"ảnh {sorted(only)}" if only is not None
                else f"ảnh còn thiếu ({order})" if keep
                else f"TOÀN BỘ ảnh từ đầu ({order})")
        progress_cb(f"{main_l} không tạo được ảnh — «{err1[:160]}» → chuyển sang "
                    f"{backup_l} vẽ lại {what}…", 1.0)
        try:
            log = config.images_dir(project_id, project_name) / "_image_fallback.log"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {main_l} lỗi → "
                        f"{backup_l} ({what}): {err1}\n")
        except Exception:
            pass
        try:
            return _run(backup)
        except JobCancelled:
            raise
        except Exception as e2:
            raise RuntimeError(f"{' '.join(str(e2).split())} || (Trước đó {main_l} lỗi: "
                               f"{err1[:200]})") from e2


def _clip_generator():
    """Hàm tạo clip theo máy đang chọn — cùng chữ ký generate_clips."""
    engine = config.clip_engine()
    if engine == "flow":
        from .flow_driver import generate_clips
        return generate_clips
    if engine == "muse":
        from .muse_video import generate_clips_muse
        return generate_clips_muse
    from .gemini_video import generate_clips_gemini
    return generate_clips_gemini


def clip_engine_label() -> str:
    return config.CLIP_ENGINE_LABELS[config.clip_engine()]


def step_clips(project_id: int,
               progress_cb: Callable[[str, float], None],
               mode: str = "restart",
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    # mode="resume": giữ clip đã tạo, chỉ tạo tiếp clip còn thiếu.
    # style_key: "2d"/"3d" → gợi ý phong cách gắn vào prompt chuyển động.
    # Máy tạo clip: Muse.ai (mặc định) / Flow / Gemini Video (config.clip_engine()).
    _validate_saved_workflow(project_id, project_name)
    # Thumbnail phải tồn tại TRƯỚC khi tạo clip 00, vì clip intro dùng chính ảnh
    # thumbnail có chữ làm tham chiếu. render_thumbnail có cache nên resume không
    # tạo lại/tốn thêm lượt khi đầu vào chưa đổi.
    if _project_intro_mode(project_id) == "thumbnail":
        style, subtitle, _finish, recipe = _title_look(project_id, project_name)
        progress_cb("Chuẩn bị thumbnail có chữ làm tham chiếu video intro 00:00", 0.5)
        render_thumbnail_ab_set(
            project_id, project_name, subtitle,
            log=lambda m: progress_cb(m, 1.0), style=style, recipe=recipe,
            cancel_check=getattr(progress_cb, "is_cancelled", None))
        thumb = config.final_dir(project_id, project_name) / "thumbnail.png"
        if not thumb.exists():
            raise RuntimeError(
                "Không tạo được thumbnail có chữ nên chưa thể làm video intro. "
                "Hãy kiểm tra ChatGPT Thumbnail rồi chạy tiếp.")
    generate_clips = _clip_generator()
    return generate_clips(project_id, config.PARAMS, progress_cb,
                          resume=(mode == "resume"), project_name=project_name,
                          style=config.normalize_style(style_key))


def run_manual_clips(project_id: int,
                     progress_cb: Callable[[str, float], None],
                     project_name: str | None = None,
                     style_key: str | None = None,
                     only: Optional[list[int]] = None,
                     assemble: bool = True) -> dict:
    """"Tạo video thủ công" (bán tự động): tool mở Flow + đưa nguyên liệu, người
    dùng dán prompt + bấm Tạo, tool tải + đặt tên clip_NN.mp4. Đủ clip và có
    nhạc → ghép video (+ đăng nháp nếu project bật auto_upload)."""
    from .flow_manual import manual_clips
    _validate_saved_workflow(project_id, project_name)
    manual_clips(project_id, progress_cb, project_name=project_name,
                 style=config.normalize_style(style_key), only=only)
    total = config.PARAMS.total_clips
    clip_dir = config.clips_dir(project_id, project_name)
    missing = [k for k in range(total)
               if not (clip_dir / f"clip_{k:02d}.mp4").exists()]
    if missing:
        progress_cb(f"Còn thiếu {len(missing)} clip ({', '.join(f'{k:02d}' for k in missing)}) "
                    f"— chưa ghép video.", 100.0)
        return {"clips_missing": missing}
    if not assemble or only:
        progress_cb(f"Đã đủ {total} clip — bấm 'Ghép video' khi sẵn sàng.", 100.0)
        return {"clips_missing": []}
    if not find_latest_audio(project_id, project_name):
        progress_cb(f"Đã đủ {total} clip nhưng chưa có nhạc nền — mix audio rồi "
                    f"bấm 'Ghép video'.", 100.0)
        return {"clips_missing": [], "assembled": False}
    progress_cb("Đủ clip — ghép video + gắn nhạc…", 96.0)
    result = step_assemble(project_id,
                           lambda m, p: progress_cb(m, 96.0 + p * 0.04),
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
    return result if isinstance(result, dict) else {"assembled": True}


def _load_saved_prompts(project_id: int,
                        project_name: str | None) -> Optional[dict]:
    """Đọc THẲNG bộ prompt ảnh đã lưu (images/prompts.json → 'prompts') để tạo
    lại đúng 1 ảnh mà KHÔNG lệ thuộc điều kiện cache phong cách. Trả None nếu
    chưa có (→ driver dùng DEFAULT_PROMPTS)."""
    prompts_path = config.images_dir(project_id, project_name) / "prompts.json"
    if not prompts_path.exists():
        return None
    try:
        saved = json.loads(prompts_path.read_text(encoding="utf-8"))
        pr = saved.get("prompts")
        if isinstance(pr, dict) and pr:
            return {str(k): v for k, v in pr.items()}
    except Exception:
        pass
    return None


def regen_image(project_id: int,
                progress_cb: Callable[[str, float], None],
                index: int,
                project_name: str | None = None,
                style_key: str | None = None) -> list[str]:
    """Tạo lại ĐÚNG 1 ảnh (ghi đè), GIỮ NGUYÊN mọi ảnh khác. Dùng lại prompt đã
    lưu để nhân vật/bối cảnh vẫn khớp cả bộ."""
    style_key = config.normalize_style(style_key)
    prompts_override = _validate_saved_workflow(project_id, project_name)["prompts"]
    return _generate_images(
        project_id, (project_name or "Healing"), progress_cb,
        project_name=project_name, prompts_override=prompts_override,
        style=style_key, only=[int(index)])


def regen_clip(project_id: int,
               progress_cb: Callable[[str, float], None],
               index: int,
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    """Tạo lại ĐÚNG 1 clip (ghi đè), GIỮ NGUYÊN mọi clip khác. Nguyên liệu mỗi
    clip là cố định (ảnh 1 + ảnh 2) nên không cần kế hoạch cũ."""
    generate_clips = _clip_generator()
    _validate_saved_workflow(project_id, project_name)
    return generate_clips(
        project_id, config.PARAMS, progress_cb, project_name=project_name,
        style=config.normalize_style(style_key), only=[int(index)])


def regen_motions(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  project_name: str | None = None,
                  style_key: str | None = None) -> dict:
    """Sinh LẠI CHỈ bộ prompt cảnh (motions) từ HAI bảng nhân vật đã khoá và ghi
    đè 'motions' trong images/prompts.json. KHÔNG tạo lại ảnh/clip — lần tạo clip
    sau sẽ dùng bộ cảnh mới. Trả {"count": N} khi thành công."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_scene_prompts

    def _log(m: str):
        if progress_cb:
            progress_cb(m, 1.0)

    prompts_path = config.images_dir(project_id, project_name) / "prompts.json"
    if not prompts_path.exists():
        raise RuntimeError(
            "Chưa có bộ prompt (images/prompts.json). Hãy tạo ảnh từ 'Ý tưởng' "
            "một lần để sinh prompt, rồi mới sinh lại bộ cảnh.")
    from .prompt_workflow import ensure_english_manifest
    try:
        # tự cắt 40 → N cảnh + dịch hồ sơ cũ sang tiếng Anh
        saved = ensure_english_manifest(project_id, project_name, extend=False)
    except Exception as e:
        raise RuntimeError(f"Không đọc được prompts.json ({e}).")
    prompts = saved.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise RuntimeError("prompts.json không có 'prompts' hợp lệ.")
    if config.single_image():
        # Tool Cover: viết lại chuyển động intro, loop, 4 góc quay; GIỮ prompt ảnh.
        from . import cover_scene
        from .prompt_workflow import validate_manifest, save_manifest
        cast_mode = config.images_cast_mode(prompts_path.parent)
        style_key = config.normalize_style(style_key or saved.get("style_key"))
        # Giữ đúng nhân vật của ảnh đã vẽ (hồ sơ cũ không có «cast» = 1 cô gái).
        couple = (saved.get("continuity") or {}).get("cast") == "couple"
        _, motions, cont = cover_scene.build(
            saved.get("context") or {}, config.style_info(style_key)["brief"], cast_mode,
            api_config=get_api_config_from_db(str(DB_PATH)), log=_log, couple=couple)
        saved["motions"] = motions
        saved["prompt_hashes"] = validate_manifest(project_id, saved, len(prompts),
                                                   len(motions))
        save_manifest(prompts_path, saved)
        _log("Đã viết lại chuyển động intro, loop + 4 góc quay — tạo clip lần sau sẽ dùng.")
        return {"count": len(motions)}
    locks = saved.get("continuity") or {}
    character_sheet = (locks.get("character_sheet") or "").strip()
    pet_sheet = (locks.get("pet_sheet") or "").strip()
    if len(character_sheet) < 20 or len(pet_sheet) < 20:
        raise RuntimeError(
            "prompts.json thiếu khoá continuity (character_sheet / pet_sheet). "
            "Hãy chuẩn bị lại bộ prompt bằng 'Làm lại từ đầu'.")
    idea = (saved.get("idea") or "").strip()
    style_key = config.normalize_style(style_key or saved.get("style_key"))
    scenes = config.PARAMS.total_clips   # sinh lại ĐỦ số cảnh theo cấu hình hiện tại
    from .prompt_workflow import manifest_cast_mode, scene_suffix
    cast_mode = manifest_cast_mode(saved)
    # hồ sơ chỉ cảnh vật: character_sheet = bảng bối cảnh, pet_sheet = bảng ánh sáng
    mode_kw = {"cast_mode": cast_mode} if cast_mode == "scenery" else {}

    _log(f"Sinh lại {scenes} prompt cảnh từ "
         + ("bảng bối cảnh + bảng ánh sáng" if mode_kw else "hai bảng nhân vật")
         + " đã khoá…")
    motions = generate_scene_prompts(
        character_sheet=character_sheet, pet_sheet=pet_sheet, idea=idea,
        style=config.style_info(style_key)["brief"],
        api_config=get_api_config_from_db(str(DB_PATH)),
        scene_count=scenes, log=_log,
        instrument=(saved.get("context") or {}).get("instrument", "")
        or _project_instrument(project_id), **mode_kw)
    if not motions or len(motions) != scenes:
        raise RuntimeError(
            "AI chưa sinh lại đủ bộ prompt cảnh (kiểm tra API key/model ở ⚙️ "
            "Settings rồi thử lại).")

    from .prompt_workflow import validate_manifest, save_manifest
    suffix = scene_suffix(cast_mode)
    saved["motions"] = {k: v + suffix for k, v in motions.items()}
    if "scene_count" in saved and saved["scene_count"] != scenes:
        from .prompt_workflow import _resign
        _resign(dict(saved), saved, scenes)
        saved["scene_count"] = scenes
    saved["prompt_hashes"] = validate_manifest(project_id, saved,
                                               len(prompts), scenes)
    try:
        save_manifest(prompts_path, saved)
    except Exception as e:
        raise RuntimeError(f"Không lưu được bộ cảnh mới ({e}).")
    _log(f"Đã cập nhật {len(motions)} prompt cảnh — tạo clip lần sau sẽ dùng.")
    return {"count": len(motions)}


def step_assemble(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  audio_path: Optional[str] = None,
                  seed: Optional[int] = None,
                  project_name: str | None = None,
                  footage_at_end: bool = False) -> dict:
    """Có footage: intro → mỗi clip AI chạy đúng 1 lần → các footage lặp đi lặp
    lại tới hết video. footage_at_end=True (nút «Tạo video có footage») chỉ
    bắt buộc project phải có footage."""
    if footage_at_end and not config.footage_file(project_id, project_name):
        raise RuntimeError("Project chưa có footage — hãy upload footage trước.")
    audio = audio_path or find_latest_audio(project_id, project_name)
    if not audio:
        raise RuntimeError("Không tìm thấy audio. Hãy render 1 bản mix ở phần "
                           "Audio, HOẶC upload 1 track nhạc nền, trước khi ghép "
                           "video (hoặc chỉ định audio_path).")
    P = config.PARAMS
    clip_dir = config.clips_dir(project_id, project_name)
    expected = [clip_dir / f"clip_{i:02d}.mp4"
                for i in range(P.total_clips)]
    missing = [p.name for p in expected if not p.exists()]
    if missing:
        raise RuntimeError(
            f"Chưa đủ {P.total_clips} clip để ghép; còn thiếu: "
            f"{', '.join(missing)}.")
    clips = [str(p) for p in expected]

    dur = probe_duration(audio)

    # clip_00 là intro và chỉ xuất hiện đúng MỘT lần trong toàn video. Các clip
    # còn lại (mỗi clip một bối cảnh riêng) được trộn ngẫu nhiên thành cycle rồi
    # loop; mỗi clip xuất hiện đúng một lần/cycle. Cycle đủ nhiều clip phân biệt
    # nên cả trong cycle lẫn qua ranh giới loop đều thỏa T+10.
    import random
    rest = clips[1:]
    random.Random(seed).shuffle(rest)

    # Intro = clip_00 đã được máy video tạo từ chính thumbnail có chữ.
    intro = clips[0]
    title = _video_display_title(project_id, project_name)
    intro_mode = _project_intro_mode(project_id)
    # Kiểu chữ + dòng phụ + màu/chuyển cảnh theo công thức riêng của video và
    # phong cách kênh (thumbnail_recipe / channel_looks) — chống dập khuôn.
    style, subtitle, finish, recipe = _title_look(project_id, project_name)
    if config.images_cast_mode(config.images_dir(project_id, project_name)) == "scenery":
        # Chỉ cảnh vật: phát đúng tốc độ thật — làm chậm 0.7x khiến cảnh vật trông
        # như đứng hình (người dùng muốn cảnh động, chuyển động bình thường).
        finish = {**finish, "slow_speed": config.SCENERY_PLAYBACK_SPEED}
    if recipe:
        from .channel_looks import look_info
        progress_cb(f"Phong cách kênh «{look_info(recipe.get('look'))['label']}»: chữ "
                    f"{recipe.get('layout')} · {recipe.get('treatment')} · "
                    f"{recipe.get('font_lock') or recipe.get('font')}", 3.5)

    # Thumbnail TRƯỚC (ChatGPT/API vẽ ảnh + chữ; lỗi → chữ font PIL) lên ảnh 0
    # SẠCH → final/thumbnail.png. intro_mode="thumbnail" (mặc định): chính ảnh
    # này mở đầu video từ 00:00 rồi hoà tan vào clip 00 → intro giống hệt
    # thumbnail, không còn một kiểu chữ/thiết kế khác.
    from .job_manager import JobCancelled
    thumb_path = None
    try:
        thumb_path = render_thumbnail(project_id, project_name, subtitle,
                                      log=lambda m: progress_cb(m, 4.0), style=style,
                                      recipe=recipe,
                                      cancel_check=getattr(progress_cb, "is_cancelled", None))
    except JobCancelled:
        raise
    except Exception as e:
        progress_cb(f"Bỏ qua tạo thumbnail.png ({e})", 4.0)
    lead_image = None
    if intro_mode == "thumbnail" and thumb_path and Path(thumb_path).exists():
        # Không chèn thêm ảnh tĩnh: clip_00 đã lấy thumbnail này làm reference.
        # Như vậy thumbnail/intro chỉ xuất hiện một lần duy nhất tại 00:00.
        progress_cb("Intro 00:00 = clip 00 tạo từ chính thumbnail có chữ "
                    "(phát đúng một lần, không chèn lại ảnh tĩnh)", 4.5)
    else:
        if intro_mode == "thumbnail":
            progress_cb("Chưa có final/thumbnail.png → intro dùng chữ font có sẵn "
                        "đè lên clip 00.", 4.5)
        # intro_mode="font" (tuỳ chọn): chồng tiêu đề bằng font thật lên clip 00.
        cover = config.images_dir(project_id, project_name) / "0.png"
        titled_intro = config.final_dir(project_id, project_name) / "_intro_titled.mp4"
        if title and overlay_title_on_clip(
                intro, title, subtitle, str(titled_intro),
                log=lambda m: progress_cb(m, 4.5), style=style,
                bg_hint=str(cover) if cover.exists() else None):
            intro = str(titled_intro)
    footage_list = config.footage_files(project_id, project_name)
    if footage_list and len(rest) > 1:
        # Clip cuối theo thứ tự = cảnh xem TV/màn chiếu → làm nền khung footage;
        # đặt cuối seq để assembler dùng làm nền và không chiếu màn hình trống.
        rest = [c for c in rest if c != clips[-1]] + [clips[-1]]
    seq = [intro, *rest]
    head = ("Intro clip 00 từ thumbnail một lần" if intro_mode == "thumbnail"
            else "Intro clip 00 (chữ font) một lần")
    progress_cb(
        f"{head}; shuffle T+{P.t_window} cycle "
        f"{len(seq) - 1} clip còn lại, chuyển cảnh {finish['transition']} "
        f"{finish['blend_seconds']:.1f}s, tốc độ {finish['slow_speed']:.2f}x, "
        f"nội suy 60 FPS để chuyển động mượt, lặp đến {dur:.0f}s", 5.0)

    out = config.final_dir(project_id, project_name) / "final.mp4"
    footage = bool(footage_list)
    footage_mode = _project_footage_scene_mode(project_id)
    if footage:
        progress_cb(f"Có {len(footage_list)} footage: mỗi clip AI chạy đúng 1 lần, sau "
                    "đó footage (màu phim, giảm saturation, hạt film) lặp tới hết video.", 4.8)
    result = assemble_video_blend(
        seq, audio, str(out), target_seconds=dur,
        blend_seconds=finish["blend_seconds"], slow_speed=finish["slow_speed"],
        grade=finish["grade"], transition=finish["transition"],
        lead_image=None, lead_seconds=0.0,
        footage_paths=[str(f) for f in footage_list],
        footage_mode=footage_mode,
        # Clip cuối được prompt riêng thành cảnh nhân vật xem TV/máy chiếu (hoặc
        # màn chiếu không người). Assembler dùng nó đúng một lần làm nền footage
        # rồi loại khỏi cycle để không lặp lại màn hình trống.
        footage_background=(seq[-1] if footage and len(seq) > 2 else None),
        progress_cb=lambda m, p: progress_cb(m, 5.0 + p * 0.80),
    )
    # Sau intro: tên bài hiện tại ở góc (không ca sĩ) + lời 3 dòng.
    from backend.cover.overlay import apply_overlay
    progress_cb("In tên bài đang phát + lyrics lên video…", 86.0)
    intro_overlay_seconds = (float(P.clip_seconds) / finish["slow_speed"]
                             if intro_mode == "thumbnail" else 0.0)
    ov = apply_overlay(project_id, result.get("output") or str(out), audio,
                       intro_overlay_seconds,
                       log=lambda m, p: progress_cb(m, 86.0 + p * 0.13))
    result["overlay"] = ov
    return result


def render_thumbnail(project_id: int, project_name: str | None,
                     subtitle: str | None = None, log=None,
                     style: Optional[dict] = None, *,
                     recipe: Optional[dict] = None, engine: str | None = None,
                     force_ai: bool = False, interactive: bool = False,
                     cancel_check=None, out_name: str = "thumbnail.png") -> Optional[str]:
    """Ảnh 0 SẠCH (không chữ) + tiêu đề = tên project →
    final/thumbnail.png theo công thức thumbnail của project (bố cục, kiểu chữ,
    màu, font). Trả đường dẫn, hoặc None nếu chưa có ảnh 0.

    engine: None = theo Cài đặt `thumb_engine` (mặc định "chatgpt": ChatGPT web
    → lỗi thì API nếu bật tự động + có key → lỗi thì PIL; "api": API nếu bật
    tự động + có key → PIL)
    | "chatgpt" = bắt buộc ChatGPT (lỗi → PIL, KHÔNG tự sang API tốn phí)
    | "ai" = bắt buộc API | "pil" = chữ font thật (miễn phí).
    force_ai = vẽ bản AI mới (bỏ qua ảnh đã lưu). interactive = người dùng bấm
    nút (chờ đăng nhập ChatGPT lâu hơn)."""
    import shutil
    from . import chatgpt_thumbnail, thumbnail_ai
    from .job_manager import JobCancelled
    from .thumbnail import render_title
    title = _video_display_title(project_id, project_name)
    src0 = config.images_dir(project_id, project_name) / "0.png"
    if not title or not src0.exists():
        return None
    if style is None or recipe is None:
        st, auto_sub, _, rec = _title_look(project_id, project_name)
        style = st if style is None else style
        recipe = rec if recipe is None else recipe
        if subtitle is None:
            subtitle = auto_sub
    if subtitle is None:
        subtitle = _thumbnail_keywords()
    if config.images_cast_mode(src0.parent) == "scenery":
        # ảnh bìa chỉ có cảnh vật → prompt thumbnail không nhắc nhân vật/khuôn mặt
        recipe = {**(recipe or {}), "scenery": True}
    if config.single_image():
        # Tool Cover (2026-10-29): thumbnail kiểu mixtape Việt — tiêu đề 3D to,
        # dòng thể loại, danh sách bài đánh số (AI vẽ; PIL chỉ vẽ tiêu đề + dòng phụ).
        from backend.cover.branding import cover_tracklist
        from backend.cover.music_line import project_line
        single_track = _project_kind(project_id) == "single"
        recipe = {**(recipe or {}), "mixtape": not single_track,
                  "single_track": single_track,
                  "tracklist": [] if single_track else cover_tracklist(project_id),
                  "music_line": project_line(project_id)}
    final_d = config.final_dir(project_id, project_name)
    thumb = final_d / out_name
    thumb.parent.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    if engine != "pil":
        settings = thumbnail_ai.load_settings()
        if engine == "chatgpt":
            chain = ["chatgpt"]
        elif engine == "ai":
            chain = ["ai"]
        elif settings.engine == "chatgpt":
            chain = ["chatgpt"] + (["ai"] if settings.active else [])
        elif settings.engine == "api":
            chain = ["ai"] if settings.active else []
        else:
            chain = []
        for i, eng in enumerate(chain):
            nxt = ("API" if chain[i + 1:] == ["ai"] else "chữ font (PIL)")
            try:
                if eng == "chatgpt":
                    chatgpt_thumbnail.generate(
                        title=title, subtitle=subtitle, recipe=recipe, cover=src0,
                        out=thumb, settings=settings, log=log, force=force_ai,
                        interactive=interactive, cancel_check=cancel_check)
                else:
                    thumbnail_ai.generate(title=title, subtitle=subtitle, recipe=recipe,
                                          cover=src0, out=thumb, settings=settings,
                                          log=log, force=force_ai)
                return str(thumb)
            except JobCancelled:
                raise
            except Exception as e:
                who = "ChatGPT" if eng == "chatgpt" else "API"
                notes.append(f"{who} lỗi: {e}")
                if log:
                    log(f"Thumbnail {who} không dùng được ({e}) → thử {nxt}.")
    fallback_note = " · ".join(notes)
    shutil.copyfile(src0, thumb)
    render_title(thumb, title, subtitle, log=log, style=style)
    thumbnail_ai.mark_current(final_d, "pil", fallback_note)
    return str(thumb)


def render_thumbnail_ab_set(project_id: int, project_name: str | None,
                            subtitle: str | None = None, log=None,
                            style: Optional[dict] = None, *,
                            recipe: Optional[dict] = None,
                            engine: str | None = None,
                            force_ai: bool = False, interactive: bool = False,
                            cancel_check=None) -> list[str]:
    """Tạo bộ 3 thumbnail cho YouTube Test & compare.

    Cả ba khoá cùng layout, hệ màu, font/channel signature và chữ lớn; chỉ đổi
    hướng thiết kế (cinematic / editorial / spatial). Ảnh số 1 đồng thời là
    ``thumbnail.png`` dùng cho intro 00:00.
    """
    final_d = config.final_dir(project_id, project_name)
    paths: list[str] = []
    for variant in (1, 2, 3):
        rec = {**(recipe or {}), "ab_variant": variant}
        name = "thumbnail.png" if variant == 1 else f"thumbnail_ab_{variant}.png"
        if log:
            log(f"Thumbnail A/B {variant}/3: phân tích và tạo style "
                f"{('cinematic' if variant == 1 else 'editorial' if variant == 2 else 'spatial')}…")
        path = render_thumbnail(
            project_id, project_name, subtitle, log=log, style=style,
            recipe=rec, engine=engine, force_ai=force_ai,
            interactive=interactive, cancel_check=cancel_check, out_name=name)
        if not path or not Path(path).exists():
            raise RuntimeError(f"Không tạo được thumbnail A/B số {variant}.")
        paths.append(path)
    # Tên rõ ràng để driver YouTube nhận đủ 3 file, kể cả ảnh chính.
    first_ab = final_d / "thumbnail_ab_1.png"
    shutil.copyfile(paths[0], first_ab)
    result = [str(first_ab), paths[1], paths[2]]
    import hashlib
    digests = {hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in result}
    if len(digests) != 3:
        raise RuntimeError(
            "Bộ thumbnail A/B bị trùng ảnh (AI đã rơi về ảnh dự phòng). "
            "Hãy tạo lại khi ChatGPT/API ảnh hoạt động; tool không dùng 3 bản giống nhau.")
    return result


def retitle_video(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  project_name: str | None = None,
                  style_key: str | None = None,
                  regen_cover: bool = False) -> dict:
    """Sau khi ĐỔI TÊN project: cập nhật chữ tiêu đề trên thumbnail + intro.
    regen_cover=True: tạo lại ảnh bìa (ảnh 0) + clip intro 00
    trước — prompt đã khoá giữ nguyên nên nhân vật/bối cảnh vẫn khớp cả bộ.
    Rồi vẽ lại final/thumbnail.png và (nếu đủ clip + nhạc) ghép lại final.mp4
    để chữ ở intro là tên mới. KHÔNG tự đăng YouTube (bấm «Đăng nháp» sau)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    out: dict = {"regen_cover": bool(regen_cover)}
    lo = 1.0
    if regen_cover:
        progress_cb(f"Tạo lại ảnh bìa (ảnh 0) bằng {config.image_engine_label()}…", 1.0)
        regen_image(project_id, scaled(1.0, 35.0), 0, project_name, style_key)
        progress_cb(f"Tạo lại clip intro 00 ({clip_engine_label()})…", 35.0)
        regen_clip(project_id, scaled(35.0, 70.0), 0, project_name, style_key)
        lo = 70.0
    progress_cb("Vẽ lại chữ tiêu đề lên thumbnail…", lo)
    out["thumbnail"] = render_thumbnail(
        project_id, project_name, log=lambda m: progress_cb(m, lo),
        cancel_check=getattr(progress_cb, "is_cancelled", None))
    clip_dir = config.clips_dir(project_id, project_name)
    have_clips = all((clip_dir / f"clip_{i:02d}.mp4").exists()
                     for i in range(config.PARAMS.total_clips))
    audio = find_latest_audio(project_id, project_name)
    if have_clips and audio:
        progress_cb("Ghép lại video để intro mang tên mới…", lo + 2)
        out["final"] = step_assemble(project_id, scaled(lo + 2, 100.0),
                                     audio_path=audio, project_name=project_name)
        progress_cb("Xong — thumbnail + intro đã mang tên mới. Bấm «Đăng nháp "
                    "YouTube» nếu muốn cập nhật bản nháp.", 100.0)
    else:
        progress_cb("Xong — đã cập nhật thumbnail. Chưa đủ clip/nhạc nên chưa "
                    "ghép video (chữ intro sẽ đúng tên mới khi ghép).", 100.0)
    return out


def make_thumbnail(project_id: int,
                   progress_cb: Callable[[str, float], None],
                   project_name: str | None = None,
                   engine: str = "chatgpt") -> dict:
    """Nút «Tạo thumbnail»: vẽ lại final/thumbnail.png từ ảnh bìa (ảnh 0).
    engine="chatgpt" → bản MỚI do ChatGPT web vẽ (tài khoản ChatGPT của người
    dùng; lỗi → chữ PIL); "ai" → bản MỚI qua API (tốn phí; lỗi → PIL);
    "pil" → chữ font thật (miễn phí). Không ghép lại video, không đăng."""
    from . import thumbnail_ai
    src0 = config.images_dir(project_id, project_name) / "0.png"
    if not src0.exists():
        raise RuntimeError("Chưa có ảnh bìa (ảnh 0) — hãy tạo ảnh trước.")
    engine = engine if engine in ("chatgpt", "ai", "pil") else "chatgpt"
    progress_cb({"chatgpt": "Tạo thumbnail bằng ChatGPT (ảnh + chữ, tài khoản của bạn)…",
                 "ai": "Tạo thumbnail bằng AI (ảnh + chữ qua API)…",
                 "pil": "Vẽ chữ tiêu đề lên thumbnail (font)…"}[engine], 5.0)
    paths = render_thumbnail_ab_set(
        project_id, project_name, log=lambda m: progress_cb(m, 50.0),
        engine=engine, force_ai=(engine != "pil"), interactive=True,
        cancel_check=getattr(progress_cb, "is_cancelled", None))
    path = str(config.final_dir(project_id, project_name) / "thumbnail.png")
    st = thumbnail_ai.status(config.final_dir(project_id, project_name))
    used = {"chatgpt": "ChatGPT", "ai": "AI (API)"}.get(st["engine"], "font (PIL)")
    stale_intro = (_project_intro_mode(project_id) == "thumbnail"
                   and (config.final_dir(project_id, project_name) / "final.mp4").exists())
    progress_cb(f"Xong — thumbnail vẽ bằng {used}. "
                + ("Intro final.mp4 vẫn là thumbnail cũ — bấm «Ghép video» để mở đầu "
                   "bằng thumbnail mới. " if stale_intro else "")
                + "Bấm «Đăng nháp YouTube» nếu muốn cập nhật bản nháp.", 100.0)
    return {"thumbnail": path, "thumbnail_ab": paths,
            "engine": st["engine"], "note": st["note"]}


# ── Pipeline đầy đủ ─────────────────────────────────────────────

def run_full_video(project_id: int,
                   progress_cb: Callable[[str, float], None],
                   topic: str,
                   audio_path: Optional[str] = None,
                   seed: Optional[int] = None,
                   project_name: str | None = None,
                   idea: str | None = None,
                   style_key: str | None = None,
                   mode: str = "restart") -> dict:
    """Chạy trọn: ảnh → clip → ghép. progress_cb(msg, percent 0..100).
    mode="resume": giữ ảnh/clip đã có, chỉ tạo phần còn thiếu (dùng khi chạy lại)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    style_key = config.normalize_style(style_key)
    mode = "resume" if mode == "resume" else "restart"
    progress_cb("Bắt đầu — tạo ảnh", 1.0)
    step_images(project_id, scaled(1.0, 30.0), topic, mode=mode,
                project_name=project_name, idea=idea, style_key=style_key)

    progress_cb(f"Tạo clip video ({clip_engine_label()})", 30.0)
    step_clips(project_id, scaled(30.0, 80.0), mode=mode,
               project_name=project_name, style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
    return result


def run_video_from_images(project_id: int,
                          progress_cb: Callable[[str, float], None],
                          audio_path: Optional[str] = None,
                          seed: Optional[int] = None,
                          project_name: str | None = None,
                          style_key: str | None = None,
                          clips_mode: str = "restart") -> dict:
    """Tạo lại VIDEO từ ảnh ĐÃ CÓ (KHÔNG tạo lại ảnh): clip → ghép.
    clips_mode="restart": tạo lại toàn bộ clip từ ảnh sẵn có; "resume": chỉ tạo
    tiếp clip còn thiếu. progress_cb(msg, percent 0..100)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    style_key = config.normalize_style(style_key)
    mode = "resume" if clips_mode == "resume" else "restart"
    progress_cb(f"Tạo clip video từ ảnh đã có ({clip_engine_label()})", 1.0)
    step_clips(project_id, scaled(1.0, 80.0), mode=mode,
               project_name=project_name, style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
    return result


# ── Chạy tiếp chu trình sau gián đoạn ──────────────────────────────
# «Tạo tiếp ảnh thiếu» / «Tạo tiếp clip thiếu»: làm phần còn thiếu rồi đi TIẾP
# cả chuỗi Ghép video → Đăng nháp YouTube → Đăng nháp Short. Bước nào đã xong
# (final.mp4 mới hơn clip + nhạc; đã có nháp cho final.mp4 này) thì bỏ qua.

def _final_up_to_date(project_id: int, project_name: str | None,
                      audio: str) -> bool:
    final = config.final_dir(project_id, project_name) / "final.mp4"
    if not final.exists():
        return False
    clip_dir = config.clips_dir(project_id, project_name)
    srcs = [clip_dir / f"clip_{i:02d}.mp4" for i in range(config.PARAMS.total_clips)]
    srcs.append(Path(audio))
    try:
        newest = max(p.stat().st_mtime for p in srcs if p.exists())
    except ValueError:
        return False
    if final.stat().st_mtime < newest:
        return False
    # Bản cũ 24/30fps phải được ghép lại bằng pipeline nội suy 60fps; nếu chỉ
    # nhìn mtime thì nút Auto sẽ bỏ qua và người dùng vẫn nhận video giật.
    from .assembler import probe_fps
    return probe_fps(str(final)) >= 59.0


def _draft_done_since(project_id: int, since_ts: float, shorts: bool) -> bool:
    """Đã có bản nháp (video dài hoặc Short) tạo SAU mốc since_ts chưa."""
    from datetime import datetime, timezone
    con = _db()
    try:
        rows = con.execute(
            "SELECT created_at FROM youtubeupload WHERE project_id=? AND status='draft' AND "
            + ("video_path LIKE '%short.mp4'" if shorts
               else "video_path NOT LIKE '%short.mp4'"), (project_id,)).fetchall()
    except Exception:
        return False
    finally:
        con.close()
    for r in rows:
        try:
            t = datetime.fromisoformat(r["created_at"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t.timestamp() >= since_ts:
                return True
        except Exception:
            continue
    return False


def _continue_chain(project_id: int, progress_cb: Callable[[str, float], None],
                    project_name: str | None, lo: float) -> dict:
    """Ghép (nếu cần) → nháp YouTube (nếu chưa) → nháp Short (nếu bật & chưa).
    Đăng nháp chạy KỂ CẢ khi project tắt auto_upload (người dùng đã bấm chạy tiếp).
    Lỗi đăng nháp chỉ báo qua progress, không làm hỏng video đã ghép."""
    from . import shorts
    from .job_manager import JobCancelled

    def scaled(a, b):
        return lambda m, p: progress_cb(m, a + (p / 100.0) * (b - a))

    audio = find_latest_audio(project_id, project_name)
    if not audio:
        progress_cb("Đủ clip nhưng chưa có nhạc nền — mix Audio rồi bấm "
                    "«Tạo tiếp clip thiếu» để chạy tiếp (ghép → nháp YouTube → Short).", 100.0)
        return {"assembled": False, "reason": "no_audio"}

    result: dict = {}
    if _final_up_to_date(project_id, project_name, audio):
        progress_cb("final.mp4 đã ghép từ đúng clip + nhạc hiện tại — bỏ qua bước ghép.", 85.0)
        result["assembled"] = "skipped"
    else:
        progress_cb("Ghép video + gắn nhạc", lo)
        res = step_assemble(project_id, scaled(lo, 85.0), project_name=project_name)
        result = res if isinstance(res, dict) else {}
        result["assembled"] = True

    final = config.final_dir(project_id, project_name) / "final.mp4"
    final_ts = final.stat().st_mtime
    fields = _read_project_fields(project_id)

    def _try(label: str, fn) -> bool:
        try:
            result[label] = fn()
            return True
        except JobCancelled:
            raise
        except Exception as e:
            result[f"{label}_error"] = f"{type(e).__name__}: {e}"
            progress_cb(f"{'Đăng nháp YouTube' if label == 'upload' else 'Short'} lỗi "
                        f"(video ĐÃ ghép xong): {result[f'{label}_error']}", 100.0)
            return False

    if _draft_done_since(project_id, final_ts, shorts=False):
        progress_cb("Đã có bản nháp YouTube cho final.mp4 này — bỏ qua đăng lại.", 90.0)
    else:
        progress_cb("Chạy tiếp: đăng nháp lên YouTube…", 86.0)
        if not _try("upload", lambda: step_upload_youtube(
                project_id, scaled(86.0, 95.0), project_name,
                fields.get("instrument"), fields.get("music_style"))):
            return result

    if not shorts.settings()["auto_short"]:
        progress_cb("Hoàn thành (tự tạo Short đang tắt).", 100.0)
        return result
    if _draft_done_since(project_id, final_ts, shorts=True):
        progress_cb("Đã có nháp Short cho final.mp4 này — hoàn thành.", 100.0)
        return result
    progress_cb("Chạy tiếp: tạo & đăng nháp YouTube Short…", 95.0)
    if _try("short", lambda: step_upload_short(
            project_id, scaled(95.0, 100.0), project_name,
            fields.get("instrument"), fields.get("music_style"))):
        progress_cb("Hoàn thành chu trình: video → nháp YouTube → nháp Short.", 100.0)
    return result


def continue_from_images(project_id: int,
                         progress_cb: Callable[[str, float], None],
                         topic: str,
                         project_name: str | None = None,
                         idea: str | None = None,
                         style_key: str | None = None) -> dict:
    """«Tạo tiếp ảnh thiếu»: ảnh thiếu → clip thiếu → ghép → nháp YouTube → Short."""
    def scaled(a, b):
        return lambda m, p: progress_cb(m, a + (p / 100.0) * (b - a))
    style_key = config.normalize_style(style_key)
    progress_cb("Chạy tiếp: tạo ảnh còn thiếu", 1.0)
    step_images(project_id, scaled(1.0, 25.0), topic, mode="resume",
                project_name=project_name, idea=idea, style_key=style_key)
    progress_cb(f"Chạy tiếp: tạo clip còn thiếu ({clip_engine_label()})", 25.0)
    step_clips(project_id, scaled(25.0, 75.0), mode="resume",
               project_name=project_name, style_key=style_key)
    return _continue_chain(project_id, progress_cb, project_name, 75.0)


def continue_from_clips(project_id: int,
                        progress_cb: Callable[[str, float], None],
                        project_name: str | None = None,
                        style_key: str | None = None) -> dict:
    """«Tạo tiếp clip thiếu»: clip thiếu → ghép → nháp YouTube → Short."""
    style_key = config.normalize_style(style_key)
    progress_cb(f"Chạy tiếp: tạo clip còn thiếu ({clip_engine_label()})", 1.0)
    step_clips(project_id, lambda m, p: progress_cb(m, 1.0 + p * 0.74),
               mode="resume", project_name=project_name, style_key=style_key)
    return _continue_chain(project_id, progress_cb, project_name, 75.0)


# ── Đăng nháp YouTube qua GPMLogin ──────────────────────────────

def _db():
    """Kết nối SQLite app.db (worker thread) — trả sqlite3.Connection."""
    import sqlite3
    from backend.database import DB_PATH
    # Bản clone mới chưa có data/ → tạo thư mục để lỗi «no such table»
    # rơi vào except của nơi gọi (trả mặc định) thay vì văng lỗi mở file.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _project_instrument(project_id: int) -> str:
    """Nhạc cụ người dùng chọn cho project ('' nếu chưa chọn / lỗi đọc DB)."""
    con = _db()
    try:
        row = con.execute("SELECT instrument FROM project WHERE id=?",
                          (project_id,)).fetchone()
    except Exception:
        return ""
    finally:
        con.close()
    return (row["instrument"] or "") if row else ""


def _project_intro_mode(project_id: int) -> str:
    """intro_mode của project: "thumbnail" (mặc định) | "font"."""
    con = _db()
    try:
        row = con.execute("SELECT intro_mode FROM project WHERE id=?",
                          (project_id,)).fetchone()
    except Exception:
        return config.DEFAULT_INTRO_MODE
    finally:
        con.close()
    return config.normalize_intro_mode(row["intro_mode"] if row else None)


def _project_footage_scene_mode(project_id: int) -> str:
    """Bố cục footage đã chọn; auto bám cast_mode của project."""
    con = _db()
    try:
        try:
            row = con.execute(
                "SELECT footage_scene_mode, cast_mode FROM project WHERE id=?",
                (project_id,)).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        con.close()
    mode = (row["footage_scene_mode"] if row else "auto") or "auto"
    if mode not in ("auto", "character_screen", "screen_only"):
        mode = "auto"
    if mode == "auto":
        return "screen_only" if row and row["cast_mode"] == "scenery" else "character_screen"
    return mode


def match_channel(rows: list[dict], instrument: str, music_style: str,
                  music_line: str = "") -> dict | None:
    """Dòng ánh xạ khớp (instrument, music_style) — không phân biệt hoa/thường &
    khoảng trắng thừa; dòng mới nhất thắng. Dùng cho project CHƯA chọn kênh.

    Tool Cover (project không có nhạc cụ) khớp theo dòng nhạc + thể loại, thứ tự:
    cùng dòng + cùng thể loại → cùng dòng, kênh chưa ghi thể loại → cùng dòng →
    kênh chưa ghi dòng nhạc + cùng thể loại. Project chưa có dòng nhạc → chỉ
    khớp thể loại như trước."""
    def _n(r: dict, key: str) -> str:
        return (r.get(key) or "").strip().lower()

    ins = (instrument or "").strip().lower()
    sty = (music_style or "").strip().lower()
    line = (music_line or "").strip().lower()
    rows = sorted(rows, key=lambda r: r.get("id") or 0, reverse=True)
    if not ins:
        if line:
            same = [r for r in rows if _n(r, "music_line") == line]
            hit = (next((r for r in same if sty and _n(r, "music_style") == sty), None)
                   or next((r for r in same if not _n(r, "music_style")), None)
                   or (same[0] if same else None))
            if hit or not sty:
                return hit
            return next((r for r in rows if not _n(r, "music_line")
                         and _n(r, "music_style") == sty), None)
        if not sty:
            return None
        return next((r for r in rows if _n(r, "music_style") == sty), None)
    if not sty:
        return None
    hit = next((r for r in rows if (r.get("instrument") or "").strip().lower() == ins
                and (r.get("music_style") or "").strip().lower() == sty), None)
    if hit:
        return hit
    # 'Piano' (danh mục) ↔ 'Lofi Piano' (ánh xạ): cùng nhạc cụ + cùng thể loại.
    from .music_spec import same_instrument
    return next((r for r in rows if (r.get("music_style") or "").strip().lower() == sty
                 and same_instrument(r.get("instrument") or "", ins)), None)


def _lookup_channel_mapping(instrument: str, music_style: str,
                            music_line: str = "") -> dict | None:
    """Tra ChannelMapping theo (instrument, music_style, music_line). Trả dict/None."""
    con = _db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM channelmapping").fetchall()]
    except Exception:
        return None
    finally:
        con.close()
    return match_channel(rows, instrument, music_style, music_line)


def _lookup_channel_by_id(channel_id) -> dict | None:
    """ChannelMapping theo id (kênh người dùng chọn cho project)."""
    try:
        cid = int(channel_id or 0)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    con = _db()
    try:
        row = con.execute("SELECT * FROM channelmapping WHERE id=?", (cid,)).fetchone()
    except Exception:
        return None
    finally:
        con.close()
    return dict(row) if row else None


def channel_for(channel_id, instrument: str, music_style: str,
                music_line: str = "") -> dict | None:
    """Kênh YouTube của 1 project: kênh người dùng CHỌN (channel_id) — kênh nào
    cũng nhận nhạc cụ/thể loại nào; chưa chọn → tự chọn giống form project hiển thị
    (tool Cover: dòng nhạc + thể loại; project cũ: nhạc cụ + thể loại)."""
    m = _lookup_channel_by_id(channel_id)
    if m:
        return m
    if not (music_style or "").strip() and not (music_line or "").strip():
        return None
    return _lookup_channel_mapping(instrument or "", music_style or "", music_line or "")


def channel_for_project(project_id: int, instrument: str | None = None,
                        music_style: str | None = None) -> dict | None:
    fields = _read_project_fields(project_id)
    ins = (instrument if instrument is not None else fields.get("instrument")) or ""
    sty = (music_style if music_style is not None else fields.get("music_style")) or ""
    return channel_for(fields.get("channel_id"), ins, sty, fields.get("music_line") or "")


def _no_channel_error(instrument: str, music_style: str, what: str = "video",
                      music_line: str = "") -> str:
    if instrument.strip() and music_style.strip():
        hint = (f" (hoặc thêm dòng ánh xạ nhạc cụ '{instrument}' + phong cách "
                f"'{music_style}' ở ⚙️ Cài đặt → YouTube/GPMLogin)")
    elif music_line.strip() or music_style.strip():
        parts = [f"dòng nhạc '{music_line.strip()}'" if music_line.strip() else "",
                 f"thể loại '{music_style.strip()}'" if music_style.strip() else ""]
        hint = (f" (hoặc ghi {' + '.join(p for p in parts if p)} cho 1 kênh ở "
                "Global Settings → Kênh YouTube để tool tự chọn)")
    else:
        hint = ""
    return (f"Project chưa chọn kênh YouTube — không biết đăng {what} lên kênh nào. "
            "Chọn kênh ở tab Video (mục «Đăng nháp YouTube») hoặc ✏️ Sửa project"
            + hint + ".")


def _read_gpm_settings_db() -> tuple[str, str | None]:
    """Đọc gpm_api_base_url + gpm_exe_path từ appsettings (id=1)."""
    con = _db()
    try:
        row = con.execute(
            "SELECT gpm_api_base_url, gpm_exe_path FROM appsettings WHERE id=1"
        ).fetchone()
    except Exception:
        return ("", None)
    finally:
        con.close()
    if not row:
        return ("", None)
    return (row["gpm_api_base_url"] or "", row["gpm_exe_path"])


def _read_project_fields(project_id: int) -> dict:
    con = _db()
    row = None
    try:
        # DB chưa chạy migration M022 (music_line) / M015 (channel_id) → bớt cột.
        for extra in (", channel_id, music_line", ", channel_id", ""):
            try:
                row = con.execute(
                    "SELECT name, instrument, music_style, auto_upload" + extra
                    + " FROM project WHERE id=?", (project_id,)
                ).fetchone()
                break
            except sqlite3.OperationalError:
                continue
    finally:
        con.close()
    return dict(row) if row else {}


def _past_titles(profile_id: str, limit: int = 20,
                 shorts: bool = False) -> list[str]:
    """Title đã đăng của kênh — video dài và Short tách riêng (Short = short.mp4)."""
    con = _db()
    try:
        rows = con.execute(
            "SELECT title FROM youtubeupload WHERE profile_id=? AND "
            + ("video_path LIKE '%short.mp4'" if shorts
               else "video_path NOT LIKE '%short.mp4'")
            + " ORDER BY id DESC LIMIT ?", (profile_id, limit)
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [r["title"] for r in rows if r["title"]]


def _record_upload(project_id: int, mapping: dict, meta: dict,
                   video_path: str, status: str,
                   error: str | None = None) -> None:
    from datetime import datetime, timezone
    con = _db()
    try:
        con.execute(
            "INSERT INTO youtubeupload "
            "(project_id, profile_id, channel_name, title, description, "
            " hashtags, video_path, status, error_message, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, mapping.get("gpm_profile_id", ""),
             mapping.get("channel_name", ""), meta.get("title", ""),
             meta.get("description", ""), meta.get("hashtags", ""),
             video_path, status, error,
             datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
    except Exception:
        pass
    finally:
        con.close()


def step_upload_youtube(project_id: int,
                        progress_cb: Callable[[str, float], None],
                        project_name: str | None = None,
                        instrument: str | None = None,
                        music_style: str | None = None) -> dict:
    """Đăng NHÁP video final.mp4 lên YouTube qua profile GPMLogin của kênh project
    đã chọn (chưa chọn → khớp instrument + music_style). AI viết title/description
    từ thumbnail. KHÔNG publish — chỉ lưu bản nháp."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from . import gpm_client as gpmc
    from . import youtube_meta, youtube_driver

    # Lấy instrument/music_style: ưu tiên tham số, fallback đọc từ project.
    fields = _read_project_fields(project_id)
    instrument = (instrument if instrument is not None else fields.get("instrument")) or ""
    music_style = (music_style if music_style is not None else fields.get("music_style")) or ""
    project_name = project_name or fields.get("name")

    progress_cb("Chuẩn bị đăng nháp YouTube…", 2.0)
    # Kênh = kênh người dùng chọn cho project (nhạc cụ nào cũng được); project
    # cũ chưa chọn → tra theo (nhạc cụ, phong cách) như trước.
    music_line = fields.get("music_line") or ""
    mapping = channel_for(fields.get("channel_id"), instrument, music_style, music_line)
    if not mapping:
        raise RuntimeError(_no_channel_error(instrument, music_style,
                                             music_line=music_line))

    profile_id = mapping.get("gpm_profile_id", "")
    if not profile_id:
        raise RuntimeError("Dòng ánh xạ thiếu profile GPMLogin.")

    # File final + thumbnail
    out_dir = config.final_dir(project_id, project_name)
    final = out_dir / "final.mp4"
    thumb = out_dir / "thumbnail.png"
    if not final.exists():
        raise RuntimeError("Chưa có final.mp4 — hãy render video trước khi đăng.")

    # YouTube Test & compare bắt buộc đủ 3 ảnh. Nếu project cũ mới có một ảnh,
    # tạo nốt bộ A/B trước khi mở Studio; không âm thầm đăng chỉ một thumbnail.
    ab_paths = [out_dir / f"thumbnail_ab_{i}.png" for i in (1, 2, 3)]
    if not all(p.exists() for p in ab_paths):
        style, subtitle, _finish, recipe = _title_look(project_id, project_name)
        progress_cb("Phân tích và tạo đủ 3 thumbnail A/B cùng hệ màu/bố cục…", 5.0)
        made = render_thumbnail_ab_set(
            project_id, project_name, subtitle,
            log=lambda m: progress_cb(m, 7.0), style=style, recipe=recipe,
            cancel_check=getattr(progress_cb, "is_cancelled", None))
        ab_paths = [Path(p) for p in made]

    # Mô tả YouTube KHÔNG kèm tracklist/mốc thời gian (yêu cầu người dùng
    # 2026-10-07) — chỉ phần AI viết + hashtag.
    meta_warnings: list[str] = []

    # Metadata (AI vision + bộ nhớ chống trùng)
    progress_cb("AI viết tiêu đề & mô tả từ thumbnail…", 10.0)
    api_config = get_api_config_from_db(str(DB_PATH))
    # Tool Cover: viết theo ngôn ngữ của video (vi/en) + danh sách bài có mốc
    # thời gian (chương YouTube) lấy từ bản mix đã dùng.
    from backend.cover import youtube_text
    meta = youtube_text.generate(
        project_id, project_name or "", str(thumb),
        channel_name=mapping.get("channel_name", ""),
        default_hashtags=mapping.get("default_hashtags", ""),
        past_titles=_past_titles(profile_id),
        api_config=api_config,
        audio_path=find_latest_audio(project_id, project_name),
        log=lambda m: progress_cb(m, 12.0),
    )

    # Bật GPMLogin profile → CDP
    progress_cb("Bật GPMLogin & mở kênh YouTube…", 22.0)
    base_url, exe_path = _read_gpm_settings_db()
    client = gpmc.GPMClient(base_url or None)
    try:
        client.ensure_app_running(exe_path)   # best-effort (không raise nếu thiếu exe)
    except gpmc.GPMError as e:
        progress_cb(f"(Bỏ qua tự mở app: {e})", 22.0)

    def _do_upload(cdp: str) -> dict:
        progress_cb("Điều khiển YouTube Studio (lưu nháp)…", 26.0)
        return youtube_driver.upload_draft(
            cdp_endpoint=cdp,
            video_path=str(final),
            title=meta["title"],
            description=meta["description"],
            hashtags=meta["hashtags"],
            language=meta.get("language") or mapping.get("language", "en-US"),
            # Có file thumbnail → driver BẤM chọn hình thu nhỏ và nạp ảnh này.
            thumbnail_path=str(thumb) if thumb.exists() else None,
            thumbnail_paths=[str(p) for p in ab_paths],
            progress_cb=lambda m, p: progress_cb(m, 26.0 + (p / 100.0) * 70.0),
            log=lambda m: progress_cb(m, 30.0),
            # Tài khoản có nhiều kênh → dừng nếu Studio đang mở kênh khác.
            expected_channel=mapping.get("channel_name", ""),
        )

    try:
        result = _upload_with_gpm_retry(client, profile_id, _do_upload,
                                        progress_cb)
    except Exception as e:
        _record_upload(project_id, mapping, meta, str(final),
                       status="failed", error=f"{type(e).__name__}: {e}")
        raise

    _record_upload(project_id, mapping, meta, str(final), status="draft")
    _save_long_draft_info(out_dir, meta["title"], result.get("video_url") or "")
    warns = meta_warnings + (result.get("warnings") or [])
    msg = "Đã lưu bản nháp trên YouTube."
    if warns:
        msg += " Lưu ý: " + " | ".join(warns)
    progress_cb(msg, 100.0)
    return {"status": "draft", "title": meta["title"],
            "channel_name": mapping.get("channel_name", ""),
            "video_url": result.get("video_url") or "",
            "warnings": warns}


_LONG_DRAFT_INFO = "youtube_draft.json"


def _save_long_draft_info(out_dir: Path, title: str, url: str) -> None:
    """Ghi title + link bản nháp video dài → Short dẫn link về bản đầy đủ."""
    from datetime import datetime, timezone
    try:
        (out_dir / _LONG_DRAFT_INFO).write_text(json.dumps(
            {"title": title, "url": url,
             "at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_long_draft_info(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / _LONG_DRAFT_INFO).read_text(encoding="utf-8"))
    except Exception:
        return {}


def step_upload_short(project_id: int,
                      progress_cb: Callable[[str, float], None],
                      project_name: str | None = None,
                      instrument: str | None = None,
                      music_style: str | None = None) -> dict:
    """Cắt 1 đoạn DỌC ở GIỮA final.mp4 → short.mp4 (1080×1920 + hook chữ), AI
    viết nội dung ngắn, rồi đăng NHÁP lên CÙNG kênh với video dài. KHÔNG publish."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from . import gpm_client as gpmc
    from . import shorts, youtube_meta, youtube_driver

    fields = _read_project_fields(project_id)
    instrument = (instrument if instrument is not None else fields.get("instrument")) or ""
    music_style = (music_style if music_style is not None else fields.get("music_style")) or ""
    project_name = project_name or fields.get("name")
    progress_cb("Chuẩn bị YouTube Short…", 1.0)
    music_line = fields.get("music_line") or ""
    mapping = channel_for(fields.get("channel_id"), instrument, music_style, music_line)
    if not mapping or not mapping.get("gpm_profile_id"):
        raise RuntimeError(_no_channel_error(instrument, music_style, "Short",
                                             music_line=music_line))
    profile_id = mapping["gpm_profile_id"]

    out_dir = config.final_dir(project_id, project_name)
    final = out_dir / "final.mp4"
    if not final.exists():
        raise RuntimeError("Chưa có final.mp4 — hãy render video trước khi tạo Short.")

    audio = find_latest_audio(project_id, project_name)
    tracklist = youtube_meta.load_tracklist(audio)
    try:
        if tracklist and abs(probe_duration(str(final)) - probe_duration(audio)) > 5.0:
            tracklist = []        # bản mix khác video → mốc bài sai, lấy giữa video
    except Exception:
        tracklist = []

    long_info = _load_long_draft_info(out_dir)
    built = shorts.build_short(
        str(final), out_dir, tracklist,
        channel_name=mapping.get("channel_name", ""),
        instrument=instrument, music_style=music_style,
        long_title=long_info.get("title") or project_name or "",
        past_titles=_past_titles(profile_id, shorts=True),
        api_config=get_api_config_from_db(str(DB_PATH)),
        progress_cb=lambda m, p: progress_cb(m, 1.0 + p * 0.5),
        project_id=project_id,
    )
    meta = dict(built["meta"])
    meta["description"] = shorts.compose_description(
        meta, mapping.get("channel_name", ""), long_info.get("url") or "")
    progress_cb(f"Short: «{meta['title']}»", 18.0)

    base_url, exe_path = _read_gpm_settings_db()
    client = gpmc.GPMClient(base_url or None)
    try:
        client.ensure_app_running(exe_path)
    except gpmc.GPMError as e:
        progress_cb(f"(Bỏ qua tự mở app: {e})", 20.0)

    def _do_upload(cdp: str) -> dict:
        progress_cb("Đăng NHÁP Short lên YouTube Studio…", 22.0)
        return youtube_driver.upload_draft(
            cdp_endpoint=cdp, video_path=built["path"],
            title=meta["title"], description=meta["description"],
            hashtags=meta["hashtags"],
            language=mapping.get("language", "en-US"),
            thumbnail_path=None,          # Short: YouTube tự chọn khung hình
            progress_cb=lambda m, p: progress_cb(m, 22.0 + (p / 100.0) * 76.0),
            log=lambda m: progress_cb(m, 24.0),
            expected_channel=mapping.get("channel_name", ""),
        )

    try:
        result = _upload_with_gpm_retry(client, profile_id, _do_upload, progress_cb)
    except Exception as e:
        _record_upload(project_id, mapping, meta, built["path"],
                       status="failed", error=f"{type(e).__name__}: {e}")
        raise
    _record_upload(project_id, mapping, meta, built["path"], status="draft")
    warns = result.get("warnings") or []
    progress_cb("Đã lưu NHÁP YouTube Short." +
                (" Lưu ý: " + " | ".join(warns) if warns else ""), 100.0)
    return {"status": "draft", "title": meta["title"], "hook": meta["hook"],
            "path": built["path"], "video_url": result.get("video_url") or "",
            "channel_name": mapping.get("channel_name", ""), "warnings": warns}


def _upload_with_gpm_retry(client, profile_id: str,
                           do_upload: Callable[[str], dict],
                           progress_cb: Callable[[str, float], None],
                           attempts: int = 3, pause_sec: float = 15.0,
                           sleep: Callable[[float], None] | None = None) -> dict:
    """Bật profile GPMLogin → do_upload(cdp). Nếu KHÔNG NỐI ĐƯỢC CDP (trình duyệt
    của profile chết ngay sau khi bật, cổng debug không mở — «WebSocket error:
    connect ECONNREFUSED») thì đóng profile, nghỉ rồi bật lại (cổng MỚI) và thử
    lại. Lỗi SAU khi đã nối (trong YouTube Studio) KHÔNG thử lại — tránh tải
    trùng video. Luôn đóng profile khi xong."""
    import time as _time
    from .youtube_driver import CDPConnectError
    sleep = sleep or _time.sleep
    last: Exception | None = None
    for i in range(1, attempts + 1):
        if i > 1:
            progress_cb(f"Trình duyệt GPMLogin không phản hồi — đóng profile, "
                        f"bật lại (lần {i}/{attempts})…", 22.0)
        cdp = client.start_profile(profile_id)
        try:
            return do_upload(cdp)
        except CDPConnectError as e:
            last = e
        except Exception as e:
            # Cửa sổ trình duyệt GPM bị ĐÓNG giữa chừng (tay người / GPMLogin
            # tắt) → không tự thử lại (có thể người dùng chủ ý đóng).
            if "has been closed" in str(e) or type(e).__name__ == "TargetClosedError":
                raise RuntimeError(
                    "Trình duyệt GPMLogin của kênh bị ĐÓNG giữa lúc đang tải video "
                    "lên YouTube — đừng đóng cửa sổ GPM (hoặc tắt GPMLogin) khi tool "
                    "đang đăng nháp. Bấm «Đăng nháp YouTube» để chạy lại (nếu Studio còn "
                    f"bản nháp «final» dở dang thì xoá nó sau khi bản mới xong). ({e})") from e
            raise
        finally:
            client.close_profile(profile_id)
        # GPMLogin có lúc mất dấu Chrome cũ của profile (vẫn giữ khoá thư mục) →
        # Chrome mới thoát ngay, cổng debug không mở. Dọn tiến trình sót rồi mới bật lại.
        kill = getattr(client, "kill_leftover_browsers", None)
        if kill:
            n = kill(profile_id)
            if n:
                progress_cb(f"Đã tắt {n} tiến trình Chrome sót của profile GPMLogin "
                            f"(GPM đóng không sạch) — bật lại…", 22.0)
        if i < attempts:
            sleep(pause_sec)     # GPMLogin giải phóng profile chậm sau khi stop
    raise RuntimeError(
        f"{last} — đã đóng/bật lại profile {attempts} lần vẫn không nối được. "
        f"Hãy mở GPMLogin, bấm «Mở» profile này 1 lần xem trình duyệt có lên "
        f"không (lỗi proxy/nhân trình duyệt/profile hỏng), đóng nó lại rồi bấm "
        f"«Đăng nháp YouTube» lần nữa (nếu GPMLogin báo profile đang mở mà không "
        f"thấy cửa sổ: tắt hẳn GPMLogin rồi mở lại). Video đã dựng xong, không mất gì.")


def step_upload_youtube_and_short(project_id: int,
                                  progress_cb: Callable[[str, float], None],
                                  project_name: str | None = None,
                                  instrument: str | None = None,
                                  music_style: str | None = None) -> dict:
    """Nút «Đăng nháp YouTube»: nháp video dài, xong thì tự nháp Short (nếu bật
    auto_short). Short lỗi KHÔNG làm hỏng kết quả video dài."""
    from . import shorts
    res = step_upload_youtube(project_id, progress_cb, project_name,
                              instrument, music_style)
    if shorts.settings()["auto_short"]:
        try:
            progress_cb("Tự động tạo & đăng nháp YouTube Short…", 100.0)
            res["short"] = step_upload_short(project_id, progress_cb, project_name,
                                             instrument, music_style)
        except Exception as e:
            from .job_manager import JobCancelled
            if isinstance(e, JobCancelled):
                raise
            res["short_error"] = f"{type(e).__name__}: {e}"
            progress_cb(f"Auto Short lỗi (video dài ĐÃ lưu nháp): "
                        f"{res['short_error']}", 100.0)
    return res


def _maybe_auto_upload(project_id: int,
                       progress_cb: Callable[[str, float], None],
                       project_name: str | None) -> None:
    """Nếu project bật auto_upload → tự đăng nháp sau khi render xong. KHÔNG làm
    hỏng job render nếu upload lỗi (chỉ báo qua progress)."""
    fields = _read_project_fields(project_id)
    if not fields.get("auto_upload"):
        return
    try:
        progress_cb("Tự động đăng nháp lên YouTube…", 100.0)
        step_upload_youtube(project_id, progress_cb, project_name,
                            fields.get("instrument"), fields.get("music_style"))
    except Exception as e:
        progress_cb(f"Auto-upload YouTube lỗi (video ĐÃ render xong, bỏ qua "
                    f"đăng nháp): {type(e).__name__}: {e}", 100.0)
        return
    # Nháp video dài xong → tự cắt & đăng nháp Short (tắt: auto_short=false).
    from . import shorts
    if not shorts.settings()["auto_short"]:
        return
    try:
        progress_cb("Tự động tạo & đăng nháp YouTube Short…", 100.0)
        step_upload_short(project_id, progress_cb, project_name,
                          fields.get("instrument"), fields.get("music_style"))
    except Exception as e:
        progress_cb(f"Auto Short lỗi (video dài ĐÃ lưu nháp): "
                    f"{type(e).__name__}: {e}", 100.0)
