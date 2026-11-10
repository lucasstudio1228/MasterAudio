"""
routers/sources.py — Tab ① Tải nhạc gốc: danh sách bài gốc của project (tải từ
nhaccuatui: file + link + lời), cửa sổ tự đăng nhập nhaccuatui, nhập file bản
cover (gắn sẵn tên/ca sĩ/lời của bài gốc), nút «Tạo lời bài hát».
Tab ② Suno Cover: tự nhập bài cover đã làm sẵn (file + lời), kiểm tra bản cover
bị lỗi (cover_qc → báo đỏ), «Tạo lại trên Suno» cho bản lỗi.
"""
from __future__ import annotations

import re
import json
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend import core_bridge
from backend.cover import cover_qc, cover_tempo, mastering
from backend.cover import jobs as align_jobs
from backend.cover import nct, nct_jobs, suno_jobs, tempo
from backend.database import get_session
from backend.models import CoverSong, DeletedTrack, Project, SourceSong, Track
from backend.routers.projects import get_project_or_404
from backend.video import config as vconfig

router = APIRouter(prefix="/api/sources", tags=["sources"])

MAX_LINES = 50
RETRYABLE = {"queued", "failed", "login", "not_found", "stopped", "lyrics_only", "running"}
MANUAL = "manual"                 # bài người dùng tự nhập (không tải từ nhaccuatui)
REJECT_STEM = "_loi"              # ban_cover/_loi/ — bản cover lỗi đã bỏ (không xoá)
EXCLUDED = "excluded"              # bỏ khỏi mix nhưng giữ file + snapshot để khôi phục
INSTRUMENT_STYLE_DIRTY = "instrument-changed"  # nhạc cụ đổi → viết lại Styles khi tạo lại


# ---------------- helpers ----------------
def _get_source(session: Session, source_id: int) -> SourceSong:
    src = session.get(SourceSong, source_id)
    if src is None:
        raise HTTPException(404, "Bài gốc không tồn tại")
    return src


def _covers_of(session: Session, project: Project) -> dict[int, list[dict]]:
    """Bản cover của từng bài gốc, kèm kết quả kiểm tra lỗi (cover_qc)."""
    rows = session.exec(select(CoverSong, Track).where(
        CoverSong.project_id == project.id, CoverSong.source_id.is_not(None),
        CoverSong.track_id == Track.id).order_by(CoverSong.position, CoverSong.id)).all()
    originals = {}
    for src in session.exec(select(SourceSong).where(SourceSong.project_id == project.id)).all():
        # Cover được tạo từ bản đã đổi tempo nên QC cũng phải so với đúng bản đó.
        candidate = src.tempo_file_path or src.file_path
        if candidate and Path(candidate).exists():
            originals[src.id] = candidate
    qc = cover_qc.evaluate(project.id, project.name,
                           [(t.id, t.filepath, song.source_id) for song, t in rows], originals)
    out: dict[int, list[dict]] = {}
    for song, track in rows:
        out.setdefault(song.source_id, []).append({
            "cover_song_id": song.id, "track_id": track.id, "filename": track.filename,
            "duration_seconds": track.duration_seconds, "qc": qc.get(track.id),
            "tempo_factor": float(song.tempo_factor or 1.0),
            "master": mastering.status_of(Path(track.filepath))})
    return out


def _source_out(src: SourceSong, covers: list[dict]) -> dict:
    status = src.status
    if status in ("queued", "running") and not nct_jobs.is_active(src.id):
        status = "stopped"      # server khởi động lại giữa chừng
    suno_status = src.suno_status or ""
    if suno_status in ("queued", "running") and not suno_jobs.is_active(src.id):
        suno_status = "stopped"
    has_file = bool(src.file_path) and Path(src.file_path).exists()
    official_source = not nct.is_variant_title(src.title or "")
    visible_error = src.error
    tempo_ready = bool(src.tempo_file_path) and Path(src.tempo_file_path).exists()
    source_url = (src.nct_url or "").lower()
    source_provider = ("ytmusic" if "music.youtube.com" in source_url
                       else "youtube" if "youtu.be" in source_url or "youtube.com" in source_url
                       else "zingmp3" if "zingmp3.vn" in source_url
                       else "nhaccuatui" if "nhaccuatui.com" in source_url else "")
    return {
        "id": src.id, "position": src.position, "query": src.query,
        "title": src.title, "artist": src.artist, "nct_url": src.nct_url,
        "source_provider": source_provider,
        "has_file": has_file, "file_name": Path(src.file_path).name if has_file else "",
        "tempo_factor": float(src.tempo_factor or 1.0),
        "tempo_ready": tempo_ready,
        "tempo_file_name": Path(src.tempo_file_path).name if tempo_ready else "",
        "quality": src.quality, "lyrics_text": src.lyrics_text,
        "lyrics_suno_ready": bool((src.lyrics_suno_text or "").strip()),
        "lyrics_source": src.lyrics_source or "",
        "lyric_line_count": len([ln for ln in src.lyrics_text.splitlines() if ln.strip()]),
        "official_source": official_source,
        "status": status, "error": visible_error, "suno_status": suno_status,
        "suno_error": src.suno_error, "vocal_gender": src.vocal_gender or "",
        "suno_editor_bpm": int(src.suno_editor_bpm or 0),
        "featured_instrument": src.featured_instrument or "",
        "suno_created": bool((src.suno_song_ids or "").strip()),
        "covers": covers,
    }


def clean_lyrics(text: str) -> str:
    """Lời dán/đọc từ file .txt/.lrc → mỗi câu 1 dòng: bỏ mốc thời gian [01:23.45],
    thẻ LRC ([ar:…], [ti:…]), khoảng trắng thừa, quá 1 dòng trống liền nhau."""
    from backend.cover.lyrics import strip_lyrics_header
    out: list[str] = []
    for raw in strip_lyrics_header(text).split("\n"):
        line = raw.strip()
        if re.fullmatch(r"\[[a-zA-Z]+:[^\]]*\]", line):
            continue
        line = re.sub(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]", "", line).strip()
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def parse_lines(text: str) -> list[str]:
    """Ô nhập nhiều dòng → mỗi dòng 1 bài (bỏ dòng trống, đánh số, trùng)."""
    out, seen = [], set()
    for raw in (text or "").splitlines():
        line = re.sub(r"^\s*(\d+[.)]|[-*•])\s+", "", raw).strip()
        if not line:
            continue
        key = line if nct.is_song_url(line) else nct.fold(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:300])
    return out


