"""
cover/nct_jobs.py — Hàng đợi tải nhạc gốc từ nhaccuatui (tab ① Tải nhạc gốc).

1 trình duyệt duy nhất (profile riêng .browser_profile_nct) dùng cho cả hàng đợi
tải VÀ cửa sổ «Mở nhaccuatui để đăng nhập» → không bao giờ 2 cửa sổ tranh 1
profile. Mở trình duyệt 1 lần cho cả loạt bài, xong loạt thì đóng.

Trạng thái từng bài lưu trong SourceSong.status; thông báo tiến trình giữ trong
RAM để giao diện hỏi định kỳ.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from backend.database import DATA_DIR, engine
from backend.models import Project, SourceSong
from backend.video import config as vconfig
from backend.video.browser_base import OperationCancelled, set_cancel_check, wait_ms

from . import audio_sources, lyrics_web, nct, song_match, tempo

SOURCE_STEM = "nhac_goc"          # media/<project>/nhac_goc/ — nhạc gốc + lời .txt
COVER_STEM = "ban_cover"          # media/<project>/ban_cover/ — bản cover nhập vào
LOGIN_FILE = DATA_DIR / "nct_login.json"
LOGIN_HOLD_SECONDS = 1800
AUTO_TEMPO_FACTOR = 4.5          # tải xong → xuất WAV này ngay, chưa tự chạy Suno

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nct")
_lock = threading.Lock()
_pending: deque[int] = deque()
_verify_lyrics: set[int] = set()
_stop = threading.Event()
# mode: None | "download" | "login";  draining/login_scheduled: đã xếp vào executor
_state: dict = {"mode": None, "current": None, "message": "", "draining": False,
                "login_scheduled": False}


class Busy(RuntimeError):
    pass


def source_dir(project_id: int, project_name: str | None) -> Path:
    return vconfig.project_dir(project_id, project_name) / SOURCE_STEM


def cover_dir(project_id: int, project_name: str | None) -> Path:
    return vconfig.project_dir(project_id, project_name) / COVER_STEM


def _ensure_tempo_export(source_id: int, log=None) -> Path | None:
    """Đảm bảo bài vừa tải có WAV 4.5× sẵn cho tab Suno Cover.

    Chỉ chuẩn bị đầu vào; tuyệt đối không tự bấm Create trên Suno. Hàm an toàn
    gọi lại khi resume và không encode lại nếu file đúng 4.5× còn tồn tại.
    """
    with Session(engine) as session:
        src = session.get(SourceSong, source_id)
        if (src is None or src.status in ("skipped_no_lyrics", "excluded")
                or not (src.lyrics_text or "").strip()
                or not src.file_path or not Path(src.file_path).is_file()):
            return None
        current = Path(src.tempo_file_path) if src.tempo_file_path else None
        if (abs(float(src.tempo_factor or 1.0) - AUTO_TEMPO_FACTOR) < 0.0005
                and current is not None and current.is_file()):
            return current
        project = session.get(Project, src.project_id)
        if log:
            log(f"đang tự động xuất WAV tempo {AUTO_TEMPO_FACTOR:g}×…")
        exported = tempo.export_file(
            Path(src.file_path), src.id, src.project_id,
            project.name if project else None, src.title, src.artist, src.query,
            AUTO_TEMPO_FACTOR)
        old = current
        src.tempo_factor = AUTO_TEMPO_FACTOR
        src.tempo_file_path = str(exported)
        # Derivative mới không được dùng lại ID Suno của derivative cũ.
        src.suno_song_ids = ""
        src.suno_editor_song_id = ""
        src.suno_status = ""
        src.suno_error = None
        session.add(src)
        session.commit()
        if old and old != exported:
            try:
                old.resolve().relative_to(
                    tempo.tempo_dir(src.project_id, project.name if project else None).resolve())
                old.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        if log:
            log(f"đã tự động xuất {exported.name}; sẵn sàng để kiểm tra ở Suno Cover.")
        return exported


def info_stem(title: str, artist: str, query: str) -> str:
    """Tên file (không đuôi) của nhạc gốc + file .txt lời: «Tên bài - Ca sĩ»."""
    return nct.safe_name(" - ".join(x for x in (title, artist) if x) or query)


# ── trạng thái đăng nhập (chỉ có/không — tool không lưu mật khẩu) ──
def login_status() -> dict:
    try:
        return json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"logged_in": None, "checked_at": None}


def record_login(state: str) -> None:
    if state not in ("logged_in", "logged_out"):
        return
    try:
        LOGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOGIN_FILE.write_text(json.dumps({
            "logged_in": state == "logged_in",
            "checked_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    except Exception:
        pass


def status() -> dict:
    with _lock:
        st = dict(_state)
        pending = list(_pending)
    return {"busy": bool(st["draining"] or st["login_scheduled"] or st["mode"]),
            "mode": st["mode"] or ("login" if st["login_scheduled"] else
                                   "download" if st["draining"] else None),
            "current": st["current"], "message": st["message"],
            "pending": pending, "login": login_status()}


def is_active(source_id: int) -> bool:
    with _lock:
        return source_id in _pending or _state["current"] == source_id


def _set_msg(msg: str) -> None:
    with _lock:
        _state["message"] = msg


# ── lệnh từ giao diện ────────────────────────────────────────────
def submit(source_ids: list[int], *, verify_lyrics: bool = False) -> int:
    """Xếp bài vào hàng đợi tải. Trả số bài mới được xếp."""
    added = 0
    with Session(engine) as s:
        for sid in source_ids:
            with _lock:
                if sid in _pending or _state["current"] == sid:
                    continue
                _pending.append(sid)
                if verify_lyrics:
                    _verify_lyrics.add(sid)
            src = s.get(SourceSong, sid)
            if src:
                src.status, src.error = "queued", None
                s.add(src)
            added += 1
        s.commit()
    with _lock:
        if _pending and not _state["draining"]:
            _state["draining"] = True
            if _state["mode"] == "login" or _state["login_scheduled"]:
                _state["message"] = ("Đang chờ bạn đóng cửa sổ đăng nhập nhaccuatui "
                                     "rồi mới bắt đầu tải…")
            _executor.submit(_drain)
    return added


def stop() -> None:
    """Dừng tải (bài đang chạy + bỏ các bài đang chờ) và đóng cửa sổ đăng nhập."""
    with _lock:
        dropped = list(_pending)
        _pending.clear()
        _verify_lyrics.difference_update(dropped)
        _stop.set()
    _mark(dropped, "stopped", "Đã dừng — bấm «Thử lại» để tải tiếp.")


def open_login_window() -> None:
    with _lock:
        if _state["mode"] or _state["draining"] or _state["login_scheduled"]:
            raise Busy("Trình duyệt nhaccuatui đang bận (đang tải hoặc cửa sổ đăng nhập "
                       "đã mở).")
        _state["login_scheduled"] = True
        _state["message"] = "Đang mở nhaccuatui…"
    _executor.submit(_login_window)


def close_login_window() -> bool:
    with _lock:
        if _state["mode"] != "login" and not _state["login_scheduled"]:
            return False
    _stop.set()
    return True


def _mark(ids: list[int], status_: str, error: str | None) -> None:
    if not ids:
        return
    with Session(engine) as s:
        for sid in ids:
            src = s.get(SourceSong, sid)
            if src:
                src.status, src.error = status_, error
                s.add(src)
        s.commit()


def quarantine_legacy_invalid_sources() -> dict[str, int]:
    """Khôi phục policy official→Remix fallback và loại dữ liệu thiếu lyrics."""
    restored_variants: list[int] = []
    no_lyrics: list[tuple[int, Path]] = []
    with Session(engine) as s:
        rows = s.exec(select(SourceSong)).all()
        for src in rows:
            has_file = bool(src.file_path and Path(src.file_path).is_file())
            if not has_file:
                continue
            is_variant = (nct.is_variant_title(src.title or "")
                          or nct.is_variant_title(Path(src.file_path).stem))
            if is_variant and src.status == "failed" and src.error and \
                    "File cũ là Remix/Cover/biến thể" in src.error:
                src.status = "done" if (src.lyrics_text or "").strip() else "skipped_no_lyrics"
                src.error = None if src.status == "done" else src.error
                s.add(src)
                restored_variants.append(src.id)
                if src.status == "skipped_no_lyrics":
                    project = s.get(Project, src.project_id)
                    no_lyrics.append((src.id, source_dir(
                        src.project_id, project.name if project else None)))
                continue
            if (src.status == "done" and not (src.lyrics_text or "").strip()):
                project = s.get(Project, src.project_id)
                no_lyrics.append((src.id, source_dir(
                    src.project_id, project.name if project else None)))
        # Trạng thái cũ «không có official» phải được mở lại theo chính sách mới.
        for src in rows:
            if src.status == "skipped_no_official":
                src.status = "stopped"
                src.error = ("Không có official — bấm Thử lại; tool sẽ dùng Remix/Cover "
                             "phù hợp nhất làm phương án dự phòng.")
                s.add(src)
                restored_variants.append(src.id)
        s.commit()
    for sid, dest in no_lyrics:
        _skip_without_lyrics(sid, dest)
    return {"variants": len(restored_variants), "no_lyrics": len(no_lyrics)}


# ── trình duyệt ──────────────────────────────────────────────────
def _open_browser():
    from backend.video.browser_base import BrowserSession
    bs = BrowserSession(headless=False, site="nct")
    bs.__enter__()
    return bs


def _close_browser(bs) -> None:
    if bs is None:
        return
    try:
        bs.__exit__(None, None, None)
    except Exception:
        pass


@contextmanager
def _browser():
    bs = _open_browser()
    try:
        yield bs
    finally:
        _close_browser(bs)


def _live_page(bs, page, work: bool = True):
    if page is not None:
        try:
            if not page.is_closed():
                return page
        except Exception:
            pass
    if not work:      # cửa sổ đăng nhập: dùng luôn tab đầu, khỏi thừa tab trắng
        pages = [p for p in bs.context.pages if not p.is_closed()]
        if pages:
            return pages[0]
    # Tải file thì làm ở TAB MỚI: Cốc Cốc đóng tab đầu tiên của trình duyệt khi tab đó
    # tải file (link blob:) → mất luôn cả cửa sổ giữa chừng. Tab đầu để nguyên làm «neo».
    return bs.new_page()


def _ensure_page(bs, page):
    """(phiên, tab) còn sống; người dùng lỡ đóng cả cửa sổ → mở lại trình duyệt."""
    if bs is not None:
        try:
            return bs, _live_page(bs, page)
        except Exception:
            _close_browser(bs)
    _set_msg("Đang mở trình duyệt nhaccuatui…")
    bs = _open_browser()
    return bs, _live_page(bs, None)


def _drain() -> None:
    set_cancel_check(_stop.is_set)
    bs, page = None, None
    try:
        while True:
            with _lock:
                if not _pending:
                    _state.update(draining=False, mode=None, current=None)
                    break
                sid = _pending.popleft()
                _state.update(mode="download", current=sid)
                _stop.clear()     # cùng khoá với stop() → không lỡ lệnh dừng
            try:
                bs, page = _ensure_page(bs, page)
            except Exception as e:  # noqa: BLE001
                bs = None
                traceback.print_exc()
                with _lock:
                    rest = [sid, *_pending]
                    _pending.clear()
                _mark(rest, "failed", f"Không mở được trình duyệt: {e}"[:500])
                _set_msg(f"Lỗi mở trình duyệt: {e}")
                continue
            page = _process(sid, bs, page)
    finally:
        set_cancel_check(None)
        _close_browser(bs)
        with _lock:
            _state.update(draining=False, mode=None, current=None)
            if _pending:      # có bài mới xếp vào đúng lúc đang đóng → chạy tiếp
                _state["draining"] = True
                _executor.submit(_drain)


def _process(sid: int, bs, page):
    """Tải 1 bài. Trả page dùng tiếp (mở lại nếu người dùng lỡ đóng tab)."""
    with _lock:
        force_verify_lyrics = sid in _verify_lyrics
        _verify_lyrics.discard(sid)
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return page
        project = s.get(Project, src.project_id)
        src.status, src.error = "running", None
        s.add(src)
        s.commit()
        query, url = src.query, src.nct_url
        pid, pname = src.project_id, (project.name if project else None)
        had_file = bool(src.file_path and Path(src.file_path).exists())
        existing_saved_lyrics = src.lyrics_text or ""
        had_lyrics = bool(existing_saved_lyrics.strip()) and not force_verify_lyrics
        stored_title = src.title or ""
        stored_file = src.file_path or ""
    label = (query or url)[:60]

    def log(m: str) -> None:
        _set_msg(f"{label}: {m}")

    dest = source_dir(pid, pname)
    info: dict = {}
    nct_ready = False
    fallback_pool = None      # ứng viên Zing/YT Music/YouTube đã tìm sẵn (nếu có)
    match_note = ""           # cảnh báo khi không có bản đúng tên + ca sĩ
    try:
        log("đang tìm trên nhaccuatui…")
        try:
            # nct_url có thể là kết quả cũ do tool tự lưu. Chỉ coi URL là lựa
            # chọn cố định khi chính ô query của người dùng là URL; nếu query là
            # «Tên - Ca sĩ» phải tìm/chấm lại để policy mới thay được Remix cũ.
            if url and nct.is_song_url(query):
                page.goto(url, wait_until="domcontentloaded")
                info = nct.ensure_official(
                    nct.read_song(page), query or url, allow_variant=True)
            else:
                try:
                    # Chỉ nhận ngay bản ĐÚNG TÊN + ĐÚNG CA SĨ (official).
                    info = nct.find_and_open(page, query, log=log, strict=True)
                except nct.NctNoExactMatch as miss:
                    info, fallback_pool = _nct_or_better(page, query, miss, log)
            if info:
                nct_ready = True
                match_note = info.pop("match_note", "") or ""
                record_login(nct.login_state(page))
            else:
                info = audio_sources.metadata_from_query(query or url)
        except (nct.NctNotFound, nct.NctLoginRequired, nct.NctError) as exc:
            # NhacCuaTui chỉ là nguồn ưu tiên, không còn là điểm lỗi cuối.
            log(f"NhacCuaTui chưa dùng được ({exc}) — chuyển nguồn dự phòng…")
            info = audio_sources.metadata_from_query(query or url)
        nct_lyrics = (info.get("lyrics") or "").strip() if nct_ready else ""
        if not had_lyrics and nct_lyrics and not force_verify_lyrics:
            # Lời có sẵn trên trang bài nhaccuatui (khớp đúng file nhạc sẽ tải)
            # được dùng luôn — KHÔNG đi tìm/đè bằng lời Google/Zing.
            log(f"dùng lời có sẵn trên nhaccuatui ({lyrics_web.line_count(nct_lyrics)} câu) "
                "— không tìm thêm trên Google.")
            info["lyrics"] = nct_lyrics
            info["lyrics_source"] = ""
        elif not had_lyrics:
            # nhaccuatui không có lời (hoặc người dùng bấm «kiểm tra lại lời»):
            # mới tìm Zing MP3 / LRCLIB / Google.
            original_lyrics = existing_saved_lyrics or info.get("lyrics") or ""
            verified = _web_lyrics(bs, info, log, existing_lyrics=original_lyrics)
            if verified.get("lyrics"):
                info.update(verified)
            else:
                info["lyrics"] = ""
                info["lyrics_source"] = ""
        stem = info_stem(info.get("title") or "", info.get("artist") or "", query)
        _save_info(sid, info, dest / f"{stem}.txt",
                   overwrite_lyrics=force_verify_lyrics)
        final_lyrics = (info.get("lyrics") or "").strip() if not had_lyrics else \
            existing_saved_lyrics.strip()
        if not final_lyrics:
            _skip_without_lyrics(sid, dest)
            log("không tìm được lyrics — đã loại bài; không tải/xuất tempo và không đưa sang Suno Cover.")
            return page
        if had_file:
            try:
                _ensure_tempo_export(sid, log)
            except tempo.TempoError as exc:
                _finish(sid, "done", f"Đã có file gốc nhưng tự động xuất tempo lỗi: {exc}")
                log(f"đã có file gốc nhưng chưa xuất được tempo: {exc}")
                return page
            _finish(sid, "done", None)
            log("đã có file từ trước — cập nhật lời/link và đã chuẩn bị bản tempo.")
            return page
        if nct_ready:
            log("đang tải bản High quality từ NhacCuaTui…")
            try:
                path, quality = nct.download(page, dest, stem, log=log)
            except (nct.NctLoginRequired, nct.NctError) as exc:
                log(f"NhacCuaTui không tải được ({exc}) — chuyển Zing MP3/YouTube Music/YouTube…")
                path, quality, fallback = audio_sources.download_fallback(
                    page, dest, stem, query or url, log=log)
                match_note = fallback.get("match_note") or ""
                # URL nguồn audio thực tế phải được lưu để người dùng kiểm tra.
                info["url"] = fallback.get("url") or info.get("url")
                _save_info(sid, info, dest / f"{stem}.txt")
        else:
            path, quality, fallback = audio_sources.download_fallback(
                page, dest, stem, query or url, log=log, candidates=fallback_pool)
            match_note = fallback.get("match_note") or ""
            info["url"] = fallback.get("url") or info.get("url")
            # Metadata từ nguồn tìm kiếm có thể chính xác hơn chuỗi người dùng gõ.
            info["title"] = fallback.get("title") or info.get("title")
            info["artist"] = fallback.get("artist") or info.get("artist")
            _save_info(sid, info, dest / f"{stem}.txt")
        with Session(engine) as s:
            src = s.get(SourceSong, sid)
            if src:
                # File nguồn vừa thay đổi: bản tempo cũ không còn là derivative
                # hợp lệ và tuyệt đối không được đưa lên Suno.
                old_tempo = Path(src.tempo_file_path) if src.tempo_file_path else None
                src.file_path, src.quality = str(path.resolve()), quality
                src.tempo_factor, src.tempo_file_path = 1.0, ""
                src.suno_song_ids, src.suno_editor_song_id = "", ""
                src.suno_status, src.suno_error = "", None
                src.status, src.error = "done", (match_note or None)
                s.add(src)
                s.commit()
                if old_tempo:
                    try:
                        old_tempo.resolve().relative_to(tempo.tempo_dir(
                            src.project_id, project.name if project else None).resolve())
                        old_tempo.unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
        try:
            _ensure_tempo_export(sid, log)
        except tempo.TempoError as exc:
            _finish(sid, "done", f"Đã tải file gốc nhưng tự động xuất tempo lỗi: {exc}")
            log(f"đã tải file gốc nhưng chưa xuất được tempo: {exc}")
            return page
        if match_note:
            log(match_note)
        log(f"xong ({quality}) — {path.name}; đã xuất WAV tempo {AUTO_TEMPO_FACTOR:g}× — dừng để kiểm tra Suno Cover.")
    except OperationCancelled:
        _finish(sid, "stopped", "Đã dừng — bấm «Thử lại» để tải tiếp.")
        _set_msg("Đã dừng.")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        shot = nct.save_debug(page, dest / "_debug", label) if page is not None else ""
        msg = f"{type(e).__name__}: {e}"[:400] + (f" (ảnh chụp: {shot})" if shot else "")
        _finish(sid, "lyrics_only" if info.get("url") else "failed", msg)
        log(f"lỗi: {e}")
    return page


def _nct_or_better(page, query: str, miss, log):
    """nhaccuatui không có bản đúng tên + ca sĩ: so ứng viên tốt nhất của
    nhaccuatui với Zing MP3 / YouTube Music / YouTube.

    Trả ``(info, None)`` khi dùng nhaccuatui (hoà hạng → nhaccuatui thắng vì có
    lời + chất lượng cao), hoặc ``(None, pool)`` khi nguồn khác khớp hơn."""
    log(f"{miss} So với Zing MP3 / YouTube Music / YouTube…")
    try:
        pool = audio_sources.find_best(page, query, log=log)
    except audio_sources.AudioSourceError as exc:
        log(f"nguồn dự phòng chưa dùng được ({exc}).")
        pool = []
    other = pool[0][1].tier if pool else song_match.NONE
    if miss.row is not None and miss.match.tier >= other:
        info = nct.open_candidate(page, miss.search_query, miss.row, query, log=log)
        info["match_note"] = audio_sources.tag(
            dict(miss.row, provider="nhaccuatui"), miss.match, query)["match_note"]
        return info, None
    if not pool:
        raise nct.NctNotFound(miss.args[0] if miss.args else "không tìm thấy bài")
    return None, pool


def _invalidate_variant_files(sid: int, dest: Path) -> None:
    """Bỏ liên kết tới derivative cũ và chuyển file biến thể vào thư mục giữ lại."""
    paths: list[Path] = []
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return
        if src.file_path:
            paths.append(Path(src.file_path))
        if src.tempo_file_path:
            paths.append(Path(src.tempo_file_path))
        src.file_path = src.quality = ""
        src.tempo_factor, src.tempo_file_path = 1.0, ""
        src.suno_song_ids = src.suno_editor_song_id = ""
        src.suno_status, src.suno_error = "", None
        s.add(src)
        s.commit()
    rejected = dest / "_rejected_variants"
    for path in paths:
        try:
            resolved = path.resolve()
            # Chỉ di chuyển file do tool quản lý trong project, không đụng file ngoài.
            resolved.relative_to(dest.parent.resolve())
            if resolved.is_file():
                rejected.mkdir(parents=True, exist_ok=True)
                target = rejected / resolved.name
                if target.exists():
                    target = rejected / f"{resolved.stem}_{int(time.time())}{resolved.suffix}"
                resolved.replace(target)
        except (OSError, ValueError):
            pass


def _skip_without_lyrics(sid: int, dest: Path) -> None:
    """Thiếu lời là trạng thái loại cuối: không còn derivative/Suno đầu vào."""
    old_tempo: Path | None = None
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return
        old_tempo = Path(src.tempo_file_path) if src.tempo_file_path else None
        src.status = "skipped_no_lyrics"
        src.error = ("Không tìm được lyrics từ Zing MP3/NhacCuaTui/LRCLIB/Google — "
                     "đã loại, không đưa sang Suno Cover.")
        src.tempo_factor, src.tempo_file_path = 1.0, ""
        src.suno_song_ids = src.suno_editor_song_id = ""
        src.suno_status, src.suno_error = "", None
        s.add(src)
        s.commit()
    if old_tempo:
        try:
            resolved = old_tempo.resolve()
            resolved.relative_to(dest.parent.resolve())
            if resolved.is_file():
                rejected = dest / "_rejected_no_lyrics"
                rejected.mkdir(parents=True, exist_ok=True)
                target = rejected / resolved.name
                if target.exists():
                    target = rejected / f"{resolved.stem}_{int(time.time())}{resolved.suffix}"
                resolved.replace(target)
        except (OSError, ValueError):
            pass


def _skip_no_official(sid: int, reason: str) -> None:
    """Không có bản official là kết quả nghiệp vụ, không phải lỗi hệ thống đỏ."""
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return
        src.status = "skipped_no_official"
        src.error = reason
        src.tempo_factor, src.tempo_file_path = 1.0, ""
        src.suno_song_ids = src.suno_editor_song_id = ""
        src.suno_status, src.suno_error = "", None
        s.add(src)
        s.commit()


def _web_lyrics(bs, info: dict, log, existing_lyrics: str = "") -> dict:
    """Đối chiếu Zing MP3/LRCLIB/Google trong tab riêng, giữ trang NCT để tải."""
    pg = None
    try:
        pg = bs.new_page()
        # Tìm lời theo tên bài lõi, không mang nhãn nguồn audio như
        # «(Inso Cover)»/«Remix» khiến Zing và Google bỏ sót lời bài gốc.
        lyrics_title = nct.official_query(info.get("title") or "")
        got = lyrics_web.find_lyrics(
            pg, lyrics_title, info.get("artist") or "", log=log,
            existing_lyrics=existing_lyrics,
            existing_source="NhacCuaTui")
    except OperationCancelled:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        got = {}
    finally:
        if pg is not None:
            try:
                pg.close()
            except Exception:
                pass
    if got.get("lyrics"):
        n = lyrics_web.line_count(got["lyrics"])
        log(f"đã lấy {n} câu lời trên mạng ({got.get('source')}).")
        return {"lyrics": got["lyrics"], "lyrics_source": got.get("source") or "web"}
    if got.get("confidence") == "needs_review":
        log("các nguồn lyrics không khớp nhau — dừng để bạn kiểm tra/dán lời thủ công.")
    else:
        log("không tìm được lyrics đủ tin cậy — bạn có thể tự dán lời.")
    return {}


def _save_info(sid: int, info: dict, txt_path: Path,
               overwrite_lyrics: bool = False) -> None:
    """Lưu tên/ca sĩ/link/lời vào DB ngay (kể cả khi bước tải file lỗi) + file .txt
    cạnh nhạc gốc. Lời người dùng đã sửa thì giữ nguyên."""
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return
        src.title = info.get("title") or src.title
        src.artist = info.get("artist") or src.artist
        src.nct_url = info.get("url") or src.nct_url
        if overwrite_lyrics or (not src.lyrics_text.strip() and info.get("lyrics")):
            from backend.cover.lyrics import strip_lyrics_header
            src.lyrics_text = strip_lyrics_header(info.get("lyrics") or "").strip()
            src.lyrics_suno_text = ""
            src.lyrics_source = info.get("lyrics_source") or ""
        s.add(src)
        s.commit()
        note = f"Lời lấy trên mạng: {src.lyrics_source}\n" if src.lyrics_source else ""
        body = f"{src.title} – {src.artist}\n{src.nct_url}\n{note}\n{src.lyrics_text}\n"
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(body, encoding="utf-8")
    except Exception:
        pass


def _finish(sid: int, status_: str, error: str | None) -> None:
    _mark([sid], status_, error)


def _login_window() -> None:
    _stop.clear()
    set_cancel_check(_stop.is_set)
    with _lock:
        _state.update(mode="login", login_scheduled=False)
    hints = {
        "logged_in": "✅ Đã đăng nhập nhaccuatui — bấm «Đóng cửa sổ» rồi tải nhạc.",
        "logged_out": "Chưa đăng nhập — hãy TỰ bấm «Đăng nhập» trên nhaccuatui trong cửa "
                      "sổ vừa mở (tool KHÔNG nhập mật khẩu).",
    }
    try:
        with _browser() as bs:
            page = _live_page(bs, None, work=False)
            try:
                page.goto(nct.BASE_URL, wait_until="domcontentloaded")
            except Exception:
                pass
            deadline = time.time() + LOGIN_HOLD_SECONDS
            while time.time() < deadline:
                try:
                    pages = [p for p in bs.context.pages if not p.is_closed()]
                    if not pages:
                        break
                    page = page if not page.is_closed() else pages[0]
                    wait_ms(page, 1500)
                    st = nct.login_state(page)
                except OperationCancelled:
                    raise
                except Exception:
                    break     # người dùng tự đóng cửa sổ
                record_login(st)
                left = int(deadline - time.time()) // 60
                _set_msg(hints.get(st, "Cửa sổ nhaccuatui đang mở — tự đăng nhập nếu cần.")
                         + f" (tự đóng sau ~{left} phút)")
    except OperationCancelled:
        pass
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _set_msg(f"Lỗi mở nhaccuatui: {e}")
        return
    finally:
        set_cancel_check(None)
        with _lock:
            _state.update(mode=None, login_scheduled=False)
    st = login_status()
    _set_msg("Đã đóng cửa sổ nhaccuatui." + (" Đã đăng nhập ✓" if st.get("logged_in") else ""))
