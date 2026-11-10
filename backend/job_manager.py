"""
Job manager: 1 worker duy nhất (max_workers=1) + SSE progress qua asyncio.Queue.

Luồng: submit() chạy fn trong thread → fn gọi progress_cb → _push_sse đẩy event
vào asyncio.Queue (thread-safe qua run_coroutine_threadsafe) → SSE endpoint
consume qua stream().
"""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from backend.keep_awake import keep_awake


class JobCancelled(Exception):
    """Ném ra từ progress callback khi user bấm Cancel → dừng job hợp tác."""


@dataclass
class JobProgress:
    mix_id: int
    status: str = "pending"       # pending|running|paused|completed|failed|cancelled
    step: int = 0                 # 1-6
    step_name: str = ""
    percent: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict] = None
    finished_at: Optional[float] = None


class JobManager:
    """Global singleton. Chỉ 1 job render tại 1 thời điểm."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._jobs: dict[int, JobProgress] = {}
        self._queues: dict[int, asyncio.Queue] = {}
        self._cancels: dict[int, threading.Event] = {}
        self._pauses: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Gọi 1 lần ở startup để worker thread biết loop nào để push event."""
        self._loop = loop

    # ---------- submit / run ----------
    def submit(self, mix_id: int, fn, *args, on_success=None, on_failure=None,
               on_cancel=None, **kwargs) -> None:
        with self._lock:
            self._jobs[mix_id] = JobProgress(mix_id=mix_id, status="pending")
            self._queues.setdefault(mix_id, asyncio.Queue())
            self._cancels[mix_id] = threading.Event()
            self._pauses[mix_id] = threading.Event()
        self._executor.submit(self._run, mix_id, fn, args, kwargs,
                              on_success, on_failure, on_cancel)

    def cancel(self, mix_id: int) -> bool:
        """Yêu cầu huỷ hợp tác: đặt cờ → progress callback kế tiếp ném
        JobCancelled → job dừng. Trả True nếu job đang chạy/chờ/tạm dừng."""
        with self._lock:
            job = self._jobs.get(mix_id)
            ev = self._cancels.get(mix_id)
            pause = self._pauses.get(mix_id)
            if not job or not ev or job.status not in ("pending", "running", "paused"):
                return False
            ev.set()
            if pause is not None:
                pause.set()   # đánh thức vòng chờ pause để nó thấy cờ huỷ
            return True

    def pause(self, mix_id: int) -> bool:
        """Tạm dừng hợp tác: callback kế tiếp sẽ chặn tại bước hiện tại (giữ
        nguyên state), resume tiếp tục ngay không phải render lại từ đầu."""
        with self._lock:
            job = self._jobs.get(mix_id)
            pause = self._pauses.get(mix_id)
            if not job or not pause or job.status not in ("pending", "running"):
                return False
            pause.set()
            job.status = "paused"
            job.message = "Đã tạm dừng — bấm Tiếp tục để render tiếp"
            return True

    def resume(self, mix_id: int) -> bool:
        """Bỏ chặn để job đang tạm dừng render tiếp từ đúng bước đang dở."""
        with self._lock:
            job = self._jobs.get(mix_id)
            pause = self._pauses.get(mix_id)
            if not job or not pause or job.status != "paused":
                return False
            pause.clear()
            return True

    def _run(self, mix_id, fn, args, kwargs, on_success, on_failure, on_cancel):
        self._update(mix_id, status="running")
        try:
            with keep_awake():
                result = fn(mix_id, self._progress_callback(mix_id),
                            *args, **kwargs)
            self._update(mix_id, status="completed", percent=100.0, result=result)
            if on_success:
                on_success(mix_id, result)
        except JobCancelled:
            self._update(mix_id, status="cancelled", message="Đã huỷ")
            if on_cancel:
                on_cancel(mix_id)
            self._push_sse(mix_id, "progress", {
                "step": 0, "step_name": "cancelled",
                "percent": 0.0, "message": "Đã huỷ theo yêu cầu",
            })
        except Exception as exc:              # noqa: BLE001 - báo lỗi ra SSE + DB
            self._update(mix_id, status="failed", error=str(exc))
            if on_failure:
                on_failure(mix_id, str(exc))
            self._push_sse(mix_id, "error", {"message": str(exc)})
        finally:
            job = self.get_status(mix_id)
            self._push_sse(mix_id, "done", {
                "status": job.status if job else "unknown",
            })

    def _progress_callback(self, mix_id):
        ev = self._cancels.get(mix_id)
        pause = self._pauses.get(mix_id)

        def callback(step: int, step_name: str, percent: float, message: str = ""):
            if ev is not None and ev.is_set():
                raise JobCancelled()
            # Tạm dừng: chặn tại bước này, giữ nguyên state; vẫn cho huỷ khi chờ.
            if pause is not None and pause.is_set():
                self._update(mix_id, status="paused",
                             message="Đã tạm dừng — bấm Tiếp tục để render tiếp")
                self._push_sse(mix_id, "progress", {
                    "step": step, "step_name": step_name,
                    "percent": round(percent, 1), "message": "Đã tạm dừng",
                })
                while pause.is_set():
                    if ev is not None and ev.is_set():
                        raise JobCancelled()
                    time.sleep(0.3)
                self._update(mix_id, status="running")
            self._update(mix_id, step=step, step_name=step_name,
                         percent=percent, message=message)
            self._push_sse(mix_id, "progress", {
                "step": step, "step_name": step_name,
                "percent": round(percent, 1), "message": message,
            })
        return callback

    # ---------- state ----------
    def _update(self, mix_id, **kwargs):
        with self._lock:
            job = self._jobs.get(mix_id)
            if job:
                for key, value in kwargs.items():
                    setattr(job, key, value)
                if kwargs.get("status") in ("completed", "failed", "cancelled"):
                    job.finished_at = time.time()

    def all_jobs(self) -> list[JobProgress]:
        """Mọi job mix (kể cả đã xong) — cho thanh tác vụ toàn cục."""
        with self._lock:
            return list(self._jobs.values())

    def get_status(self, mix_id: int) -> Optional[JobProgress]:
        with self._lock:
            return self._jobs.get(mix_id)

    def is_busy(self) -> bool:
        with self._lock:
            return any(j.status in ("pending", "running", "paused")
                       for j in self._jobs.values())

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values()
                       if j.status in ("pending", "running", "paused"))

    # ---------- SSE plumbing ----------
    def _get_queue(self, mix_id: int) -> asyncio.Queue:
        with self._lock:
            return self._queues.setdefault(mix_id, asyncio.Queue())

    def _push_sse(self, mix_id, event_type, data):
        """Thread-safe: worker thread đẩy event vào queue của event loop."""
        queue = self._get_queue(mix_id)
        event = {"type": event_type, "data": data}
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        else:
            # Fallback (test không có loop): put trực tiếp nếu đang trong loop.
            try:
                queue.put_nowait(event)
            except Exception:
                pass

    async def stream(self, mix_id: int):
        """Async generator cho SSE endpoint."""
        # Replay nếu job đã kết thúc TRƯỚC KHI client connect.
        with self._lock:
            job = self._jobs.get(mix_id)
        if job:
            if job.status == "completed":
                yield {"type": "progress", "data": {
                    "step": 6, "step_name": "Completed",
                    "percent": 100.0, "message": "",
                }}
                yield {"type": "done", "data": {"status": "completed"}}
                return
            elif job.status == "failed":
                yield {"type": "error", "data": {
                    "message": job.error or "Unknown error"}}
                yield {"type": "done", "data": {"status": "failed"}}
                return
            elif job.status == "cancelled":
                yield {"type": "progress", "data": {
                    "step": 0, "step_name": "cancelled",
                    "percent": 0.0, "message": "Đã huỷ"}}
                yield {"type": "done", "data": {"status": "cancelled"}}
                return

        # Job đang chạy hoặc pending — consume từ queue bình thường.
        queue = self._get_queue(mix_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield event
                if event["type"] == "done":
                    break
            except asyncio.TimeoutError:
                yield {"type": "ping", "data": {}}     # keep-alive


# Global singleton
job_manager = JobManager()
