"""Quản lý track của 1 project: scan, add, upload, xoá vào thùng rác, khôi phục."""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend import core_bridge
from backend.database import get_session
from backend.models import CoverSong, DeletedTrack, SourceSong, Track
from backend.routers.projects import get_project_or_404
from backend.schemas import (AddFileRequest, ScanRequest, ScanResult,
                             TrackResponse)
from backend.video import config as video_config

router = APIRouter(prefix="/projects", tags=["tracks"])


def _safe_filename(name: str) -> str:
    """Bỏ ký tự nguy hiểm khỏi tên file upload (chống path traversal)."""
    base = Path(name or "").name                       # bỏ mọi thành phần thư mục
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return base or "upload"


def _delete_track_stems(session: Session, track: Track) -> None:
    """Xóa stem files + TrackStem record của 1 track (FK track_id → track.id)."""
    import shutil
    from backend.models import TrackStem
    from backend.stem_service import STEMS_ROOT

    stem_dir = STEMS_ROOT / str(track.project_id) / str(track.id)
    if stem_dir.exists():
        shutil.rmtree(stem_dir, ignore_errors=True)
    stem = session.exec(
        select(TrackStem).where(TrackStem.track_id == track.id)).first()
    if stem:
        session.delete(stem)
    # Tool Cover: lời bài hát gắn với track → xoá theo.
    from backend.models import CoverSong
    song = session.exec(
        select(CoverSong).where(CoverSong.track_id == track.id)).first()
    if song:
        session.delete(song)
    session.flush()


def _existing_paths(session: Session, project_id: int) -> set[str]:
    rows = session.exec(
        select(Track.filepath).where(Track.project_id == project_id)).all()
    return {str(Path(p).resolve()) for p in rows}


def _persist(session: Session, project_id: int, core_track) -> Track:
    track = Track(
        project_id=project_id,
        filename=Path(core_track.path).name,
        filepath=str(Path(core_track.path).resolve()),
        duration_seconds=core_track.duration_seconds,
        sample_rate=core_track.sample_rate,
        channels=core_track.channels,
        format=core_track.format,
        is_lossy=core_track.is_lossy,
        subtype=core_track.subtype,
    )
    session.add(track)
    return track


def _trash_track(session: Session, track: Track) -> None:
    """Chụp đủ Track + CoverSong trước khi xoá bản ghi; file audio luôn được giữ."""
    song = session.exec(
        select(CoverSong).where(CoverSong.track_id == track.id)).first()
    cover_data = ""
    if song:
        data = song.model_dump(exclude={"id", "track_id"})
        cover_data = json.dumps(data, ensure_ascii=False, default=str)
    session.add(DeletedTrack(
        project_id=track.project_id, original_track_id=track.id,
        filename=track.filename, filepath=track.filepath,
        duration_seconds=track.duration_seconds, sample_rate=track.sample_rate,
        channels=track.channels, format=track.format, is_lossy=track.is_lossy,
        subtype=track.subtype, added_at=track.added_at,
        cover_song_json=cover_data))
    _delete_track_stems(session, track)
    session.delete(track)


def _restore_snapshot(session: Session, row: DeletedTrack) -> Track | None:
    path = Path(row.filepath)
    if not path.is_file():
        return None
    resolved = str(path.resolve())
    duplicate = session.exec(select(Track).where(
        Track.project_id == row.project_id, Track.filepath == resolved)).first()
    if duplicate:
        session.delete(row)
        return duplicate
    core_track = core_bridge.probe_file(str(path))
    if core_track is None:
        return None
    track = _persist(session, row.project_id, core_track)
    track.added_at = row.added_at
    session.flush()
    if row.cover_song_json:
        try:
            data = json.loads(row.cover_song_json)
            data.pop("id", None)
            data["project_id"] = row.project_id
            data["track_id"] = track.id
            # SQLModel table constructors do not validate/parse fields. Snapshot
            # JSON serialises datetime as text, so ``CoverSong(**data)`` reaches
            # SQLite with a str for aligned_at and aborts the whole restore.
            # model_validate restores typed datetime/float/bool values first.
            session.add(CoverSong.model_validate(data))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    session.delete(row)
    return track


