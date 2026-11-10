"""
tasks.py — Thanh tác vụ TOÀN CỤC: gom mọi tác vụ đang chạy/xếp hàng (worker
Video/Suno/Upload, worker Mix, tách stem, lô sản xuất) + vài tác vụ vừa xong,
để UI luôn hiện 1 thanh ở dưới dù đang xem project nào. Chỉ đọc, không đổi gì.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.database import engine, get_session
from backend.models import BatchRun, Mix, Project, SourceSong, Track, TrackStem

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

RECENT_SECONDS = 15 * 60
_ACTIVE = ("pending", "running", "paused")
_stopped_batch_keys: set[str] = set()
_auto_threads: dict[int, threading.Thread] = {}
_auto_state: dict[int, dict] = {}
_auto_cancel: dict[int, threading.Event] = {}
_auto_lock = threading.Lock()

# kind của video_job_manager → (nhãn, tab trong trang project)
_VIDEO_KIND = {
    "suno": ("Suno — tạo & tải WAV", "suno"),
    "suno-login": ("Suno — đăng nhập / kiểm tra selector", "suno"),
    "suno-fix": ("Tạo lại bài Suno lỗi", "audio"),
    "remux": ("Thay nhạc trong final.mp4", "audio"),
    "upload": ("Đăng nháp YouTube", "video"),
    "continue": ("Chạy tiếp: thiếu → ghép → nháp YouTube → Short", "video"),
    "upload_short": ("Tạo & đăng nháp YouTube Short", "video"),
    "full": ("Dựng video (ảnh → clip → ghép)", "video"),
    "rebuild": ("Dựng video từ ảnh có sẵn (clip → ghép)", "video"),
    "prompts": ("Chuẩn bị prompt video", "video"),
    "motions": ("Tạo lại prompt chuyển động", "video"),
    "clips-manual": ("Clip thủ công", "video"),
    "retitle": ("Đổi tên — cập nhật chữ thumbnail/intro", "video"),
    "thumbnail": ("Tạo thumbnail (ChatGPT / AI / font)", "video"),
    "browser-chatgpt": ("Cửa sổ ChatGPT — tự đăng nhập", "video"),
    "browser-muse": ("Cửa sổ Muse.ai — đúng profile GPM", "video"),
    "browser-gemini": ("Cửa sổ Gemini (profile của tool)", "video"),
    "browser-flow": ("Cửa sổ Flow (profile của tool)", "video"),
    "images": ("Tạo ảnh", "video"),
    "clips": ("Tạo clip", "video"),
    "assemble": ("Ghép video", "video"),
}


def _name(db: Session, cache: dict, pid) -> str:
    if pid is None:
        return ""
    if pid not in cache:
        p = db.get(Project, pid)
        cache[pid] = p.name if p else f"(project #{pid} đã xoá)"
    return cache[pid]


def _suno_counts(pid: int) -> str:
    try:
        from backend.video import suno_service
        b = suno_service.batch_status(pid) or {}
        if b.get("target_tracks"):
            return (f"tạo {b.get('selected_tracks', 0)} · tải "
                    f"{b.get('downloaded_tracks', 0)} · import "
                    f"{b.get('imported_tracks', 0)}/{b['target_tracks']}")
    except Exception:       # noqa: BLE001
        pass
    return ""


@router.get("/active")
def active_tasks(db: Session = Depends(get_session)):
    from backend.job_manager import job_manager
    from backend.stem_job_manager import stem_job_manager
    from backend.video.job_manager import video_job_manager

    now = time.time()
    names: dict = {}
    tasks, recent = [], []

    # 0a) Bộ điều phối «Tự động chạy đến cuối». Nó chỉ xếp/tiếp tục đúng chuỗi
    # hiện có; tác vụ con (Suno/Mix/Video) vẫn hiện riêng bên dưới.
    with _auto_lock:
        auto_rows = [(pid, dict(st)) for pid, st in _auto_state.items()]
    for pid, st in auto_rows:
        item = {
            "id": f"auto:{pid}", "source": "auto", "project_id": pid,
            "project_name": _name(db, names, pid), "kind": "auto-run",
            "label": "Tự động chạy đến cuối", "tab": "suno",
            "status": st.get("status", "running"),
            "percent": st.get("percent"), "message": st.get("message", ""),
            "error": st.get("error"), "detail": "",
        }
        if st.get("status") in _ACTIVE:
            tasks.append(item)
        elif st.get("finished_at") and now - st["finished_at"] <= RECENT_SECONDS:
            item["ago_seconds"] = int(now - st["finished_at"])
            recent.append(item)

    # 0) Hàng đợi Cover Suno riêng (không đi qua video_job_manager).
    try:
        from backend.cover import suno_jobs
        st = suno_jobs.status()
        if st.get("busy"):
            ids = ([st.get("current")] if st.get("current") else []) + list(st.get("pending") or [])
            src = next((db.get(SourceSong, sid) for sid in ids if sid), None)
            pid = src.project_id if src else None
            tasks.append({
                "id": "cover-suno:queue", "source": "cover-suno",
                "project_id": pid, "project_name": _name(db, names, pid),
                "kind": "cover-suno", "label": "Cover trên Suno", "tab": "suno",
                "status": "paused" if st.get("paused") else "running",
                "percent": None, "message": (st.get("message") or "")[:300],
                "error": None,
                "detail": f"còn {len(st.get('pending') or []) + (1 if st.get('current') else 0)} bài",
            })
    except Exception:       # noqa: BLE001
        pass

    # 1) Worker Video/Suno/Upload (1 luồng — pending = đang xếp hàng)
    for j in video_job_manager.all_jobs():
        label, tab = _VIDEO_KIND.get(j.kind, (j.kind, "video"))
        pid = j.project_id or None     # 0 = tác vụ chung (cửa sổ ChatGPT ở Cài đặt)
        item = {
            "id": f"video:{j.project_id}", "source": "video",
            "project_id": pid,
            "project_name": _name(db, names, pid),
            "kind": j.kind, "label": label, "tab": tab,
            "status": j.status, "percent": round(j.percent or 0, 1),
            "message": (j.message or "")[:300],
            "error": (j.error or "").split("\n")[0][:300] or None,
            "detail": _suno_counts(j.project_id)
                      if j.kind == "suno" and j.status in _ACTIVE else "",
        }
        if j.status in _ACTIVE:
            tasks.append(item)
        elif j.finished_at and now - j.finished_at <= RECENT_SECONDS:
            item["ago_seconds"] = int(now - j.finished_at)
            recent.append(item)

    # 2) Worker Mix (riêng)
    for j in job_manager.all_jobs():
        active = j.status in _ACTIVE
        if not active and not (j.finished_at and now - j.finished_at <= RECENT_SECONDS):
            continue
        mix = db.get(Mix, j.mix_id)
        pid = mix.project_id if mix else None
        item = {
            "id": f"mix:{j.mix_id}", "source": "mix", "project_id": pid,
            "project_name": _name(db, names, pid),
            "kind": "mix", "label": f"Mix audio · {mix.title}" if mix and mix.title
                                    else f"Mix audio #{j.mix_id}",
            "tab": "audio", "status": j.status,
            "percent": round(j.percent or 0, 1),
            "message": (j.message or j.step_name or "")[:300],
            "error": (j.error or "")[:300] or None, "detail": "",
        }
        if active:
            tasks.append(item)
        else:
            item["ago_seconds"] = int(now - j.finished_at)
            recent.append(item)

    # 3) Tách stem (Demucs)
    for sid, st, msg in stem_job_manager.active():
        stem = db.get(TrackStem, sid)
        pid = stem.project_id if stem else None
        tasks.append({
            "id": f"stem:{sid}", "source": "stem", "project_id": pid,
            "project_name": _name(db, names, pid), "kind": "stem",
            "label": "Tách stem (Demucs)", "tab": "audio", "status": st,
            "percent": None, "message": msg[:300], "error": None, "detail": "",
        })

    # 4) Lô sản xuất hàng loạt đang chạy (thread orchestrator còn sống)
    try:
        from backend import batch_service
        for run in db.exec(select(BatchRun).where(BatchRun.status == "running")).all():
            t = batch_service._threads.get(run.batch_key)
            if t is None or not t.is_alive():
                continue
            done = run.completed + run.failed
            tasks.append({
                "id": f"batch:{run.batch_key}", "source": "batch",
                "project_id": run.current_project_id,
                "project_name": _name(db, names, run.current_project_id),
                "kind": "batch",
                "label": f"Lô sản xuất — sản phẩm "
                         f"{min(run.current_index + 1, run.total)}/{run.total}",
                "tab": "suno", "status": "running",
                "percent": round(100 * done / run.total, 1) if run.total else 0,
                "message": (run.message or "")[:300], "error": None,
                "detail": f"{run.failed} lỗi" if run.failed else "",
            })
    except Exception:       # noqa: BLE001
        pass

    order = {"auto": 0, "batch": 1, "video": 2, "mix": 3, "stem": 4}
    tasks.sort(key=lambda t: (order.get(t["source"], 9), t["status"] == "pending"))
    recent.sort(key=lambda t: t["ago_seconds"])
    try:
        from backend.cover import suno_jobs
        cover_can_resume = bool(suno_jobs.status().get("resume_count"))
    except Exception:       # noqa: BLE001
        cover_can_resume = False
    return {"tasks": tasks, "recent": recent[:3],
            "can_resume": cover_can_resume or bool(_stopped_batch_keys)}


def _auto_update(project_id: int, **values) -> None:
    with _auto_lock:
        row = _auto_state.setdefault(project_id, {})
        row.update(values)
        if values.get("status") in ("completed", "failed", "cancelled"):
            row["finished_at"] = time.time()


def _auto_wait_idle(project_id: int) -> None:
    """Đợi thao tác hiện tại tới checkpoint/xong rồi mới nối bước kế tiếp."""
    from backend.cover import nct_jobs, suno_jobs
    from backend.job_manager import job_manager
    from backend.video.job_manager import video_job_manager

    while True:
        ev = _auto_cancel.get(project_id)
        if ev is not None and ev.is_set():
            raise RuntimeError("Đã dừng tự động chạy theo yêu cầu.")
        with _auto_lock:
            paused = _auto_state.get(project_id, {}).get("status") == "paused"
        if paused:
            time.sleep(1)
            continue
        busy = (video_job_manager.is_busy() or job_manager.is_busy()
                or suno_jobs.status().get("busy") or nct_jobs.status().get("busy"))
        if not busy:
            return
        _auto_update(project_id, status="running", percent=5.0,
                     message="Đợi bước hiện tại hoàn tất rồi tự nối bước tiếp theo…")
        time.sleep(2)


def _auto_worker(project_id: int) -> None:
    """Nối workflow bền vững từ dữ liệu thật: lô cover nếu có, nếu không thì
    Mix/Video resume. Không xoá hay tạo lại thành quả đã có."""
    from backend import batch_service
    from backend.job_manager import job_manager
    from backend.routers import mixes
    from backend.video import service as video_service
    from backend.video.job_manager import video_job_manager

    try:
        _auto_update(project_id, status="running", percent=1.0,
                     message="Đang xác định checkpoint hiện tại…", error=None)
        with Session(engine) as session:
            project = session.get(Project, project_id)
            if not project or project.deleted_at:
                raise RuntimeError(f"Project {project_id} không tồn tại.")
            project.auto_video = True
            project.auto_upload = True
            session.add(project)
            session.commit()
            session.refresh(project)
            batch_key = project.batch_id or ""
            run = (session.exec(select(BatchRun).where(
                BatchRun.batch_key == batch_key)).first() if batch_key else None)

        # Khi bấm Auto từ trạng thái tạm dừng: tiếp tục đúng checkpoint một lần.
        # Nếu người dùng bấm Tạm dừng SAU đó, vòng chờ tôn trọng lệnh và không
        # tự ý bật lại cho tới khi họ bấm Tiếp tục.
        current = video_job_manager.get(project_id)
        if current and current.status == "paused":
            video_job_manager.resume(project_id)
        from backend.cover import suno_jobs
        if suno_jobs.status().get("paused"):
            suno_jobs.resume()

        # Đang mở cửa sổ theo dõi trình duyệt: bấm auto = đóng cửa sổ đó rồi chạy.
        current = video_job_manager.get(project_id)
        if current and current.kind.startswith("browser-") \
                and current.status in _ACTIVE:
            video_job_manager.cancel(project_id)

        # Từ bất kỳ checkpoint nào cũng phải nối được. Project Mix Set vừa tạo
        # thường còn nhiều SourceSong=stopped; trước đây controller nhảy thẳng
        # vào resume_batch, thấy thiếu lyrics rồi chết ngay mà UI không báo.
        with Session(engine) as session:
            source_rows = session.exec(select(SourceSong).where(
                SourceSong.project_id == project_id).order_by(
                SourceSong.position, SourceSong.id)).all()
        if source_rows:
            from backend.cover import nct_jobs
            _auto_wait_idle(project_id)
            retry_ids = []
            for source in source_rows:
                if source.status in ("skipped_no_lyrics", "excluded", "manual"):
                    continue
                file_ok = bool(source.file_path and Path(source.file_path).is_file())
                tempo_ok = bool(source.tempo_file_path
                                and Path(source.tempo_file_path).is_file())
                lyrics_ok = bool((source.lyrics_text or "").strip())
                if source.status != "done" or not file_ok or not tempo_ok or not lyrics_ok:
                    retry_ids.append(source.id)
            if retry_ids:
                _auto_update(
                    project_id, status="running", percent=3.0,
                    message=f"Đang nối lại bước tải nhạc/lyrics/tempo cho "
                            f"{len(retry_ids)} bài…")
                nct_jobs.submit(retry_ids)
                _auto_wait_idle(project_id)
            with Session(engine) as session:
                source_rows = session.exec(select(SourceSong).where(
                    SourceSong.project_id == project_id).order_by(
                    SourceSong.position, SourceSong.id)).all()
            unresolved = [source for source in source_rows
                          if source.status not in
                          ("done", "manual", "skipped_no_lyrics", "excluded")]
            if unresolved:
                names = "; ".join(
                    (source.title or source.query or f"Bài #{source.id}")
                    for source in unresolved[:5])
                raise RuntimeError(
                    f"Còn {len(unresolved)} bài chưa hoàn tất bước tải nhạc: {names}.")

        if run:
            live = batch_service._threads.get(run.batch_key)
            if live is not None and live.is_alive():
                _auto_update(project_id, status="completed", percent=100.0,
                             message="Chuỗi end-to-end đã chạy; bộ điều phối lô sẽ đưa đến cuối.")
                return
            _auto_wait_idle(project_id)
            _auto_update(project_id, status="running", percent=10.0,
                         message="Tiếp tục lô cover từ checkpoint đã lưu…")
            batch_service.resume_batch(run.batch_key)
            _stopped_batch_keys.discard(run.batch_key)
            _auto_update(project_id, status="completed", percent=100.0,
                         message="Đã giao cho chuỗi end-to-end; phần đã xong được bỏ qua.")
            return

        _auto_wait_idle(project_id)
        with Session(engine) as session:
            project = session.get(Project, project_id)
            sources = session.exec(select(SourceSong).where(
                SourceSong.project_id == project_id)).all()
            tracks = session.exec(select(Track).where(Track.project_id == project_id)).all()
            mixes_done = session.exec(select(Mix).where(
                Mix.project_id == project_id, Mix.status == "completed")).all()

        # Project cover cũ chưa có BatchRun: tạo checkpoint bền vững rồi dùng
        # chính orchestrator chuẩn. Không làm nhánh tự động riêng dễ loạn state.
        if sources:
            missing = [s for s in sources if s.status not in
                       ("skipped_no_lyrics", "excluded")
                       and not (s.lyrics_text or "").strip()]
            if missing:
                raise RuntimeError(
                    f"Còn {len(missing)} bài chưa có lyrics — bổ sung hoặc bỏ qua "
                    "bài thiếu lời rồi bấm Tự động chạy lại.")
            key = uuid.uuid4().hex
            kind = "cover_singles" if project.kind == "single" else "cover_mixset"
            params = {"kind": kind, "auto_run": True,
                      "music_style": project.music_style,
                      "music_line": project.music_line}
            with Session(engine) as session:
                project = session.get(Project, project_id)
                project.batch_id = key
                session.add(project)
                session.add(BatchRun(
                    batch_key=key, total=1, status="blocked", current_index=0,
                    current_project_id=project_id,
                    params_json=json.dumps(params, ensure_ascii=False),
                    message="Đã tạo checkpoint — tự động chạy đến cuối."))
                session.commit()
            batch_service.resume_batch(key)
            _auto_update(project_id, status="completed", percent=100.0,
                         message="Đã khởi động chuỗi cover end-to-end.")
            return

        if not tracks:
            raise RuntimeError("Project chưa có bài gốc/track để tự động chạy.")

        # Track lẻ bỏ qua Mix. Mix Set chưa có mix.wav thì render Mix; callback
        # force_video + hai cờ trên sẽ tự nối Video và lưu nháp YouTube.
        if project.kind != "single" and not any(
                m.output_dir and (Path(m.output_dir) / "mix.wav").exists()
                for m in mixes_done):
            mixes.start_mix_for_project(project_id, force_video=True)
            _auto_update(project_id, status="completed", percent=100.0,
                         message="Đã khởi động Mix → Video → nháp YouTube.")
            return

        if video_job_manager.is_busy() or job_manager.is_busy():
            raise RuntimeError("Worker vẫn bận; hãy bấm lại sau khi bước hiện tại dừng.")
        audio = video_service.find_latest_audio(project_id, project.name)
        if not audio:
            raise RuntimeError("Không tìm thấy WAV đã master/mix để dựng video.")
        video_job_manager.submit(
            project_id, "continue", video_service.continue_from_images,
            project.name, project.name, project.video_idea or None,
            project.video_style)
        _auto_update(project_id, status="completed", percent=100.0,
                     message="Đã nối Ảnh → Clip → Video → nháp YouTube từ phần còn thiếu.")
    except Exception as exc:  # noqa: BLE001
        _auto_update(project_id, status="failed", percent=0.0,
                     message="Không thể tự động nối quy trình.",
                     error=f"{type(exc).__name__}: {exc}")


@router.post("/auto-run")
def auto_run(project_id: Optional[int] = None, db: Session = Depends(get_session)):
    """Từ bất kỳ checkpoint nào, tiếp tục tới final + nháp YouTube.

    project_id được UI lấy từ trang đang mở; nếu đang ở trang khác thì dùng tác
    vụ gần nhất. Chỉ bật một controller cho mỗi project để chống bấm đúp.
    """
    if project_id is None:
        from backend.video.job_manager import video_job_manager
        active = video_job_manager.active_jobs()
        project_id = active[0].project_id if active else None
        if project_id is None:
            run = db.exec(select(BatchRun).order_by(BatchRun.updated_at.desc())).first()
            project_id = run.current_project_id if run else None
    project = db.get(Project, project_id) if project_id is not None else None
    if not project or project.deleted_at:
        raise HTTPException(400, "Hãy mở một project hoặc chọn tác vụ rồi bấm Tự động chạy.")
    with _auto_lock:
        old = _auto_threads.get(project.id)
        if old is not None and old.is_alive():
            return {"ok": True, "project_id": project.id, "status": "already_running"}
        thread = threading.Thread(target=_auto_worker, args=(project.id,),
                                  name=f"auto-run-{project.id}", daemon=True)
        _auto_threads[project.id] = thread
        _auto_cancel[project.id] = threading.Event()
        _auto_state[project.id] = {"status": "pending", "percent": 0.0,
                                   "message": "Đã xếp hàng tự động chạy đến cuối.",
                                   "error": None}
        thread.start()
    return {"ok": True, "project_id": project.id, "status": "started"}


@router.post("/pause-all")
def pause_all():
    """Tạm dừng mọi worker có checkpoint hợp tác, không xoá state đang làm."""
    from backend.cover import suno_jobs
    from backend.job_manager import job_manager
    from backend.video.job_manager import video_job_manager

    changed = 0
    with _auto_lock:
        for pid, st in _auto_state.items():
            if st.get("status") in ("pending", "running"):
                st["status"] = "paused"
                st["message"] = "Đã tạm dừng chuỗi tự động tại checkpoint hiện tại."
    changed += int(suno_jobs.pause())
    for j in video_job_manager.all_jobs():
        if j.status in ("pending", "running"):
            changed += int(video_job_manager.pause(j.project_id))
    for j in job_manager.all_jobs():
        if j.status in ("pending", "running"):
            changed += int(job_manager.pause(j.mix_id))
    return {"ok": True, "changed": changed}


@router.post("/stop-all")
def stop_all(db: Session = Depends(get_session)):
    """Dừng mọi quy trình đang thực hiện; dữ liệu trung gian được giữ để resume."""
    from backend import batch_service
    from backend.cover import nct_jobs, suno_jobs
    from backend.job_manager import job_manager
    from backend.video.job_manager import video_job_manager

    changed = 0
    with _auto_lock:
        for pid, ev in _auto_cancel.items():
            if not ev.is_set():
                ev.set()
                st = _auto_state.get(pid, {})
                if st.get("status") in _ACTIVE:
                    st.update(status="cancelled", message="Đã dừng chuỗi tự động.")
    if suno_jobs.status().get("busy"):
        suno_jobs.stop(); changed += 1
    if nct_jobs.status().get("busy"):
        nct_jobs.stop(); changed += 1
    # Mix/Video: giữ thread + checkpoint để nút Tiếp tục nối đúng thao tác.
    # Đây là "dừng xử lý" chứ không huỷ/xoá kết quả trung gian.
    for j in video_job_manager.all_jobs():
        if j.status == "running":
            changed += int(video_job_manager.pause(j.project_id))
    for j in job_manager.all_jobs():
        if j.status == "running":
            changed += int(job_manager.pause(j.mix_id))
    for run in db.exec(select(BatchRun).where(BatchRun.status == "running")).all():
        _stopped_batch_keys.add(run.batch_key)
        changed += int(batch_service.cancel_batch(run.batch_key) is not None)
    return {"ok": True, "changed": changed}


@router.post("/resume-all")
def resume_all(db: Session = Depends(get_session)):
    """Tiếp tục worker pause; Cover Suno còn tiếp tục được cả sau Dừng lại."""
    from backend.cover import suno_jobs
    from backend.job_manager import job_manager
    from backend.video.job_manager import video_job_manager

    changed = suno_jobs.resume()
    with _auto_lock:
        for st in _auto_state.values():
            if st.get("status") == "paused":
                st["status"] = "running"
                st["message"] = "Đã tiếp tục chuỗi tự động."
    for j in video_job_manager.all_jobs():
        if j.status == "paused":
            changed += int(video_job_manager.resume(j.project_id))
    for j in job_manager.all_jobs():
        if j.status == "paused":
            changed += int(job_manager.resume(j.mix_id))
    # Mix Set bị Dừng dùng resume bền vững: bước đã xong được bỏ qua.
    from backend import batch_service
    for key in list(_stopped_batch_keys):
        run = db.exec(select(BatchRun).where(BatchRun.batch_key == key)).first()
        if run is not None and run.status != "running":
            if batch_service.resume_batch(key) is not None:
                _stopped_batch_keys.discard(key)
                changed += 1
    return {"ok": True, "changed": changed}
