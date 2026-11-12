"""
batch_service.py — Orchestrator sản xuất HÀNG LOẠT N sản phẩm YouTube.

Ý tưởng: tái dùng NGUYÊN chuỗi một-chạm của từng project (Suno STEP 0 → Mix →
Video → đăng nháp YouTube) và chạy TUẦN TỰ cho lần lượt N project. Giữ nguyên
nhạc cụ + style ảnh + thể loại cho cả lô; AI sinh tên/ý tưởng/mô tả/prompt khác
nhau cho từng project (backend/video/batch_ideas.py).

RÀNG BUỘC AN TOÀN (bám sát):
  • Chạy tuần tự tuyệt đối — 1 VideoJobManager toàn cục (max_workers=1) + Suno
    account lock. Không bao giờ submit song song.
  • "Chuỗi project i xong" KHÔNG chỉ là "job Suno xong": start_mix_for_project chỉ
    *submit* mix rồi trả ngay, và mix→video bàn giao qua 2 manager khác nhau
    (job_manager cho mix, video_job_manager cho video). Vì job mix được đánh dấu
    completed TRƯỚC khi on_success submit video (job_manager._run), có một khe hở
    ngắn cả hai manager cùng rảnh. Do đó điều kiện "xong" = Suno phase terminal
    (COMPLETED/CANCELLED) VÀ cả hai manager rảnh, GIỮ ỔN ĐỊNH qua nhiều lần poll
    liên tiếp (debounce) để không nhảy sớm ngay tại khe bàn giao.
  • Gặp trạng thái blocking của Suno (login/credit/UI đổi/...) → DỪNG lô an toàn,
    báo rõ project nào ở phase nào; không kéo các project còn lại vào lỗi.
  • Không tự mua credit, không publish (YouTube chỉ lưu nháp qua luồng hiện có).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from backend.database import engine
from backend.models import BatchRun, CoverSong, Project, SourceSong, Track
from backend.job_manager import job_manager               # mix manager
from backend.video.job_manager import video_job_manager   # suno + video manager
from backend.video import suno_service

# Phase Suno chặn lô — cần người xử lý rồi mới resume được.
_BLOCKING = {
    suno_service.WAITING_FOR_LOGIN,
    suno_service.WAITING_FOR_HUMAN,
    suno_service.SUBMISSION_UNCERTAIN,
    suno_service.INSUFFICIENT_GENERATION_CREDITS,
    suno_service.INSUFFICIENT_DOWNLOAD_ALLOWANCE,
    suno_service.UI_CHANGED,
    suno_service.FAILED,
    suno_service.PAUSED,
}
_TERMINAL_OK = {suno_service.COMPLETED}


def _suno_done(st: Optional[dict]) -> bool:
    """Khâu Suno của project đã xong? COMPLETED, HOẶC đã nhập đủ số bài mục tiêu
    mà phase còn kẹt ở bước trung gian (vd DOWNLOADING do khâu rà soát tạo lại
    bài ghi đè phase) — lúc đó chạy lại Suno là đốt credit vô ích và lô từng chờ
    COMPLETED tới trần 12 giờ. Gọi khi chuỗi đã RẢNH (không còn job Suno chạy)."""
    st = st or {}
    phase = st.get("phase")
    if phase in _TERMINAL_OK:
        return True
    if phase in _BLOCKING or phase in (None, suno_service.CANCELLED):
        return False
    imported = int(st.get("imported_tracks") or 0)
    target = int(st.get("target_tracks") or 0)
    return target > 0 and imported >= target

# Chặn CỨNG: cần con người (đăng nhập / nạp credit / tự tay pause) — thử lại vô
# ích và còn đốt thêm credit ⇒ dừng cả lô, báo rõ.
_HARD_STOP = {
    suno_service.WAITING_FOR_LOGIN,
    suno_service.INSUFFICIENT_GENERATION_CREDITS,
    suno_service.INSUFFICIENT_DOWNLOAD_ALLOWANCE,
    suno_service.PAUSED,
    # Create chưa xác nhận: tự chạy lại = bấm Create lần nữa (spec: KHÔNG bấm lại).
    # 2026-10-12 lô tự thử lại đã bấm Create 3 lần liền cho project 33.
    suno_service.SUBMISSION_UNCERTAIN,
}


def _needs_human(phase: Optional[str], detail: str) -> bool:
    """Chặn cần người: phase cứng, hoặc Suno đòi CAPTCHA (WAITING_FOR_HUMAN kèm
    chữ CAPTCHA — sản phẩm kế cũng sẽ bị đòi, tool không tự giải)."""
    return phase in _HARD_STOP or (phase == suno_service.WAITING_FOR_HUMAN
                                   and "captcha" in (detail or "").lower())
# Số lần TỰ resume 1 sản phẩm trước khi bỏ qua nó và chạy sản phẩm kế.
_MAX_RETRY_PER_PRODUCT = 2

# Mix Set chờ người dùng dán lời. Sau 5 phút không có bài nào được bổ sung lời,
# các bài vẫn thiếu sẽ được đánh dấu bỏ qua và chuỗi tự nối tiếp.
_LYRICS_WAIT_SECONDS = 5 * 60


class _MissingLyricsWait(RuntimeError):
    pass

# Lỗi Video ở mức TÀI KHOẢN (Flow/Gemini gắn cờ chống tự động hoá, hết hạn mức):
# sản phẩm nào cũng sẽ hỏng y hệt ⇒ không "bỏ qua sang sản phẩm kế" (mỗi sản phẩm
# kế lại đốt 15 credit Suno mà vẫn không ra video) — DỪNG lô NGAY, cũng KHÔNG thử
# lại tại chỗ (cờ bám theo profile, chạy lại chỉ làm nó bám chặt hơn).
_ACCOUNT_BLOCK_MARKERS = ("hoạt động bất thường", "unusual activity",
                          "flowblocked", "google flow từ chối",
                          # Gemini Video hết lượt/giới hạn (gemini_video.GeminiVideoLimit)
                          "gemini báo hết lượt", "geminivideolimit",
                          # Muse.ai (muse_video): hết lượt / kẹt kết nối / mất đăng nhập
                          "muse báo hết lượt", "muse chưa sẵn sàng",
                          "muse.ai đang ở trang đăng nhập", "không mở được profile gpm")

# Poll & debounce
_POLL_SEC = 5.0
_IDLE_STREAK_REQUIRED = 3      # số lần poll liên tiếp thấy cả 2 manager rảnh
# Trần an toàn 1 sản phẩm (tránh kẹt vô hạn). Flow chạy chậm kiểu người (40 clip,
# 4 clip/phiên, nghỉ 20–30' giữa phiên) + upload 5 GB ≈ 6–8 giờ ⇒ để 12 giờ.
_PER_PRODUCT_TIMEOUT_SEC = 12 * 3600
_SUBMIT_GRACE_SEC = 30         # chờ job vừa submit vào worker rồi mới chấm "rảnh"

# Cờ huỷ theo batch_key (thread orchestrator đọc mỗi vòng).
_cancel_flags: dict[str, threading.Event] = {}
_threads: dict[str, threading.Thread] = {}
_lyrics_timers: dict[str, threading.Timer] = {}
_lyrics_timer_lock = threading.Lock()
# Khoá chống bấm «Tạo» 2 lần khi AI đang nghĩ ý tưởng (call LLM > 30s).
_create_lock = threading.Lock()


def _active_batch_key() -> Optional[str]:
    """batch_key của lô có thread orchestrator còn sống (tối đa 1 lô chạy)."""
    return next((k for k, t in _threads.items() if t.is_alive()), None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _touch(session: Session, run: BatchRun, **kw) -> None:
    for k, v in kw.items():
        setattr(run, k, v)
    run.updated_at = _now()
    session.add(run)
    session.commit()
    session.refresh(run)


def _cancel_lyrics_timer(batch_key: str) -> None:
    with _lyrics_timer_lock:
        timer = _lyrics_timers.pop(batch_key, None)
    if timer:
        timer.cancel()


def _schedule_auto_skip(batch_key: str, delay_sec: float = _LYRICS_WAIT_SECONDS,
                        *, preserve_deadline: bool = False) -> None:
    """Đặt/reset đồng hồ tự bỏ qua. Timer là daemon và được phục hồi khi server mở."""
    _cancel_lyrics_timer(batch_key)
    deadline = _now().timestamp() + max(0.0, delay_sec)
    with Session(engine) as session:
        run = session.exec(select(BatchRun).where(
            BatchRun.batch_key == batch_key)).first()
        if not run:
            return
        params = json.loads(run.params_json or "{}")
        if not preserve_deadline:
            params["lyrics_last_activity_at"] = _now().isoformat()
            params["lyrics_auto_skip_at"] = datetime.fromtimestamp(
                deadline, timezone.utc).isoformat()
            run.params_json = json.dumps(params, ensure_ascii=False)
            session.add(run)
            session.commit()

    def fire() -> None:
        with _lyrics_timer_lock:
            _lyrics_timers.pop(batch_key, None)
        try:
            skip_missing_lyrics(batch_key, automatic=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[mixset] tự bỏ qua lyrics lỗi: {exc}", flush=True)

    timer = threading.Timer(max(0.1, delay_sec), fire)
    timer.daemon = True
    with _lyrics_timer_lock:
        _lyrics_timers[batch_key] = timer
    timer.start()


def _schedule_resume_after_skip(batch_key: str, delay_sec: float = 0.1) -> None:
    """Auto-skip có thể trùng lúc job khác bận; kiên trì nối lại khi chuỗi rảnh."""
    _cancel_lyrics_timer(batch_key)

    def attempt() -> None:
        with _lyrics_timer_lock:
            _lyrics_timers.pop(batch_key, None)
        try:
            resume_batch(batch_key)
        except ValueError as exc:
            low = str(exc).lower()
            if "đang có job" in low or "đang chạy" in low:
                _schedule_resume_after_skip(batch_key, 15.0)
            else:
                print(f"[mixset] không tự tiếp tục sau khi bỏ qua: {exc}", flush=True)

    timer = threading.Timer(max(0.1, delay_sec), attempt)
    timer.daemon = True
    with _lyrics_timer_lock:
        _lyrics_timers[batch_key] = timer
    timer.start()


def note_lyrics_activity(project_id: int) -> None:
    """Một bài vừa được dán lời: reset 5 phút nếu Mix Set vẫn còn bài thiếu."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project or not project.batch_id:
            return
        run = session.exec(select(BatchRun).where(
            BatchRun.batch_key == project.batch_id)).first()
        if not run or run.status not in ("blocked", "interrupted"):
            return
        params = json.loads(run.params_json or "{}")
        if params.get("kind") not in _COVER_KINDS:
            return
        missing = [s for s in _batch_sources(session, project.batch_id)
                   if s.status not in _INACTIVE_SOURCE]
        has_missing = any(not (s.lyrics_text or "").strip() for s in missing)
    if has_missing:
        _schedule_auto_skip(project.batch_id)
    else:
        _cancel_lyrics_timer(project.batch_id)