def _remove_source_files(session: Session, src: SourceSong) -> None:
    """Xoá file nhạc gốc + .txt lời — chỉ những file nằm trong thư mục nhac_goc
    của tool (kể cả .txt của bài chỉ lấy được lời, chưa tải được nhạc)."""
    files: list[Path] = []
    if src.file_path:
        p = Path(src.file_path)
        if p.parent.name == nct_jobs.SOURCE_STEM:
            files += [p, p.with_suffix(".txt")]
    if src.tempo_file_path:
        p = Path(src.tempo_file_path)
        project = session.get(Project, src.project_id)
        expected = tempo.tempo_dir(src.project_id, project.name if project else None)
        try:
            p.resolve().relative_to(expected.resolve())
            files.append(p)
        except (OSError, ValueError):
            pass
    if src.title:
        project = session.get(Project, src.project_id)
        folder = nct_jobs.source_dir(src.project_id, project.name if project else None)
        files.append(folder / f"{nct_jobs.info_stem(src.title, src.artist, src.query)}.txt")
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _unique(path: Path) -> Path:
    """«x.wav» đã có → «x 2.wav», «x 3.wav»…"""
    dest, i = path, 1
    while dest.exists():
        i += 1
        dest = path.with_name(f"{path.stem} {i}{path.suffix}")
    return dest


def _audio_ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext not in vconfig.AUDIO_EXTS:
        raise HTTPException(400, f"«{name}»: định dạng không hỗ trợ "
                                 f"({', '.join(vconfig.AUDIO_EXTS)}).")
    return ext


def _save_upload(up: UploadFile, dest: Path, saved: list[Path]) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        shutil.copyfileobj(up.file, out)
    up.file.close()
    saved.append(dest)
    return dest


def _save_covers(session: Session, project: Project, src: SourceSong,
                 files: list[UploadFile], saved: list[Path]) -> list[Track]:
    """Lưu file bản cover vào ban_cover/ «<tên bài> - cover[ N].ext» rồi đăng ký.
    File đã ghi được thêm vào `saved` để bên gọi dọn nếu lỗi."""
    dest_dir = nct_jobs.cover_dir(project.id, project.name)
    for up in files:                      # kiểm định dạng hết trước khi ghi file nào
        _audio_ext(Path(up.filename or "cover").name)
    paths = []
    for up in files:
        name = Path(up.filename or "cover").name
        stem = nct.safe_name(f"{src.title or Path(name).stem} - cover", 80)
        paths.append(_save_upload(up, _unique(dest_dir / f"{stem}{_audio_ext(name)}"), saved))
    tracks = [attach_cover_file(session, project, src, p) for p in paths]
    if src.suno_status in ("redo", "failed", "stopped", "rejected", "tested") \
            and not suno_jobs.is_active(src.id):
        src.suno_status, src.suno_error = "", None     # đã có bản mới (nhập tay)
        session.add(src)
    return tracks


def attach_cover_file(session: Session, project: Project, src: Optional[SourceSong],
                      path: Path) -> Track:
    """Đăng ký 1 file bản cover thành Track + CoverSong mang sẵn tên/ca sĩ/lời của
    bài gốc. Dùng chung cho «Nhập file cover» và (đợt B) Suno tự tải về."""
    from backend.routers.tracks import _persist
    core_track = core_bridge.probe_file(str(path))
    if core_track is None:
        raise HTTPException(400, f"Không đọc được file audio: {path.name}")
    track = _persist(session, project.id, core_track)
    session.flush()
    top = max([s.position for s in session.exec(select(CoverSong).where(
        CoverSong.project_id == project.id)).all()] + [0])
    session.add(CoverSong(
        project_id=project.id, track_id=track.id, position=top + 1,
        title=(src.title if src and src.title else path.stem),
        artist=src.artist if src else "",
        lyrics_text=src.lyrics_text if src else "",
        source_id=src.id if src else None))
    return track


