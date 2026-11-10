"""Prepare, freeze and validate image/motion prompts before paid media generation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from . import config, prompt_catalog

# v2: 3 ảnh (bìa + 2 bảng phân tích nhân vật) → N clip dùng chung 2 bảng đó làm
# nguyên liệu. Bộ prompt v1 (41 ảnh ↔ 41 clip 1:1) không tương thích.
WORKFLOW_VERSION = 2
# v3 (tool Cover 2026-10-29): 1 ảnh bìa → clip 00 intro + clip 01 loop
# (video/cover_scene.py). Không có bảng nhân vật / khoá continuity.
# v4 (2026-10-29): + bảng nhân vật (1) + bảng bối cảnh (2) vẽ TỪ ảnh bìa,
# + clip 02–05 = 4 góc quay nhân vật. Hồ sơ v3 tự nâng lên v4 (upgrade_cover_manifest).
SINGLE_WORKFLOW_VERSION = 4
COVER_V3 = 3


def _cover_rev() -> int:
    from .cover_scene import COVER_REV
    return COVER_REV


def workflow_version() -> int:
    return SINGLE_WORKFLOW_VERSION if config.single_image() else WORKFLOW_VERSION

# Ràng buộc bám thực tế gắn vào CUỐI mọi prompt cảnh: nguyên liệu là BẢNG THIẾT
# KẾ chứ không phải khung hình đầu clip, nên KHÔNG khoá máy quay như quy trình
# cũ — chỉ cấm dựng lại chính bảng thiết kế và cấm cắt cảnh.
SCENE_SUFFIX = (
    " One single continuous shot, no cuts. Do not reproduce the reference "
    "sheets: no side-by-side views, no labels, no text, no colour-swatch strip, "
    "no flat studio backdrop, no duplicated character. Keep the character and "
    "creature identity unchanged.")
# Chế độ CHỈ CẢNH VẬT (cast_mode="scenery"): không người, không thú.
SCENE_SUFFIX_SCENERY = (
    " One single continuous shot, no cuts. Do not reproduce the reference "
    "sheets: no side-by-side panels, no labels, no text, no colour-swatch strip, "
    "no flat studio backdrop. No people and no animals at any moment; keep the "
    "place unchanged.")


def scene_suffix(cast_mode: str | None) -> str:
    return SCENE_SUFFIX_SCENERY if cast_mode == "scenery" else SCENE_SUFFIX


def manifest_cast_mode(saved: dict | None) -> str:
    """cast_mode của hồ sơ prompt đã lưu (hồ sơ cũ = có nhân vật)."""
    saved = saved or {}
    mode = saved.get("cast_mode") or (saved.get("continuity") or {}).get("cast_mode")
    return config.normalize_cast_mode(mode)


def project_context(project_id: int, title: str = "", idea: str | None = None) -> dict:
    from sqlmodel import Session, select
    from backend.database import engine
    from backend.models import CoverSong, Project, SourceSong, SunoBatch
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} không tồn tại.")
        batch = session.exec(select(SunoBatch).where(SunoBatch.project_id == project_id)
                             .order_by(SunoBatch.created_at.desc())).first()
        music_styles = ""
        if batch:
            try:
                music_styles = json.loads(batch.config_json or "{}").get("styles", "")
            except (ValueError, AttributeError):
                pass
        # Kiểu giọng là dữ liệu hình ảnh bắt buộc: nam → nhân vật nam, nữ → nữ,
        # duet/mixed → cặp nam-nữ. Ưu tiên lựa chọn đã lưu từ bước Suno; thiếu
        # thì dùng AI phân tích metadata bài và lưu lại để mọi lần dựng đồng bộ.
        sources = session.exec(select(SourceSong).where(
            SourceSong.project_id == project_id).order_by(SourceSong.position,
                                                           SourceSong.id)).all()
        casts: list[str] = []
        vocal_sources: list[str] = []
        for source in sources:
            cast = (source.vocal_gender or "").strip().lower()
            if cast not in ("male", "female", "duet") and source.artist.strip():
                try:
                    from backend.cover.suno_jobs import guess_vocal_gender
                    cast = guess_vocal_gender(source.title, source.artist)
                except Exception:
                    cast = ""
                if cast:
                    source.vocal_gender = cast
                    session.add(source)
            if cast:
                casts.append(cast)
            vocal_sources.append(" – ".join(x for x in
                                 (source.title, source.artist) if x.strip()))
        if not sources:
            songs = session.exec(select(CoverSong).where(
                CoverSong.project_id == project_id).order_by(CoverSong.position,
                                                              CoverSong.id)).all()
            for song in songs:
                vocal_sources.append(" – ".join(x for x in
                                     (song.title, song.artist) if x.strip()))
                if song.artist.strip():
                    try:
                        from backend.cover.suno_jobs import guess_vocal_gender
                        cast = guess_vocal_gender(song.title, song.artist)
                    except Exception:
                        cast = ""
                    if cast:
                        casts.append(cast)
        session.commit()
        cast_set = set(casts)
        vocal_cast = (next(iter(cast_set)) if len(cast_set) == 1 else
                      "duet" if cast_set else "auto")
        from backend.cover.genre_catalog import canonical_genre_context
        genre_analysis = canonical_genre_context(
            project.music_style or "", project.description or "", project.name or "")
        ctx = {
            "title": title or project.name,
            "visual_idea": (idea if idea is not None else project.video_idea) or "",
            "music_idea": project.suno_idea or "",
            "instrument": project.instrument or "",
            "purpose": project.music_style or "chill, relaxing, meditation",
            # Dòng nhạc (VN / US-UK …) — chỉ thêm khi có, xem _legacy_signature.
            **({"music_line": project.music_line.strip()}
               if (getattr(project, "music_line", "") or "").strip() else {}),
            "description": project.description or "",
            "music_styles": music_styles,
            "genre_analysis": genre_analysis,
            "vocal_cast": vocal_cast,
            "vocal_sources": [x for x in vocal_sources if x],
        }
        footage_mode = (getattr(project, "footage_scene_mode", None) or "auto").strip()
        resolved_footage = ("screen_only" if config.normalize_cast_mode(
            getattr(project, "cast_mode", None)) == "scenery" else "character_screen") \
            if footage_mode == "auto" else footage_mode
        ctx["footage_scene_mode"] = resolved_footage
        if resolved_footage == "character_screen":
            ctx["footage_scene_directive"] = (
                "Reserve one coherent cinematic presentation scene with the main "
                "character seen naturally watching a large television or projector "
                "screen. The screen must be a clean, front-facing 16:9 rectangle with "
                "a dark neutral surface, unobstructed and suitable for replacing with "
                "real footage during editing.")
        else:
            ctx["footage_scene_directive"] = (
                "Reserve one coherent cinematic presentation scene containing a large "
                "television or projector screen, with no people or animals. The screen "
                "must be a clean, front-facing 16:9 rectangle with a dark neutral "
                "surface, unobstructed and suitable for replacing with real footage.")
        # Chỉ thêm khoá khi CHỈ CẢNH VẬT → chữ ký hồ sơ có nhân vật giữ nguyên.
        if config.normalize_cast_mode(getattr(project, "cast_mode", None)) == "scenery":
            ctx["cast_mode"] = "scenery"
        return ctx


def _legacy_signatures(inputs: dict) -> list[str]:
    """Chữ ký hồ sơ kiểu cũ → ảnh/clip đã làm vẫn resume được sau khi thêm các
    trường ngữ cảnh mới. Làm lại từ đầu sẽ dùng đầy đủ quy trình mới."""
    ctx = inputs.get("context") or {}
    out: list[str] = []
    footage = {"footage_scene_mode", "footage_scene_directive"}
    variants = [footage, footage | {"music_line"}]
    for removed in variants:
        if not (removed & set(ctx)):
            continue
        old = {**inputs, "context": {k: v for k, v in ctx.items() if k not in removed}}
        sig = prompt_catalog.prompt_hash(json.dumps(old, ensure_ascii=False, sort_keys=True))
        if sig not in out:
            out.append(sig)
    return out


def _legacy_signature(inputs: dict) -> str | None:
    """Tương thích test/plugin cũ chỉ mong một chữ ký."""
    sigs = _legacy_signatures(inputs)
    return sigs[0] if sigs else None


def validate_set(values, count: int, label: str) -> dict[str, str]:
    expected = {str(i) for i in range(count)}
    if (not isinstance(values, dict) or set(values) != expected
            or any(not isinstance(v, str) or not v.strip() for v in values.values())):
        raise ValueError(f"{label} phải đủ {count} mục không trống, đúng chỉ số 0–{count - 1}. "
                         "Hãy chuẩn bị lại bộ prompt; không dùng cảnh/chuyển động mặc định thay thế.")
    return {k: v.strip() for k, v in values.items()}


def save_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".prompts-", suffix=".tmp", delete=False) as fh:
            name = fh.name
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def read_manifest(project_id: int, project_name: str | None) -> dict | None:
    path = config.images_dir(project_id, project_name) / "prompts.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise ValueError("Không đọc được prompts.json; giữ nguyên media và sửa bộ prompt trước.") from exc
    if not isinstance(data, dict):
        raise ValueError("prompts.json không phải hồ sơ hợp lệ.")
    trimmed = fit_scene_count(data)
    if trimmed is not data:
        # Giữ bản đủ cảnh cũ (vd 40) cạnh file — cắt về N không làm mất dữ liệu.
        backup = path.with_name(f"prompts.{trimmed['trimmed_from_scene_count']}scenes.bak.json")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        save_manifest(path, trimmed)
    return trimmed


_ENGLISH_FIELDS = ("prompts", "motions", "continuity", "idea", "creative_brief", "title")


def _to_english_keep_locks(text: str, lock_map: dict[str, str], api_config, log) -> str:
    """Dịch 1 prompt nhưng giữ khối khoá nhân vật GIỐNG HỆT nhau ở mọi prompt:
    tách chuỗi theo các khối khoá (bản gốc) → thay bằng bản dịch dùng chung của
    khoá, chỉ dịch phần còn lại (preamble / bối cảnh). Nhờ vậy validate_manifest
    ('khoá phải nằm nguyên văn trong mọi prompt') vẫn đạt sau khi dịch."""
    from .translate import needs_translation, to_english
    keys = sorted((k for k in lock_map if k), key=len, reverse=True)
    parts = re.split("(" + "|".join(map(re.escape, keys)) + ")", text) if keys else [text]
    out = []
    for part in parts:
        if part in lock_map:
            out.append(lock_map[part])
        elif needs_translation(part):
            core = part.strip()
            lead = part[:len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()):]
            out.append(lead + to_english(core, api_config, log) + trail)
        else:
            out.append(part)
    return "".join(out)


def english_manifest(project_id: int, saved: dict, api_config=None, log=None) -> dict:
    """Hồ sơ cũ (trước quy tắc 2026-09-26) còn tiếng Việt → trả BẢN SAO đã dịch
    100% sang tiếng Anh (Flow/Gemini hiểu tiếng Anh tốt nhất). Đã là tiếng Anh →
    trả chính `saved`. Không dịch được → TranslationError (caller dừng, không
    gửi prompt lẫn tiếng Việt)."""
    from .translate import TranslationError, non_english_fields, to_english
    view = {k: saved.get(k) for k in _ENGLISH_FIELDS if saved.get(k) is not None}
    if not non_english_fields(view):
        return saved
    out = dict(saved)
    locks = dict(saved.get("continuity") or {})
    lock_map: dict[str, str] = {}
    for key in ("character_sheet", "pet_sheet"):
        v = locks.get(key)
        if isinstance(v, str) and v.strip():
            en = to_english(v.strip(), api_config, log)
            lock_map[v.strip()] = en
            locks[key] = en
    for key, v in list(locks.items()):
        if key not in ("character_sheet", "pet_sheet"):
            locks[key] = to_english(v, api_config, log)
    if saved.get("continuity") is not None:
        out["continuity"] = locks
    for field in ("prompts", "motions"):
        values = saved.get(field)
        if isinstance(values, dict):
            out[field] = {k: (_to_english_keep_locks(v, lock_map, api_config, log)
                              if isinstance(v, str) else v) for k, v in values.items()}
    for field in ("idea", "creative_brief", "title"):
        if saved.get(field) is not None:
            out[field] = to_english(saved[field], api_config, log)
    left = non_english_fields({k: out.get(k) for k in _ENGLISH_FIELDS if out.get(k) is not None})
    if left:
        raise TranslationError("Dịch hồ sơ prompt sang tiếng Anh chưa trọn: "
                               + ", ".join(left[:8]))
    try:
        out["prompt_hashes"] = validate_manifest(project_id, out)
    except ValueError:
        pass
    out["translated_to_english"] = True
    return out


def ensure_english_manifest(project_id: int, project_name: str | None,
                            api_config=None, log=None, *, extend: bool = True) -> dict | None:
    """read_manifest + tự dịch hồ sơ cũ sang tiếng Anh (lưu lại, giữ bản gốc ở
    prompts.vi.bak.json). Lỗi dịch → RuntimeError, KHÔNG trả hồ sơ tiếng Việt.
    extend=True: hồ sơ ít cảnh hơn cấu hình (20 → 40) được AI viết bổ sung."""
    from .translate import TranslationError
    saved = read_manifest(project_id, project_name)
    if saved is None:
        return None
    try:
        out = english_manifest(project_id, saved, api_config, log)
    except TranslationError as exc:
        raise RuntimeError(f"Hồ sơ prompt cũ còn tiếng Việt và chưa dịch được sang "
                           f"tiếng Anh: {exc}") from exc
    if out is not saved:
        path = config.images_dir(project_id, project_name) / "prompts.json"
        backup = path.with_name("prompts.vi.bak.json")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        save_manifest(path, out)
        if log:
            log("Đã chuyển hồ sơ prompt cũ sang 100% tiếng Anh (bản gốc: prompts.vi.bak.json).")
    out = _footwear_safe(project_id, project_name, out, log)
    grown = upgrade_cover_manifest(project_id, out)
    if grown is not out:
        save_manifest(config.images_dir(project_id, project_name) / "prompts.json", grown)
        if log:
            log("Nâng hồ sơ ảnh bìa: thêm bảng phân tích nhân vật + bảng bối cảnh (vẽ từ "
                "ảnh bìa) và 4 clip góc quay nhân vật — giữ nguyên ảnh bìa + clip đã có.")
        out = grown
    if extend:
        grown = extend_scene_count(project_id, out, api_config=api_config, log=log)
        if grown is not out:
            save_manifest(config.images_dir(project_id, project_name) / "prompts.json", grown)
            if log:
                log(f"Đã bổ sung prompt cảnh: {grown['extended_from_scene_count']} → "
                    f"{len(grown['motions'])}.")
            out = grown
    return out


def _footwear_safe(project_id: int, project_name: str | None, saved: dict, log=None) -> dict:
    """Hồ sơ tả nhân vật CHÂN TRẦN → Gemini chặn ngầm bảng nhân vật (trả «I
    encountered an error…» mãi). Khi bảng nhân vật (1.png) CHƯA vẽ, đổi sang dép
    rơm ở prompts + motions + khoá continuity (cùng một phép thay → nhân vật vẫn
    nhất quán), lưu lại; bản gốc ở prompts.barefoot.bak.json. Đã có 1.png thì giữ
    nguyên (danh tính đã chốt bằng ảnh)."""
    from .footwear_safety import sanitize_manifest
    img_dir = config.images_dir(project_id, project_name)
    if (img_dir / f"{config.IMG_MAIN_SHEET}.png").exists():
        return saved
    fixed = sanitize_manifest(saved)
    if fixed is saved:
        return saved
    try:
        fixed["prompt_hashes"] = validate_manifest(
            project_id, fixed, len(fixed.get("prompts") or {}) or None,
            len(fixed.get("motions") or {}) or None)
    except ValueError as exc:
        if log:
            log(f"Không tự đổi được mô tả chân trần ({exc}) — giữ nguyên hồ sơ.")
        return saved
    path = img_dir / "prompts.json"
    backup = path.with_name("prompts.barefoot.bak.json")
    if not backup.exists():
        try:
            backup.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    save_manifest(path, fixed)
    if log:
        log("Hồ sơ tả nhân vật chân trần (Gemini chặn ngầm ảnh bàn chân) → đã đổi sang "
            "dép rơm ở mọi prompt ảnh/cảnh (bản gốc: prompts.barefoot.bak.json).")
    return fixed


_SIGNATURE_KEYS = ("context", "style", "count", "scene_count", "style_version", "workflow_version")


def fit_scene_count(saved: dict, target: int | None = None) -> dict:
    """Hồ sơ lưu NHIỀU cảnh hơn cấu hình hiện tại (vd 40 → 20 clip/video): giữ
    nguyên hồ sơ nhân vật + ảnh, chỉ cắt motions về 0..N-1 và cập nhật chữ ký để
    project đang dở chạy tiếp mà không phải 'Làm lại từ đầu'. Trả CHÍNH `saved`
    nếu không cần cắt, ngược lại trả bản sao đã cắt."""
    target = target or config.PARAMS.total_clips
    motions = saved.get("motions")
    if not isinstance(motions, dict) or len(motions) <= target:
        return saved
    if config.single_image() and saved.get("workflow_version") != SINGLE_WORKFLOW_VERSION:
        return saved      # hồ sơ 3 ảnh cũ: không cắt — chế độ 1 ảnh cần làm lại hồ sơ
    if not all(str(i) in motions for i in range(target)):
        return saved
    out = dict(saved)
    out["motions"] = {str(i): motions[str(i)] for i in range(target)}
    if "scene_count" in saved:
        out["scene_count"] = target
    _resign(saved, out, target)
    hashes = saved.get("prompt_hashes")
    if isinstance(hashes, dict) and isinstance(hashes.get("flow"), dict):
        out["prompt_hashes"] = {**hashes, "flow": {k: v for k, v in hashes["flow"].items()
                                                   if k in out["motions"]}}
    out["trimmed_from_scene_count"] = int(saved.get("scene_count") or len(motions))
    return out


def _resign(saved: dict, out: dict, target: int) -> None:
    """Cập nhật input_signature theo scene_count mới — chỉ khi chữ ký cũ khớp."""
    old_sig = saved.get("input_signature")
    if old_sig and all(k in saved for k in _SIGNATURE_KEYS):
        before = {k: saved[k] for k in _SIGNATURE_KEYS}
        if prompt_catalog.prompt_hash(json.dumps(before, ensure_ascii=False, sort_keys=True)) == old_sig:
            after = {**before, "scene_count": target}
            out["input_signature"] = prompt_catalog.prompt_hash(
                json.dumps(after, ensure_ascii=False, sort_keys=True))


def upgrade_cover_manifest(project_id: int, saved: dict) -> dict:
    """Hồ sơ tool Cover v3 (1 ảnh bìa, clip 00 + 01) → v4: GIỮ prompt ảnh bìa +
    intro + loop, thêm prompt 2 bảng (vẽ từ ảnh bìa) + 4 góc quay. Không gọi AI.
    Trả CHÍNH `saved` nếu không cần nâng, ngược lại bản sao đã nâng + kiểm tra."""
    if (not config.single_image() or not saved.get("single_image")
            or saved.get("workflow_version") != COVER_V3):
        return saved
    prompts, motions = saved.get("prompts") or {}, saved.get("motions") or {}
    if set(prompts) != {"0"} or set(motions) != {"0", "1"}:
        return saved
    from . import cover_scene
    cast_mode = manifest_cast_mode(saved)
    scene = (saved.get("continuity") or {}).get("scene") or "the place in the cover image"
    out = dict(saved)
    continuity = saved.get("continuity") or {}
    couple = continuity.get("cast") == "couple"
    male = continuity.get("vocal_cast") == "male"
    out["prompts"] = {**prompts, **cover_scene.sheet_prompts(
        scene, cast_mode, couple, male)}
    out["motions"] = {**motions, **cover_scene.angle_motions(
        scene, "", cast_mode, couple, male)}
    count, scenes = config.PARAMS.image_count, config.PARAMS.total_clips
    old_sig = saved.get("input_signature")
    if old_sig and all(k in saved for k in _SIGNATURE_KEYS):
        before = {k: saved[k] for k in _SIGNATURE_KEYS}
        if prompt_catalog.prompt_hash(json.dumps(before, ensure_ascii=False, sort_keys=True)) == old_sig:
            after = {**before, "count": count, "scene_count": scenes,
                     "workflow_version": SINGLE_WORKFLOW_VERSION}
            out.update(after)
            out["input_signature"] = prompt_catalog.prompt_hash(
                json.dumps(after, ensure_ascii=False, sort_keys=True))
    out["workflow_version"] = SINGLE_WORKFLOW_VERSION
    out["upgraded_from_workflow_version"] = COVER_V3
    out["prompt_hashes"] = validate_manifest(project_id, out, count, scenes)
    return out


def extend_scene_count(project_id: int, saved: dict, target: int | None = None,
                       api_config=None, log=None, _generate=None) -> dict:
    """Hồ sơ lưu ÍT cảnh hơn cấu hình hiện tại (vd 20 → 40 clip/video): giữ
    nguyên hồ sơ nhân vật, ảnh và các cảnh đã có; AI chỉ viết THÊM cảnh
    N..target-1 từ hai bảng nhân vật đã khoá (tránh bối cảnh cũ). Trả CHÍNH
    `saved` nếu không cần bổ sung, ngược lại trả bản sao đã bổ sung + kiểm tra."""
    target = target or config.PARAMS.total_clips
    motions = saved.get("motions")
    if (not isinstance(motions, dict) or len(motions) >= target
            or saved.get("workflow_version") != WORKFLOW_VERSION):
        return saved
    have = len(motions)
    if not all(str(i) in motions for i in range(have)):
        return saved
    locks = saved.get("continuity") or {}
    character_sheet = (locks.get("character_sheet") or "").strip()
    pet_sheet = (locks.get("pet_sheet") or "").strip()
    if len(character_sheet) < 20 or len(pet_sheet) < 20:
        raise ValueError("Hồ sơ thiếu khoá continuity — không bổ sung cảnh được; "
                         "dùng Làm lại từ đầu.")
    need = target - have
    cast_mode = manifest_cast_mode(saved)
    sheets = ("bảng bối cảnh + bảng ánh sáng" if cast_mode == "scenery"
              else "hai bảng nhân vật")
    if log:
        log(f"Nâng {have} → {target} clip/video: AI viết thêm {need} prompt cảnh "
            f"từ {sheets} đã khoá (giữ nguyên ảnh + cảnh cũ)…")
    if _generate is None:
        from .prompt_gen import generate_scene_prompts as _generate
        if api_config is None:
            from backend.core_bridge import get_api_config_from_db
            from backend.database import DB_PATH
            api_config = get_api_config_from_db(str(DB_PATH))
    context = saved.get("context")
    idea = json.dumps(context, ensure_ascii=False) if context else (saved.get("idea") or "")
    avoid = list(locks.get("settings") or [])
    style = config.style_info(config.normalize_style(saved.get("style_key") or saved.get("style")))["brief"]
    kwargs = {"cast_mode": cast_mode} if cast_mode == "scenery" else {}
    extra = _generate(character_sheet=character_sheet, pet_sheet=pet_sheet, idea=idea,
                      style=style, api_config=api_config, scene_count=need, log=log,
                      avoid=avoid or None, **kwargs)
    extra = validate_set(extra, need, "Prompt cảnh bổ sung") if extra else None
    if not extra:
        raise RuntimeError(f"AI chưa viết thêm được {need} prompt cảnh (kiểm tra API "
                           "key/model ở ⚙️ Settings rồi thử lại).")
    new = dict(motions)
    suffix = scene_suffix(cast_mode)
    for j in range(need):
        new[str(have + j)] = extra[str(j)] + suffix
    out = dict(saved)
    out["motions"] = new
    if "scene_count" in saved:
        out["scene_count"] = target
    _resign(saved, out, target)
    out.pop("trimmed_from_scene_count", None)
    out["extended_from_scene_count"] = have
    out["prompt_hashes"] = validate_manifest(
        project_id, out, len(saved.get("prompts") or {}) or None, target)
    return out


def validate_manifest(project_id: int, saved: dict, count: int | None = None,
                      scene_count: int | None = None) -> dict:
    count = count or config.PARAMS.image_count
    scene_count = scene_count or config.PARAMS.total_clips
    prompts = validate_set(saved.get("prompts"), count, "Prompt ảnh")
    motions = validate_set(saved.get("motions"), scene_count, "Prompt cảnh (clip)")
    if saved.get("workflow_version") == WORKFLOW_VERSION:
        for label, values, n in (("ảnh", prompts, count), ("cảnh", motions, scene_count)):
            if len({prompt_catalog.prompt_hash(v) for v in values.values()}) != n:
                raise ValueError(f"Bộ prompt {label} lặp nguyên văn giữa các shot; cần biến thể theo từng {label}.")
        # Khoá continuity: character_sheet nằm trong ảnh 0 (bìa) + ảnh 1 (bảng
        # nhân vật chính); pet_sheet nằm trong ảnh 0 + ảnh 2 (bảng linh thú).
        # CẢ HAI phải nằm trong MỌI prompt cảnh — đó là thứ giữ N clip cùng
        # một nhân vật.
        locks = saved.get("continuity", {})
        owners = {"character_sheet": (config.IMG_THUMBNAIL, config.IMG_MAIN_SHEET),
                  "pet_sheet": (config.IMG_THUMBNAIL, config.IMG_PET_SHEET)}
        for key, idxs in owners.items():
            lock = locks.get(key, "")
            if not isinstance(lock, str) or len(lock.strip()) < 20:
                raise ValueError(f"Thiếu khoá continuity {key}.")
            for i in idxs:
                if lock not in prompts[str(i)]:
                    raise ValueError(f"Prompt ảnh {i} đã mất khối khoá {key}.")
            if any(lock not in value for value in motions.values()):
                raise ValueError(f"Một số prompt cảnh đã mất khối khoá {key}.")
    hashes = {
        "gemini": prompt_catalog.claim_prompts(project_id, "gemini", prompts),
        "flow": prompt_catalog.claim_prompts(project_id, "flow", motions),
    }
    return hashes


def _prepare_single(project_id: int, img_dir: Path, inputs: dict, signature: str,
                    context: dict, style_key: str, cast_mode: str, recipe, directive: str,
                    api_config, log) -> dict:
    """Tool Cover: prompt ảnh bìa + 2 bảng (vẽ từ ảnh bìa); intro, loop, 4 góc quay."""
    from . import cover_scene
    log("Chuẩn bị hồ sơ: ảnh bìa → bảng phân tích nhân vật + bảng bối cảnh (vẽ từ ảnh "
        "bìa) → clip 00 intro, clip 01 lặp nhẹ, clip 02–05 bốn góc quay nhân vật…")
    prompts, motions, continuity = cover_scene.build(
        context, config.style_info(style_key)["brief"], cast_mode, directive,
        api_config, log)
    saved = {**inputs, "input_signature": signature, "continuity": continuity,
             "style_key": style_key, "prompts": prompts, "motions": motions,
             "single_image": True, "cover_rev": cover_scene.COVER_REV,
             "review_note": "Ảnh bìa + 2 bảng → intro, loop, 4 góc quay; xem lại "
                            "ảnh/clip trước khi đăng."}
    if cast_mode == "scenery":
        saved["cast_mode"] = "scenery"
    if recipe:
        saved["thumbnail_recipe"] = recipe
    saved["prompt_hashes"] = validate_manifest(project_id, saved, len(prompts), len(motions))
    save_manifest(img_dir / "prompts.json", saved)
    src = "AI viết" if continuity.get("source") == "ai" else "mẫu theo thể loại"
    log(f"Đã lưu hồ sơ ảnh bìa ({src}): bối cảnh «{continuity.get('scene', '')[:90]}».")
    return saved


def prepare(project_id: int, project_name: str | None = None, idea: str | None = None,
            style_key: str | None = None, log=None, *, allow_rebuild: bool = False) -> dict:
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_prompts, generate_scene_prompts

    log = log or (lambda _: None)
    context = project_context(project_id, project_name or "", idea)
    style_key = config.normalize_style(style_key)
    count = config.PARAMS.image_count          # 3 ảnh: bìa + 2 bảng nhân vật
    scenes = config.PARAMS.total_clips         # N clip, N bối cảnh khác nhau
    inputs = {"context": context, "style": style_key, "count": count,
              "scene_count": scenes,
              "style_version": config.STYLE_VERSION, "workflow_version": workflow_version()}
    signature = prompt_catalog.prompt_hash(json.dumps(inputs, ensure_ascii=False, sort_keys=True))
    img_dir = config.images_dir(project_id, project_name)
    saved = ensure_english_manifest(project_id, project_name, log=log)
    legacy = _legacy_signatures(inputs)
    if (saved and saved.get("input_signature") in legacy and not allow_rebuild):
        signature = saved.get("input_signature")  # hồ sơ cũ — chạy tiếp như cũ
    if (saved and saved.get("input_signature") == signature and allow_rebuild
            and config.single_image() and saved.get("single_image")
            and saved.get("cover_rev") != _cover_rev()):
        # «Làm lại từ đầu»: hồ sơ 1 ảnh viết theo mẫu ảnh bìa cũ (người quay lưng)
        # → viết lại theo mẫu mới. Chạy tiếp (resume) vẫn giữ hồ sơ cũ khớp ảnh cũ.
        log("Mẫu ảnh bìa đã đổi (nhân vật nữ che mặt, nhìn thẳng camera kiểu mixtape) "
            "— viết lại hồ sơ.")
        saved = None
    if saved and saved.get("input_signature") == signature:
        try:
            validate_manifest(project_id, saved, count, scenes)
        except ValueError:
            if not allow_rebuild:
                raise
            log("Bộ đã lưu không còn hợp lệ; viết lại đầy đủ trước khi thay ảnh.")
        else:
            log("Dùng lại hồ sơ và prompt đã khoá của project; không gọi AI lại.")
            return saved
    if not allow_rebuild and any(img_dir.glob("*.png")):
        # Historical manifests can resume unchanged. Never replace prompts while
        # keeping existing images produced from a different identity/layout.
        if saved and not saved.get("workflow_version"):
            same_idea = not context["visual_idea"] or saved.get("idea", "").strip() == context["visual_idea"].strip()
            same_style = config.normalize_style(saved.get("style_key")) == style_key
            if same_idea and same_style:
                validate_manifest(project_id, saved, count, scenes)
                log("Tiếp tục bộ prompt cũ, giữ nguyên ảnh và nhân vật đã tạo.")
                return saved
        raise ValueError("Hồ sơ/số lượng/phong cách đã đổi nhưng project có ảnh. "
                         "Dùng Làm lại từ đầu khi muốn thay cả bộ; không trộn prompt mới với ảnh cũ.")

    brief = prompt_catalog.project_brief(project_id, context["title"],
                                         json.dumps(context, ensure_ascii=False))
    api_config = get_api_config_from_db(str(DB_PATH))
    locks = {}
    effective_idea = json.dumps(context, ensure_ascii=False)
    # Công thức thumbnail riêng của video (bố cục/góc máy/ánh sáng/kiểu chữ) —
    # chống lặp theo kênh; KHÔNG đưa vào input_signature (chỉ là hướng dẫn bố cục).
    recipe, directive = None, ""
    cast_mode = config.normalize_cast_mode(context.get("cast_mode"))
    scenery = cast_mode == "scenery"
    try:
        from . import thumbnail_recipe
        recipe = thumbnail_recipe.new_recipe_for_prompts(project_id, style_key)
        directive = thumbnail_recipe.composition_directive(recipe, scenery=scenery)
        if directive:
            log(f"Ảnh bìa: bố cục «{recipe.get('layout')}», góc máy «{recipe.get('shot')}», "
                f"ánh sáng «{recipe.get('light')}», kiểu chữ «{recipe.get('treatment')}».")
    except Exception as exc:
        log(f"Không chọn được công thức thumbnail ({type(exc).__name__}: {exc}) — dùng bố cục cũ.")
        recipe, directive = None, ""
    if config.single_image():
        return _prepare_single(project_id, img_dir, inputs, signature, context, style_key,
                               cast_mode, recipe, directive, api_config, log)
    sheets = ("bảng bối cảnh + bảng ánh sáng" if scenery
              else "2 bảng phân tích nhân vật")
    mode_kw = {"cast_mode": cast_mode} if scenery else {}
    log(f"Chuẩn bị hồ sơ chung → {count} ảnh (bìa + {sheets}) "
        f"và {scenes} prompt cảnh; kiểm tra continuity và chống trùng…")
    result = generate_prompts(
        idea=effective_idea, title=context["title"], keywords=context["purpose"],
        scene_count=scenes, aspect_ratio=config.PARAMS.aspect_ratio,
        style=config.style_info(style_key)["brief"], api_config=api_config, log=log,
        creative_brief=brief["visual"] + "\nMusic: " + brief["music"], metadata_out=locks,
        instrument=context["instrument"], thumbnail_directive=directive, **mode_kw)
    if not result:
        raise RuntimeError("AI chưa tạo được hồ sơ/prompt đầy đủ. Kiểm tra cấu hình API rồi thử lại; "
                           "Tool chưa mở Gemini/Flow và không thay bằng prompt chung.")
    prompts, motions = result
    prompts = validate_set(prompts, count, "Prompt ảnh")
    try:
        motions = validate_set(motions, scenes, "Prompt cảnh (clip)")
    except ValueError:
        log(f"Prompt cảnh chưa đủ; viết bổ sung từ {sheets} đã khoá.")
        motions = generate_scene_prompts(
            character_sheet=locks.get("character_sheet", ""),
            pet_sheet=locks.get("pet_sheet", ""), idea=effective_idea,
            style=config.style_info(style_key)["brief"], api_config=api_config,
            scene_count=scenes, log=log, avoid=locks.get("settings") or None,
            instrument=context["instrument"], **mode_kw)
        motions = validate_set(motions, scenes, "Prompt cảnh (clip)")
    suffix = scene_suffix(cast_mode)
    motions = {key: value + suffix for key, value in motions.items()}
    if directive and directive not in prompts.get("0", ""):
        prompts["0"] = prompts["0"].rstrip() + "\n\n" + directive
    saved = {**inputs, "input_signature": signature, "creative_brief": brief,
             "continuity": locks, "title": context["title"], "idea": context["visual_idea"],
             "style_key": style_key, "prompts": prompts, "motions": motions,
             "review_note": "Structural checks passed; visual/music quality still requires output review."}
    if scenery:
        saved["cast_mode"] = "scenery"
    if recipe:
        saved["thumbnail_recipe"] = recipe
    saved["prompt_hashes"] = validate_manifest(project_id, saved, count, scenes)
    save_manifest(img_dir / "prompts.json", saved)
    log(f"Đã lưu bộ prompt đầy đủ: {count} ảnh + {scenes} cảnh — mọi clip dùng "
        f"chung {sheets} làm nguyên liệu, chỉ khác bối cảnh.")
    return saved
