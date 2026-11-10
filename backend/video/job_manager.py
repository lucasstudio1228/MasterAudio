"""
video/job_manager.py — Queue 1 worker cho tác vụ Video (Playwright rất nặng,
không chạy song song). SSE progress theo project_id. Mẫu giống stem_job_manager.
"""
from __future__ import annotations
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from backend.keep_awake import keep_awake


class JobCancelled(Exception):
    """Ném từ progress callback khi user bấm Cancel → dừng job hợp tác."""


def _reap_thread_browsers() -> None:
    """Job đã xong mà luồng worker còn phiên Playwright sống = phiên RÒ → dọn
    ngay, kẻo job sau (cùng luồng) lỗi «Sync API inside the asyncio loop»."""
    try:
        from .browser_base import reap_leaked_playwright
        reap_leaked_playwright("job kết thúc")
    except Exception:       # noqa: BLE001
        pass


@dataclass
class VideoJob:
    project_id: int
    kind: str                      # images | clips | assemble | full
    status: str = "pending"        # pending|running|paused|completed|failed|cancelled
    percent: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


class VideoJobManager:
    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="video_worker")
        self._jobs: dict[int, VideoJob] = {}
        self._queues: dict[int, asyncio.Queue] = {}
        self._cancels: dict[int, threading.Event] = {}
        self._pauses: dict[int, threading.Event] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop): self._loop = loop

    def is_busy(self) -> bool:
        with self._lock:
            return any(j.status in ("pending", "running", "paused")
                       for j in self._jobs.values())

    def active_jobs(self) -> list[VideoJob]:
        """Các job chưa kết thúc (running/paused trước, pending = đang xếp hàng)."""
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.status in ("pending", "running", "paused")]
        return sorted(jobs, key=lambda j: j.status == "pending")

    def get(self, project_id: int) -> Optional[VideoJob]:
        with self._lock:
            return self._jobs.get(project_id)

    def submit(self, project_id: int, kind: str, fn, *args,
               queued_message: str = "", **kwargs) -> None:
        from backend.edition import ALLOWED_JOB_KINDS, AUDIO_ONLY
        if AUDIO_ONLY and kind not in ALLOWED_JOB_KINDS:
            # Cover Audio Tool không có phần Video/YouTube → bỏ qua job này.
            print(f"[audio-only] bỏ qua job «{kind}» của project {project_id} "
                  "(bản Audio không dựng video).", flush=True)
            return
        with self._lock:
            self._jobs[project_id] = VideoJob(project_id=project_id, kind=kind,
                                              message=queued_message)
            self._queues.setdefault(project_id, asyncio.Queue())
            self._cancels[project_id] = threading.Event()
            self._pauses[project_id] = threading.Event()
        self._executor.submit(self._run, project_id, fn, args, kwargs)

    def cancel(self, project_id: int) -> bool:
        """Yêu cầu huỷ hợp tác: đặt cờ → progress callback kế tiếp ném
        JobCancelled → dừng ở ranh giới bước/ảnh/clip gần nhất. Job đang tạm
        dừng cũng huỷ được (bỏ chặn pause để callback ném JobCancelled)."""
        with self._lock:
            job = self._jobs.get(project_id)
            ev = self._cancels.get(project_id)
            pause = self._pauses.get(project_id)
            if not job or not ev or job.status not in ("pending", "running", "paused"):
                return False
            ev.set()
            if pause is not None:
                pause.set()   # đánh thức vòng chờ pause để nó thấy cờ huỷ
            return True

    def pause(self, project_id: int) -> bool:
        """Tạm dừng hợp tác: callback kế tiếp sẽ chặn tại checkpoint (giữ
        nguyên state trong RAM), resume tiếp tục ngay không phải chạy lại."""
        with self._lock:
            job = self._jobs.get(project_id)
            pause = self._pauses.get(project_id)
            if not job or not pause or job.status not in ("pending", "running"):
                return False
            pause.set()
            job.status = "paused"
            job.message = "Đã tạm dừng — bấm Tiếp tục để chạy tiếp"
            return True

    def resume(self, project_id: int) -> bool:
        """Bỏ chặn để job đang tạm dừng chạy tiếp từ đúng checkpoint."""
        with self._lock:
            job = self._jobs.get(project_id)
            pause = self._pauses.get(project_id)
            if not job or not pause or job.status != "paused":
                return False
            pause.clear()
            return True

    def _run(self, project_id, fn, args, kwargs):
        self._update(project_id, status="running")
        try:
            with keep_awake():
                result = fn(project_id, self._progress(project_id),
                            *args, **kwargs)
            self._update(project_id, status="completed", percent=100.0,
                         result=result if isinstance(result, dict) else None)
            self._push(project_id, {"type": "done", "status": "completed",
                                    "result": result if isinstance(result, dict) else None})
        except JobCancelled:
            self._update(project_id, status="cancelled", message="Đã huỷ")
            self._push(project_id, {"type": "progress",
                                    "message": "Đã huỷ theo yêu cầu",
                                    "percent": 0.0})
            self._push(project_id, {"type": "done", "status": "cancelled"})
        except Exception as exc:                          # noqa: BLE001
            import traceback
            msg = str(exc).strip()
            detail = f"{type(exc).__name__}: {msg}" if msg else \
                     f"{type(exc).__name__} (không có message)"
            detail += "\n" + traceback.format_exc()[-800:]
            self._update(project_id, status="failed", error=detail)
            self._push(project_id, {"type": "error", "message": detail})
            self._push(project_id, {"type": "done", "status": "failed"})
        finally:
            _reap_thread_browsers()

    def _progress(self, project_id):
        ev = self._cancels.get(project_id)
        pause = self._pauses.get(project_id)

        def cb(message: str, percent: float):
            if ev is not None and ev.is_set():
                raise JobCancelled()
            # Tạm dừng: chặn tại checkpoint này, giữ nguyên state; vẫn cho phép
            # huỷ (ev) trong lúc chờ. Resume → pause.clear() → chạy tiếp.
            if pause is not None and pause.is_set():
                self._update(project_id, status="paused",
                             message="Đã tạm dừng — bấm Tiếp tục để chạy tiếp")
                self._push(project_id, {"type": "progress",
                                        "message": "Đã tạm dừng",
                                        "percent": round(percent, 1)})
                while pause.is_set():
                    if ev is not None and ev.is_set():
                        raise JobCancelled()
                    time.sleep(0.3)
                self._update(project_id, status="running")
            self._update(project_id, message=message, percent=percent)
            self._push(project_id, {"type": "progress",
                                    "message": message,
                                    "percent": round(percent, 1)})
        # Cho job kiểm cờ huỷ/tạm dừng GIỮA các thao tác chặn lâu (không đổi
        # message/percent) — xem browser_base.set_cancel_check.
        cb.is_cancelled = lambda: ev is not None and ev.is_set()
        return cb

    def _update(self, project_id, **kw):
        with self._lock:
            job = self._jobs.get(project_id)
            if job:
                for k, v in kw.items():
                    setattr(job, k, v)
                if kw.get("status") in ("completed", "failed", "cancelled"):
                    job.finished_at = time.time()

    def all_jobs(self) -> list[VideoJob]:
        """Mọi job (kể cả đã xong) — cho thanh tác vụ toàn cục."""
        with self._lock:
            return list(self._jobs.values())

    def _queue(self, project_id) -> asyncio.Queue:
        with self._lock:
            return self._queues.setdefault(project_id, asyncio.Queue())

    def _push(self, project_id, event: dict):
        q = self._queue(project_id)
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

    async def stream(self, project_id: int):
        with self._lock:
            job = self._jobs.get(project_id)
        if job and job.status in ("completed", "failed", "cancelled"):
            if job.status == "completed":
                yield {"type": "progress", "message": job.message,
                       "percent": 100.0}
                yield {"type": "done", "status": "completed",
                       "result": job.result}
            elif job.status == "cancelled":
                yield {"type": "progress", "message": "Đã huỷ", "percent": 0.0}
                yield {"type": "done", "status": "cancelled"}
            else:
                yield {"type": "error", "message": job.error or "Unknown"}
                yield {"type": "done", "status": "failed"}
            return
        q = self._queue(project_id)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield event
                if event.get("type") == "done":
                    break
            except asyncio.TimeoutError:
                yield {"type": "ping"}


video_job_manager = VideoJobManager()
