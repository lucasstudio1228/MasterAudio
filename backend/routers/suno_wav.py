"""Công cụ tải WAV/ảnh/lyrics/ZIP qua phiên Suno, không qua bên thứ ba."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.cover.suno_wav_downloader import (
    SunoWavDownloadError, download_authorized_song_package, resolve_song_id,
)
from backend.database import get_session
from backend.models import Project
from backend.video import config as video_config
from backend.video.browser_base import BrowserSession
from backend.video.config import project_dir

router = APIRouter(prefix="/api/projects/{project_id}/suno-wav", tags=["suno-wav"])


class DownloadBody(BaseModel):
    links: str
    allow_compatible: bool = True


@router.post("/download")
def download_suno_wav(project_id: int, body: DownloadBody,
                      db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project or getattr(project, "deleted_at", None):
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    folder = (project_dir(project_id, project.name, kind=getattr(project, "kind", None))
              / "ban_cover" / "_suno" / "wav_downloader")
    rows = list(dict.fromkeys(x.strip() for x in body.links.splitlines() if x.strip()))
    if not rows:
        raise HTTPException(422, "Hãy dán ít nhất một link hoặc ID bài Suno.")
    if len(rows) > 20:
        raise HTTPException(422, "Mỗi lượt tải tối đa 20 bài.")
    items = []
    try:
        with BrowserSession(headless=False, profile_dir=video_config.SUNO_PROFILE_DIR,
                            site="suno") as browser:
            page = browser.new_page()
            for raw in rows:
                try:
                    song_id = resolve_song_id(raw)
                    items.append({"ok": True, **download_authorized_song_package(
                        page, song_id, folder)})
                except Exception as exc:  # bài lỗi không xóa những bài đã tải xong
                    items.append({"ok": False, "input": raw, "error": str(exc)[:800]})
    except SunoWavDownloadError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Không mở được phiên Suno: {str(exc)[:500]}") from exc
    return {"folder": str(folder), "items": items,
            "downloaded": sum(1 for item in items if item.get("ok"))}
