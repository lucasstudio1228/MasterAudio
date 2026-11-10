"""FastAPI app: CORS, startup, router wiring."""
import asyncio
import os
import sys

# Fix Windows event loop TRƯỚC KHI import uvicorn / bất kỳ thứ gì dùng loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException        # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.database import create_db_and_tables  # noqa: E402
from backend.job_manager import job_manager        # noqa: E402
from backend.migrations import run_all as run_migrations  # noqa: E402
from backend.routers import mixes, projects, settings, tracks  # noqa: E402
from backend.routers.stems import router as stems_router        # noqa: E402
from backend.routers.video import router as video_router        # noqa: E402
from backend.routers.youtube import router as youtube_router    # noqa: E402
from backend.routers.suno import router as suno_router          # noqa: E402
from backend.routers.suno_wav import router as suno_wav_router  # noqa: E402
from backend.routers.track_qc import router as track_qc_router  # noqa: E402
from backend.routers.tasks import router as tasks_router        # noqa: E402
from backend.routers.storage import router as storage_router    # noqa: E402
from backend.routers.cover import router as cover_router        # noqa: E402
from backend.routers.sources import router as sources_router    # noqa: E402
from backend.stem_job_manager import stem_job_manager           # noqa: E402
from backend.video.job_manager import video_job_manager         # noqa: E402

app = FastAPI(title="Cover Audio Tool API")

_raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5184")
CORS_ORIGINS = [o.strip() for o in _raw_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    run_migrations()          # TRƯỚC create_db_and_tables
    create_db_and_tables()
    from backend.cover import nct_jobs
    repaired = nct_jobs.quarantine_legacy_invalid_sources()
    if repaired["variants"] or repaired["no_lyrics"]:
        print(f"[source] Chuẩn hoá dữ liệu cũ: {repaired['variants']} bản biến thể mở lại; "
              f"{repaired['no_lyrics']} bài thiếu lyrics.", flush=True)
    # Worker thread cần loop này để push SSE event.
    loop = asyncio.get_running_loop()
    job_manager.bind_loop(loop)
    stem_job_manager.bind_loop(loop)
    video_job_manager.bind_loop(loop)   # ← phần Video
    _start_deferred_sweep()
    _check_mastering()
    from backend import batch_service
    batch_service.restore_pending_lyrics_timers()


def _check_mastering() -> None:
    """Tạo data/master_config.json (nếu chưa có) + kiểm tra ffmpeg. Thiếu ffmpeg
    chỉ báo lỗi kèm hướng dẫn cài — tải nhạc vẫn chạy, chỉ bỏ qua bước master."""
    from backend.cover import mastering
    try:
        mastering.ensure_config_file()
    except OSError as e:
        print(f"[master] không tạo được master_config.json: {e}", flush=True)
    mastering.check_ffmpeg(log=lambda m: print(m, flush=True))


def _start_deferred_sweep(delay_sec: float = 30.0) -> None:
    """Sau khi khởi động: dọn file còn sót của các lần xoá project trước (bị
    khoá lúc xoá) + vỏ quarantine rỗng. Chạy nền, trễ 30s, lỗi thì bỏ qua."""
    import threading
    import time

    def run():
        time.sleep(delay_sec)
        try:
            from sqlmodel import Session
            from backend.database import engine
            from backend.project_cleanup import sweep_pending
            with Session(engine) as session:
                result = sweep_pending(session)
            if result["cleared"] or result["freed_bytes"]:
                print(f"[storage] Dọn sót sau xoá project: "
                      f"{result['freed_bytes'] / 1e9:.2f} GB", flush=True)
        except Exception as exc:        # noqa: BLE001
            print(f"[storage] sweep lỗi: {exc}", flush=True)
        # KHÔNG tự xoá vĩnh viễn thùng rác theo mặc định. Từng gây mất project
        # khi chỉ restart backend để nạp code. Chỉ bật khi quản trị viên chủ
        # động đặt ENABLE_AUTO_TRASH_PURGE=1.
        if os.getenv("ENABLE_AUTO_TRASH_PURGE", "0").strip() != "1":
            return
        # Khi đã opt-in: project quá 7 ngày → xoá vĩnh viễn mỗi 6 giờ.
        while True:
            try:
                from sqlmodel import Session
                from backend.database import engine
                from backend.project_trash import purge_expired
                with Session(engine) as session:
                    info = purge_expired(session)
                if info["purged"] or info["freed_bytes"]:
                    print(f"[trash] Xoá vĩnh viễn (quá hạn 7 ngày): "
                          f"{', '.join(info['purged']) or '-'} · "
                          f"{info['freed_bytes'] / 1e9:.2f} GB", flush=True)
                for err in info["errors"]:
                    print(f"[trash] chưa xoá được: {err}", flush=True)
            except Exception as exc:    # noqa: BLE001
                print(f"[trash] purge lỗi: {exc}", flush=True)
            time.sleep(6 * 3600)

    threading.Thread(target=run, name="deletion-sweep", daemon=True).start()


app.include_router(projects.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
app.include_router(mixes.router, prefix="/api")
app.include_router(settings.router)
app.include_router(stems_router)   # đã có prefix="/api" nội bộ
app.include_router(video_router)   # đã có prefix nội bộ
app.include_router(youtube_router) # đã có prefix="/api/youtube" nội bộ
app.include_router(suno_router)    # STEP 0 — Suno; prefix nội bộ
app.include_router(suno_wav_router)  # tải WAV từ link Suno, không qua bên thứ ba
app.include_router(track_qc_router)  # rà soát track lẻ + tạo lại Suno + vá mix
app.include_router(tasks_router)     # thanh tác vụ toàn cục (chỉ đọc)
app.include_router(storage_router)   # dung lượng + dọn dữ liệu thừa
app.include_router(cover_router)     # Tool Cover: lời bài hát, căn lời AI
app.include_router(sources_router)   # Tool Cover: tab ① tải nhạc gốc nhaccuatui


@app.get("/api/health")
def health():
    return {"status": "ok", "active_jobs": job_manager.active_count()}


# ─── Serve built frontend (production / distributable) ──────────────
from pathlib import Path                              # noqa: E402
from fastapi.staticfiles import StaticFiles           # noqa: E402
from fastapi.responses import FileResponse            # noqa: E402

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if (_FRONTEND_DIST / "index.html").exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    # index.html KHÔNG được cache: mỗi lần `npm run build` tên file assets đổi
    # (hash) → index.html cũ trong cache trỏ tới asset đã xoá → 404 trắng trang.
    _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}

    def _index_response():
        return FileResponse(str(_FRONTEND_DIST / "index.html"), headers=_NO_CACHE)

    @app.get("/")
    def _serve_index():
        return _index_response()

    # SPA catch-all: file tĩnh thì trả file, còn lại trả index.html.
    # /api/* không tồn tại → 404 JSON (không trả HTML 200 khiến frontend hiểu nhầm).
    @app.get("/{full_path:path}")
    def _serve_spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API không tồn tại")
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST.resolve()):
            return FileResponse(str(candidate))
        return _index_response()
