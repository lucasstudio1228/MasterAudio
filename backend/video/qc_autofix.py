"""
video/qc_autofix.py — TỰ tạo lại Suno khi rà soát track lẻ phát hiện bài lỗi.

Sau mỗi bản mix ĐẦY ĐỦ (bản «Vá mix» không đi qua đây → không lặp vô hạn):
  1. Rà soát track (track_qc, ~0.6 s/bài) — chạy nền, TRƯỚC khi dựng video.
  2. Có bài Suno ở trạng thái lỗi + công tắc «tự tạo lại» của project bật →
     job "suno-fix" (bài dự phòng cùng prompt 0 credit trước, rồi Create tối đa
     AUTO_MAX_CREATES_PER_TRACK lượt/bài; thiếu credit → dừng, không mua thêm).
  3. Thay được ≥1 bài → «Vá mix» (giữ nguyên thứ tự) → xong mới dựng video từ
     bản mix đã vá (chuỗi auto). Video đã có sẵn → mix_patch tự thay nhạc final.mp4.
  4. Không có lỗi / công tắc tắt / tạo lại hỏng → nối chuỗi như cũ bằng bản mix gốc.

Chống vòng lặp + tốn credit: 1 lượt tự sửa cho mỗi bản mix (ghi trong
track_qc.json → "auto_fix"), tối đa AUTO_MAX_TRACKS bài/lượt.
Trong lúc đang rà soát / sửa / vá, `is_active(pid)` = True để orchestrator lô
(batch_service) không tưởng chuỗi đã xong rồi tự chen bước video.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

AUTO_MAX_CREATES_PER_TRACK = 2
AUTO_MAX_TRACKS = 5
IDLE_WAIT_SEC = 20 * 60          # chờ worker video rảnh (job Suno vừa xong…)
PATCH_WAIT_SEC = 60 * 60         # chờ render «Vá mix»
_POLL = 2.0

_active: set[int] = set()
_active_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_active(pid: int, on: bool) -> None:
    with _active_lock:
        (_active.add if on else _active.discard)(pid)


def is_active(pid: Optional[int] = None) -> bool:
    with _active_lock:
        return bool(_active) if pid is None else pid in _active


# ── Công tắc + trạng thái (lưu trong track_qc.json của project) ──────────
def auto_enabled(report: dict) -> bool:
    return bool(report.get("auto_regen", True))


def set_auto_enabled(project_id: int, project_name: Optional[str], on: bool) -> None:
    from backend.video import track_qc
    with track_qc._lock:
        data = track_qc.load_report(project_id, project_name)
        data["auto_regen"] = bool(on)
        track_qc.save_report(project_id, project_name, data)


def _record(project_id: int, project_name: Optional[str], **kw) -> None:
    from backend.video import track_qc
    with track_qc._lock:
        data = track_qc.load_report(project_id, project_name)
        cur = data.get("auto_fix") if isinstance(data.get("auto_fix"), dict) else {}
        if kw.get("mix_id") is not None and cur.get("mix_id") != kw["mix_id"]:
            cur = {}
        cur.update(kw, at=_now())
        data["auto_fix"] = cur
        track_qc.save_report(project_id, project_name, data)


def pick_tracks(report: dict, suno_ids: set[int]) -> list[int]:
    """Bài cần tự tạo lại: trạng thái lỗi + do Suno tạo (có prompt để Create lại)."""
    out = []
    for tid, e in (report.get("tracks") or {}).items():
        if e.get("status") == "error" and int(tid) in suno_ids:
            out.append(int(tid))
    return out


def _suno_track_ids(project_id: int) -> set[int]:
    from sqlmodel import Session, select
    from backend.database import engine
    from backend.models import SunoCandidate
    with Session(engine) as s:
        return {c.track_id for c in s.exec(select(SunoCandidate).where(
            SunoCandidate.project_id == project_id,
            SunoCandidate.track_id != None)).all()}     # noqa: E711


def _wait_video_idle(timeout: float) -> bool:
    from backend.video.job_manager import video_job_manager
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not video_job_manager.is_busy():
            return True
        time.sleep(_POLL)
    return not video_job_manager.is_busy()


def _start_video(auto_ctx: Optional[dict], audio_path: Optional[str] = None) -> None:
    """Nối chuỗi auto: dựng video (đợi worker rảnh — job sửa vừa kết thúc)."""
    if not auto_ctx:
        return
    from backend.routers.mixes import _maybe_trigger_auto_video
    _wait_video_idle(IDLE_WAIT_SEC)
    _maybe_trigger_auto_video(auto_ctx["project_id"], auto_ctx["project_name"],
                              auto_ctx["idea"], audio_path or auto_ctx["audio_path"],
                              auto_ctx["style_key"])


# ── Điểm vào: gọi từ mixes._on_success ───────────────────────────────────
def after_mix(project_id: int, mix_id: int, auto_ctx: Optional[dict]) -> None:
    """Không chặn callback mix: đánh dấu bận NGAY rồi làm trong thread nền."""
    _set_active(project_id, True)
    threading.Thread(target=_after_mix, args=(project_id, mix_id, auto_ctx),
                     name=f"qc-autofix-{project_id}", daemon=True).start()


def _after_mix(project_id: int, mix_id: int, auto_ctx: Optional[dict],
               *, _submit: Optional[Callable] = None) -> None:
    handed_off = False      # True = job sửa đã nhận phần nối chuỗi
    try:
        handed_off = _decide(project_id, mix_id, auto_ctx, _submit=_submit)
    except Exception:       # noqa: BLE001 — bước phụ, không được làm đứt chuỗi
        handed_off = False
    finally:
        if not handed_off:
            try:
                _start_video(auto_ctx)
            finally:
                _set_active(project_id, False)


def _decide(project_id: int, mix_id: int, auto_ctx: Optional[dict],
            *, _submit: Optional[Callable] = None) -> bool:
    from sqlmodel import Session
    from backend.database import engine
    from backend.models import Project
    from backend.video import track_qc
    from backend.video.job_manager import video_job_manager

    report = track_qc.review_project(project_id, mix_id=mix_id)
    with Session(engine) as s:
        p = s.get(Project, project_id)
        pname = p.name if p else None
    bad = pick_tracks(report, _suno_track_ids(project_id))
    if not bad:
        return False
    if not auto_enabled(report):
        _record(project_id, pname, mix_id=mix_id, status="off", track_ids=bad,
                message=f"{len(bad)} bài lỗi — tự tạo lại đang TẮT, cần bấm tay.")
        return False
    prev = report.get("auto_fix") or {}
    if prev.get("mix_id") == mix_id and prev.get("status") not in (None, "off"):
        return False                       # đã tự sửa cho bản mix này rồi
    skipped = bad[AUTO_MAX_TRACKS:]
    bad = bad[:AUTO_MAX_TRACKS]
    if not _wait_video_idle(IDLE_WAIT_SEC):
        _record(project_id, pname, mix_id=mix_id, status="skipped", track_ids=bad,
                message="Worker video bận quá lâu — bỏ qua tự tạo lại, bấm tay sau.")
        return False
    note = (f" (còn {len(skipped)} bài lỗi khác — tối đa {AUTO_MAX_TRACKS} bài/lượt tự động)"
            if skipped else "")
    _record(project_id, pname, mix_id=mix_id, status="running", track_ids=bad,
            message=f"Tự tạo lại Suno cho {len(bad)} bài lỗi{note}…")
    submit = _submit or video_job_manager.submit
    submit(project_id, "suno-fix", auto_fix_job, mix_id=mix_id, track_ids=bad,
           auto_ctx=auto_ctx, project_name=pname,
           queued_message=f"Tự tạo lại Suno {len(bad)} bài lỗi (rà soát sau mix)…")
    return True


def auto_fix_job(project_id: int, progress_cb: Callable, *, mix_id: int,
                 track_ids: list[int], auto_ctx: Optional[dict],
                 project_name: Optional[str],
                 _replace: Optional[Callable] = None,
                 _patch: Optional[Callable] = None) -> dict:
    """Job video_job_manager (kind="suno-fix"). Luôn tự lo phần nối chuỗi."""
    from backend.video import suno_fix
    from backend.video.job_manager import JobCancelled
    replace = _replace or suno_fix.replace_tracks
    try:
        res = replace(project_id, progress_cb, track_ids=track_ids,
                      max_creates_per_track=AUTO_MAX_CREATES_PER_TRACK)
    except JobCancelled:
        _record(project_id, project_name, mix_id=mix_id, status="cancelled",
                message="Đã huỷ tự tạo lại — chuỗi dừng ở đây (bấm tay nếu cần).")
        _set_active(project_id, False)
        raise
    except Exception as e:      # noqa: BLE001
        _record(project_id, project_name, mix_id=mix_id, status="failed",
                message=f"Tự tạo lại lỗi: {e} — dựng video bằng bản mix gốc.")
        _continue_in_background(project_id, auto_ctx, None)
        raise
    replaced = res.get("replaced") or []
    failed = res.get("failed") or []
    if not replaced:
        _record(project_id, project_name, mix_id=mix_id, status="failed",
                replaced=0, failed=len(failed),
                message="Không tìm được bài thay đạt chuẩn — dựng video bằng bản mix gốc.")
        _continue_in_background(project_id, auto_ctx, None)
        return res
    progress_cb(f"Đã thay {len(replaced)} bài — tự «Vá mix» (giữ nguyên thứ tự)…", 100)
    try:
        from backend import mix_patch
        patch_id = (_patch or mix_patch.start_patch)(project_id)
    except Exception as e:      # noqa: BLE001
        _record(project_id, project_name, mix_id=mix_id, status="failed",
                replaced=len(replaced), failed=len(failed),
                message=f"Đã thay {len(replaced)} bài nhưng vá mix lỗi: {e}")
        _continue_in_background(project_id, auto_ctx, None)
        return res
    _record(project_id, project_name, mix_id=mix_id, status="patching",
            replaced=len(replaced), failed=len(failed), patch_mix_id=patch_id,
            message=f"Đã thay {len(replaced)} bài"
                    + (f", {len(failed)} bài chưa thay được" if failed else "")
                    + " — đang vá mix…")
    _continue_in_background(project_id, auto_ctx, patch_id, project_name, mix_id)
    return res


def _continue_in_background(project_id: int, auto_ctx: Optional[dict],
                            patch_id: Optional[int],
                            project_name: Optional[str] = None,
                            mix_id: Optional[int] = None) -> None:
    threading.Thread(target=_continue, args=(project_id, auto_ctx, patch_id,
                                             project_name, mix_id),
                     name=f"qc-autofix-cont-{project_id}", daemon=True).start()


def _continue(project_id: int, auto_ctx: Optional[dict], patch_id: Optional[int],
              project_name: Optional[str], mix_id: Optional[int]) -> None:
    try:
        audio = None
        if patch_id is not None:
            status, wav = _wait_patch(patch_id)
            if status == "completed" and wav:
                audio = wav
                _record(project_id, project_name, mix_id=mix_id, status="done",
                        message="Đã tự tạo lại + vá mix xong"
                                + (" — dựng video bằng bản mix đã vá." if auto_ctx
                                   and not _final_exists(project_id, project_name) else "."))
            else:
                _record(project_id, project_name, mix_id=mix_id, status="failed",
                        message=f"Vá mix {status} — dựng video bằng bản mix gốc.")
        # Video đã có → _on_patch_success đã tự thay nhạc final.mp4; chỉ dựng
        # video khi chưa có (chuỗi auto lần đầu).
        if not _final_exists(project_id, project_name):
            _start_video(auto_ctx, audio)
    except Exception:       # noqa: BLE001
        pass
    finally:
        _set_active(project_id, False)


def _final_exists(project_id: int, project_name: Optional[str]) -> bool:
    try:
        from backend.video import config as vconfig
        return (vconfig.final_dir(project_id, project_name) / "final.mp4").exists()
    except Exception:       # noqa: BLE001
        return False


def _wait_patch(patch_id: int) -> tuple[str, Optional[str]]:
    from sqlmodel import Session
    from backend.database import engine
    from backend.models import Mix
    end = time.monotonic() + PATCH_WAIT_SEC
    while time.monotonic() < end:
        with Session(engine) as s:
            m = s.get(Mix, patch_id)
            st = m.status if m else "missing"
            out = m.output_dir if m else None
        if st in ("completed", "failed", "cancelled", "missing"):
            wav = str(Path(out) / "mix.wav") if (st == "completed" and out) else None
            return st, wav
        time.sleep(_POLL)
    return "timeout", None
