"""
cover/suno_jobs.py — Hàng đợi tự làm cover trên Suno (tab ② Suno Cover, đợt B).

Mỗi bài: mở suno.com/create → đính file đã chỉnh tốc độ → Open in Editor →
Tempo 32/35 theo thể loại → Save as New Song → Remix → Cover → v6, Max Mode,
Weirdness 10, Style Influence 75, Audio Influence 50, Variety Off, giọng
Nam/Nữ theo ca sĩ gốc → tên bài (không kèm ca sĩ) + lời → Create (Suno ra 2
bản) → tải/giải mã liên tục 2 asset thành WAV → nhập cover → kiểm tra lỗi.

1 trình duyệt duy nhất (profile riêng .browser_profile_suno) cho cả hàng đợi VÀ
cửa sổ «Mở Suno để đăng nhập». Trạng thái từng bài lưu ở SourceSong.suno_status:
  queued | running | done | tested (chạy thử xong, chưa Create) | rejected (Suno
  từ chối bài gốc) | failed | stopped | redo (bản cũ lỗi, chờ tạo lại)

An toàn credit: id 2 bài Suno vừa tạo được lưu NGAY sau Create → thử lại chỉ TẢI
về, không bấm Create lần nữa. Lỗi tài khoản (chưa đăng nhập, captcha không ai
giải, không chắc Suno nhận lệnh, hết credit/lượt tải, giao diện đổi) → dừng CẢ
hàng đợi, không bao giờ tự bấm Create lại.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from backend.database import DATA_DIR, DB_PATH, engine
from backend.models import CoverSettings, Project, SourceSong
from backend.video import config as vconfig
from backend.video.browser_base import OperationCancelled, set_cancel_check, wait_ms
from backend.video.suno_driver import (SunoBudgetError, SunoCaptchaRequired,
                                       SunoDownloadLimit, SunoGenerationFailed,
                                       SunoLoginRequired,
                                       SunoSubmissionUncertain, SunoUIChanged)

from . import mastering, nct, nct_jobs
from .suno_cover import (REJECT_RE, TERMS_RE, CoverDriver, SunoCoverRejected,
                         editor_tempo_for_genre)
from .music_line import lyrics_language
from .style_prompt import (enforce_vocal_first_styles, generate_cover_styles,
                           style_signature)

LOGIN_FILE = DATA_DIR / "suno_login.json"
# Chế độ BÁN THỦ CÔNG (2026-11-09): người dùng tự chỉnh Tempo + Save as New Song
# trong Suno Editor và tự bấm Create; tool làm mọi bước còn lại.
WORKFLOW_FILE = DATA_DIR / "suno_workflow.json"
LOGIN_HOLD_SECONDS = 1800
VERSIONS = 2                    # mỗi lượt Create Suno ra 2 bản
MIN_COVER_SEC = 60              # bản tải về ngắn hơn → coi là hỏng
FIRST_DOWNLOAD_WAIT_SEC = 90    # chờ Suno làm xong bài trước khi xin rights/audio
DOWNLOAD_PASSES = 4
DOWNLOAD_RETRY_SEC = 60
WORK_STEM = "_suno"             # ban_cover/_suno/ — nhật ký + ảnh chụp chẩn đoán
# ban_cover/_suno/_downloads.json: {song_id Suno: tên WAV đã tải đúng ID}. Chỉ
# file ghi ĐÚNG id mới được dùng lại — file của lượt Create cũ không bao giờ bị
# nhận nhầm là bản của lượt mới.
DOWNLOAD_LEDGER = "_downloads.json"


def _read_ledger(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_ledger(path: Path, ledger: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass

# Lỗi của TÀI KHOẢN / lượt Create — dừng cả hàng, chờ người dùng xem.
HALT_ERRORS = (SunoLoginRequired, SunoCaptchaRequired, SunoSubmissionUncertain,
               SunoBudgetError, SunoDownloadLimit, SunoUIChanged)
STOPPED_MSG = "Đã dừng — bấm «▶ Tiếp tục chạy cover trên Suno» để làm tiếp."

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="suno")
# Lyrics được chuẩn hóa cục bộ trước khi điền form; không mở thêm Playwright và
# không truyền lời sang ChatGPT, nên chỉ cần một executor cho hàng đợi Suno.
_lock = threading.Lock()
_pending: deque[int] = deque()
_dry: set[int] = set()          # bài xếp hàng ở chế độ «Chạy thử» (không Create)
_stop = threading.Event()
_pause = threading.Event()
_resume_ids: set[int] = set()   # các bài bị Dừng — cho phép Tiếp tục không mất state
# Bài bị Suno chặn lyrics: worker chính vẫn đánh dấu rejected/chuyển bài theo
# quy tắc an toàn. Khi người dùng xác nhận đã thay bằng lyrics tự sáng tác/được
# phép, một lượt "watch" chỉ theo dõi 2 song mới — tuyệt đối không bấm Create.
_manual_watch: set[int] = set()
_manual_baselines: dict[int, set[str]] = {}
# mode: None | "cover" | "login";  human: việc đang chờ NGƯỜI DÙNG làm trên Suno
_state: dict = {"mode": None, "current": None, "message": "", "draining": False,
                "login_scheduled": False, "human": "", "dry_run": False}
_last_create = 0.0              # time.monotonic() của lượt Create gần nhất
# Bảng cam «lyrics contain copyrighted material» sau Create: tool chỉ BÁO người
# dùng (bíp + popup + đưa cửa sổ Suno lên). Người dùng tự sửa lyrics và tự bấm
# Create trong cửa sổ Suno; tool chỉ theo dõi 2 bản mới rồi tải về. Tool KHÔNG tự
# điền/sửa lyrics và KHÔNG tự bấm Create lại.
MANUAL_WAIT_SEC = 1800
MANUAL_POLL_MS = 2000
MAX_LYRICS_RETRIES = 5
_lyrics_event = threading.Event()        # người dùng bấm «Bỏ qua bài này»
_lyrics_wait: dict = {"sid": None, "why": "", "attempt": 0, "since": None}


class _CopyrightToast(RuntimeError):
    """Bảng cam Suno báo lyrics bản quyền ngay sau Create."""


class Busy(RuntimeError):
    pass


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


def get_workflow() -> dict:
    try:
        data = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {"semi_manual": bool(data.get("semi_manual", False))}


def set_workflow(semi_manual: bool) -> dict:
    WORKFLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW_FILE.write_text(json.dumps({"semi_manual": bool(semi_manual)}),
                             encoding="utf-8")
    return get_workflow()


def status() -> dict:
    with _lock:
        st = dict(_state)
        pending = list(_pending)
    return {"busy": bool(st["draining"] or st["login_scheduled"] or st["mode"]),
            "paused": _pause.is_set(),
            "mode": st["mode"] or ("login" if st["login_scheduled"] else
                                   "cover" if st["draining"] else None),
            "current": st["current"], "message": st["message"],
            "pending": pending, "login": login_status(),
            "resume_count": len(_resume_ids),
            "human": st["human"], "dry_run": bool(st["dry_run"] and st["current"]),
            "awaiting_lyrics": awaiting_lyrics(),
            "semi_manual": get_workflow()["semi_manual"]}


def awaiting_lyrics() -> dict | None:
    """Bài đang CHỜ người dùng tự sửa lyrics + tự Create trên Suno (sau bảng cam)."""
    with _lock:
        if _lyrics_wait["sid"] is None:
            return None
        return {"source_id": _lyrics_wait["sid"], "why": _lyrics_wait["why"],
                "attempt": _lyrics_wait["attempt"], "since": _lyrics_wait["since"]}


def skip_lyrics(source_id: int) -> bool:
    """Người dùng chọn «Bỏ qua bài này» → đánh dấu rejected, sang bài tiếp theo."""
    with _lock:
        if _lyrics_wait["sid"] != source_id or _lyrics_event.is_set():
            return False
        _lyrics_event.set()
    return True


def _beep_human(on_human, drv=None):
    """Bíp + báo trong tool + đưa cửa sổ Suno lên để người dùng tự xử lý."""
    def alert(msg: str) -> None:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        if drv is not None:
            try:
                from backend.video.window_focus import raise_page_window
                raise_page_window(drv.page)
            except Exception:
                pass
        on_human(msg)
    return alert


def _known_ids(drv) -> set[str]:
    try:
        ids = set(drv.snapshot_song_ids())
    except Exception:
        ids = set()
    return ids | set(getattr(drv, "_gen_response_ids", ()) or ())


def _sent_count(drv) -> int:
    """Số request POST /generate đã gửi từ lúc _arm_create_watch."""
    return sum(1 for e in list(getattr(drv, "_gen_events", []) or [])
               if e == "generate_sent")


def _poll_user_ids(drv, baseline: set[str], sent0: int, seen: dict, log) -> list[str] | None:
    """Một nhịp dò 2 bản của lượt Create NGƯỜI DÙNG bấm. Có ``_gen_batches``: chỉ
    nhận ID từ response /generate thứ ``sent0`` trở đi (các lượt trước — vd. lượt
    bị bảng cam từ chối — bị loại kể cả khi ID tới muộn). Không có: so danh sách."""
    if isinstance(getattr(drv, "_gen_batches", None), list):
        batches = [list(b) for b in list(drv._gen_batches)]
        dead = set(baseline)
        for b in batches[:sent0]:
            dead.update(b)
        for b in reversed(batches[sent0:]):
            fresh = list(dict.fromkeys(i for i in b if i not in dead))
            if len(fresh) >= VERSIONS:
                log("nhận 2 bản từ lượt Create bạn vừa bấm: "
                    + ", ".join(x[:8] for x in fresh[:VERSIONS]))
                return fresh[:VERSIONS]
        if len(batches) > sent0 and seen.get("at") is None:
            seen["at"] = time.monotonic()
        # Dự phòng: Suno nhận lượt Create nhưng response không chứa ID → sau 60
        # giây dò danh sách, bỏ mọi ID của các lượt trước.
        if seen.get("at") is not None and time.monotonic() - seen["at"] > 60:
            fresh = [i for i in _known_ids(drv) if i not in dead]
            if len(fresh) >= VERSIONS:
                log("nhận 2 bản mới trong danh sách Suno: "
                    + ", ".join(x[:8] for x in fresh[:VERSIONS]))
                return fresh[:VERSIONS]
        return None
    fresh = [i for i in _known_ids(drv) if i not in baseline]
    return fresh[:VERSIONS] if len(fresh) >= VERSIONS else None


def _await_semi_create(drv, sid: int, before: set[str], log, alert) -> tuple[list[str], str]:
    """Chế độ BÁN THỦ CÔNG: form Cover đã điền đủ → chờ NGƯỜI DÙNG tự bấm Create,
    trả ID 2 bản của lượt đó. Bảng cam bản quyền hiện ra → báo (popup có nút «Bỏ
    qua bài này»), loại các ID của lượt bị từ chối, chờ người dùng sửa lyrics và
    Create lại. Tool không bấm Create, không sửa lyrics. Trả (ID, chữ bảng cam
    cuối cùng đã thấy — để bước canh sau không tính lại bảng cam cũ còn đọng)."""
    arm = getattr(drv, "_arm_create_watch", None)
    if callable(arm):
        arm()                       # bắt response /generate của lượt bạn bấm
    baseline = set(before) | _known_ids(drv)
    sent0 = 0
    seen: dict = {"at": None}
    toast_seen = ""
    with _lock:
        _lyrics_event.clear()
    _mark([sid], "running", "✋ Form Cover đã điền đủ — chờ bạn kiểm tra và tự bấm "
          "Create trên Suno.", suno_song_ids="")
    log("✋ form Cover đã điền đủ lyrics/style/thông số — chờ bạn tự bấm Create "
        f"(tối đa {MANUAL_WAIT_SEC // 60} phút).")
    alert("✋ CẦN BẠN: form Cover đã điền đủ lyrics, style và thông số — kiểm tra rồi "
          "tự bấm Create trên Suno. Tool tự nhận 2 bản, tải về và làm tiếp.")
    end = time.monotonic() + MANUAL_WAIT_SEC
    try:
        while True:
            if _stop.is_set():
                raise OperationCancelled()
            if _lyrics_event.is_set():
                raise SunoCoverRejected(
                    f"Suno báo lyrics bản quyền («{toast_seen[:200]}»); bạn chọn bỏ qua bài này")
            ids = _poll_user_ids(drv, baseline, sent0, seen, log)
            if ids:
                return ids, toast_seen
            toast = _toast_text(drv)
            if toast and toast != toast_seen:
                toast_seen = toast
                # Lượt vừa bấm bị từ chối: ID của nó (kể cả tới muộn) không được nhận.
                sent0 = max(sent0, _sent_count(drv))
                seen["at"] = None
                baseline |= _known_ids(drv)
                shot = drv.save_debug("copyright_toast") if hasattr(drv, "save_debug") else ""
                log(f"bảng cam Suno: «{toast}»" + (f" (ảnh: {shot})" if shot else ""))
                with _lock:
                    _lyrics_wait.update(sid=sid, why=toast, attempt=1,
                                        since=datetime.now(timezone.utc).isoformat())
                alert("⛔ CẦN BẠN: Suno báo lyrics có bản quyền. Hãy tự sửa lyrics (tự "
                      "sáng tác/được phép) và tự bấm Create lại — tool tự nhận 2 bản mới.")
            if time.monotonic() >= end:
                raise _NeedInput(f"Hết {MANUAL_WAIT_SEC // 60} phút chờ bạn bấm Create trên "
                                 "Suno — bấm «↻ Làm lại trên Suno» khi sẵn sàng.")
            try:
                drv._wait(MANUAL_POLL_MS)
            except OperationCancelled:
                raise
            except Exception:
                time.sleep(MANUAL_POLL_MS / 1000)
    finally:
        with _lock:
            _lyrics_wait.update(sid=None, why="", attempt=0, since=None)
            _lyrics_event.clear()


def _await_manual_create(drv, sid: int, why: str, attempt: int, log, alert) -> list[str]:
    """Chờ người dùng TỰ sửa lyrics và TỰ bấm Create trong cửa sổ Suno, rồi trả
    ID 2 bản mới. Tool không điền lyrics, không bấm gì. Bỏ qua/hết giờ →
    SunoCoverRejected; Dừng → OperationCancelled."""
    baseline = _known_ids(drv)
    # Lượt Create của TOOL (bị từ chối) là response /generate đầu tiên; ID của
    # nó có thể tới MUỘN (LIVE 09:15: 36028d8b tới sau bảng cam → bị nhận nhầm
    # là bản người dùng tự Create, bản thật thứ 2 bị bỏ sót). Chỉ nhận ID từ các
    # response /generate SAU số request đã gửi trước lúc chờ.
    has_batches = isinstance(getattr(drv, "_gen_batches", None), list)
    sent0 = max(1, _sent_count(drv)) if has_batches else 0
    seen: dict = {"at": None}
    with _lock:
        _lyrics_event.clear()
        _lyrics_wait.update(sid=sid, why=why, attempt=attempt,
                            since=datetime.now(timezone.utc).isoformat())
    _mark([sid], "running", ("⛔ Suno báo lyrics có bản quyền: «" + why[:200] + "» — "
          "bạn tự sửa lyrics (tự sáng tác/được phép) và tự bấm Create trong cửa sổ "
          "Suno; tool đang theo dõi để tự nhận và tải 2 bản mới.")[:800], suno_song_ids="")
    log("⛔ Suno hiện bảng cam bản quyền — chờ bạn tự sửa lyrics và tự bấm Create "
        f"trên Suno (tối đa {MANUAL_WAIT_SEC // 60} phút).")
    alert("⛔ CẦN BẠN: Suno báo lyrics có bản quyền. Hãy tự sửa lyrics và tự bấm "
          "Create trong cửa sổ Suno — tool sẽ tự nhận và tải 2 bản mới.")
    end = time.monotonic() + MANUAL_WAIT_SEC
    try:
        while True:
            if _stop.is_set():
                raise OperationCancelled()
            if _lyrics_event.is_set():
                raise SunoCoverRejected(
                    f"Suno báo lyrics bản quyền («{why[:200]}»); bạn chọn bỏ qua bài này")
            ids = _poll_user_ids(drv, baseline, sent0, seen, log)
            if ids:
                return ids
            if time.monotonic() >= end:
                raise SunoCoverRejected(
                    f"Suno báo lyrics bản quyền («{why[:200]}») và sau "
                    f"{MANUAL_WAIT_SEC // 60} phút chưa thấy bạn Create bản mới")
            try:
                drv._wait(MANUAL_POLL_MS)
            except OperationCancelled:
                raise
            except Exception:
                time.sleep(MANUAL_POLL_MS / 1000)
    finally:
        with _lock:
            _lyrics_wait.update(sid=None, why="", attempt=0, since=None)
            _lyrics_event.clear()


def is_active(source_id: int) -> bool:
    with _lock:
        return source_id in _pending or _state["current"] == source_id


def _set_msg(msg: str) -> None:
    with _lock:
        _state["message"] = msg


def _set_human(msg: str) -> None:
    with _lock:
        _state["human"] = msg


# ── lệnh từ giao diện ────────────────────────────────────────────
def submit(source_ids: list[int], dry_run: bool = False) -> int:
    """Xếp bài vào hàng đợi làm cover. Trả số bài mới được xếp."""
    added = 0
    _pause.clear()
    with Session(engine) as s:
        for sid in source_ids:
            src = s.get(SourceSong, sid)
            with _lock:
                if sid in _pending or _state["current"] == sid:
                    continue
                # Bài Suno đã từ chối lyrics: KHÔNG bao giờ Create lại với lời cũ.
                # Chỉ lượt «theo dõi 2 bản làm tay» (không Create) được đi qua.
                if src is not None and src.suno_status == "rejected" \
                        and sid not in _manual_watch:
                    continue
                _pending.append(sid)
                _resume_ids.discard(sid)
                (_dry.add if dry_run else _dry.discard)(sid)
            if src:
                src.suno_status, src.suno_error = "queued", None
                s.add(src)
            added += 1
        s.commit()
    with _lock:
        if _pending and not _state["draining"]:
            _state["draining"] = True
            if _state["mode"] == "login" or _state["login_scheduled"]:
                _state["message"] = ("Đang chờ bạn đóng cửa sổ đăng nhập Suno rồi mới "
                                     "bắt đầu…")
            _executor.submit(_drain)
    return added


def submit_manual_watch(source_id: int) -> int:
    """Theo dõi 2 bản người dùng tự Create sau khi thay lyrics hợp lệ.

    Đây không phải đường né kiểm duyệt: giao diện/API phải yêu cầu người dùng
    xác nhận quyền sử dụng lyrics. Worker chỉ nhận ID mới, tải và nhập như lượt
    Create bình thường; không chỉnh lyrics và không tiêu credit.
    """
    with _lock:
        _manual_watch.add(source_id)
    return submit([source_id], dry_run=False)


def stop() -> None:
    """Dừng (bài đang chạy + bỏ các bài đang chờ) và đóng cửa sổ đăng nhập."""
    with _lock:
        dropped = list(_pending)
        current = _state.get("current")
        _pending.clear()
        _resume_ids.update(dropped)
        if current:
            _resume_ids.add(int(current))
        _stop.set()
        _pause.clear()
    _mark(dropped, "stopped", STOPPED_MSG)


def pause() -> bool:
    """Tạm dừng hợp tác tại checkpoint an toàn gần nhất, giữ nguyên hàng đợi."""
    with _lock:
        active = bool(_state["draining"] or _state["current"] or _pending)
        if not active:
            return False
        _pause.set()
        _state["message"] = "Đang tạm dừng ở bước an toàn gần nhất…"
    return True


def resume() -> int:
    """Tiếp tục cả hàng đang pause hoặc các bài vừa bị Dừng."""
    if _pause.is_set():
        _pause.clear()
        _set_msg("Đã tiếp tục hàng đợi Suno.")
        return 1
    with _lock:
        ids = list(_resume_ids)
        if not ids:
            return 0
        # Nếu người dùng bấm Tiếp tục ngay khi lệnh Dừng của bài hiện tại chưa
        # kịp thoát Playwright, đặt bài đó lại cuối hàng ngay trong cùng worker.
        added = 0
        for sid in ids:
            if sid not in _pending:
                _pending.append(sid)
                _dry.discard(sid)
                added += 1
        _resume_ids.difference_update(ids)
        if _pending and not _state["draining"]:
            _state["draining"] = True
            _executor.submit(_drain)
    return added


def _checkpoint() -> None:
    """Chặn tại ranh giới thao tác; vẫn nhận Dừng trong lúc đang pause."""
    announced = False
    while _pause.is_set():
        if _stop.is_set():
            raise OperationCancelled()
        if not announced:
            _set_msg("Đã tạm dừng — bấm Tiếp tục để chạy tiếp.")
            announced = True
        time.sleep(0.25)


def open_login_window() -> None:
    with _lock:
        if _state["mode"] or _state["draining"] or _state["login_scheduled"]:
            raise Busy("Trình duyệt Suno đang bận (đang làm cover hoặc cửa sổ đăng nhập "
                       "đã mở).")
        _state["login_scheduled"] = True
        _state["message"] = "Đang mở Suno…"
    _executor.submit(_login_window)


def close_login_window() -> bool:
    with _lock:
        if _state["mode"] != "login" and not _state["login_scheduled"]:
            return False
    _stop.set()
    return True


def _mark(ids: list[int], status_: str, error: str | None, **fields) -> None:
    if not ids:
        return
    with Session(engine) as s:
        for sid in ids:
            src = s.get(SourceSong, sid)
            if src:
                src.suno_status, src.suno_error = status_, error
                for k, v in fields.items():
                    setattr(src, k, v)
                s.add(src)
        s.commit()


def _halt_queue(reason: str) -> None:
    """Lỗi tài khoản/giao diện → các bài đang chờ không làm nữa."""
    with _lock:
        dropped = list(_pending)
        _pending.clear()
    _mark(dropped, "stopped",
          f"Chưa làm — hàng đợi dừng vì bài trước gặp lỗi: {reason}"[:500])


# ── trình duyệt ──────────────────────────────────────────────────
def _open_browser():
    from backend.video.browser_base import BrowserSession
    # Gắn site rõ ràng để cấu hình trình duyệt riêng của Suno có hiệu lực.
    # Nếu bỏ `site`, resolver sẽ rơi về Cốc Cốc mặc định và trình duyệt này đã
    # được ghi nhận tự đóng ngay sau hộp thoại upload trên Suno.
    bs = BrowserSession(headless=False, profile_dir=vconfig.SUNO_PROFILE_DIR,
                        site="suno")
    bs.__enter__()
    return bs


@contextmanager
def _browser():
    bs = _open_browser()
    try:
        yield bs
    finally:
        nct_jobs._close_browser(bs)


def _ensure_page(bs, page):
    """(phiên, tab) còn sống; người dùng lỡ đóng cả cửa sổ → mở lại trình duyệt."""
    if bs is not None:
        try:
            return bs, nct_jobs._live_page(bs, page)
        except Exception:
            nct_jobs._close_browser(bs)
    _set_msg("Đang mở trình duyệt Suno…")
    bs = _open_browser()
    return bs, nct_jobs._live_page(bs, None)


def _drain() -> None:
    set_cancel_check(_stop.is_set)
    bs, page = None, None
    try:
        while True:
            _checkpoint()
            with _lock:
                if not _pending:
                    _state.update(draining=False, mode=None, current=None, human="")
                    break
                sid = _pending.popleft()
                _resume_ids.discard(sid)
                dry = sid in _dry
                _dry.discard(sid)
                _state.update(mode="cover", current=sid, human="", dry_run=dry)
                _stop.clear()     # cùng khoá với stop() → không lỡ lệnh dừng
            try:
                bs, page = _ensure_page(bs, page)
            except Exception as e:  # noqa: BLE001
                bs = None
                traceback.print_exc()
                with _lock:
                    rest = [sid, *_pending]
                    _pending.clear()
                _mark(rest, "failed", f"Không mở được trình duyệt Suno: {e}"[:500])
                _set_msg(f"Lỗi mở trình duyệt Suno: {e}")
                continue
            def reopen_page():
                """Cốc Cốc có thể tự đóng sau một lượt tải WAV; mở phiên mới
                ngay trong cùng worker để tiếp tục ID còn thiếu."""
                nonlocal bs, page
                nct_jobs._close_browser(bs)
                bs, page = _ensure_page(None, None)
                return page

            page = _process(sid, page, dry, reopen_page=reopen_page)
    finally:
        set_cancel_check(None)
        nct_jobs._close_browser(bs)
        with _lock:
            _state.update(draining=False, mode=None, current=None, human="",
                          dry_run=False)
            if _pending:      # có bài mới xếp vào đúng lúc đang đóng → chạy tiếp
                _state["draining"] = True
                _executor.submit(_drain)


# ── 1 bài ────────────────────────────────────────────────────────
def guess_vocal_gender(title: str, artist: str) -> str:
    """Hỏi AI kiểu giọng chính → male | female | duet | "".

    ``duet`` chỉ dùng khi bài thật sự có cả giọng nam và nữ, không chỉ vì tên
    nghệ sĩ có ``feat.``. Người dùng chọn tay ở tab ② thì không gọi hàm này.
    """
    if not artist.strip():
        return ""
    try:
        _checkpoint()
        from openai import OpenAI

        from backend.core_bridge import get_api_config_from_db
        api_config = get_api_config_from_db(str(DB_PATH))
        if not api_config.api_key:
            return ""
        client = OpenAI(**api_config.to_client_kwargs())
        model = getattr(api_config, "model", None) or "gpt-4o-mini"
        if model.lower().startswith("claude"):
            model = "gpt-4o-mini"
        r = client.chat.completions.create(
            model=model, temperature=0, max_tokens=8, timeout=30,
            messages=[{"role": "user", "content":
                       f"Giọng hát chính của bài «{title}» do «{artist}» thể hiện là "
                       "nam, nữ hay song ca nam-nữ? Chỉ trả lời đúng 1 từ: male, "
                       "female, duet hoặc unknown. Chỉ chọn duet khi cả nam và nữ "
                       "đều thực sự hát trong bài."}])
        ans = (r.choices[0].message.content or "").strip().lower()
    except Exception:
        return ""
    return ("duet" if ans.startswith("duet") else
            "female" if ans.startswith("female") else
            "male" if ans.startswith("male") else "")


def _append_log(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except Exception:
        pass


def _pace(drv, log) -> None:
    """Giữ khoảng cách tối thiểu giữa 2 lượt Create (giống tool cũ)."""
    gap = float(getattr(vconfig.SUNO, "create_min_gap_sec", 30) or 0)
    left = gap - (time.monotonic() - _last_create)
    if _last_create and left > 0:
        log(f"chờ {int(left)} giây giữa 2 lượt Create…")
        drv._wait(left * 1000)


def _transform_lyrics_isolated(lyrics: str, log) -> str:
    """API tương thích cũ: chuẩn hóa cục bộ, không mở/gửi lên ChatGPT."""
    from .suno_lyrics import normalize_for_suno
    result = normalize_for_suno(lyrics)
    if log:
        log("Bước 6: chuẩn hóa lyrics cục bộ; giữ khoảng trắng tự nhiên, không dùng dấu _.")
    return result


def _process(sid: int, page, dry: bool, reopen_page=None):
    """Làm cover cho 1 bài. Trả page dùng tiếp."""
    global _last_create
    with _lock:
        manual_watch = sid in _manual_watch
        _manual_watch.discard(sid)
        manual_baseline = set(_manual_baselines.get(sid, set()))
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return page
        project = s.get(Project, src.project_id)
        cover_settings = s.exec(select(CoverSettings).where(
            CoverSettings.project_id == src.project_id)).first()
        # Lớp bảo vệ dữ liệu: nguồn web hoặc cache bước 6 cũ có thể còn dòng
        # "Bài hát: Tên - Ca sĩ" / "Bài_hát:_...". Xóa trước khi đọc, lưu và
        # trước mọi lượt Create để Suno không hát/hiểu nhầm dòng metadata này.
        from .lyrics import strip_lyrics_header
        src.lyrics_text = strip_lyrics_header(src.lyrics_text or "").strip()
        src.lyrics_suno_text = strip_lyrics_header(src.lyrics_suno_text or "").strip()
        src.suno_status, src.suno_error = "running", None
        s.add(src)
        s.commit()
        pid, pname = src.project_id, (project.name if project else None)
        title = (src.title or src.query or f"Bài {sid}").strip()
        artist, lyrics = src.artist or "", src.lyrics_text or ""
        lyrics_suno = src.lyrics_suno_text or ""
        cached_styles = src.suno_style_prompt or ""
        cached_style_sig = src.suno_style_signature or ""
        preserve_style = bool(src.suno_preserve_style)
        original = Path(src.file_path) if src.file_path else None
        adjusted = Path(src.tempo_file_path) if src.tempo_file_path else None
        gender = src.vocal_gender or ""
        featured_instrument = src.featured_instrument or ""
        editor_bpm = int(src.suno_editor_bpm or 0) or editor_tempo_for_genre(
            project.music_style if project else "",
            project.description if project else "",
            cover_settings.cover_style if cover_settings else "")
        project_music_style = project.music_style if project else ""
        project_description = project.description if project else ""
        project_cover_style = cover_settings.cover_style if cover_settings else ""
        project_music_line = (project.music_line or "") if project else ""
        # Ngôn ngữ hát = ngôn ngữ LỜI GỐC (đọc trước bước 6), không phải dòng nhạc.
        lyrics_lang = lyrics_language(src.lyrics_text or "", project_music_line)
        editor_song_id = src.suno_editor_song_id or ""
        song_ids = [x for x in (src.suno_song_ids or "").split(",") if x.strip()]
    semi = get_workflow()["semi_manual"]
    label = title[:50]
    out_dir = nct_jobs.cover_dir(pid, pname)
    work = out_dir / WORK_STEM
    log_file = work / "_suno.log"

    def log(m: str) -> None:
        _set_msg(f"{label}: {m}")
        _append_log(log_file, f"[{label}] {m}")

    def on_human(m: str) -> None:
        _set_human(m)
        _set_msg(f"{label}: {m}")

    drv = CoverDriver(page, dry_run=dry, selectors=vconfig.get_selectors("suno"),
                      log=log, on_human=on_human)
    drv.debug_dir = work / "_debug"
    latest_drv = [drv]
    before: set[str] = set()

    def reopen_driver():
        if reopen_page is None:
            return None
        new_drv = CoverDriver(
            reopen_page(), dry_run=dry, selectors=vconfig.get_selectors("suno"),
            log=log, on_human=on_human)
        new_drv.debug_dir = work / "_debug"
        latest_drv[0] = new_drv
        return new_drv
    try:
        if manual_watch:
            drv.open_create_page()
            record_login("logged_in")
            if not manual_baseline:
                manual_baseline = drv.snapshot_song_ids()
            msg = ("📝 Đang chờ 2 bản Suno bạn tạo thủ công bằng lyrics tự sáng tác/"
                   "được phép. Hãy thay lyrics trong cửa sổ Suno và bấm Create; "
                   "tool sẽ tự nhận, tải WAV, nhập vào project rồi tiếp tục automation.")
            on_human(msg)
            songs = drv.wait_for_new_songs(
                manual_baseline, VERSIONS, timeout_sec=1800, accept_sec=None,
                captcha_wait_sec=vconfig.SUNO.captcha_wait_sec, on_human=on_human,
                captcha_beep_sec=vconfig.SUNO.captcha_beep_sec)
            _set_human("")
            song_ids = [x.song_id for x in songs][:VERSIONS]
            _mark([sid], "running", None, suno_song_ids=",".join(song_ids))
            with _lock:
                _manual_baselines.pop(sid, None)
            log("✅ Đã nhận đủ 2 bản tạo thủ công; đang tải WAV và nhập tự động…")
            drv._wait(FIRST_DOWNLOAD_WAIT_SEC * 1000)
            files = _download_all(
                drv, song_ids, out_dir, title, log,
                reopen_driver=reopen_driver if reopen_page is not None else None)
            _checkpoint()
            drv = latest_drv[0]
            mastering.submit(files, expected=len(song_ids),
                             log=lambda m: _append_log(log_file, f"[{label}] {m}"))
            _attach(sid, files, len(song_ids), log)
            log("✅ Tiếp nhận 2 bản thủ công thành công; automation được tiếp tục.")
            return drv.page

        # Bước 6 chỉ cần trước một lượt Create mới. Resume tải theo song_ids không
        # chuẩn hóa/điền lại lyrics và không đụng form Suno.
        if not song_ids:
            # Chặn ngay tại worker (lớp bảo vệ cuối): tuyệt đối không nhờ ChatGPT
            # tự sinh nội dung thay thế và không mở form Suno khi thiếu lời gốc.
            if not lyrics.strip():
                raise _NeedInput("Bài chưa có lời — dán lời ở tab ① trước.")
            # Bước 6 (2026-11-08): tool tự chuẩn hoá lời theo quy tắc cho Suno
            # (bỏ nhãn đoạn, font nghệ thuật → chữ thường, bỏ «_», gộp dấu câu,
            # ngắt câu dài tại dấu phẩy) — không còn gửi lên ChatGPT. Luôn tính lại
            # từ lời gốc nên bản cũ có «_» đã lưu cũng được thay bằng bản mới.
            from .suno_lyrics import normalize_for_suno
            suno_text = normalize_for_suno(lyrics)
            if not suno_text.strip():
                raise _NeedInput("Lời bài hát chỉ toàn nhãn/ký tự trang trí — sửa lời ở tab ① trước.")
            if suno_text != lyrics_suno:
                _mark([sid], "running", None, lyrics_suno_text=suno_text)
            log(f"Bước 6: đã chuẩn hoá lời cho Suno ngay trong tool "
                f"({sum(1 for x in suno_text.splitlines() if x.strip())} câu, "
                f"{suno_text.count(chr(10) * 2) + 1} đoạn).")
            lyrics = suno_text
        if song_ids and not dry:
            log("đã Create trên Suno từ lần trước — chỉ tải lại các bản (không bấm "
                "Create lần nữa, không tốn credit).")
            drv.open_create_page()
            record_login("logged_in")
        else:
            if original is None or not original.exists():
                raise _NeedInput("Chưa có file bài gốc — tải ở tab ① (hoặc nhập file bài gốc) "
                                 "rồi bấm «↻ Làm lại trên Suno».")
            if adjusted is None or not adjusted.exists():
                raise _NeedInput("Chưa xuất file đã chỉnh tempo — vào tab ① chọn tốc độ "
                                 "và bấm «Xuất file chỉnh tempo» trước.")
            if not lyrics.strip():
                raise _NeedInput("Bài chưa có lời — dán lời ở tab ① trước.")
            if gender not in ("male", "female", "duet"):
                gender = guess_vocal_gender(title, artist)
                if gender:
                    _mark([sid], "running", None, vocal_gender=gender)
                    cast_vi = {"male": "nam", "female": "nữ",
                               "duet": "song ca nam–nữ"}[gender]
                    log(f"AI phân tích giọng hát gốc là {cast_vi}"
                        " (sửa được ở tab ②).")
            # API AI phân tích riêng từng thể loại. Cache chỉ hợp lệ khi toàn bộ
            # dữ liệu ảnh hưởng prompt (genre/mô tả/cover style/giọng) còn giống.
            from backend.core_bridge import get_api_config_from_db
            api_config = get_api_config_from_db(str(DB_PATH))
            base_sig = style_signature(
                project_music_style, project_description, project_cover_style, gender,
                featured_instrument, project_music_line, lyrics_lang)
            expected_sig = ("ai:" if getattr(api_config, "api_key", None)
                            else "fallback:") + base_sig
            if preserve_style and cached_styles.strip():
                styles = cached_styles.strip()
                log("Styles bước 7: chế độ tạo lại chỉ đổi Tempo — giữ nguyên "
                    "toàn bộ prompt Styles đã dùng.")
            elif cached_styles.strip() and cached_style_sig == expected_sig:
                styles = cached_styles.strip()
                log("Styles bước 7: dùng lại prompt AI đã lưu (thể loại không đổi).")
            else:
                styles, style_source = generate_cover_styles(
                    project_music_style, project_description, project_cover_style,
                    gender, title, artist, featured_instrument,
                    music_line=project_music_line, lyrics_lang=lyrics_lang,
                    api_config=api_config, log=log)
                sig_prefix = "ai:" if style_source == "ai" else "fallback:"
                _mark([sid], "running", None,
                      suno_style_prompt=styles,
                      suno_style_signature=sig_prefix + base_sig)
            # Áp dụng cả cho cache cũ/chế độ giữ Styles: bỏ mọi yêu cầu solo và
            # khóa vocal làm trung tâm trước khi điền form Suno.
            guarded_styles = enforce_vocal_first_styles(styles)
            if guarded_styles != styles:
                styles = guarded_styles
                _mark([sid], "running", None, suno_style_prompt=styles)
            log(f"Styles bước 7: {styles}")
            log("đang mở Suno…")
            if semi:
                log("workflow BÁN THỦ CÔNG: tool upload + mở Editor → BẠN chỉnh Tempo "
                    f"(gợi ý {editor_bpm}) + Save as New Song → tool Remix → Cover, "
                    "điền form → BẠN bấm Create → tool tải về, master, sang bài sau.")
            else:
                log(f"workflow Editor: đặt Tempo = {editor_bpm}, Save as New Song, "
                    "rồi Remix → Cover.")
            log("tiếp tục bước 7: dùng lyrics đã qua bước 6 để điền form Cover.")
            def remember_editor(saved_id: str) -> None:
                _mark([sid], "running", None, suno_editor_song_id=saved_id)
                log(f"đã lưu trạng thái Editor song {saved_id} để lần lỗi sau "
                    "không upload lại.")
            drv.prepare_cover(adjusted, title, lyrics, gender,
                              int(vconfig.SUNO.credits_per_create), editor_bpm=editor_bpm,
                              editor_song_id=editor_song_id,
                              styles=styles,
                              on_editor_saved=remember_editor,
                              **({"manual_editor": True,
                                  "alert": _beep_human(on_human, drv),
                                  "manual_wait_sec": MANUAL_WAIT_SEC} if semi else {}))
            # CoverDriver chỉ gán thuộc tính này sau khi đã đọc lại chính
            # control Tempo của Suno Editor và xác nhận đúng. Driver tương thích
            # cũ/test không có thuộc tính thì dùng BPM đã yêu cầu.
            actual_editor_bpm = int(getattr(drv, "last_editor_tempo", editor_bpm))
            _mark([sid], "running", None, suno_editor_bpm=actual_editor_bpm)
            log(f"đã đọc và lưu Tempo hiện tại từ Suno Editor: "
                f"{actual_editor_bpm} BPM.")
            _checkpoint()
            record_login("logged_in")
            _set_human("")
            if dry:
                shot = drv.save_debug("dryrun") or ""
                _mark([sid], "tested", "🧪 Chạy thử xong: form Cover đã điền đủ, tool "
                      "KHÔNG bấm Create." + (f" Ảnh chụp: {shot}" if shot else ""))
                log("🧪 chạy thử xong — form đã điền đủ, không bấm Create.")
                return page
            attempt, stale, manual_ids = 0, "", None
            if semi:
                _checkpoint()
                before = drv.snapshot_song_ids()
                manual_ids, stale = _await_semi_create(drv, sid, before,
                                                       log, _beep_human(on_human, drv))
                _last_create = time.monotonic()
                _set_human("")
                log(f"thấy {len(manual_ids)} bản từ lượt Create bạn bấm — tool tiếp tục "
                    "theo dõi bảng cam, tải về và master.")
            else:
                _pace(drv, log)
                _checkpoint()
            # Bộ lọc thư viện có thể che bài mới khỏi DOM. Việc xoá filter chỉ
            # là lớp dự phòng; SunoDriver còn nhận UUID trực tiếp từ response
            # /generate nên không phụ thuộc hoàn toàn vào giao diện danh sách.
            if not semi:
                try:
                    drv._clear_library_filters()
                except Exception as exc:
                    log(f"không xoá được filter thư viện trước Create: {exc}; "
                        "sẽ nhận ID từ phản hồi Create.")
                before = drv.snapshot_song_ids()
                log("bấm Create…")
                drv.click_create()
                _last_create = time.monotonic()
            while True:
                try:
                    song_ids = _create_and_watch(drv, sid, before, stale, log, on_human,
                                                 song_ids=manual_ids)
                    break
                except _CopyrightToast as toast:
                    attempt += 1
                    shot = drv.save_debug("copyright_toast") or ""
                    log(f"bảng cam Suno: «{toast}»" + (f" (ảnh: {shot})" if shot else ""))
                    if attempt > MAX_LYRICS_RETRIES:
                        raise SunoCoverRejected(
                            f"Suno báo lyrics bản quyền {attempt} lần liên tiếp: «{toast}»")
                    manual_ids = _await_manual_create(
                        drv, sid, str(toast), attempt, log, _beep_human(on_human, drv))
                    stale = str(toast)
                    _set_human("")
                    log(f"thấy {len(manual_ids)} bản mới bạn tự Create trên Suno — "
                        "tool tiếp tục nhận diện và tải về.")
                    _checkpoint()
        files = _download_all(
            drv, song_ids, out_dir, title, log,
            reopen_driver=reopen_driver if reopen_page is not None else None)
        _checkpoint()
        drv = latest_drv[0]
        # Master ở luồng riêng (tối đa 2 file song song) — không chặn hàng đợi Suno.
        mastering.submit(files, expected=len(song_ids),
                         log=lambda m: _append_log(log_file, f"[{label}] {m}"))
        _attach(sid, files, len(song_ids), log)
    except OperationCancelled:
        with _lock:
            if sid not in _pending:
                _resume_ids.add(sid)
        _mark([sid], "stopped", STOPPED_MSG)
        _set_msg("Đã dừng.")
    except _NeedInput as e:
        _mark([sid], "failed", str(e))
        log(str(e))
    except SunoCoverRejected as e:
        try:
            baseline = before or drv.snapshot_song_ids()
        except Exception:
            baseline = set()
        with _lock:
            _manual_baselines[sid] = set(baseline)
        _mark([sid], "rejected", f"{e} — không tự biến dạng lyrics hoặc Create lại. "
              "Hãy thay bằng lyrics tự sáng tác/được phép trong Suno, rồi bấm "
              "«Tôi đã thay lyrics hợp lệ — theo dõi 2 bản» để tool tự tiếp nhận.")
        log("Suno từ chối vì bản quyền — đã dừng riêng bài này, không obfuscate lyrics "
            "và không tự Create lại.")
    except SunoGenerationFailed as e:
        # Cả 2 bản 'failed to generate' (asset 404) thường là Suno chặn lyrics
        # sau khi Create. Không tự Create lại (tốn credit, gần như chắc chắn
        # hỏng tiếp) và không biến dạng lyrics: GIỮ bài ở trạng thái chờ người
        # dùng xử lý tay, hàng đợi đi tiếp bài sau.
        with _lock:
            _manual_baselines[sid] = set(before)
        why = (f"⏸ Chờ nhập thủ công — {e} Thường do Suno chặn lyrics (bản quyền/"
               "kiểm duyệt). Tool đã dừng riêng bài này, không tự Create lại. "
               "Hãy thay bằng lyrics tự sáng tác/được phép trong Suno, bấm Create, "
               "rồi chọn «Tôi đã thay lyrics hợp lệ — theo dõi 2 bản».")
        _mark([sid], "rejected", why[:800], suno_song_ids="")
        log("Suno failed to generate cả 2 bản — giữ bài chờ nhập thủ công, "
            "không tự Create lại, chuyển sang bài tiếp theo.")
    except HALT_ERRORS as e:
        why = str(e)
        if _looks_rejected(why):
            _mark([sid], "rejected", (f"Suno từ chối: {why} Không tự biến dạng lyrics "
                  "hoặc Create lại; chỉ thử lại với nội dung tự sáng tác/được phép.")[:800])
            log("Suno từ chối vì bản quyền — bỏ qua an toàn, không obfuscate lyrics.")
        else:
            if isinstance(e, SunoLoginRequired):
                record_login("logged_out")
                why = ("Chưa đăng nhập Suno — bấm «🔑 Mở Suno để đăng nhập», đăng nhập "
                       "xong bấm «↻ Làm lại trên Suno».")
            elif isinstance(e, SunoSubmissionUncertain):
                why = ("Không chắc Suno đã nhận lệnh Create — mở Suno xem thư viện: có 2 "
                       f"bản mới thì tải tay rồi «⬆ Nhập file cover», chưa có thì bấm "
                       f"«↻ Làm lại». Chi tiết: {why}")
            _mark([sid], "failed", why[:800])
            _halt_queue(why[:200])
            log(f"dừng cả hàng đợi: {why[:200]}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        why = f"{type(e).__name__}: {e}"
        if _looks_rejected(str(e)):
            _mark([sid], "rejected", (f"Suno từ chối: {e} Không tự biến dạng lyrics "
                  "hoặc Create lại; chỉ thử lại với nội dung tự sáng tác/được phép.")[:800])
            log("Suno từ chối vì bản quyền — bỏ qua an toàn, không obfuscate lyrics.")
        else:
            shot = drv.save_debug("error") or ""
            _mark([sid], "failed", why[:600] + (f" (ảnh chụp: {shot})" if shot else ""))
            log(f"lỗi: {e}")
    finally:
        _set_human("")
    return drv.page


class _NeedInput(RuntimeError):
    """Bài thiếu file gốc / lời — không mở Suno."""


def _toast_text(drv) -> str:
    """Chữ bảng cam bản quyền sau Create ('' nếu không có / driver cũ không hỗ trợ)."""
    fn = getattr(drv, "create_rejection_text", None)
    try:
        return fn() if fn else ""
    except Exception:
        return ""


def _create_and_watch(drv, sid: int, before: set[str], stale: str, log,
                      on_human, song_ids: list[str] | None = None) -> list[str]:
    """Sau khi bấm Create: nhận diện 2 bản mới rồi canh bảng cam bản quyền suốt
    thời gian chờ Suno làm bài. Có bảng cam → _CopyrightToast (form còn nguyên).
    ``stale``: chữ bảng cam còn đọng từ lượt trước — chỉ tính khi nó đã biến mất
    rồi hiện lại. ``song_ids``: đã có sẵn (người dùng tự Create) → bỏ bước chờ."""
    seen_clear = [not stale]

    def toast_now() -> str:
        t = _toast_text(drv)
        if not t:
            seen_clear[0] = True
            return ""
        return t if seen_clear[0] else ""

    try:
        songs = None if song_ids else drv.wait_for_new_songs(
            before, VERSIONS, timeout_sec=300,
            accept_sec=vconfig.SUNO.create_accept_sec,
            captcha_wait_sec=vconfig.SUNO.captcha_wait_sec, on_human=on_human,
            captcha_beep_sec=vconfig.SUNO.captcha_beep_sec)
    except (SunoSubmissionUncertain, SunoCaptchaRequired, SunoGenerationFailed) as e:
        t = toast_now()
        if t:
            raise _CopyrightToast(t) from e
        if re.search(r"couldn.?t generate|copyrighted material", str(e), re.I):
            m = re.search(r"«([^»]+)»", str(e))
            raise _CopyrightToast(m.group(1) if m else str(e)[:300]) from e
        raise
    _set_human("")
    song_ids = list(song_ids or [x.song_id for x in songs])[:VERSIONS]
    t = toast_now()
    if t:
        raise _CopyrightToast(t)
    # Lưu NGAY: lỗi ở bước tải → thử lại chỉ tải, không Create lại.
    _mark([sid], "running", None, suno_song_ids=",".join(song_ids))
    log(f"Suno đang làm {len(song_ids)} bản — chờ ~{FIRST_DOWNLOAD_WAIT_SEC} giây "
        "rồi tải về (vẫn canh bảng cam bản quyền)…")
    # Đếm theo nhịp chờ của driver (1 giây/nhịp, mỗi nhịp đọc bảng cam một lần).
    for _ in range(max(1, int(FIRST_DOWNLOAD_WAIT_SEC))):
        drv._wait(1000)
        t = toast_now()
        if t:
            raise _CopyrightToast(t)
    return song_ids


def _looks_rejected(text: str) -> bool:
    return bool(REJECT_RE.search(text or "")) and not TERMS_RE.search(text or "")


def _download_all(drv, song_ids: list[str], out_dir: Path, title: str,
                  log, reopen_driver=None) -> list[Path]:
    """Tải WAV của từng ID vừa Create bằng rights của player Suno.

    Không qua Studio và không thu PCM realtime: tải asset mã hóa liên tục,
    giải mã AES-CTR rồi decode một lần sang WAV. Driver cũ trong test/plugin
    vẫn được hỗ trợ qua ``download_wav`` khi chưa có ``download_asset_wav``.
    Trả các file HỢP LỆ; file hỏng cất vào ban_cover/_loi."""
    from backend.routers.sources import REJECT_STEM, _unique
    from backend.video import suno_verify

    stem = nct.safe_name(f"{title} - cover", 80)
    got: dict[str, Path] = {}
    last_err: dict[str, str] = {}
    generation_failed: set[str] = set()
    skip_retry_wait = False

    def browser_is_closed(current_drv) -> bool:
        try:
            page = current_drv.page
            if page.is_closed():
                return True
            # Đọc pages cũng ném TargetClosedError khi context/browser đã chết.
            list(page.context.pages)
            return False
        except Exception:
            return True

    def reopen_after_close(current_drv):
        if reopen_driver is None or not browser_is_closed(current_drv):
            return current_drv
        replacement = reopen_driver()
        if replacement is None:
            return current_drv
        log("Cốc Cốc đã đóng sau lượt tải — đã tự mở phiên Suno mới để "
            "tải tiếp ID còn thiếu.")
        return replacement

    # Một browser có thể đóng ngay sau khi route handler ghi WAV xong, trước
    # khi job kịp attach vào DB. Resume nhận lại file hợp lệ ĐÃ GHI ĐÚNG ID trong
    # sổ tải (không ghép theo thứ tự: WAV của lượt Create cũ vẫn nằm trong thư
    # mục để so sánh, tuyệt đối không được nhận nhầm là bản của ID mới).
    ledger_path = out_dir / WORK_STEM / DOWNLOAD_LEDGER
    ledger = _read_ledger(ledger_path)
    for song_id in song_ids:
        name = ledger.get(song_id)
        path = out_dir / name if name else None
        if path is None or not path.is_file():
            continue
        res = suno_verify.verify_wav(path, minimum_duration_seconds=MIN_COVER_SEC)
        if res.valid:
            got[song_id] = path
            log(f"dùng lại WAV hợp lệ đã tải trước đó: {path.name} "
                f"(đúng ID {song_id[:8]}, không tải lại).")

    for attempt in range(DOWNLOAD_PASSES):
        todo = [i for i in song_ids if i not in got and i not in generation_failed]
        if not todo:
            break
        if attempt and not skip_retry_wait:
            log(f"chờ {DOWNLOAD_RETRY_SEC} giây rồi tải lại {len(todo)} bản…")
            try:
                drv._wait(DOWNLOAD_RETRY_SEC * 1000)
            except OperationCancelled:
                raise
            except Exception as wait_exc:  # noqa: BLE001
                # LIVE 2026-11-09: Cốc Cốc đóng giữa lúc chờ tải lại → lỗi
                # TargetClosedError làm hỏng cả bài dù bản 1 đã tải xong.
                if not browser_is_closed(drv):
                    raise
                replacement = reopen_after_close(drv)
                if replacement is drv:
                    log("Cốc Cốc đã đóng trong lúc chờ tải lại và không mở lại được "
                        "— giữ các bản đã tải, ID còn thiếu tải lại sau.")
                    for song_id in todo:
                        last_err[song_id] = f"browser đóng khi chờ tải lại: {wait_exc}"[:300]
                    break
                drv = replacement
        skip_retry_wait = False
        for song_id in todo:
            log(f"đang tải bản {song_ids.index(song_id) + 1}/{len(song_ids)} "
                f"từ ID {song_id[:8]} bằng asset liên tục của Suno…")
            dest = _unique(out_dir / f"{stem}.wav")
            ledger[song_id] = dest.name      # ghi trước: browser có thể đóng giữa chừng
            _write_ledger(ledger_path, ledger)
            try:
                direct = getattr(drv, "download_asset_wav", None)
                if callable(direct):
                    downloaded = direct(song_id, dest)
                    path = Path(downloaded.get("path", dest)
                                if isinstance(downloaded, dict) else downloaded)
                else:  # tương thích driver giả/cũ; production luôn đi nhánh trên
                    path = Path(drv.download_wav(song_id, dest, timeout_sec=240))
                if path.name != dest.name and (out_dir / path.name).is_file():
                    ledger[song_id] = path.name
                    _write_ledger(ledger_path, ledger)
            except (OperationCancelled, SunoLoginRequired):
                raise
            except SunoGenerationFailed as e:
                generation_failed.add(song_id)
                last_err[song_id] = str(e)[:300]
                removed = "404" in str(e)
                log(f"bản {song_id[:8]} " + ("đã bị Suno gỡ (404)" if removed else
                    "đã failed to generate") + " — bỏ qua ID hỏng.")
                continue
            except Exception as e:  # noqa: BLE001
                closed = "target page, context or browser has been closed" in str(e).casefold()
                # File có thể đã được route handler ghi xong rồi browser mới
                # đóng. Xác minh ngay trước khi kết luận lượt tải thất bại.
                if dest.is_file():
                    recovered = suno_verify.verify_wav(
                        dest, minimum_duration_seconds=MIN_COVER_SEC)
                    if recovered.valid:
                        got[song_id] = dest
                        log(f"browser đóng sau khi tải nhưng WAV đã hoàn chỉnh: "
                            f"{dest.name} — giữ file, không tải lại.")
                    else:
                        last_err[song_id] = str(e)[:300]
                else:
                    last_err[song_id] = str(e)[:300]
                # Không rơi về thu PCM realtime vì có thể tạo khe ngắt/tiếng nổ.
                # Lượt sau xin rights mới rồi tải lại từ đầu asset.
                if closed and reopen_driver is not None:
                    try:
                        replacement = reopen_driver()
                        if replacement is not None:
                            drv = replacement
                            skip_retry_wait = True
                            log("Cốc Cốc đã đóng — đã tự mở phiên Suno mới để "
                                "tải tiếp các ID còn thiếu.")
                    except Exception as reopen_exc:  # noqa: BLE001
                        last_err[song_id] = (
                            f"browser đóng; không mở lại được: {reopen_exc}")[:300]
                if song_id not in got:
                    log(f"chưa tải được bản {song_id[:8]}: {str(e)[:160]}")
                else:
                    drv = reopen_after_close(drv)
                continue
            finally:
                # Đây chỉ là bước dọn tab. Cốc Cốc có thể đã tự đóng ngay sau
                # khi route tải WAV hoàn tất; không để lỗi cleanup che mất file
                # vừa tải hợp lệ và chặn cơ chế mở lại browser bên dưới.
                try:
                    drv.close_extra_tabs()
                except Exception:
                    pass
            res = suno_verify.verify_wav(path, minimum_duration_seconds=MIN_COVER_SEC)
            if res.valid:
                got[song_id] = path
                drv = reopen_after_close(drv)
                continue
            bad = out_dir / REJECT_STEM / path.name
            try:
                bad.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(_unique(bad)))
            except OSError:
                pass
            last_err[song_id] = f"file tải về hỏng: {res.reason}"
            log(f"bản {song_id[:8]} tải về bị hỏng ({res.reason}) — thử lại.")
    if not got and generation_failed:
        n, total = len(generation_failed), len(song_ids)
        head = (f"Cả {n}/{total} bản Suno đều" if n == total else
                f"{n}/{total} bản Suno, bản còn lại không tải được;")
        raise SunoGenerationFailed(
            f"{head} failed to generate hoặc đã bị Suno gỡ (trang 404); "
            "cần Create lượt mới.")
    if not got:
        detail = "; ".join(f"{k[:8]}: {v}" for k, v in last_err.items())
        raise RuntimeError("Không tải được bản nào từ Suno (đã lưu id bài — bấm «↻ Làm "
                           f"lại» để chỉ tải lại, không tốn credit). {detail}"[:700])
    return [got[i] for i in song_ids if i in got]


def _attach(sid: int, files: list[Path], expected: int, log) -> None:
    """Nhập các file vừa tải thành bản cover của bài + chạy kiểm tra lỗi."""
    from backend.cover import cover_qc
    from backend.routers.sources import attach_cover_file
    with Session(engine) as s:
        src = s.get(SourceSong, sid)
        if src is None:
            return
        project = s.get(Project, src.project_id)
        for f in files:
            attach_cover_file(s, project, src, f)
        src.suno_status = "done"
        src.suno_error = (None if len(files) >= expected else
                          f"Chỉ tải được {len(files)}/{expected} bản từ Suno.")
        src.suno_song_ids = ""          # lần sau «Làm lại» = tạo bản MỚI
        src.suno_preserve_style = False  # lượt tempo-only đã hoàn tất
        s.add(src)
        s.commit()
        pid = project.id
    log(f"xong — đã nhập {len(files)} bản cover, đang kiểm tra lỗi…")
    cover_qc.review_async(pid)


# ── cửa sổ đăng nhập ─────────────────────────────────────────────
def _login_state(page) -> str:
    from backend.video.suno_driver import suno_logged_out
    from backend.video.suno_selfcheck import is_logged_in
    try:
        if is_logged_in(page, vconfig.get_selectors("suno"), timeout_ms=1500):
            return "logged_in"
        if suno_logged_out(page):
            return "logged_out"
    except OperationCancelled:
        raise
    except Exception:
        pass
    return ""


def _login_window() -> None:
    _stop.clear()
    set_cancel_check(_stop.is_set)
    with _lock:
        _state.update(mode="login", login_scheduled=False)
    hints = {
        "logged_in": "✅ Đã đăng nhập Suno — bấm «Đóng cửa sổ» rồi chạy tạo cover.",
        "logged_out": "Chưa đăng nhập — hãy TỰ đăng nhập Suno trong cửa sổ vừa mở (tool "
                      "KHÔNG nhập mật khẩu). Xong mở trang Create để tool nhận ra.",
    }
    try:
        with _browser() as bs:
            page = nct_jobs._live_page(bs, None, work=False)
            try:
                page.goto(vconfig.SUNO_URL, wait_until="domcontentloaded")
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
                    st = _login_state(page)
                except OperationCancelled:
                    raise
                except Exception:
                    break     # người dùng tự đóng cửa sổ
                record_login(st)
                left = int(deadline - time.time()) // 60
                _set_msg(hints.get(st, "Cửa sổ Suno đang mở — tự đăng nhập nếu cần.")
                         + f" (tự đóng sau ~{left} phút)")
    except OperationCancelled:
        pass
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _set_msg(f"Lỗi mở Suno: {e}")
        return
    finally:
        set_cancel_check(None)
        with _lock:
            _state.update(mode=None, login_scheduled=False)
    st = login_status()
    _set_msg("Đã đóng cửa sổ Suno." + (" Đã đăng nhập ✓" if st.get("logged_in") else ""))
