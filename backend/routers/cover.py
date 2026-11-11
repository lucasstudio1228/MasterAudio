"""
routers/cover.py — API riêng của tool Cover: cài đặt video cover, danh sách bài
(thứ tự, tên, ca sĩ, lời), AI căn lời, sửa mốc thời gian, nhập/xuất .lrc.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.cover import jobs
from backend.cover.align import quality_report
from backend.cover.genre_defaults import project_genre, resolve
from backend.cover.lyrics import clean_lyrics, dump_lines, load_lines, parse_lrc, to_lrc
from backend.database import get_session
from backend.models import CoverSettings, CoverSong, Project, Track
from backend.routers.projects import get_project_or_404

router = APIRouter(prefix="/api/cover", tags=["cover"])

LANGUAGES = {"vi", "en"}
STYLES = {"ballad", "remix", "acoustic", "original"}
WHISPER_MODELS = {"large-v3", "large-v3-turbo", "medium", "small"}


# ---------------- helpers ----------------
def get_settings(session: Session, project_id: int) -> CoverSettings:
    cfg = session.exec(select(CoverSettings).where(
        CoverSettings.project_id == project_id)).first()
    if cfg is None:
        cfg = CoverSettings(project_id=project_id)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)
    return cfg


def sync_songs(session: Session, project_id: int) -> list[tuple[CoverSong, Track]]:
    """Mỗi Track của project có đúng 1 CoverSong (tạo thiếu, xoá thừa), trả
    danh sách theo thứ tự phát."""
    tracks = {t.id: t for t in session.exec(
        select(Track).where(Track.project_id == project_id)).all()}
    songs = session.exec(select(CoverSong).where(
        CoverSong.project_id == project_id)).all()
    changed = False
    have = set()
    for s in songs:
        if s.track_id not in tracks:
            session.delete(s)
            changed = True
        else:
            have.add(s.track_id)
    top = max([s.position for s in songs if s.track_id in tracks] + [0])
    for tid in sorted(set(tracks) - have):
        top += 1
        session.add(CoverSong(project_id=project_id, track_id=tid, position=top,
                              title=Path(tracks[tid].filename).stem))
        changed = True
    if changed:
        session.commit()
    rows = session.exec(select(CoverSong).where(
        CoverSong.project_id == project_id)
        .order_by(CoverSong.position, CoverSong.id)).all()
    return [(s, tracks[s.track_id]) for s in rows if s.track_id in tracks]


def _song_out(song: CoverSong, track: Track) -> dict:
    lines = load_lines(song.lyrics_json)
    status = song.align_status
    if status in ("queued", "running") and not jobs.is_queued(song.id):
        status = "failed"   # server khởi động lại giữa chừng
    return {
        "id": song.id, "track_id": track.id, "position": song.position,
        "title": song.title, "artist": song.artist,
        "filename": track.filename, "duration_seconds": track.duration_seconds,
        "tempo_factor": float(song.tempo_factor or 1.0),
        "lyrics_text": song.lyrics_text,
        "lyric_line_count": len(clean_lyrics(song.lyrics_text)),
        "lines": lines, "align_status": status, "align_error": song.align_error,
        "aligned_at": song.aligned_at, "quality": quality_report(lines),
    }


def _get_song(session: Session, song_id: int) -> tuple[CoverSong, Track]:
    song = session.get(CoverSong, song_id)
    track = session.get(Track, song.track_id) if song else None
    if song is None or track is None:
        raise HTTPException(404, "Bài hát không tồn tại")
    return song, track


# ---------------- settings ----------------
class SettingsIn(BaseModel):
    language: Optional[str] = None
    cover_style: Optional[str] = None
    repeat_to_fill: Optional[bool] = None
    show_tracklist: Optional[bool] = None
    show_lyrics: Optional[bool] = None
    show_waveform: Optional[bool] = None
    thumb_subtitle: Optional[str] = None
    whisper_model: Optional[str] = None


def _settings_out(cfg: CoverSettings, project: Project) -> dict:
    """Giá trị đã lưu ("" = theo thể loại) + ``effective`` = giá trị thật sự dùng."""
    return {**cfg.model_dump(), "effective": resolve(cfg.model_dump(), project_genre(project))}


@router.get("/projects/{project_id}/settings")
def read_settings(project_id: int, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    return _settings_out(get_settings(session, project_id), project)


@router.put("/projects/{project_id}/settings")
def update_settings(project_id: int, data: SettingsIn,
                    session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    cfg = get_settings(session, project_id)
    patch = data.model_dump(exclude_none=True)
    # "" = «theo thể loại project» (tự suy, không cần chỉnh tay)
    if patch.get("language") and patch["language"] not in LANGUAGES:
        raise HTTPException(400, "Ngôn ngữ chỉ được 'vi' hoặc 'en'")
    if patch.get("cover_style") and patch["cover_style"] not in STYLES:
        raise HTTPException(400, f"Phong cách phải thuộc {sorted(STYLES)}")
    if "whisper_model" in patch and patch["whisper_model"] not in WHISPER_MODELS:
        raise HTTPException(400, f"Model phải thuộc {sorted(WHISPER_MODELS)}")
    for k, v in patch.items():
        setattr(cfg, k, v.strip() if isinstance(v, str) else v)
    cfg.updated_at = datetime.now(timezone.utc)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return _settings_out(cfg, project)


# ---------------- songs ----------------
@router.get("/projects/{project_id}/songs")
def list_songs(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    songs = [_song_out(s, t) for s, t in sync_songs(session, project_id)]
    return {"songs": songs, "align": jobs.status(project_id)}


class OrderIn(BaseModel):
    song_ids: list[int]


@router.put("/projects/{project_id}/order")
def reorder(project_id: int, data: OrderIn, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    rows = {s.id: s for s, _ in sync_songs(session, project_id)}
    order = [i for i in data.song_ids if i in rows]
    order += [i for i in rows if i not in order]
    for pos, sid in enumerate(order, start=1):
        rows[sid].position = pos
        session.add(rows[sid])
    session.commit()
    return {"ok": True}


class SongIn(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    lyrics_text: Optional[str] = None


@router.put("/songs/{song_id}")
def update_song(song_id: int, data: SongIn, session: Session = Depends(get_session)):
    song, track = _get_song(session, song_id)
    if data.title is not None:
        song.title = data.title.strip() or Path(track.filename).stem
    if data.artist is not None:
        song.artist = data.artist.strip()
    if data.lyrics_text is not None:
        raw = data.lyrics_text
        song.lyrics_text = raw.strip()
        # Dán nguyên file .lrc (đã có mốc thời gian) → dùng luôn, khỏi căn AI.
        from backend.cover.lyrics import looks_like_lrc
        if looks_like_lrc(raw):
            lines = parse_lrc(raw)
            if lines:
                song.lyrics_text = "\n".join(l["text"] for l in lines)
                song.lyrics_json = dump_lines(lines)
                song.align_status = "edited"
    session.add(song)
    session.commit()
    session.refresh(song)
    return _song_out(song, track)


class LinesIn(BaseModel):
    lines: list[dict]


@router.put("/songs/{song_id}/lines")
def save_lines(song_id: int, data: LinesIn, session: Session = Depends(get_session)):
    """Lưu bảng câu + mốc thời gian sau khi người dùng sửa tay."""
    song, track = _get_song(session, song_id)
    song.lyrics_json = dump_lines(data.lines)
    song.align_status = "edited"
    song.align_error = None
    session.add(song)
    session.commit()
    session.refresh(song)
    return _song_out(song, track)


@router.post("/songs/{song_id}/shift")
def shift_lines(song_id: int, seconds: float = Body(..., embed=True),
                session: Session = Depends(get_session)):
    """Dời toàn bộ lời sớm/muộn (vd. -0.3 s) — sửa nhanh khi lệch đều cả bài."""
    song, track = _get_song(session, song_id)
    lines = load_lines(song.lyrics_json)
    for l in lines:
        l["start"] = max(l["start"] + seconds, 0.0)
        l["end"] = max(l["end"] + seconds, l["start"] + 0.1)
    song.lyrics_json = dump_lines(lines)
    song.align_status = "edited"
    session.add(song)
    session.commit()
    session.refresh(song)
    return _song_out(song, track)


# ---------------- AI căn lời ----------------
@router.post("/songs/{song_id}/align")
def align_song(song_id: int, session: Session = Depends(get_session)):
    song, _ = _get_song(session, song_id)
    jobs.submit(song.project_id, [song.id])
    return jobs.status(song.project_id)


@router.post("/projects/{project_id}/align-all")
def align_all(project_id: int, only_missing: bool = Body(True, embed=True),
              session: Session = Depends(get_session)):
    """Căn tất cả bài (mặc định chỉ bài chưa căn / lỗi; bài đã sửa tay giữ nguyên)."""
    get_project_or_404(session, project_id)
    ids = [s.id for s, _ in sync_songs(session, project_id)
           if not only_missing or s.align_status in ("none", "failed")]
    added = jobs.submit(project_id, ids)
    return {"added": added, **jobs.status(project_id)}


@router.get("/projects/{project_id}/align-status")
def align_status(project_id: int):
    return jobs.status(project_id)


# ---------------- LRC + nghe thử ----------------
@router.get("/songs/{song_id}/lrc", response_class=PlainTextResponse)
def export_lrc(song_id: int, session: Session = Depends(get_session)):
    song, _ = _get_song(session, song_id)
    return to_lrc(load_lines(song.lyrics_json), song.title, song.artist)


@router.get("/projects/{project_id}/lyrics-srt")
def export_lyrics_srt(project_id: int, mix_id: Optional[int] = None,
                      session: Session = Depends(get_session)):
    """Lời bài hát theo câu (.srt) cho CapCut — chỉ có lời, không tên bài/chữ khác.
    Đồng thời lưu vào thư mục nhac_mix cạnh file nhạc mix."""
    from urllib.parse import quote
    from fastapi.responses import Response
    from backend.cover.lyrics_export import srt_for_project, write_srt
    from backend.cover.mix_export import MIX_AUDIO_DIR, _safe
    project = get_project_or_404(session, project_id)
    mix_wav = None
    if mix_id is not None:
        from backend.models import Mix
        mix = session.get(Mix, mix_id)
        if mix is None or mix.project_id != project_id or not mix.output_dir:
            raise HTTPException(404, "Không tìm thấy bản Mix")
        mix_wav = Path(mix.output_dir) / "mix.wav"
    content = srt_for_project(project_id, mix_wav)
    if not content.strip():
        raise HTTPException(404, "Chưa có lời đã căn thời gian hoặc chưa có bản Mix "
                                 "— hãy căn lời ở tab Lời bài hát và tạo Mix trước.")
    fname = f"{_safe(project.name or f'project {project_id}')}.srt"
    try:
        from backend.video import config as vconfig
        folder = vconfig.project_dir(project_id, project.name) / MIX_AUDIO_DIR
        folder.mkdir(parents=True, exist_ok=True)
        write_srt(folder / fname, content)
    except Exception:
        pass   # tải về vẫn được dù không ghi được thư mục
    body = ("\ufeff" + content).replace("\n", "\r\n").encode("utf-8")
    return Response(body, media_type="application/x-subrip", headers={
        "Content-Disposition": f"attachment; filename=\"lyrics.srt\"; "
                               f"filename*=UTF-8''{quote(fname)}"})


@router.get("/songs/{song_id}/audio")
def song_audio(song_id: int, session: Session = Depends(get_session)):
    """File gốc để nghe thử trong màn hình rà lời (trình duyệt tự tua)."""
    _, track = _get_song(session, song_id)
    p = Path(track.filepath)
    if not p.exists():
        raise HTTPException(404, "Không thấy file nhạc trên ổ đĩa")
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
             ".m4a": "audio/mp4", ".ogg": "audio/ogg"}.get(p.suffix.lower(),
                                                          "application/octet-stream")
    return FileResponse(str(p), media_type=media)