def restore_pending_lyrics_timers() -> None:
    """Khôi phục đồng hồ 5 phút sau khi backend khởi động lại."""
    now = _now()
    with Session(engine) as session:
        runs = session.exec(select(BatchRun).where(
            BatchRun.status.in_(["blocked", "interrupted"]))).all()
        pending: list[tuple[str, float, bool]] = []
        for run in runs:
            params = json.loads(run.params_json or "{}")
            if params.get("kind") not in _COVER_KINDS:
                continue
            if params.get("lyrics_resume_pending"):
                pending.append((run.batch_key, 0.1, False))
                continue
            sources = [s for s in _batch_sources(session, run.batch_key)
                       if s.status not in _INACTIVE_SOURCE]
            if not sources:
                continue
            if not any(not (s.lyrics_text or "").strip() for s in sources):
                continue
            raw = params.get("lyrics_auto_skip_at")
            try:
                deadline = datetime.fromisoformat(raw) if raw else None
                if deadline and deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                delay = max(0.1, (deadline - now).total_seconds()) if deadline else _LYRICS_WAIT_SECONDS
            except (TypeError, ValueError):
                delay = _LYRICS_WAIT_SECONDS
            pending.append((run.batch_key, delay, bool(raw)))
    for key, delay, preserve in pending:
        if delay <= 0.1 and not preserve:
            _schedule_resume_after_skip(key, delay)
        else:
            _schedule_auto_skip(key, delay, preserve_deadline=preserve)


def get_batch(batch_key: str) -> Optional[dict]:
    """Trạng thái lô + danh sách project (id/name/phase Suno) cho UI."""
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        projects = session.exec(
            select(Project).where(Project.batch_id == batch_key)
            .order_by(Project.id)).all()
        items = []
        for p in projects:
            batch = suno_service.batch_status(p.id)
            items.append({
                "project_id": p.id,
                "name": p.name,
                "suno_phase": (batch or {}).get("phase"),
                "suno_message": (batch or {}).get("message"),
            })
        out = _run_dict(run, items)
        params = json.loads(run.params_json or "{}")
        if params.get("kind") in _COVER_KINDS:
            missing = session.exec(
                select(SourceSong).join(Project, SourceSong.project_id == Project.id)
                .where(Project.batch_id == batch_key)
                .order_by(SourceSong.position, SourceSong.id)).all()
            out["missing_lyrics"] = [
                {"source_id": s.id, "project_id": s.project_id,
                 "query": s.query, "title": s.title, "artist": s.artist}
                for s in missing if s.status not in ("skipped_no_lyrics", "excluded")
                and not (s.lyrics_text or "").strip()
            ]
        # DB ghi "running" nhưng thread orchestrator đã chết (server khởi động
        # lại giữa chừng) → báo "interrupted" để UI hiện nút Tiếp tục lô.
        t = _threads.get(batch_key)
        if out["status"] == "running" and (t is None or not t.is_alive()):
            out["status"] = "interrupted"
        return out


def _run_dict(run: BatchRun, items: list[dict]) -> dict:
    return {
        "batch_key": run.batch_key,
        "total": run.total,
        "completed": run.completed,
        "failed": run.failed,
        "status": run.status,
        "current_index": run.current_index,
        "current_project_id": run.current_project_id,
        "message": run.message,
        "params": json.loads(run.params_json or "{}"),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "projects": items,
        "missing_lyrics": [],
    }


def create_batch(count: int, instrument: str, music_style: str, video_style: str,
                 theme: str = "", *, target_tracks: Optional[int] = None,
                 max_create_actions: Optional[int] = None,
                 channel_name: str = "",
                 channel_id: Optional[int] = None,
                 mix_duration_minutes: float = 120.0,
                 intro_mode: str = "thumbnail",
                 cast_mode: str = "character_pet",
                 music_line: str = "") -> dict:
    """
    Sinh N ý tưởng phân biệt (1 call LLM, KHÔNG tốn credit Suno) → tạo N Project
    cùng batch_id → tạo BatchRun → khởi động thread chạy tuần tự.

    Ném RuntimeError nếu đầu vào không hợp lệ hoặc AI không sinh đủ ý tưởng
    (không tạo project rác).
    """
    from backend.database import DB_PATH
    from backend.core_bridge import get_api_config_from_db
    from backend.video import batch_ideas
    from backend.video import config as vconfig

    count = int(count)
    if not 1 <= count <= 50:
        raise RuntimeError("Số lượng sản phẩm phải từ 1 đến 50.")
    instrument = (instrument or "").strip()
    music_style = (music_style or "").strip()
    music_line = (music_line or "").strip()
    # Dùng chính bộ phong cách của video/config (10 key) — không tự chế danh sách
    # riêng, tránh gửi key lạ rồi bị normalize_style âm thầm đổi về "2d".
    # "channel" = phong cách riêng của kênh: xoay vòng trong cặp phong cách của
    # kênh cho từng project (styles_cycle); key cụ thể = giữ nguyên cả lô.
    from backend.video import channel_looks
    from backend.video.service import _lookup_channel_by_id
    # Kênh người dùng chọn cho cả lô — kênh nào cũng nhận nhạc cụ/thể loại nào.
    chosen = _lookup_channel_by_id(channel_id) if channel_id else None
    if channel_id and not chosen:
        raise RuntimeError("Kênh YouTube đã chọn không còn trong ⚙️ Cài đặt — chọn lại kênh.")
    channel_id = chosen["id"] if chosen else None
    intro_mode = vconfig.normalize_intro_mode(intro_mode)
    cast_mode = vconfig.normalize_cast_mode(cast_mode)
    if chosen:
        channel_name = chosen.get("channel_name") or channel_name
    styles_cycle: list[str] = []
    if (video_style or "").strip().lower() in ("", "channel"):
        m = channel_looks.mapping_for(instrument, music_style, channel_id=channel_id,
                                      music_line=music_line)
        if m:
            recent = channel_looks.recent_channel_styles(m)
            styles_cycle = [channel_looks.style_for_channel(m, recent, offset=i)
                            for i in range(count)]
    style_key = (styles_cycle[0] if styles_cycle
                 else channel_looks.resolve_video_style(video_style, instrument, music_style,
                                                        channel_id=channel_id,
                                                        music_line=music_line))
    if not music_style:
        raise RuntimeError("Thiếu thể loại nhạc cho lô sản xuất.")

    tgt = int(target_tracks) if target_tracks else int(vconfig.SUNO.target_tracks)
    mca = (int(max_create_actions) if max_create_actions
           else int(vconfig.SUNO.max_create_actions))

    # Chỉ 1 lô chạy một lúc (worker Suno/Video tuần tự) — chặn cả lần bấm lại khi
    # trình duyệt đã báo timeout nhưng server vẫn đang tạo lô trước đó.
    active = _active_batch_key()
    if active:
        raise RuntimeError("Đang có 1 lô sản xuất chạy — đợi lô đó xong hoặc huỷ "
                           "rồi mới tạo lô mới (tránh tạo trùng).")
    if not _create_lock.acquire(blocking=False):
        raise RuntimeError("Đang tạo 1 lô khác (AI đang nghĩ ý tưởng) — đợi ít phút, "
                           "lô sẽ hiện ở thanh tiến độ.")
    try:
        api_config = get_api_config_from_db(str(DB_PATH))
        ideas = batch_ideas.generate_batch_ideas(
            count, instrument, style_key, theme, api_config, music_style=music_style,
            cast_mode=cast_mode)
        return _create_batch_rows(ideas, count, instrument, music_style, style_key,
                                  theme, channel_name, tgt, mca, mix_duration_minutes,
                                  styles_cycle=styles_cycle, channel_id=channel_id,
                                  intro_mode=intro_mode, cast_mode=cast_mode,
                                  music_line=music_line)
    finally:
        _create_lock.release()