def _orphan_cover_files(session: Session, project) -> list[Path]:
    """File cover còn trên disk nhưng Track đã bị xoá trước khi có thùng rác."""
    from backend.cover import nct_jobs
    from backend.video import config as video_config
    folder = nct_jobs.cover_dir(project.id, project.name)
    active = _existing_paths(session, project.id)
    if not folder.is_dir():
        return []
    paths = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in video_config.AUDIO_EXTS
        and str(p.resolve()) not in active]
    # Bản không hậu tố đứng trước "cover 2", "cover 3" như lúc tạo ban đầu.
    return sorted(paths, key=lambda p: (
        bool(re.search(r"\s\d+$", p.stem)), p.name.casefold()))


def _source_for_cover(session: Session, project_id: int, path: Path):
    stem = path.stem.casefold()
    rows = session.exec(select(SourceSong).where(
        SourceSong.project_id == project_id)).all()
    matches = [s for s in rows if s.title and s.title.casefold() in stem]
    return max(matches, key=lambda s: len(s.title), default=None)


@router.get("/{project_id}/tracks", response_model=list[TrackResponse])
def list_tracks(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    return session.exec(
        select(Track).where(Track.project_id == project_id)
        .order_by(Track.added_at)).all()


@router.post("/{project_id}/tracks/scan", response_model=ScanResult)
def scan_folder(project_id: int, data: ScanRequest,
                session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    folder = Path(data.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(404, f"Folder không tồn tại: {data.folder_path}")

    core_tracks, error_files = core_bridge.scan_folder(
        str(folder), recursive=data.recursive)
    existing = _existing_paths(session, project_id)

    added = 0
    skipped_duplicates = 0
    warning_lossy = False
    for core_track in core_tracks:
        resolved = str(Path(core_track.path).resolve())
        if resolved in existing:
            skipped_duplicates += 1
            continue
        _persist(session, project_id, core_track)
        existing.add(resolved)
        added += 1
        warning_lossy = warning_lossy or core_track.is_lossy
    session.commit()

    total = len(session.exec(
        select(Track).where(Track.project_id == project_id)).all())
    return ScanResult(
        added=added, skipped_duplicates=skipped_duplicates,
        skipped_errors=len(error_files), error_files=error_files,
        total_tracks=total, warning_lossy=warning_lossy,
    )


@router.post("/{project_id}/tracks/add-file", response_model=TrackResponse)
def add_file(project_id: int, data: AddFileRequest,
             session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    path = Path(data.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"File không tồn tại: {data.file_path}")

    resolved = str(path.resolve())
    if resolved in _existing_paths(session, project_id):
        raise HTTPException(409, "File đã có trong project")

    core_track = core_bridge.probe_file(str(path))
    if core_track is None:
        raise HTTPException(400, f"Không đọc được file audio: {path.name}")

    track = _persist(session, project_id, core_track)
    session.commit()
    session.refresh(track)
    return track


@router.post("/{project_id}/tracks/upload", response_model=TrackResponse)
def upload_track(project_id: int,
                 file: UploadFile = File(...),
                 session: Session = Depends(get_session)):
    """Upload 1 file audio TỪ MÁY người dùng → lưu vào media/<project>/uploads/,
    đăng ký thành track VÀ dùng luôn làm nhạc nền video (find_latest_audio quét
    thư mục uploads). Cho phép 'upload track dài rồi tạo video' không cần mix."""
    project = get_project_or_404(session, project_id)

    fname = _safe_filename(file.filename or "upload")
    ext = Path(fname).suffix.lower()
    if ext not in video_config.AUDIO_EXTS:
        raise HTTPException(
            400, f"Định dạng '{ext or '?'}' không hỗ trợ. Chấp nhận: "
                 f"{', '.join(video_config.AUDIO_EXTS)}")

    up_dir = video_config.uploads_dir(project_id, project.name)
    up_dir.mkdir(parents=True, exist_ok=True)
    dest = up_dir / fname
    # Tránh ghi đè file trùng tên: thêm hậu tố _1, _2, ...
    if dest.exists():
        stem, suffix = Path(fname).stem, Path(fname).suffix
        i = 1
        while (up_dir / f"{stem}_{i}{suffix}").exists():
            i += 1
        dest = up_dir / f"{stem}_{i}{suffix}"

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        file.file.close()

    resolved = str(dest.resolve())
    if resolved in _existing_paths(session, project_id):
        raise HTTPException(409, "File đã có trong project")

    core_track = core_bridge.probe_file(str(dest))
    if core_track is None:
        dest.unlink(missing_ok=True)                   # dọn file rác nếu không đọc được
        raise HTTPException(400, f"Không đọc được file audio: {fname}")

    track = _persist(session, project_id, core_track)
    session.commit()
    session.refresh(track)
    return track


@router.delete("/{project_id}/tracks/{track_id}")
def delete_track(project_id: int, track_id: int,
                 session: Session = Depends(get_session)):
    track = session.get(Track, track_id)
    if track is None or track.project_id != project_id:
        raise HTTPException(404, f"Track {track_id} không tồn tại trong project")
    _trash_track(session, track)
    session.commit()
    return {"ok": True, "recoverable": True}


@router.delete("/{project_id}/tracks")
def delete_all_tracks(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    for track in tracks:
        _trash_track(session, track)
    session.commit()
    return {"deleted": len(tracks), "recoverable": len(tracks)}


@router.get("/{project_id}/tracks/recovery")
def recovery_info(project_id: int, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    trash = session.exec(select(DeletedTrack).where(
        DeletedTrack.project_id == project_id).order_by(DeletedTrack.deleted_at.desc())).all()
    orphans = _orphan_cover_files(session, project)
    return {
        "trash_count": len(trash), "orphan_count": len(orphans),
        "total": len(trash) + len(orphans),
        "items": [{"id": r.id, "filename": r.filename,
                   "file_exists": Path(r.filepath).is_file(),
                   "deleted_at": r.deleted_at} for r in trash],
        "orphan_files": [p.name for p in orphans],
    }


class RestoreSelection(BaseModel):
    """Chỉ khôi phục các bài người dùng tích chọn (id thùng rác + tên file cũ)."""
    trash_ids: list[int] = []
    orphan_files: list[str] = []


@router.get("/{project_id}/tracks/recovery/{trash_id}/audio")
def recovery_audio(project_id: int, trash_id: int,
                   session: Session = Depends(get_session)):
    """Nghe thử bài trong thùng rác trước khi chọn khôi phục."""
    row = session.get(DeletedTrack, trash_id)
    if row is None or row.project_id != project_id or not Path(row.filepath).is_file():
        raise HTTPException(404, "File của bài đã xoá không còn trên ổ đĩa.")
    return FileResponse(row.filepath)


@router.get("/{project_id}/tracks/recovery-orphan/audio")
def recovery_orphan_audio(project_id: int, name: str,
                          session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    for path in _orphan_cover_files(session, project):
        if path.name == name:           # chỉ file nằm trong danh sách mồ côi
            return FileResponse(str(path))
    raise HTTPException(404, "Không tìm thấy file cũ này.")


@router.post("/{project_id}/tracks/recovery/restore")
def restore_selected_tracks(project_id: int, data: RestoreSelection,
                            session: Session = Depends(get_session)):
    if not data.trash_ids and not data.orphan_files:
        raise HTTPException(400, "Chưa chọn bài nào để khôi phục.")
    return _restore_tracks(session, project_id, set(data.trash_ids),
                           set(data.orphan_files))


@router.post("/{project_id}/tracks/recovery/restore-all")
def restore_all_tracks(project_id: int, session: Session = Depends(get_session)):
    return _restore_tracks(session, project_id, None, None)


def _restore_tracks(session: Session, project_id: int,
                    trash_ids: set[int] | None, orphan_names: set[str] | None):
    """None = khôi phục hết; tập rỗng = không khôi phục nhóm đó."""
    project = get_project_or_404(session, project_id)
    restored = 0
    missing = 0
    trash = session.exec(select(DeletedTrack).where(
        DeletedTrack.project_id == project_id).order_by(DeletedTrack.deleted_at)).all()
    if trash_ids is not None:
        trash = [r for r in trash if r.id in trash_ids]
    for row in trash:
        if _restore_snapshot(session, row):
            restored += 1
        else:
            missing += 1

    # Tương thích các lần xoá cũ: dựng lại Track + CoverSong từ WAV còn trong
    # ban_cover. Không lấy thư mục mastered để tránh nhân đôi mỗi bản.
    from backend.routers.sources import attach_cover_file
    discovered = 0
    for path in _orphan_cover_files(session, project):
        if orphan_names is not None and path.name not in orphan_names:
            continue
        src = _source_for_cover(session, project_id, path)
        try:
            attach_cover_file(session, project, src, path)
            session.flush()
            discovered += 1
        except Exception:
            session.rollback()
            raise
    session.commit()
    if restored or discovered:
        from backend.cover import cover_qc
        cover_qc.review_async(project_id)
    return {"restored": restored, "discovered": discovered,
            "missing_files": missing, "total": restored + discovered}
