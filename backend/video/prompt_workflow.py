"""Ngữ cảnh project (dòng nhạc, thể loại, giọng hát…) cho prompt Suno.

Bản Cover Audio Tool: phần chuẩn bị prompt ảnh/clip cho video đã được gỡ bỏ."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from . import config


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