def create_cover_mixset(name: str, song_lines: list[str], music_style: str,
                        video_style: str, theme: str = "", *, music_line: str = "",
                        channel_id: Optional[int] = None,
                        mix_duration_minutes: float = 120.0,
                        intro_mode: str = "thumbnail",
                        cast_mode: str = "character_pet",
                        footage_scene_mode: str = "auto",
                        creative_instrument: bool = True) -> dict:
    """Tạo MỘT project cover từ danh sách bài và chạy tuần tự end-to-end.
    creative_instrument: True → mỗi bài để AI tự chọn thêm 1 nhạc cụ nổi bật khi
    viết style Suno (featured_instrument ""); False → tắt cho mọi bài."""
    from backend.cover.style_prompt import NO_CREATIVE_INSTRUMENT
    from backend.video import channel_looks, config as vconfig
    from backend.video.service import _lookup_channel_by_id

    name = (name or "").strip()
    music_style = (music_style or "").strip()
    music_line = (music_line or "").strip()
    lines = [" ".join((x or "").strip().split()) for x in song_lines]
    lines = list(dict.fromkeys(x for x in lines if x))
    if not name:
        raise RuntimeError("Thiếu tên Mix Set.")
    if not music_style:
        raise RuntimeError("Thiếu thể loại nhạc cho Mix Set.")
    if not 1 <= len(lines) <= 50:
        raise RuntimeError("Danh sách Mix Set phải có từ 1 đến 50 bài.")
    if any(not re.search(r"\s[-–—]\s", x) for x in lines):
        raise RuntimeError(
            "Mỗi dòng phải có dạng «Tên bài - Ca sĩ» hoặc «Ca sĩ - Tên bài».")
    if _active_batch_key():
        raise RuntimeError("Đang có một Mix Set/lô sản xuất chạy — đợi xong hoặc huỷ trước.")
    chosen = _lookup_channel_by_id(channel_id) if channel_id else None
    if channel_id and not chosen:
        raise RuntimeError("Kênh YouTube đã chọn không còn trong Cài đặt.")
    style_key = channel_looks.resolve_video_style(
        video_style, "", music_style, channel_id=channel_id, music_line=music_line)
    intro_mode = vconfig.normalize_intro_mode(intro_mode)
    cast_mode = vconfig.normalize_cast_mode(cast_mode)
    footage_scene_mode = (footage_scene_mode if footage_scene_mode in
                          ("auto", "character_screen", "screen_only") else "auto")
    batch_key = uuid.uuid4().hex
    params = {
        "kind": "cover_mixset", "song_count": len(lines),
        "music_style": music_style, "music_line": music_line,
        "video_style": style_key, "theme": theme, "channel_id": channel_id,
        "mix_duration_minutes": float(mix_duration_minutes),
        "intro_mode": intro_mode, "cast_mode": cast_mode,
        "footage_scene_mode": footage_scene_mode,
        "creative_instrument": bool(creative_instrument),
    }
    instrument = "" if creative_instrument else NO_CREATIVE_INSTRUMENT
    with Session(engine) as session:
        project = Project(
            name=name, description=(theme or "").strip(), video_idea=(theme or "").strip(),
            video_style=style_key, instrument="", music_style=music_style,
            music_line=music_line, channel_id=channel_id, intro_mode=intro_mode,
            cast_mode=cast_mode, footage_scene_mode=footage_scene_mode,
            creative_instrument=bool(creative_instrument), music_source="local", auto_video=True,
            auto_upload=True, batch_id=batch_key, kind="mixset",
            mix_duration_minutes=float(mix_duration_minutes))
        session.add(project)
        session.flush()
        sources = []
        for pos, line in enumerate(lines, 1):
            src = SourceSong(project_id=project.id, position=pos, query=line,
                             status="queued", featured_instrument=instrument)
            session.add(src)
            sources.append(src)
        session.flush()
        run = BatchRun(
            batch_key=batch_key, total=1, status="running", current_index=0,
            current_project_id=project.id, params_json=json.dumps(params, ensure_ascii=False),
            message=f"Đã tạo Mix Set với {len(lines)} bài — bắt đầu tải bài gốc.")
        session.add(run)
        session.commit()
        pid = project.id
        source_ids = [s.id for s in sources]

    _cancel_flags[batch_key] = threading.Event()
    t = threading.Thread(
        target=_run_cover_mixset, args=(batch_key, pid, source_ids),
        name=f"mixset-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()
    return get_batch(batch_key)


def _mixset_message(batch_key: str, message: str, **extra) -> None:
    with Session(engine) as session:
        run = session.exec(select(BatchRun).where(
            BatchRun.batch_key == batch_key)).first()
        if run:
            _touch(session, run, message=message, **extra)


def _wait_cover_worker(batch_key: str, status_fn, ev: threading.Event,
                       timeout_sec: float, label: str, prefix: str = "") -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if ev.is_set():
            return False
        state = status_fn()
        if not state.get("busy"):
            return True
        msg = state.get("message") or label
        _mixset_message(batch_key, f"{prefix}{label}: {msg}")
        time.sleep(5)
    return False


class _StopAll(RuntimeError):
    """Lỗi mà bài kế tiếp cũng sẽ gặp y hệt (Suno đăng nhập/credit/CAPTCHA, máy
    tạo video bị chặn tài khoản) — Track lẻ dừng cả lô thay vì chạy tiếp."""


def _cover_chain(batch_key: str, project_id: int, source_ids: list[int],
                 ev: threading.Event, prefix: str = "") -> None:
    """NCT → tempo 4.5× → Suno Cover → giữ bản 1 → master → mix → video → nháp.
    Trả về khi project có final.mp4 + nháp YouTube; lỗi thì raise
    (_MissingLyricsWait / _StopAll / RuntimeError). Dùng chung cho Mix Set (1
    project nhiều bài) và Track lẻ (mỗi bài 1 project)."""
    from backend.cover import mastering, nct, nct_jobs, suno_jobs, tempo
    from backend.routers import mixes, tracks as track_router

    def say(message: str) -> None:
        _mixset_message(batch_key, prefix + message)

    # Resume không được chạy lại Suno/đốt credit nếu chuỗi đã tới Video/Upload.
    final_dir, project_name = _project_paths(project_id)
    if (final_dir / "final.mp4").is_file():
        if not _has_youtube_draft(project_id):
            from backend.video import service as video_service
            with Session(engine) as session:
                project = session.get(Project, project_id)
            video_job_manager.submit(
                project_id, "upload", video_service.step_upload_youtube,
                project_name, (project.instrument if project else "") or "",
                (project.music_style if project else "") or "")
            while video_job_manager.is_busy() and not ev.is_set():
                say("Đang lưu bản nháp YouTube…")
                time.sleep(5)
        ok, detail = _verify_product(project_id)
        if not ok:
            raise RuntimeError(f"Chưa hoàn tất: {detail}.")
        return

    with Session(engine) as session:
        sources = [session.get(SourceSong, sid) for sid in source_ids]
        nct_ids = [s.id for s in sources if s and
                   (not s.file_path or not Path(s.file_path).is_file()
                    or not (s.lyrics_text or "").strip())]
    if nct_ids:
        nct_jobs.submit(nct_ids)
        if not _wait_cover_worker(batch_key, nct_jobs.status, ev, 4 * 3600,
                                  "Đang tải bài gốc", prefix):
            raise RuntimeError("Dừng/hết giờ khi tải bài gốc.")
    with Session(engine) as session:
        project = session.get(Project, project_id)
        sources = [session.get(SourceSong, sid) for sid in source_ids]
        missing_lyrics = [s for s in sources if s and s.status not in _INACTIVE_SOURCE
                          and not (s.lyrics_text or "").strip()]
        if missing_lyrics:
            for src in missing_lyrics:
                src.status = "skipped_no_lyrics"
                src.error = ("Không tìm được lyrics — tự loại khỏi Mix Set, "
                             "không đưa sang Suno Cover.")
                src.tempo_factor, src.tempo_file_path = 1.0, ""
                session.add(src)
            session.commit()
            say(f"Đã tự loại {len(missing_lyrics)} bài không tìm được lyrics.")
        sources = [s for s in sources if s and s.status not in _INACTIVE_SOURCE]
        if not sources:
            raise RuntimeError("Không còn bài nào có lyrics để tiếp tục Mix Set.")
        missing_files = [s.query for s in sources if not s.file_path
                         or not Path(s.file_path).is_file()]
        if missing_files:
            raise RuntimeError("Chưa tải đủ file bài gốc: " +
                               "; ".join(missing_files[:5]))
        auto_tempo = nct_jobs.AUTO_TEMPO_FACTOR
        say(f"Đã tải đủ bài gốc — đang xuất tempo {auto_tempo:g}×.")
        for src in sources:
            adjusted = Path(src.tempo_file_path) if src.tempo_file_path else None
            if abs(float(src.tempo_factor or 1.0) - auto_tempo) >= 0.0005 or not adjusted or not adjusted.is_file():
                exported = tempo.export_file(
                    Path(src.file_path), src.id, project.id, project.name,
                    src.title, src.artist, src.query, auto_tempo)
                src.tempo_factor = auto_tempo
                src.tempo_file_path = str(exported)
                src.suno_song_ids = src.suno_editor_song_id = ""
                session.add(src)
        session.commit()

    with Session(engine) as session:
        cover_ids = []
        for sid in source_ids:
            src = session.get(SourceSong, sid)
            if not src or src.status in _INACTIVE_SOURCE or not src.lyrics_text.strip():
                continue
            has_cover = session.exec(select(CoverSong).where(
                CoverSong.source_id == sid)).first() is not None
            if src.suno_status != "done" or not has_cover:
                cover_ids.append(sid)
    if cover_ids:
        suno_jobs.submit(cover_ids)
        if not _wait_cover_worker(batch_key, suno_jobs.status, ev, 12 * 3600,
                                  "Đang làm cover Suno", prefix):
            raise _StopAll("Dừng/hết giờ khi làm cover Suno.")
    with Session(engine) as session:
        sources = [session.get(SourceSong, sid) for sid in source_ids]
        bad = [s.query for s in sources if not s or s.suno_status != "done"]
        if bad:
            raise _StopAll("Một số bài Suno chưa xong: " + "; ".join(bad[:5]))
        chain_project = session.get(Project, project_id)
        keep_all_versions = bool(chain_project and
                                 getattr(chain_project, "kind", "") == "single")
        # Track lẻ (12/11/2026, người dùng chọn «Giữ cả 2 bản»): giữ đủ mọi bản
        # Suno trong project; video dùng bản 1 (chọn tường minh bên dưới).
        # Mix Set: mỗi dòng = đúng một bài trong mix, nên chỉ giữ phiên bản đầu;
        # phiên bản thứ hai đưa vào thùng rác track để vẫn khôi phục thủ công.
        for src in ([] if keep_all_versions else sources):
            songs = session.exec(select(CoverSong).where(
                CoverSong.source_id == src.id).order_by(CoverSong.position,
                                                        CoverSong.id)).all()
            for extra in songs[1:]:
                track = session.get(Track, extra.track_id)
                if track:
                    track_router._trash_track(session, track)
        session.commit()

    deadline = time.time() + 2 * 3600
    while mastering.is_busy() and time.time() < deadline and not ev.is_set():
        say("Đang master các bản cover đã chọn…")
        time.sleep(5)
    if ev.is_set():
        raise RuntimeError("Đã huỷ.")
    with Session(engine) as session:
        project = session.get(Project, project_id)
        is_single = bool(project and (getattr(project, "kind", "") == "single"))
        project_ctx = {
            "name": (project.name if project else "") or "Cover",
            "idea": (project.video_idea if project else "") or "",
            "style": (project.video_style if project else "") or None,
        }
    if is_single:
        # Track lẻ: tuyệt đối không render Mix một bài. Dùng trực tiếp cover đã
        # master rồi chuyển sang Video; service tự lấy đúng Track/CoverSong.
        from backend.video import service as video_service
        audio = _first_cover_audio(source_ids) or video_service.find_latest_audio(
            project_id, project_ctx["name"])
        if not audio:
            raise RuntimeError("Track lẻ chưa có file cover/master để dựng video.")
        say("Đã đủ cover (bài lẻ) — bản Audio dừng ở đây, không dựng video.")
        from backend.edition import AUDIO_ONLY
        if not AUDIO_ONLY:
          video_job_manager.submit(
            project_id, "full", video_service.run_full_video,
            project_ctx["name"], audio, None, project_ctx["name"],
            project_ctx["idea"] or None, project_ctx["style"],
        )
    else:
        say("Đã đủ cover — bắt đầu ghép Mix Set.")
        mixes.start_mix_for_project(project_id, force_video=True)

    idle = 0
    deadline = time.time() + 12 * 3600
    while time.time() < deadline and not ev.is_set():
        busy = job_manager.is_busy() or video_job_manager.is_busy()
        idle = 0 if busy else idle + 1
        say("Đang ghép nhạc/dựng video…" if busy
            else "Đang chờ bàn giao sang bước tiếp theo…")
        if idle >= 3:
            break
        time.sleep(5)
    ok, detail = _verify_product(project_id)
    if not ok:
        acct = _account_block(project_id)
        if acct:
            raise _StopAll("Máy tạo video (Muse/Flow/Gemini) chặn/giới hạn ở mức "
                           f"tài khoản — {acct[:200]}")
        raise RuntimeError(
            f"Chuỗi chưa hoàn tất ({detail}); mở project rồi bấm Tiếp tục.")


def _first_cover_audio(source_ids: list[int]) -> Optional[str]:
    """File của BẢN 1 (vị trí nhỏ nhất) thuộc bài đầu tiên trong lô.

    Track lẻ giữ cả 2 bản Suno; nếu chọn theo mtime thì video có thể lấy
    ngẫu nhiên bản 2. Luôn dùng bản 1 cho video để kết quả ổn định."""
    with Session(engine) as session:
        for sid in source_ids:
            songs = session.exec(select(CoverSong).where(
                CoverSong.source_id == sid).order_by(CoverSong.position,
                                                     CoverSong.id)).all()
            for song in songs:
                track = session.get(Track, song.track_id) if song.track_id else None
                if track and track.filepath and Path(track.filepath).is_file():
                    return track.filepath
    return None


def _run_cover_mixset(batch_key: str, project_id: int,
                      source_ids: list[int]) -> None:
    """Mix Set: 1 project nhiều bài → 1 video."""
    ev = _cancel_flags.get(batch_key) or threading.Event()
    try:
        _cover_chain(batch_key, project_id, source_ids, ev)
        _finish(batch_key, "completed", "Hoàn tất Mix Set end-to-end.")
    except _MissingLyricsWait as exc:
        _finish(batch_key, "blocked", f"Mix Set dừng an toàn: {exc}")
        _schedule_auto_skip(batch_key)
    except Exception as exc:  # noqa: BLE001
        if ev.is_set():
            _finish(batch_key, "cancelled", "Đã huỷ Mix Set an toàn.")
        else:
            _finish(batch_key, "blocked", f"Mix Set dừng an toàn: {exc}")


_COVER_KINDS = ("cover_mixset", "cover_singles")
_INACTIVE_SOURCE = ("skipped_no_lyrics", "excluded")


def _batch_sources(session: Session, batch_key: str) -> list[SourceSong]:
    """Mọi bài (SourceSong) của các project thuộc lô, theo project rồi vị trí."""
    return session.exec(
        select(SourceSong).join(Project, SourceSong.project_id == Project.id)
        .where(Project.batch_id == batch_key, Project.deleted_at == None)  # noqa: E711
        .order_by(Project.id, SourceSong.position, SourceSong.id)).all()


def create_cover_singles(song_lines: list[str], music_style: str,
                         video_style: str, theme: str = "", *, music_line: str = "",
                         channel_id: Optional[int] = None,
                         intro_mode: str = "thumbnail",
                         cast_mode: str = "character_pet",
                         footage_scene_mode: str = "auto",
                         creative_instrument: bool = True) -> dict:
    """Track lẻ: mỗi dòng trong danh sách → 1 project riêng (1 bài, 1 ảnh bìa, 1
    video, 1 nháp YouTube). Chạy tuần tự từng bài; phong cách ảnh xoay vòng theo
    kênh để mỗi video một vẻ."""
    from backend.cover.style_prompt import NO_CREATIVE_INSTRUMENT
    from backend.video import channel_looks, config as vconfig
    from backend.video.service import _lookup_channel_by_id

    music_style = (music_style or "").strip()
    music_line = (music_line or "").strip()
    lines = [" ".join((x or "").strip().split()) for x in song_lines]
    lines = list(dict.fromkeys(x for x in lines if x))
    if not music_style:
        raise RuntimeError("Thiếu thể loại nhạc cho Track lẻ.")
    if not 1 <= len(lines) <= 50:
        raise RuntimeError("Danh sách Track lẻ phải có từ 1 đến 50 bài.")
    if any(not re.search(r"\s[-–—]\s", x) for x in lines):
        raise RuntimeError(
            "Mỗi dòng phải có dạng «Tên bài - Ca sĩ» hoặc «Ca sĩ - Tên bài».")
    if _active_batch_key():
        raise RuntimeError("Đang có một Mix Set/lô sản xuất chạy — đợi xong hoặc huỷ trước.")
    chosen = _lookup_channel_by_id(channel_id) if channel_id else None
    if channel_id and not chosen:
        raise RuntimeError("Kênh YouTube đã chọn không còn trong Cài đặt.")
    channel_id = chosen["id"] if chosen else None
    styles_cycle: list[str] = []
    if (video_style or "").strip().lower() in ("", "channel"):
        m = channel_looks.mapping_for("", music_style, channel_id=channel_id,
                                      music_line=music_line)
        if m:
            recent = channel_looks.recent_channel_styles(m)
            styles_cycle = [channel_looks.style_for_channel(m, recent, offset=i)
                            for i in range(len(lines))]
    style_key = (styles_cycle[0] if styles_cycle else channel_looks.resolve_video_style(
        video_style, "", music_style, channel_id=channel_id, music_line=music_line))
    intro_mode = vconfig.normalize_intro_mode(intro_mode)
    cast_mode = vconfig.normalize_cast_mode(cast_mode)
    footage_scene_mode = (footage_scene_mode if footage_scene_mode in
                          ("auto", "character_screen", "screen_only") else "auto")
    batch_key = uuid.uuid4().hex
    params = {
        "kind": "cover_singles", "song_count": len(lines),
        "music_style": music_style, "music_line": music_line,
        "video_style": ("channel:" + ",".join(dict.fromkeys(styles_cycle))
                        if styles_cycle else style_key),
        "theme": theme, "channel_id": channel_id,
        "intro_mode": intro_mode, "cast_mode": cast_mode,
        "footage_scene_mode": footage_scene_mode,
        "creative_instrument": bool(creative_instrument),
    }
    instrument = "" if creative_instrument else NO_CREATIVE_INSTRUMENT
    theme = (theme or "").strip()
    with Session(engine) as session:
        first_id = None
        for i, line in enumerate(lines):
            project = Project(
                name=line, description=theme, video_idea=theme,
                video_style=styles_cycle[i] if styles_cycle else style_key,
                instrument="", music_style=music_style, music_line=music_line,
                channel_id=channel_id, intro_mode=intro_mode, cast_mode=cast_mode,
                footage_scene_mode=footage_scene_mode,
                creative_instrument=bool(creative_instrument),
                music_source="local", auto_video=True, auto_upload=True,
                batch_id=batch_key, kind="single")
            session.add(project)
            session.flush()
            first_id = first_id or project.id
            session.add(SourceSong(project_id=project.id, position=1, query=line,
                                   status="queued", featured_instrument=instrument))
        run = BatchRun(
            batch_key=batch_key, total=len(lines), status="running", current_index=0,
            current_project_id=first_id,
            params_json=json.dumps(params, ensure_ascii=False),
            message=f"Đã tạo {len(lines)} project Track lẻ — bắt đầu bài 1.")
        session.add(run)
        session.commit()

    _cancel_flags[batch_key] = threading.Event()
    t = threading.Thread(target=_run_cover_singles, args=(batch_key,),
                         name=f"singles-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()
    return get_batch(batch_key)


def _run_cover_singles(batch_key: str) -> None:
    """Chạy lần lượt từng project Track lẻ. Bài lỗi riêng (không tải được, thiếu
    lời…) → ghi lại rồi sang bài kế; lỗi chung (Suno/máy video bị chặn) → dừng lô."""
    ev = _cancel_flags.get(batch_key) or threading.Event()
    with Session(engine) as session:
        projects = session.exec(
            select(Project).where(Project.batch_id == batch_key,
                                  Project.deleted_at == None)  # noqa: E711
            .order_by(Project.id)).all()
        plan = []
        for p in projects:
            ids = [s.id for s in session.exec(select(SourceSong).where(
                SourceSong.project_id == p.id,
                SourceSong.status.notin_(list(_INACTIVE_SOURCE)))
                .order_by(SourceSong.position, SourceSong.id)).all()]
            plan.append((p.id, p.name, ids))
    total = len(plan)
    done = failed = 0
    waiting: list[str] = []
    errors: list[str] = []

    def update(**kw) -> None:
        with Session(engine) as session:
            run = session.exec(select(BatchRun).where(
                BatchRun.batch_key == batch_key)).first()
            if run:
                _touch(session, run, **kw)

    def cancelled(idx: int) -> None:
        _finish(batch_key, "cancelled", f"Đã huỷ Track lẻ ở bài {idx + 1}/{total}.")

    for idx, (pid, name, source_ids) in enumerate(plan):
        if ev.is_set():
            return cancelled(idx)
        prefix = f"Bài {idx + 1}/{total} «{name}»: "
        update(current_index=idx, current_project_id=pid, message=prefix + "bắt đầu.")
        if not source_ids:
            failed += 1
            errors.append(f"«{name}»: đã bỏ qua (thiếu lời)")
            update(failed=failed)
            continue
        if _verify_product(pid)[0]:
            done += 1
            update(completed=done, message=prefix + "đã xong từ trước.")
            continue
        if not _wait_until_idle(ev, timeout_sec=900):
            if ev.is_set():
                return cancelled(idx)
            _finish(batch_key, "blocked", prefix + "worker bận quá lâu trước khi bắt đầu.")
            return
        try:
            _cover_chain(batch_key, pid, source_ids, ev, prefix)
            done += 1
            update(completed=done, message=prefix + "xong (video + nháp YouTube).")
        except _MissingLyricsWait:
            waiting.append(name)
            update(message=prefix + "thiếu lời — để lại, làm bài kế tiếp.")
        except _StopAll as exc:
            if ev.is_set():
                return cancelled(idx)
            update(failed=failed + 1)
            _finish(batch_key, "blocked", f"Track lẻ dừng ở {prefix}{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            if ev.is_set():
                return cancelled(idx)
            failed += 1
            errors.append(f"«{name}»: {exc}")
            update(failed=failed, message=f"{prefix}lỗi — bỏ qua, làm bài kế: {exc}")

    summary = f"{done}/{total} bài xong"
    if failed:
        summary += f", {failed} bài lỗi ({'; '.join(errors[:3])[:400]})"
    if waiting:
        _finish(batch_key, "blocked",
                f"Track lẻ: {summary}. CHỜ BỔ SUNG LYRICS cho {len(waiting)} bài: "
                + "; ".join(waiting[:5])
                + ". Bấm «Bổ sung lyrics thủ công», lưu đủ rồi Tiếp tục lô.")
        _schedule_auto_skip(batch_key)
        return
    _finish(batch_key, "completed", f"Hoàn tất Track lẻ: {summary}.")


def _create_batch_rows(ideas, count, instrument, music_style, style_key, theme,
                       channel_name, tgt, mca, mix_duration_minutes,
                       styles_cycle: Optional[list[str]] = None,
                       channel_id: Optional[int] = None,
                       intro_mode: str = "thumbnail",
                       cast_mode: str = "character_pet",
                       music_line: str = "") -> dict:
    batch_key = uuid.uuid4().hex
    params = {
        "count": count, "instrument": instrument, "music_style": music_style,
        "music_line": music_line,
        "video_style": ("channel:" + ",".join(dict.fromkeys(styles_cycle))
                        if styles_cycle else style_key),
        "theme": theme, "channel_name": channel_name, "channel_id": channel_id,
        "intro_mode": intro_mode, "cast_mode": cast_mode,
        "target_tracks": tgt, "max_create_actions": mca,
        "mix_duration_minutes": float(mix_duration_minutes),
    }

    with Session(engine) as session:
        created_ids = []
        for i, it in enumerate(ideas):
            p = Project(
                name=it["project_name"],
                description=it["description"],
                video_idea=it["video_idea"],
                suno_idea=it["suno_idea"],
                video_style=(styles_cycle[i % len(styles_cycle)] if styles_cycle
                             else style_key),
                instrument=instrument,
                music_style=music_style,
                music_line=music_line,
                channel_id=channel_id,
                intro_mode=intro_mode,
                cast_mode=cast_mode,
                music_source="suno",
                auto_video=True,
                auto_upload=True,
                batch_id=batch_key,
                mix_duration_minutes=float(mix_duration_minutes),
            )
            session.add(p)
            session.commit()
            session.refresh(p)
            created_ids.append(p.id)

        run = BatchRun(
            batch_key=batch_key, total=count, status="running",
            current_index=0, current_project_id=created_ids[0],
            params_json=json.dumps(params, ensure_ascii=False),
            message="Đã tạo N project — bắt đầu chạy tuần tự.",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

    _cancel_flags[batch_key] = threading.Event()
    t = threading.Thread(target=_run_batch, args=(batch_key,),
                         name=f"batch-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()

    return get_batch(batch_key)


def cancel_batch(batch_key: str) -> Optional[dict]:
    """Yêu cầu dừng lô: set cờ + huỷ job hiện tại (Suno/Video) của project đang chạy.
    Mix đang render (job_manager, khoá theo mix_id) để chạy nốt — không kéo dài."""
    ev = _cancel_flags.get(batch_key)
    _cancel_lyrics_timer(batch_key)
    if ev is not None:
        ev.set()
    kind = ""
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        pid = run.current_project_id
        kind = json.loads(run.params_json or "{}").get("kind", "")
    if kind in _COVER_KINDS:
        from backend.cover import nct_jobs, suno_jobs
        nct_jobs.stop()
        suno_jobs.stop()
    if pid is not None:
        suno_service.request_cancel(pid)
        video_job_manager.cancel(pid)   # huỷ job suno HOẶC video của project này
    return get_batch(batch_key)


def resume_batch(batch_key: str) -> Optional[dict]:
    """Chạy TIẾP lô đã dừng (blocked / cancelled / completed còn sản phẩm lỗi /
    server khởi động lại giữa chừng). Sản phẩm đã có final.mp4 + nháp YouTube
    được bỏ qua; sản phẩm dở dang nối lại đúng bước còn thiếu (Suno resume batch
    cũ, không tạo batch Suno mới)."""
    t = _threads.get(batch_key)
    if t is not None and t.is_alive():
        raise ValueError("Lô này đang chạy — không cần tiếp tục.")
    if _chain_busy():
        raise ValueError("Đang có job Suno/Mix/Video chạy — đợi xong (hoặc huỷ) "
                         "rồi mới tiếp tục lô.")
    kind = ""
    project_id = None
    source_ids: list[int] = []
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        params = json.loads(run.params_json or "{}")
        kind = params.get("kind", "")
        project_id = run.current_project_id
        if kind == "cover_mixset" and not project_id:
            project = session.exec(select(Project).where(
                Project.batch_id == batch_key).order_by(Project.id)).first()
            project_id = project.id if project else None
        if kind == "cover_mixset" and project_id:
            sources = session.exec(
                select(SourceSong).where(SourceSong.project_id == project_id)
                .order_by(SourceSong.position, SourceSong.id)).all()
            active_sources = [s for s in sources
                              if s.status not in ("skipped_no_lyrics", "excluded")]
            missing_lyrics = [s for s in active_sources if not (s.lyrics_text or "").strip()]
            if missing_lyrics:
                names = "; ".join((s.title or s.query or f"Bài #{s.id}")
                                  for s in missing_lyrics[:5])
                raise ValueError(
                    f"Còn {len(missing_lyrics)} bài chưa có lyrics: {names}. "
                    "Hãy bấm «Bổ sung lyrics thủ công» và lưu đủ lời trước.")
            source_ids = [s.id for s in active_sources]
            params["lyrics_resume_pending"] = False
            run.params_json = json.dumps(params, ensure_ascii=False)
        if kind == "cover_singles":
            # Bài thiếu lời không chặn cả lô — chuỗi để lại bài đó, làm bài khác.
            params["lyrics_resume_pending"] = False
            run.params_json = json.dumps(params, ensure_ascii=False)
        _touch(session, run, status="running", completed=0, failed=0,
               current_project_id=project_id if kind == "cover_mixset"
               else run.current_project_id,
               message="Tiếp tục Mix Set — nối lại đúng bước còn thiếu."
               if kind == "cover_mixset" else
               "Tiếp tục Track lẻ — bài đã xong được bỏ qua, nối lại bài dở."
               if kind == "cover_singles" else
               "Tiếp tục lô — kiểm tra sản phẩm đã xong, nối lại chỗ dừng.")

    _cancel_flags[batch_key] = threading.Event()
    _cancel_lyrics_timer(batch_key)
    if kind == "cover_singles":
        t = threading.Thread(target=_run_cover_singles, args=(batch_key,),
                             name=f"singles-{batch_key[:8]}", daemon=True)
    elif kind == "cover_mixset":
        if not project_id or not source_ids:
            raise ValueError("Mix Set không còn danh sách bài để tiếp tục.")
        t = threading.Thread(target=_run_cover_mixset,
                             args=(batch_key, project_id, source_ids),
                             name=f"mixset-{batch_key[:8]}", daemon=True)
    else:
        t = threading.Thread(target=_run_batch, args=(batch_key,),
                             kwargs={"resume": True},
                             name=f"batch-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()
    return get_batch(batch_key)


def skip_missing_lyrics(batch_key: str, *, automatic: bool = False) -> Optional[dict]:
    """Bỏ qua riêng các bài vẫn thiếu lời rồi nối Mix Set với phần còn lại."""
    _cancel_lyrics_timer(batch_key)
    with Session(engine) as session:
        run = session.exec(select(BatchRun).where(
            BatchRun.batch_key == batch_key)).first()
        if not run:
            return None
        params = json.loads(run.params_json or "{}")
        if params.get("kind") not in _COVER_KINDS:
            raise ValueError("Chức năng này chỉ dùng cho Mix Set / Track lẻ cover.")
        if run.status not in ("blocked", "interrupted"):
            raise ValueError("Lô không ở trạng thái chờ bổ sung lyrics.")
        sources = _batch_sources(session, batch_key)
        if not sources:
            raise ValueError("Không tìm thấy project của lô.")
        missing = [s for s in sources if s.status not in ("skipped_no_lyrics", "excluded")
                   and not (s.lyrics_text or "").strip()]
        keep = [s for s in sources if s.status not in ("skipped_no_lyrics", "excluded")
                and (s.lyrics_text or "").strip()]
        if not missing:
            raise ValueError("Không còn bài nào thiếu lyrics để bỏ qua.")
        if not keep:
            # Không tạo một Mix Set rỗng và không tự xoá toàn bộ danh sách.
            _schedule_auto_skip(batch_key)
            raise ValueError(
                "Tất cả bài đều thiếu lyrics; cần dán lời cho ít nhất một bài trước.")
        reason = ("Tự động bỏ qua sau 5 phút không bổ sung lyrics."
                  if automatic else "Người dùng chọn bỏ qua bài thiếu lyrics.")
        for source in missing:
            source.status = "skipped_no_lyrics"
            source.error = reason
            session.add(source)
        params["lyrics_auto_skip_at"] = None
        params["lyrics_skipped"] = len(missing)
        params["lyrics_skip_mode"] = "automatic" if automatic else "manual"
        params["lyrics_resume_pending"] = bool(automatic)
        run.params_json = json.dumps(params, ensure_ascii=False)
        run.message = (f"{'Tự động' if automatic else 'Đã'} bỏ qua {len(missing)} bài "
                       "thiếu lyrics — tiếp tục Mix Set với các bài còn lại.")
        session.add(run)
        session.commit()
    if automatic:
        _schedule_resume_after_skip(batch_key)
        return get_batch(batch_key)
    try:
        return resume_batch(batch_key)
    except ValueError as exc:
        # Nếu một thao tác khác vừa nối workflow đồng thời, trả trạng thái hiện tại.
        if "đang chạy" in str(exc).lower():
            return get_batch(batch_key)
        raise


# ══════════════════════════════════════════════════════════════════
# Vòng lặp tuần tự (chạy trong daemon thread)
# ══════════════════════════════════════════════════════════════════
def _run_batch(batch_key: str, resume: bool = False) -> None:
    ev = _cancel_flags.get(batch_key) or threading.Event()

    with Session(engine) as session:
        projects = session.exec(
            select(Project).where(Project.batch_id == batch_key)
            .order_by(Project.id)).all()
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None or not projects:
            return
        params = json.loads(run.params_json or "{}")
        project_ids = [p.id for p in projects]

    overrides = {
        "target_tracks": params.get("target_tracks", 15),
        "max_create_actions": params.get("max_create_actions", 15),
        "max_new_song_downloads": params.get("target_tracks", 15),
    }

    for idx, pid in enumerate(project_ids):
        if ev.is_set():
            _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
            return

        name = f"#{pid}"
        suno_idea = ""
        with Session(engine) as session:
            run = session.exec(
                select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
            proj = session.get(Project, pid)
            if proj is not None and not getattr(proj, "deleted_at", None):
                name = proj.name
                suno_idea = proj.suno_idea or ""
                # Chốt lại cấu hình an toàn cho từng project (phòng khi bị đổi).
                proj.music_source = "suno"
                proj.auto_video = True
                proj.auto_upload = True
                session.add(proj)
                session.commit()
            if proj is None or getattr(proj, "deleted_at", None):
                # Project đã bị người dùng xoá (hoặc chuyển vào Thùng rác) giữa
                # lô → bỏ qua, không chạy Suno cho project đó.
                _touch(session, run, current_index=idx, current_project_id=pid,
                       failed=run.failed + 1,
                       message=f"Sản phẩm {idx + 1}/{len(project_ids)}: project #{pid} "
                               "đã bị xoá — bỏ qua.")
                continue
            _touch(session, run, current_index=idx, current_project_id=pid,
                   message=f"Sản phẩm {idx + 1}/{len(project_ids)}: «{name}» — bắt đầu Suno.")

        if resume and _verify_product(pid)[0]:
            with Session(engine) as session:
                run = session.exec(
                    select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
                _touch(session, run, completed=run.completed + 1,
                       message=f"Sản phẩm {idx + 1}/{len(project_ids)} «{name}» đã xong từ trước.")
            continue

        # ── Đợi worker rảnh rồi submit Suno (LIVE) cho project i ──
        if not _wait_until_idle(ev, timeout_sec=600):
            _finish(batch_key, "blocked",
                    f"Sản phẩm {idx + 1}: worker bận quá lâu trước khi bắt đầu.")
            return
        if ev.is_set():
            _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
            return

        ov = dict(overrides)
        ov["suno_idea"] = suno_idea

        # ── Chạy + TỰ THỬ LẠI sản phẩm i (resume, không tạo batch Suno mới) ──
        attempt = 0
        outcome, detail, phase = "blocked", "chưa chạy", None
        acct: Optional[str] = None
        while True:
            # Khi tiếp tục lô: nối lại batch Suno dở ngay lần đầu (_submit_stage
            # tự bỏ qua resume nếu batch đã terminal / chưa có).
            stage = _submit_stage(pid, ov, resume=(resume or attempt > 0))
            if stage is None:
                outcome, detail = "blocked", "không submit được bước tiếp theo"
                break

            outcome, detail, phase = _await_product_chain(batch_key, pid, ev)

            if outcome == "done":
                ok, why = _verify_product(pid)
                if ok:
                    break
                detail = why
                outcome = "blocked"     # thiếu final.mp4 / nháp YouTube → thử lại
                acct = _account_block(pid)
                if acct:
                    # Chặn mức tài khoản: chạy lại chỉ làm cờ bám chặt hơn →
                    # DỪNG ngay, không thử lại (người dùng xử lý rồi resume lô).
                    detail = f"Máy tạo video (Muse/Flow/Gemini) chặn/giới hạn ở mức tài khoản — {acct[:200]}"
                    break
            if outcome == "cancelled":
                break
            if _needs_human(phase, detail):
                break                   # cần người — không thử lại
            attempt += 1
            if attempt > _MAX_RETRY_PER_PRODUCT:
                break
            with Session(engine) as session:
                run = session.exec(
                    select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
                _touch(session, run,
                       message=(f"Sản phẩm {idx + 1}/{len(project_ids)} «{name}» lỗi "
                                f"({detail}) — tự chạy lại lần {attempt}/"
                                f"{_MAX_RETRY_PER_PRODUCT}."))
            if not _wait_until_idle(ev, timeout_sec=900) or ev.is_set():
                outcome, detail = "cancelled", "huỷ khi chờ chạy lại"
                break

        with Session(engine) as session:
            run = session.exec(
                select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
            if outcome == "done":
                _touch(session, run, completed=run.completed + 1,
                       message=f"Sản phẩm {idx + 1}/{len(project_ids)} xong: {detail}")
            elif outcome == "cancelled":
                _touch(session, run, message=f"Đã huỷ ở sản phẩm {idx + 1}.")
                _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
                return
            elif _needs_human(phase, detail) or acct:
                # Đăng nhập/credit/pause/Flow chặn tài khoản: sản phẩm kế cũng hỏng
                # y hệt (lại còn đốt credit Suno) ⇒ dừng lô, chờ người xử lý rồi resume.
                _touch(session, run, failed=run.failed + 1,
                       message=(f"DỪNG lô ở sản phẩm {idx + 1}/{len(project_ids)} "
                                f"«{name}»: {detail}"))
                _finish(batch_key, "blocked",
                        f"Sản phẩm {idx + 1} «{name}» bị chặn: {detail}")
                return
            else:
                # Lỗi riêng của sản phẩm này → bỏ qua, chạy tiếp sản phẩm sau.
                _touch(session, run, failed=run.failed + 1,
                       message=(f"Bỏ qua sản phẩm {idx + 1}/{len(project_ids)} "
                                f"«{name}» sau {_MAX_RETRY_PER_PRODUCT} lần thử: "
                                f"{detail}"))

    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        n_ok = run.completed if run else 0
        n_bad = run.failed if run else 0
    _finish(batch_key, "completed",
            f"Hoàn tất lô: {n_ok}/{len(project_ids)} sản phẩm xong"
            + (f", {n_bad} sản phẩm lỗi (xem từng project)." if n_bad else "."))


def _project_paths(pid: int) -> tuple[Path, str]:
    """(thư mục final, tên project)."""
    from backend.video import config as vconfig
    with Session(engine) as session:
        proj = session.get(Project, pid)
        name = proj.name if proj else ""
    return vconfig.final_dir(pid, name), name


def _has_youtube_draft(pid: int) -> bool:
    from backend.models import YoutubeUpload
    with Session(engine) as session:
        rows = session.exec(
            select(YoutubeUpload).where(YoutubeUpload.project_id == pid)).all()
    return any((r.status or "") == "draft"
               and not (r.video_path or "").endswith("short.mp4") for r in rows)


def _chain_busy() -> bool:
    """Còn bước nào của chuỗi đang chạy: worker video/Suno, mix, hoặc khâu rà
    soát → tự tạo lại Suno → vá mix (giữa các job, cả 2 manager có lúc rảnh)."""
    from backend.video import qc_autofix
    return (video_job_manager.is_busy() or job_manager.is_busy()
            or qc_autofix.is_active())


def _account_block(pid: int) -> Optional[str]:
    """Lỗi của job Video gần nhất nếu đó là chặn ở mức tài khoản, ngược lại None."""
    job = video_job_manager.get(pid)
    err = (getattr(job, "error", None) or "") if job else ""
    if job is None or job.status != "failed" or not err:
        return None
    low = err.lower()
    return err if any(m in low for m in _ACCOUNT_BLOCK_MARKERS) else None


def _audio_done(pid: int) -> tuple[bool, str]:
    """Cover Audio Tool: xong khi có mix.wav (Mix Set) hoặc cover đã có (bài lẻ)."""
    from backend.models import Mix
    with Session(engine) as session:
        proj = session.get(Project, pid)
        if proj and getattr(proj, "kind", "") == "single":
            from backend.video import service as video_service
            audio = video_service.find_latest_audio(pid, proj.name)
            return (bool(audio), "đã có file cover" if audio else "thiếu file cover")
        mixes = session.exec(select(Mix).where(Mix.project_id == pid)).all()
    ok = any((m.status or "") == "completed" and m.output_dir
             and (Path(m.output_dir) / "mix.wav").exists() for m in mixes)
    return (ok, "đã có mix.wav" if ok else "chưa có mix.wav")


def _verify_product(pid: int) -> tuple[bool, str]:
    from backend.edition import AUDIO_ONLY
    if AUDIO_ONLY:
        return _audio_done(pid)
    return _verify_video_product(pid)


def _verify_video_product(pid: int) -> tuple[bool, str]:
    """Sản phẩm CHỈ được tính là xong khi có final.mp4 VÀ đã lưu nháp YouTube —
    không tin mỗi 'job đã chạy xong' (job video có thể hỏng mà Suno vẫn
    COMPLETED, lúc đó cả 2 manager đều rảnh và dễ bị chấm nhầm là hoàn tất)."""
    final = _project_paths(pid)[0] / "final.mp4"
    if not final.exists():
        return (False, "thiếu final.mp4")
    if not _has_youtube_draft(pid):
        return (False, "chưa có bản nháp YouTube")
    return (True, "final.mp4 + bản nháp YouTube đã có")


def _submit_stage(pid: int, ov: dict, *, resume: bool) -> Optional[str]:
    """Submit ĐÚNG bước còn thiếu của project và trả tên bước.

    Chạy lại không phải lúc nào cũng là chạy lại Suno: nếu Suno đã COMPLETED mà
    hỏng ở khâu sau thì phải nối lại từ Mix / Video / Upload, nếu không sẽ đốt
    thêm credit Suno vô ích (hoặc resume lỗi vì batch đã terminal).
    """
    from backend.video import service as video_service

    st = suno_service.batch_status(pid) or {}
    phase = st.get("phase")
    imported = int(st.get("imported_tracks") or 0)

    if not (_suno_done(st) and imported > 0):
        can_resume = resume and phase not in (None, suno_service.COMPLETED,
                                              suno_service.CANCELLED)
        video_job_manager.submit(pid, "suno", suno_service.run_suno_step0,
                                 dry_run=False, overrides=ov, resume=can_resume)
        return "suno"

    final_dir, name = _project_paths(pid)
    from backend.edition import AUDIO_ONLY
    if AUDIO_ONLY:
        if _audio_done(pid)[0]:
            return "done"
        from backend.routers.mixes import start_mix_for_project
        start_mix_for_project(pid)
        return "mix"
    if not (final_dir / "final.mp4").exists():
        # Có mix chưa? Chưa thì chạy Mix (mix xong tự kéo sang Video).
        from backend.models import Mix
        with Session(engine) as session:
            proj = session.get(Project, pid)
            mixes = session.exec(select(Mix).where(Mix.project_id == pid)).all()
            idea = (proj.video_idea if proj else "") or ""
            style = (proj.video_style if proj else None)
        done_mix = [m for m in mixes
                    if (m.status or "") == "completed" and m.output_dir
                    and (Path(m.output_dir) / "mix.wav").exists()]
        if not done_mix:
            from backend.routers.mixes import start_mix_for_project
            start_mix_for_project(pid, force_video=True)
            return "mix"
        audio = str(Path(done_mix[-1].output_dir) / "mix.wav")
        # Chạy lại Video luôn ở chế độ resume: GIỮ ảnh + clip đã tạo (restart từng
        # xoá sạch 28 clip của lần trước chỉ vì bước sau lỗi).
        video_job_manager.submit(pid, "full", video_service.run_full_video,
                                 (name or "meditation"), audio, None, name,
                                 (idea or None), style, mode="resume")
        return "video"

    if not _has_youtube_draft(pid):
        with Session(engine) as session:
            proj = session.get(Project, pid)
            instrument = (proj.instrument if proj else "") or ""
            style_music = (proj.music_style if proj else "") or ""
        video_job_manager.submit(pid, "upload", video_service.step_upload_youtube,
                                 name, instrument, style_music)
        return "upload"
    return "done"


def _await_product_chain(batch_key: str, pid: int,
                         ev: threading.Event) -> tuple[str, str, Optional[str]]:
    """
    Trả (outcome, detail, suno_phase):
      • "done"      — Suno COMPLETED và cả 2 manager rảnh ổn định (chuỗi xong).
      • "blocked"   — Suno rơi vào phase blocking (login/credit/UI/...).
      • "cancelled" — người dùng huỷ lô.
    """
    start = time.monotonic()
    idle_streak = 0

    # Job vừa submit cần vài giây mới vào worker — đừng chấm "rảnh" ngay.
    grace = time.monotonic() + _SUBMIT_GRACE_SEC
    while time.monotonic() < grace and not ev.is_set():
        if _chain_busy():
            break
        time.sleep(min(1.0, _POLL_SEC))

    while True:
        if ev.is_set():
            return ("cancelled", "huỷ theo yêu cầu", None)

        batch = suno_service.batch_status(pid)
        phase = (batch or {}).get("phase")

        if time.monotonic() - start > _PER_PRODUCT_TIMEOUT_SEC:
            return ("blocked", "quá thời gian tối đa cho 1 sản phẩm — dừng an toàn.",
                    phase)

        both_idle = not _chain_busy()

        # Phase trong DB có thể còn là phase CŨ của lượt trước (vd WAITING_FOR_HUMAN)
        # trong lúc job resume vừa submit đang chạy → chỉ chốt khi worker đã rảnh.
        if phase in _BLOCKING and both_idle:
            msg = (batch or {}).get("message") or phase
            return ("blocked", f"Suno ở phase {phase} — {msg}", phase)
        if phase == suno_service.CANCELLED and both_idle:
            return ("cancelled", "batch Suno đã bị huỷ", phase)

        if both_idle and _suno_done(batch):
            idle_streak += 1
            if idle_streak >= _IDLE_STREAK_REQUIRED:
                return ("done",
                        "Suno + Mix + Video đã hoàn tất (đăng nháp nếu cấu hình đủ).",
                        phase)
        else:
            idle_streak = 0

        time.sleep(_POLL_SEC)


def _wait_until_idle(ev: threading.Event, timeout_sec: float) -> bool:
    """Đợi tới khi CẢ 2 manager rảnh (để submit an toàn). False nếu quá hạn/huỷ."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        if ev.is_set():
            return False
        if not _chain_busy():
            return True
        time.sleep(_POLL_SEC)
    return False


def _finish(batch_key: str, status: str, message: str) -> None:
    if status in ("completed", "cancelled", "failed"):
        _cancel_lyrics_timer(batch_key)
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is not None:
            extra = {}
            params = json.loads(run.params_json or "{}")
            if status == "completed" and params.get("kind") == "cover_mixset":
                extra["completed"] = run.total
            _touch(session, run, status=status, current_project_id=None,
                   message=message, **extra)