# ---------------- danh sách ----------------
@router.get("/projects/{project_id}")
def list_sources(project_id: int, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    covers = _covers_of(session, project)
    rows = session.exec(select(SourceSong).where(SourceSong.project_id == project_id)
                        .order_by(SourceSong.position, SourceSong.id)).all()
    return {"items": [_source_out(s, covers.get(s.id, [])) for s in rows],
            "worker": nct_jobs.status(),
            "suno_worker": suno_jobs.status(),
            "qc_running": cover_qc.is_running(project_id),
            "master_running": mastering.is_busy(),
            "folder": str(nct_jobs.source_dir(project_id, project.name))}


class AddIn(BaseModel):
    text: str
    start: bool = True


@router.post("/projects/{project_id}")
def add_sources(project_id: int, data: AddIn, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    lines = parse_lines(data.text)
    if not lines:
        raise HTTPException(400, "Nhập ít nhất 1 dòng «Tên bài – Ca sĩ» hoặc link bài "
                                 "nhaccuatui.")
    if len(lines) > MAX_LINES:
        raise HTTPException(400, f"Mỗi lần thêm tối đa {MAX_LINES} bài.")
    top = max([s.position for s in session.exec(select(SourceSong).where(
        SourceSong.project_id == project_id)).all()] + [0])
    created = []
    from backend.cover.style_prompt import NO_CREATIVE_INSTRUMENT
    instrument = "" if getattr(project, "creative_instrument", True) \
        else NO_CREATIVE_INSTRUMENT
    for i, line in enumerate(lines, 1):
        url = line if nct.is_song_url(line) else ""
        src = SourceSong(project_id=project_id, position=top + i, query=line,
                         nct_url=url.split("?")[0], status="queued" if data.start else "stopped",
                         featured_instrument=instrument)
        session.add(src)
        created.append(src)
    session.commit()
    ids = [s.id for s in created]
    if data.start:
        nct_jobs.submit(ids)
    return {"added": len(ids), "ids": ids}


class SourceIn(BaseModel):
    query: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    lyrics_text: Optional[str] = None
    vocal_gender: Optional[str] = None     # "" (tool tự đoán) | male | female | duet
    # "" = AI tự chọn (mặc định); sentinel nội bộ = tắt; còn lại = tên chỉ định.
    featured_instrument: Optional[str] = None
    # Bài đã bị Suno từ chối lyrics: chỉ nhận lyrics thay thế khi người dùng xác
    # nhận đó là nội dung tự sáng tác hoặc có quyền sử dụng (AGENTS.md).
    authorized_lyrics: Optional[bool] = None


REJECTED_LYRICS_SAME = ("Lyrics mới giống hệt lyrics Suno vừa từ chối — Create lại sẽ "
                        "bị chặn tiếp và mất credit. Hãy dán lyrics khác.")
REJECTED_NEEDS_NEW_LYRICS = (
    "Lần trước Suno báo lyrics bài này có bản quyền. Chỉ làm lại khi lyrics đã được "
    "thay bằng nội dung bạn tự sáng tác hoặc được phép sử dụng — hãy xác nhận điều "
    "đó khi bấm «Làm lại trên Suno».")


def lyrics_key(text: str) -> str:
    """So sánh lyrics bỏ qua khác biệt hình thức: Unicode, hoa/thường, dấu câu,
    khoảng trắng, dòng [Verse]/[Chorus]. Dùng để chặn Create lại đúng lời cũ."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFC", text or "").casefold()
    t = re.sub(r"\[[^\]\n]*\]", " ", t)
    return " ".join(re.findall(r"\w+", t))


@router.put("/{source_id}")
def update_source(source_id: int, data: SourceIn, session: Session = Depends(get_session)):
    """Sửa tên/ca sĩ/lời/giọng. Đổi ô tìm kiếm (hoặc dán link khác) = tìm lại từ đầu."""
    src = _get_source(session, source_id)
    if nct_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang được tải — đợi xong hoặc bấm Dừng.")
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang làm trên Suno — đợi xong hoặc bấm Dừng.")
    had_lyrics = bool((src.lyrics_text or "").strip())
    project_id = src.project_id
    patch = data.model_dump(exclude_none=True)
    authorized = bool(patch.pop("authorized_lyrics", False))
    rejected_relyric = (src.suno_status == "rejected" and "lyrics_text" in patch
                        and lyrics_key(patch["lyrics_text"]) != lyrics_key(src.lyrics_text))
    if src.suno_status == "rejected" and "lyrics_text" in patch:
        new_key = lyrics_key(patch["lyrics_text"])
        if not new_key:
            raise HTTPException(400, "Lyrics mới đang trống.")
        if new_key in (lyrics_key(src.lyrics_text), lyrics_key(src.lyrics_suno_text)):
            raise HTTPException(400, REJECTED_LYRICS_SAME)
        if not authorized:
            raise HTTPException(
                400, "Chỉ nhận lyrics thay thế khi bạn xác nhận đó là nội dung tự sáng "
                     "tác hoặc có quyền sử dụng.")
    if patch.get("vocal_gender", "") not in ("", "male", "female", "duet"):
        raise HTTPException(400, "Giọng hát chỉ nhận: tự đoán, nam, nữ hoặc song ca.")
    if "featured_instrument" in patch:
        raw_instrument = patch["featured_instrument"] or ""
        instrument = " ".join(raw_instrument.split())
        if len(instrument) > 80 or any(x in raw_instrument for x in ("\r", "\n")):
            raise HTTPException(400, "Tên nhạc cụ tối đa 80 ký tự và chỉ nằm trên 1 dòng.")
        patch["featured_instrument"] = instrument
        if instrument != (src.featured_instrument or ""):
            # Ghi dấu riêng để nút «Tạo lại · Tempo» biết người dùng còn đổi
            # nhạc cụ. Không xóa prompt cũ: nó vẫn là bản đối chiếu và mọi dữ
            # liệu khác vẫn giữ nguyên; worker chỉ viết lại Styles để áp dụng
            # đúng lựa chọn nhạc cụ mới.
            src.suno_style_signature = INSTRUMENT_STYLE_DIRTY
            src.suno_preserve_style = False
    research = False
    if "query" in patch and src.status == MANUAL:
        raise HTTPException(400, "Bài tự nhập không tìm lại trên nhaccuatui.")
    if "query" in patch:
        q = patch.pop("query").strip()
        if not q:
            raise HTTPException(400, "Ô tìm kiếm không được trống.")
        if q != src.query:
            _remove_source_files(session, src)
            src.query = q
            src.nct_url = q.split("?")[0] if nct.is_song_url(q) else ""
            src.title = src.artist = src.lyrics_text = src.file_path = src.quality = ""
            src.lyrics_suno_text = ""
            src.tempo_factor, src.tempo_file_path = 1.0, ""
            src.suno_song_ids, src.suno_editor_song_id = "", ""
            src.suno_status, src.suno_error = "", None
            src.lyrics_source = ""
            research = True
    if "lyrics_text" in patch:
        src.lyrics_source = ""       # người dùng tự sửa/dán → lời của họ
        src.lyrics_suno_text = ""    # bước 6 phải chạy lại theo lời mới
    if rejected_relyric:
        # Đã có lyrics thay thế hợp lệ → bài hết bị khoá, «Làm lại» được phép.
        src.suno_status, src.suno_error = "", None
        src.suno_song_ids = src.suno_editor_song_id = ""
    for k, v in patch.items():
        setattr(src, k, v.strip() if k != "lyrics_text" else v.replace("\r", ""))
    session.add(src)
    session.commit()
    if "lyrics_text" in patch and not had_lyrics and (src.lyrics_text or "").strip():
        # Mix Set đang chờ lời: mỗi lần bổ sung thành công sẽ reset đồng hồ 5 phút.
        from backend import batch_service
        batch_service.note_lyrics_activity(project_id)
    if research:
        nct_jobs.submit([src.id])
    covers = _covers_of(session, session.get(Project, src.project_id))
    return _source_out(src, covers.get(src.id, []))


@router.post("/{source_id}/retry")
def retry_source(source_id: int, session: Session = Depends(get_session)):
    src = _get_source(session, source_id)
    if src.status == MANUAL:
        raise HTTPException(400, "Bài tự nhập không tải từ nhaccuatui.")
    nct_jobs.submit([src.id])
    return {"ok": True}


@router.post("/{source_id}/verify-lyrics")
def verify_source_lyrics(source_id: int, session: Session = Depends(get_session)):
    """Tìm lại và đối chiếu >=2 nguồn. Không đạt thì xoá lyrics tự động cũ để
    workflow dừng, chờ người dùng dán bản đã kiểm tra."""
    src = _get_source(session, source_id)
    if src.status == MANUAL:
        raise HTTPException(400, "Bài tự nhập: hãy dán lyrics đã kiểm tra thủ công.")
    if nct_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang được xử lý — hãy đợi xong.")
    nct_jobs.submit([src.id], verify_lyrics=True)
    return {"ok": True, "message": "Đang đối chiếu lyrics từ nhiều nguồn."}


@router.post("/projects/{project_id}/start")
def start_all(project_id: int, session: Session = Depends(get_session)):
    """Tải mọi bài chưa có file (hoặc chưa lấy được lời)."""
    get_project_or_404(session, project_id)
    rows = session.exec(select(SourceSong).where(SourceSong.project_id == project_id)
                        .order_by(SourceSong.position, SourceSong.id)).all()
    ids = [s.id for s in rows if s.status != MANUAL and (
        s.status in RETRYABLE or not (s.file_path and Path(s.file_path).exists()))]
    return {"queued": nct_jobs.submit(ids)}


@router.post("/stop")
def stop_all():
    nct_jobs.stop()
    return {"ok": True}


@router.delete("/{source_id}")
def delete_source(source_id: int, delete_file: bool = True,
                  session: Session = Depends(get_session)):
    src = _get_source(session, source_id)
    if nct_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang được tải — bấm Dừng trước khi xoá.")
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang làm trên Suno — bấm Dừng trước khi xoá.")
    if delete_file:
        _remove_source_files(session, src)
    for song in session.exec(select(CoverSong).where(CoverSong.source_id == src.id)).all():
        song.source_id = None        # giữ bản cover, chỉ bỏ liên kết
        session.add(song)
    session.delete(src)
    session.commit()
    return {"ok": True}


@router.post("/{source_id}/exclude")
def exclude_source(source_id: int, session: Session = Depends(get_session)):
    """Loại cả bài khỏi mix: đưa mọi Track/CoverSong vào thùng rác, giữ file gốc."""
    from backend.routers import tracks as track_router
    src = _get_source(session, source_id)
    if nct_jobs.is_active(src.id) or suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài đang được xử lý — bấm Dừng rồi mới loại bỏ.")
    songs = session.exec(select(CoverSong).where(CoverSong.source_id == src.id)).all()
    removed = 0
    for song in songs:
        track = session.get(Track, song.track_id)
        if track:
            track_router._trash_track(session, track)
            removed += 1
    src.status = EXCLUDED
    src.suno_status = "stopped"
    src.error = "Đã loại khỏi Mix Set theo lựa chọn của người dùng."
    session.add(src)
    session.commit()
    return {"ok": True, "removed_covers": removed, "recoverable": True}


@router.post("/{source_id}/include")
def include_source(source_id: int, session: Session = Depends(get_session)):
    """Khôi phục bài đã loại cùng các bản cover còn trong thùng rác track."""
    from backend.routers import tracks as track_router
    src = _get_source(session, source_id)
    if src.status != EXCLUDED:
        raise HTTPException(409, "Bài này không ở trạng thái đã loại bỏ.")
    restored = 0
    rows = session.exec(select(DeletedTrack).where(
        DeletedTrack.project_id == src.project_id).order_by(DeletedTrack.deleted_at)).all()
    for row in rows:
        try:
            data = json.loads(row.cover_song_json or "{}")
        except json.JSONDecodeError:
            data = {}
        if data.get("source_id") != src.id:
            continue
        if track_router._restore_snapshot(session, row):
            restored += 1
    src.status = "done" if src.file_path and Path(src.file_path).is_file() else "manual"
    src.suno_status = "done" if restored else ""
    src.error = None
    session.add(src)
    session.commit()
    return {"ok": True, "restored_covers": restored}


@router.get("/{source_id}/audio")
def source_audio(source_id: int, session: Session = Depends(get_session)):
    src = _get_source(session, source_id)
    if not src.file_path or not Path(src.file_path).exists():
        raise HTTPException(404, "Chưa có file nhạc gốc")
    return FileResponse(src.file_path)


class TempoIn(BaseModel):
    tempo_factor: float


@router.post("/{source_id}/tempo")
def export_tempo(source_id: int, data: TempoIn,
                 session: Session = Depends(get_session)):
    """Giữ cao độ, đổi tốc độ 0.2×–5× và xuất WAV trong folder project."""
    src = _get_source(session, source_id)
    if nct_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang được tải — đợi xong rồi chỉnh tempo.")
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang làm trên Suno — hãy dừng trước khi đổi tempo.")
    if not src.file_path or not Path(src.file_path).is_file():
        raise HTTPException(400, "Chưa có file nhạc gốc để chỉnh tempo.")
    if src.status == "skipped_no_lyrics" or not (src.lyrics_text or "").strip():
        raise HTTPException(
            400, "Bài không có lyrics đã bị loại; không được xuất tempo hay đưa sang Suno Cover.")
    project = get_project_or_404(session, src.project_id)
    try:
        factor = tempo.normalize_factor(data.tempo_factor)
        old = Path(src.tempo_file_path) if src.tempo_file_path else None
        exported = tempo.export_file(Path(src.file_path), src.id, src.project_id, project.name,
                                     src.title, src.artist, src.query, factor)
    except tempo.TempoError as exc:
        raise HTTPException(400, str(exc)) from exc

    src.tempo_factor = factor
    src.tempo_file_path = str(exported)
    # Tempo mới phải tạo Suno mới; không được tải lại song ID của tempo cũ.
    src.suno_song_ids = ""
    src.suno_editor_song_id = ""
    has_covers = session.exec(select(CoverSong).where(CoverSong.source_id == src.id)).first()
    src.suno_status = "redo" if has_covers else ""
    src.suno_error = None
    session.add(src)
    session.commit()
    if old and old != exported:
        try:
            old.resolve().relative_to(tempo.tempo_dir(src.project_id, project.name).resolve())
            old.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    if has_covers:
        cover_qc.review_async(project.id)
    covers = _covers_of(session, project)
    return _source_out(src, covers.get(src.id, []))


@router.get("/{source_id}/tempo/audio")
def tempo_audio(source_id: int, session: Session = Depends(get_session)):
    src = _get_source(session, source_id)
    path = Path(src.tempo_file_path) if src.tempo_file_path else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Chưa xuất file đã chỉnh tempo.")
    return FileResponse(str(path), media_type="audio/wav", filename=path.name,
                        content_disposition_type="inline")


# ---------------- nhập bản cover ----------------
@router.post("/{source_id}/covers")
def import_covers(source_id: int, files: list[UploadFile] = File(...),
                  session: Session = Depends(get_session)):
    """«Nhập file cover»: 1 hoặc nhiều file (Suno cho 2 bản) → thêm vào danh sách bài
    của project, mang sẵn tên bài/ca sĩ/lời của bài gốc."""
    src = _get_source(session, source_id)
    project = get_project_or_404(session, src.project_id)
    saved: list[Path] = []
    try:
        tracks = _save_covers(session, project, src, files, saved)
        session.commit()
    except BaseException:
        session.rollback()
        for p in saved:
            p.unlink(missing_ok=True)
        raise
    cover_qc.review_async(project.id)
    return {"added": [{"track_id": t.id, "filename": t.filename} for t in tracks]}


@router.post("/projects/{project_id}/own")
def add_own_cover(project_id: int, title: str = Form(...), artist: str = Form(""),
                  lyrics: str = Form(""), files: list[UploadFile] = File(...),
                  original: Optional[UploadFile] = File(None),
                  session: Session = Depends(get_session)):
    """«Nhập bài cover tôi đã làm»: file bản cover (1 hoặc nhiều) + tên bài, ca sĩ,
    lời — kèm file bài gốc nếu có (để so khi kiểm tra lỗi / tạo lại trên Suno).
    Tạo 1 bài gốc «tự nhập» (không tải từ nhaccuatui) mang các bản cover đó."""
    project = get_project_or_404(session, project_id)
    title, artist = title.strip(), artist.strip()
    if not title:
        raise HTTPException(400, "Nhập tên bài.")
    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(400, "Chọn ít nhất 1 file bản cover.")
    has_original = original is not None and bool(original.filename)
    if has_original:
        ext_orig = _audio_ext(Path(original.filename).name)
    top = max([s.position for s in session.exec(select(SourceSong).where(
        SourceSong.project_id == project_id)).all()] + [0])
    query = " - ".join(x for x in (title, artist) if x)
    src = SourceSong(project_id=project_id, position=top + 1, query=query[:300],
                     title=title, artist=artist, lyrics_text=clean_lyrics(lyrics),
                     status=MANUAL)
    session.add(src)
    saved: list[Path] = []
    try:
        session.flush()
        if has_original:
            folder = nct_jobs.source_dir(project.id, project.name)
            stem = nct_jobs.info_stem(title, artist, query)
            src.file_path = str(_save_upload(original, _unique(folder / f"{stem}{ext_orig}"),
                                             saved))
        tracks = _save_covers(session, project, src, files, saved)
        session.commit()
    except BaseException:
        session.rollback()
        for p in saved:
            p.unlink(missing_ok=True)
        raise
    cover_qc.review_async(project.id)
    return {"id": src.id,
            "added": [{"track_id": t.id, "filename": t.filename} for t in tracks]}


# ---------------- kiểm tra bản cover lỗi + tạo lại ----------------
def _cover_track(session: Session, track_id: int) -> tuple[Track, Project]:
    track = session.get(Track, track_id)
    if track is None:
        raise HTTPException(404, "Bản cover không tồn tại")
    return track, get_project_or_404(session, track.project_id)


@router.post("/projects/{project_id}/qc")
def run_qc(project_id: int, session: Session = Depends(get_session)):
    """🔍 Kiểm tra lại mọi bản cover (chạy nền, vài giây mỗi bài)."""
    get_project_or_404(session, project_id)
    cover_qc.review_async(project_id)
    return {"running": True}


class MarkIn(BaseModel):
    flagged: Optional[bool] = None       # 🚩 Báo lỗi (máy không nghe ra)
    dismissed: Optional[bool] = None     # ✓ Nghe ổn (máy báo nhầm)


@router.post("/covers/{track_id}/mark")
def mark_cover(track_id: int, data: MarkIn, session: Session = Depends(get_session)):
    track, project = _cover_track(session, track_id)
    cover_qc.set_mark(project.id, project.name, track.id,
                      flagged=data.flagged, dismissed=data.dismissed)
    return {"ok": True}


@router.get("/covers/{track_id}/audio")
def cover_audio(track_id: int, session: Session = Depends(get_session)):
    track, _ = _cover_track(session, track_id)
    if not Path(track.filepath).exists():
        raise HTTPException(404, "Không thấy file bản cover")
    return FileResponse(track.filepath)


# ---------------- master (ffmpeg) ----------------
class MasterIn(BaseModel):
    force: bool = False                  # True = master lại dù đã có bản _master


def _manual_cfg() -> dict:
    """Bấm tay thì luôn chạy, kể cả khi đã tắt «tự master sau khi tải Suno»."""
    cfg = mastering.load_config()
    cfg["enabled"] = True
    return cfg


def _require_ffmpeg(cfg: dict) -> None:
    try:
        mastering.resolve_ffmpeg(cfg)
    except mastering.MasterError as e:
        raise HTTPException(400, str(e))


def _master_log(project: Project):
    # Log chi tiết từng bài đã ghi vào ban_cover/mastered/_master.log; đây chỉ in ra console.
    return lambda m: print(f"[master #{project.id}] {m}")


@router.post("/covers/{track_id}/master")
def master_cover(track_id: int, data: MasterIn, session: Session = Depends(get_session)):
    """🎚 Master (hoặc ↻ Master lại) 1 bản cover — chạy nền."""
    track, project = _cover_track(session, track_id)
    src = Path(track.filepath)
    if not src.is_file():
        raise HTTPException(404, "Không thấy file bản cover")
    cfg = _manual_cfg()
    _require_ffmpeg(cfg)
    mastering.submit([src], log=_master_log(project), force=data.force, cfg=cfg)
    return {"queued": 1}


@router.post("/projects/{project_id}/master")
def master_project(project_id: int, data: MasterIn, session: Session = Depends(get_session)):
    """🎚 Master mọi bản cover của project chưa có bản master (force = làm lại hết)."""
    project = get_project_or_404(session, project_id)
    cfg = _manual_cfg()
    _require_ffmpeg(cfg)
    rows = session.exec(select(Track).join(CoverSong, CoverSong.track_id == Track.id)
                        .where(CoverSong.project_id == project_id)).all()
    files = []
    for t in rows:
        src = Path(t.filepath)
        if src.is_file() and (data.force or mastering.find_master(src) is None) \
                and src not in files:
            files.append(src)
    if files:
        mastering.submit(files, log=_master_log(project), force=data.force, cfg=cfg)
    return {"queued": len(files)}


class CoverTempoIn(BaseModel):
    factor: float                        # 1.0 = tốc độ bản Suno gốc


@router.post("/covers/{track_id}/tempo")
def set_cover_tempo(track_id: int, data: CoverTempoIn, session: Session = Depends(get_session)):
    """🎚 OK ở thanh tempo của 1 bản cover: xuất bản đổi tempo (giữ cao độ) đè lên
    đúng file của bản cover → Lời bài hát, Mix, Video dùng luôn tempo mới.
    Bản Suno gốc được cất (không xoá); lời đã căn được giãn theo tỉ lệ; bản master
    cũ bị bỏ và master lại nếu trước đó đã có."""
    track, project = _cover_track(session, track_id)
    song = session.exec(select(CoverSong).where(CoverSong.track_id == track.id)).first()
    if song is None:
        raise HTTPException(404, "Bản cover này chưa có trong danh sách bài")
    try:
        factor = cover_tempo.normalize_factor(data.factor)
    except cover_tempo.CoverTempoError as e:
        raise HTTPException(400, str(e))
    old = cover_tempo.normalize_factor(song.tempo_factor or 1.0, strict=False)
    src = Path(track.filepath)
    if not src.is_file():
        raise HTTPException(404, "Không thấy file bản cover")
    if abs(factor - old) < 1e-9:
        return {"changed": False, "factor": factor,
                "duration_seconds": track.duration_seconds}
    if not cover_tempo.is_tool_file(src, nct_jobs.cover_dir(project.id, project.name)):
        raise HTTPException(400, "Chỉ chỉnh tempo được bản cover nằm trong thư mục "
                                 "ban_cover của tool.")
    if song.source_id is not None and suno_jobs.is_active(song.source_id):
        raise HTTPException(409, "Bài này đang làm trên Suno — đợi xong rồi chỉnh tempo.")
    if align_jobs.is_queued(song.id):
        raise HTTPException(409, "Bài này đang được AI căn lời — đợi xong rồi chỉnh tempo.")
    if mastering.status_of(src)["state"] in ("queued", "running"):
        raise HTTPException(409, "Bản này đang master — đợi xong rồi chỉnh tempo.")
    cfg = _manual_cfg()
    _require_ffmpeg(cfg)
    had_master = mastering.find_master(src) is not None
    try:
        backup = cover_tempo.apply(src, old, factor, track.subtype, cfg)
    except cover_tempo.CoverTempoError as e:
        raise HTTPException(409, str(e))
    cover_tempo.drop_master(src)
    track.duration_seconds = cover_tempo.duration_of(
        src, fallback=track.duration_seconds * old / factor)
    scaled = cover_tempo.scale_lyrics(song.lyrics_json, old / factor)
    if scaled is not None:
        song.lyrics_json = scaled
    song.tempo_factor = factor
    song.tempo_backup_path = str(backup)
    session.add(track)
    session.add(song)
    session.commit()
    if had_master:
        mastering.submit([src], log=_master_log(project), force=True, cfg=cfg)
    cover_qc.review_async(project.id)
    return {"changed": True, "factor": factor, "duration_seconds": track.duration_seconds,
            "remastering": had_master, "lyrics_rescaled": scaled is not None}


@router.get("/covers/{track_id}/master/audio")
def master_audio(track_id: int, session: Session = Depends(get_session)):
    track, _ = _cover_track(session, track_id)
    master = mastering.find_master(Path(track.filepath))
    if master is None:
        raise HTTPException(404, "Bản cover này chưa có bản master")
    return FileResponse(master)


def _master_settings() -> dict:
    cfg = mastering.load_config()
    if not mastering.ffmpeg_status().get("checked"):
        mastering.check_ffmpeg(cfg, log=lambda m: None)
    presets = [{"name": name, "lufs": v.get("lufs"), "note": v.get("note", "")}
               for name, v in (cfg.get("presets") or {}).items()]
    ff = mastering.ffmpeg_status()
    return {"enabled": bool(cfg.get("enabled", True)), "preset": cfg.get("preset"),
            "output_format": cfg.get("output_format"), "presets": presets,
            "ffmpeg": {"ok": bool(ff.get("ok")), "version": ff.get("version", ""),
                       "error": ff.get("error", "")},
            "running": mastering.is_busy(), "config_file": str(mastering.CONFIG_FILE)}


class MasterSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    preset: Optional[str] = None
    output_format: Optional[str] = None


@router.get("/master/settings")
def get_master_settings():
    return _master_settings()


@router.put("/master/settings")
def put_master_settings(data: MasterSettingsIn):
    try:
        mastering.save_settings(data.model_dump(exclude_none=True))
    except mastering.MasterError as e:
        raise HTTPException(400, str(e))
    return _master_settings()


@router.post("/master/ffmpeg/check")
def recheck_ffmpeg():
    """Sau khi cài ffmpeg: kiểm tra lại mà không cần khởi động lại tool."""
    mastering.check_ffmpeg(mastering.load_config(), log=print)
    return _master_settings()


class RedoIn(BaseModel):
    track_ids: list[int]
    editor_bpm: Optional[int] = None
    tempo_only: bool = False


@router.post("/{source_id}/redo")
def redo_cover(source_id: int, data: RedoIn, session: Session = Depends(get_session)):
    """«🔁 Tạo lại trên Suno»: bỏ (các) bản cover lỗi khỏi project — file cất vào
    ban_cover/_loi/, KHÔNG xoá — rồi xếp bài vào hàng đợi tự làm cover trên Suno
    (thiếu file bài gốc / lời thì chỉ đánh dấu chờ). Nhập bản mới tay cũng xoá
    trạng thái chờ."""
    from backend.routers.tracks import _delete_track_stems
    src = _get_source(session, source_id)
    project = get_project_or_404(session, src.project_id)
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang làm trên Suno — đợi xong hoặc bấm Dừng.")
    ids = list(dict.fromkeys(data.track_ids))
    if not ids:
        raise HTTPException(400, "Chưa chọn bản cover nào.")
    files: list[Path] = []
    for tid in ids:
        track = session.get(Track, tid)
        song = session.exec(select(CoverSong).where(CoverSong.track_id == tid)).first()
        if track is None or track.project_id != project.id or song is None \
                or song.source_id != src.id:
            raise HTTPException(400, f"Bản cover #{tid} không thuộc bài này.")
        p = Path(track.filepath)
        if p.parent.name == nct_jobs.COVER_STEM:     # chỉ dời file nằm trong thư mục tool
            files.append(p)
        _delete_track_stems(session, track)
        session.delete(track)
    missing = _suno_missing(src)
    if data.editor_bpm is not None:
        if not 20 <= data.editor_bpm <= 300:
            raise HTTPException(400, "Tempo Suno Editor phải từ 20 đến 300 BPM.")
        src.suno_editor_bpm = data.editor_bpm
        if data.tempo_only:
            if not (src.suno_style_prompt or "").strip():
                raise HTTPException(
                    409, "Bài chưa có Styles cũ để giữ nguyên; hãy tạo bài lần đầu trước.")
            src.suno_preserve_style = (
                src.suno_style_signature != INSTRUMENT_STYLE_DIRTY)
    src.suno_status, src.suno_error = "redo", missing
    src.suno_song_ids = ""              # tạo bản MỚI, không tải lại bản cũ
    # Bắt buộc mở lại từ file tempo → Editor và đặt BPM mới; giữ ID này chính là
    # nguyên nhân lượt tạo lại trước đây bỏ qua bước chỉnh Tempo.
    src.suno_editor_song_id = ""
    session.add(src)
    session.commit()
    reject_dir = nct_jobs.cover_dir(project.id, project.name) / REJECT_STEM
    moved = 0
    for p in files:
        try:
            if p.exists():
                reject_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(_unique(reject_dir / p.name)))
                moved += 1
        except OSError:
            pass
    queued = not missing and suno_jobs.submit([src.id]) > 0
    return {"removed": len(ids), "moved": moved, "folder": str(reject_dir),
            "queued": queued, "missing": missing}


# ---------------- đợt B: tự làm cover trên Suno ----------------
def _suno_missing(src: SourceSong) -> Optional[str]:
    """Lý do bài chưa làm được trên Suno (None = đủ file bài gốc + lời)."""
    if src.status == "skipped_no_lyrics":
        return "Bài không tìm được lyrics đã bị loại khỏi quy trình Suno Cover."
    no_file = not (src.file_path and Path(src.file_path).exists())
    no_lyrics = not src.lyrics_text.strip()
    if no_file and no_lyrics:
        return ("Chưa có file bài gốc và chưa có lời — tải ở tab ① (hoặc nhập file bài gốc, "
                "dán lời) rồi bấm «↻ Làm lại trên Suno».")
    if no_file:
        return "Chưa có file bài gốc — tải ở tab ① (hoặc nhập file bài gốc) rồi bấm «↻ Làm lại trên Suno»."
    if no_lyrics:
        return "Bài chưa có lời — dán lời ở tab ① rồi bấm «↻ Làm lại trên Suno»."
    if not (src.tempo_file_path and Path(src.tempo_file_path).exists()):
        return ("Chưa xuất file đã chỉnh tempo — ở tab ① chọn tốc độ, bấm "
                "«Xuất file chỉnh tempo», rồi mới làm trên Suno.")
    return None


class SunoStartIn(BaseModel):
    dry_run: bool = False       # 🧪 Chạy thử: điền form rồi dừng, KHÔNG bấm Create
    fresh: bool = False         # tạo mới/toàn bộ từ đầu; giữ file cover cũ để so sánh


@router.post("/projects/{project_id}/suno/start")
def suno_start_all(project_id: int, data: SunoStartIn,
                   session: Session = Depends(get_session)):
    """Tạo mới toàn bộ (fresh) hoặc tiếp tục đúng phần đang dở (fresh=False)."""
    project = get_project_or_404(session, project_id)
    covers = _covers_of(session, project)
    rows = session.exec(select(SourceSong).where(SourceSong.project_id == project_id)
                        .order_by(SourceSong.position, SourceSong.id)).all()
    ids, skipped = [], 0
    for s in rows:
        # «Tiếp tục» tôn trọng bài đã loại. Riêng «Tạo mới / tạo lại từ đầu»
        # là ý định rõ ràng muốn làm bản thay thế: cho phép đưa nguồn trở lại,
        # nhưng KHÔNG khôi phục các cover cũ đã loại.
        if s.status == EXCLUDED and not data.fresh:
            skipped += 1
            continue
        if suno_jobs.is_active(s.id) or s.suno_status == "rejected":
            continue
        if not data.fresh and covers.get(s.id) and s.suno_status not in (
                "redo", "failed", "stopped", "queued", "running"):
            continue
        if _suno_missing(s):
            skipped += 1
            continue
        ids.append(s.id)
        if data.fresh:
            if s.status == EXCLUDED:
                s.status = "done" if s.file_path and Path(s.file_path).is_file() else MANUAL
            # Tạo lại từ đầu Editor + Cover; các WAV cũ vẫn giữ để nghe/so sánh.
            s.suno_song_ids = ""
            s.suno_editor_song_id = ""
            s.suno_status, s.suno_error = "", None
            session.add(s)
    if data.fresh:
        session.commit()
    if data.dry_run:
        ids = ids[:1]           # chạy thử 1 bài là đủ để xem form điền đúng chưa
    return {"queued": suno_jobs.submit(ids, dry_run=data.dry_run), "skipped": skipped}


class SunoOneIn(BaseModel):
    dry_run: bool = False
    fresh: bool = False         # bỏ id bài Suno đã tạo → Create bản MỚI (tốn credit)
    editor_bpm: Optional[int] = None  # tạo lại ở BPM riêng; 20..300
    tempo_only: bool = False          # giữ nguyên mọi dữ liệu, chỉ thay BPM
    # Link/ID bài người dùng tự làm trên Suno → chỉ TẢI về và nhập làm cover,
    # không Create, không đụng form Suno (không tốn credit).
    song_links: Optional[list[str]] = None
    # Bài Suno từng báo bản quyền: người dùng xác nhận lyrics hiện tại do họ tự
    # sáng tác/được phép sử dụng → mới cho Create lại.
    authorized_lyrics: bool = False


class SunoManualWatchIn(BaseModel):
    authorized_lyrics: bool = False


@router.post("/{source_id}/suno/copyright-skip")
def suno_copyright_skip(source_id: int):
    """Bảng cam bản quyền: người dùng không muốn tự Create lại → đánh dấu bài
    «Suno từ chối», giữ dữ liệu, worker sang bài tiếp theo."""
    if not suno_jobs.skip_lyrics(source_id):
        raise HTTPException(409, "Tool không còn chờ bài này.")
    return {"ok": True}


@router.post("/{source_id}/suno/manual-watch")
def suno_manual_watch(source_id: int, data: SunoManualWatchIn,
                      session: Session = Depends(get_session)):
    """Chờ và tự nhập 2 bản do người dùng Create thủ công trên Suno.

    Chỉ mở sau khi Suno từ chối lyrics và người dùng xác nhận đã thay bằng nội
    dung tự sáng tác/được phép; endpoint không bấm Create và không sửa lyrics.
    """
    src = _get_source(session, source_id)
    if not data.authorized_lyrics:
        raise HTTPException(
            400, "Chỉ tiếp tục khi lyrics thay thế là nội dung tự sáng tác hoặc bạn có quyền sử dụng.")
    if src.suno_status != "rejected":
        raise HTTPException(409, "Bài này không ở trạng thái Suno từ chối lyrics.")
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đã trong hàng đợi Suno.")
    queued = suno_jobs.submit_manual_watch(src.id)
    return {"queued": queued, "message": "Đang theo dõi 2 bản Suno tạo thủ công."}


@router.post("/{source_id}/suno")
def suno_one(source_id: int, data: SunoOneIn, session: Session = Depends(get_session)):
    """↻ Làm lại trên Suno cho 1 bài (kể cả bài Suno từ chối lần trước)."""
    src = _get_source(session, source_id)
    if suno_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đã trong hàng đợi Suno.")
    if nct_jobs.is_active(src.id):
        raise HTTPException(409, "Bài này đang được tải từ nhaccuatui — đợi xong.")
    if data.song_links is not None:
        from backend.cover.suno_wav_downloader import SunoWavDownloadError, resolve_song_id
        ids: list[str] = []
        for raw in data.song_links:
            if not (raw or "").strip():
                continue
            try:
                sid_ = resolve_song_id(raw)
            except (SunoWavDownloadError, OSError, ValueError) as exc:
                raise HTTPException(400, f"Không đọc được link/ID Suno «{raw[:80]}»: {exc}")
            if sid_ not in ids:
                ids.append(sid_)
        if not ids:
            raise HTTPException(400, "Hãy dán ít nhất 1 link hoặc ID bài Suno.")
        if len(ids) > 4:
            raise HTTPException(400, "Mỗi lần nhận tối đa 4 bài Suno.")
        src.suno_song_ids = ",".join(ids)
        src.suno_status, src.suno_error = "", None
        if src.status == EXCLUDED:
            src.status = "done" if src.file_path and Path(src.file_path).is_file() else MANUAL
        session.add(src)
        session.commit()
        return {"queued": suno_jobs.submit([src.id]), "song_ids": ids}
    if src.suno_status == "rejected":
        # LIVE 2026-11-09: #149 bị Suno từ chối vì lyrics. Nút «Làm lại» chỉ chạy
        # khi người dùng xác nhận lyrics đã là nội dung tự sáng tác/được phép.
        if not data.authorized_lyrics:
            raise HTTPException(409, REJECTED_NEEDS_NEW_LYRICS)
        src.suno_status, src.suno_error = "", None
        session.add(src)
        session.commit()
    missing = _suno_missing(src)
    if missing and not (src.suno_song_ids and not data.dry_run and not data.fresh):
        raise HTTPException(400, missing)
    if src.status == EXCLUDED:
        # Tạo/tải bản Suno mới là thao tác thay thế bài đã loại. Chỉ kích hoạt
        # lại nguồn; cover cũ trong thùng rác vẫn giữ nguyên, không tự khôi phục.
        src.status = "done" if src.file_path and Path(src.file_path).is_file() else MANUAL
        session.add(src)
        session.commit()
    if data.editor_bpm is not None:
        if not 20 <= data.editor_bpm <= 300:
            raise HTTPException(400, "Tempo Suno Editor phải từ 20 đến 300 BPM.")
        src.suno_editor_bpm = data.editor_bpm
        if data.tempo_only:
            if not (src.suno_style_prompt or "").strip():
                raise HTTPException(
                    409, "Bài chưa có Styles cũ để giữ nguyên; hãy tạo bài lần đầu trước.")
            src.suno_preserve_style = (
                src.suno_style_signature != INSTRUMENT_STYLE_DIRTY)
        # BPM mới bắt buộc tạo lại cả bài trung gian Editor và lượt Cover mới.
        src.suno_song_ids = ""
        src.suno_editor_song_id = ""
        data.fresh = True
    if data.fresh and src.suno_song_ids:
        src.suno_song_ids = ""
    if data.fresh:
        src.suno_editor_song_id = ""
        src.suno_status, src.suno_error = "", None
        session.add(src)
        session.commit()
    return {"queued": suno_jobs.submit([src.id], dry_run=data.dry_run)}


@router.post("/suno/stop")
def suno_stop():
    suno_jobs.stop()
    return {"ok": True}


@router.post("/suno/pause")
def suno_pause():
    if not suno_jobs.pause():
        raise HTTPException(409, "Không có hàng đợi Suno đang chạy để tạm dừng.")
    return suno_jobs.status()


@router.post("/suno/resume")
def suno_resume():
    queued = suno_jobs.resume()
    if not queued:
        raise HTTPException(409, "Không có hàng đợi Suno đã dừng/tạm dừng để tiếp tục.")
    return {**suno_jobs.status(), "queued": queued}


class SunoWorkflowIn(BaseModel):
    semi_manual: bool


@router.get("/suno/workflow")
def get_suno_workflow():
    return suno_jobs.get_workflow()


@router.put("/suno/workflow")
def put_suno_workflow(data: SunoWorkflowIn):
    """Bán thủ công: bạn tự chỉnh Tempo + Save as New Song và tự bấm Create; tool
    làm phần còn lại. Áp dụng từ bài Suno kế tiếp (bài đang chạy giữ chế độ cũ)."""
    return suno_jobs.set_workflow(data.semi_manual)


@router.get("/suno/login")
def suno_login_status():
    return suno_jobs.status()


@router.post("/suno/login/open")
def suno_open_login():
    """Mở suno.com bằng profile RIÊNG của tool để bạn TỰ đăng nhập."""
    try:
        suno_jobs.open_login_window()
    except suno_jobs.Busy as e:
        raise HTTPException(409, str(e)) from e
    return suno_jobs.status()


@router.post("/suno/login/close")
def suno_close_login():
    if not suno_jobs.close_login_window():
        raise HTTPException(409, "Cửa sổ Suno không mở.")
    return suno_jobs.status()


# ---------------- «Tạo lời bài hát» ----------------
@router.post("/projects/{project_id}/make-lyrics")
def make_lyrics(project_id: int, session: Session = Depends(get_session)):
    """Điền lời/tên/ca sĩ từ bài gốc cho các bản cover còn thiếu, rồi xếp AI căn
    lời cho mọi bài có lời mà chưa căn. Giao diện chuyển sang tab ③ Lời bài hát."""
    get_project_or_404(session, project_id)
    from backend.routers.cover import sync_songs
    pairs = sync_songs(session, project_id)
    filled = 0
    for song, _track in pairs:
        if song.source_id is None:
            continue
        src = session.get(SourceSong, song.source_id)
        if src is None:
            continue
        changed = False
        if not song.lyrics_text.strip() and src.lyrics_text.strip():
            song.lyrics_text = src.lyrics_text
            changed = True
        if not song.artist and src.artist:
            song.artist, changed = src.artist, True
        if changed:
            session.add(song)
            filled += 1
    session.commit()
    to_align = [s.id for s, _ in pairs
                if s.lyrics_text.strip() and s.align_status in ("none", "failed")
                and not align_jobs.is_queued(s.id)]
    queued = align_jobs.submit(project_id, to_align) if to_align else 0
    return {"songs": len(pairs), "filled": filled, "queued": queued}


# ---------------- cửa sổ tự đăng nhập nhaccuatui ----------------
@router.get("/login")
def login_window_status():
    return nct_jobs.status()


@router.post("/login/open")
def open_login_window():
    """Mở nhaccuatui.com bằng profile RIÊNG của tool để bạn TỰ đăng nhập
    (tool không nhập email/mật khẩu)."""
    try:
        nct_jobs.open_login_window()
    except nct_jobs.Busy as e:
        raise HTTPException(409, str(e)) from e
    return nct_jobs.status()


@router.post("/login/close")
def close_login_window():
    if not nct_jobs.close_login_window():
        raise HTTPException(409, "Cửa sổ nhaccuatui không mở.")
    return nct_jobs.status()
