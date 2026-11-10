"""
stem_job_manager.py
Queue riêng biệt với mix job_manager.
max_workers=1: Demucs rất nặng GPU/CPU, không chạy song song.
Singleton instance: stem_job_manager
"""
import asyncio, threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from backend.stem_service import separate_track

class StemJobManager:
    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="demucs_worker"
        )
        self._queues:  dict[int, asyncio.Queue] = {}
        self._futures: dict[int, object]         = {}
        self._statuses: dict[int, str]           = {}
        self._messages: dict[int, str]           = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def submit(self, stem_id: int, track_id: int,
               project_id: int, audio_path: str) -> None:
        with self._lock:
            self._queues[stem_id]   = asyncio.Queue()
            self._statuses[stem_id] = "pending"

        def _progress(msg: str):
            self._statuses[stem_id] = "running"
            self._messages[stem_id] = msg
            self._push(stem_id, {"type": "progress", "message": msg})

        def _on_done(future):
            exc = future.exception()
            if exc:
                self._statuses[stem_id] = "failed"
                self._push(stem_id, {"type": "failed",
                                     "message": str(exc)})
            else:
                self._statuses[stem_id] = "completed"
                self._push(stem_id, {"type": "completed"})

        future = self._executor.submit(
            separate_track,
            stem_id, track_id, project_id, audio_path, _progress
        )
        future.add_done_callback(_on_done)
        with self._lock:
            self._futures[stem_id] = future

    def active(self) -> list[tuple[int, str, str]]:
        """(stem_id, status, message cuối) của các job pending/running."""
        with self._lock:
            return [(sid, st, self._messages.get(sid, ""))
                    for sid, st in self._statuses.items()
                    if st in ("pending", "running")]

    def _push(self, stem_id: int, event: dict):
        if not self._loop:
            return
        q = self._queues.get(stem_id)
        if q:
            asyncio.run_coroutine_threadsafe(q.put(event), self._loop)

    async def stream(self, stem_id: int):
        """SSE generator — replay nếu đã done."""
        # Replay nếu job đã kết thúc trước khi client connect
        with self._lock:
            done_status = self._statuses.get(stem_id)
        if done_status == "completed":
            yield {"type": "completed"}
            return
        if done_status == "failed":
            yield {"type": "failed", "message": "Separation failed"}
            return

        q = self._queues.get(stem_id)
        if not q:
            yield {"type": "failed", "message": "Job not found"}
            return

        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=60.0)
                yield event
                if event["type"] in ("completed", "failed"):
                    break
            except asyncio.TimeoutError:
                yield {"type": "ping"}

    def get_pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for f in self._futures.values()
                if not f.done()
            )

    def is_processing(self) -> bool:
        with self._lock:
            return any(
                s == "running"
                for s in self._statuses.values()
            )

stem_job_manager = StemJobManager()
