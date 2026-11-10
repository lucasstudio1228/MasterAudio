"""
video/suno_service.py — Orchestrator STEP 0 (tạo 15 bài nhạc nền từ Suno).

Điều phối các phase (mục I của spec) trên state BỀN VỮNG (SunoBatch/SunoCandidate
trong DB) để chống trùng + resume + pause/cancel. Gọi SunoDriver để thao tác UI,
suno_verify để xác thực WAV, và HÀM IMPORT THẬT của app (tracks._persist +
_existing_paths, giống nút "Add file") để đưa track vào project.

Chạy trong worker thread của video_job_manager (kind="suno") — 1 luồng, nên đã
được serialize; lock trong DB là chốt phụ chống 2 tiến trình.

Chữ ký hàm step tuân quy ước pipeline: (project_id, progress_cb, ...extra).

AN TOÀN:
  • dry_run=True → KHÔNG bấm Create, KHÔNG tải file, KHÔNG tiêu credit.
  • Live chỉ khi router gọi với dry_run=False (người dùng bấm Start / lệnh live).
  • DoD: 15 selected = 15 song_id khác nhau, 15 WAV hợp lệ, manifest đầy đủ,
    import đúng 1 lần. KHÔNG coi 15 lần Create / 15 card / 15 filename / 15
    download-đã-bắt-đầu là hoàn thành.
"""
from __future__ import annotations

import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, select

from backend.database import engine, DB_PATH
from backend.models import Project, SunoBatch, SunoCandidate, Track
from backend.storage_maintenance import link_or_copy
from backend.video import config as vconfig
from backend.video.browser_base import (BrowserSession, OperationCancelled,
                                        cancellable_sleep, set_cancel_check)
from backend.video.suno_driver import (
    SunoDriver, SunoLoginRequired, SunoUIChanged, SunoSubmissionUncertain,
    SunoBudgetError, SunoDownloadLimit, SunoError, NewSong, SunoCaptchaRequired,
)
from backend.video import suno_verify

_LOG = Callable[[str], None]


# ── Phase constants (khớp models.SunoBatch.phase) ─────────────────
PREFLIGHT = "PREFLIGHT"
GENERATING = "GENERATING"
DOWNLOADING = "DOWNLOADING"
VALIDATING = "VALIDATING"
READY_FOR_IMPORT = "READY_FOR_IMPORT"
IMPORTED = "IMPORTED"
DOWNSTREAM_RUNNING = "DOWNSTREAM_RUNNING"
COMPLETED = "COMPLETED"
# Blocking / non-terminal:
WAITING_FOR_LOGIN = "WAITING_FOR_LOGIN"
WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
INSUFFICIENT_GENERATION_CREDITS = "INSUFFICIENT_GENERATION_CREDITS"
INSUFFICIENT_DOWNLOAD_ALLOWANCE = "INSUFFICIENT_DOWNLOAD_ALLOWANCE"
UI_CHANGED = "UI_CHANGED"
FAILED = "FAILED"
PAUSED = "PAUSED"
CANCELLED = "CANCELLED"

_TERMINAL = {COMPLETED, CANCELLED}
_ASSUMED_SONGS_PER_REQUEST = 2   # paired_outputs — đo lại ở request thật đầu tiên

# Phân biệt Pause vs Cancel: cả hai đều dùng job_manager.cancel() (ném
# JobCancelled). project_id nằm trong set này ⇒ Cancel (terminal CANCELLED);
# ngược lại ⇒ Pause (PAUSED, resume được).
_cancel_requested: set[int] = set()


def request_cancel(project_id: int) -> None:
    _cancel_requested.add(project_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════
# Cấu hình batch
# ══════════════════════════════════════════════════════════════════
def resolve_batch_config(overrides: Optional[dict] = None) -> dict:
    """Ghép SunoConfig mặc định + preset (styles/exclusions nguyên văn) +
    override từ UI. Trả dict phẳng để lưu snapshot config_json."""
    base = vconfig.SUNO
    ov = dict(overrides or {})
    preset_key = ov.get("preset", base.preset)
    preset = vconfig.SUNO_PRESETS.get(preset_key)
    if preset is None:
        raise ValueError(f"Preset không tồn tại: {preset_key!r}. "
                         f"Có: {list(vconfig.SUNO_PRESETS)}")

    # Styles/Exclusions: ưu tiên override (AI viết từ ý tưởng, hoặc người dùng tự
    # sửa) → nếu không có thì dùng nguyên văn preset. LUÔN chốt an toàn "không lời"
    # (đảm bảo Exclude styles chứa đủ từ khoá loại bỏ giọng hát).
    styles = (ov.get("styles") or "").strip() or preset["styles"]
    exclusions = (ov.get("exclusions") or "").strip() or preset["exclusions"]
    from backend.video.suno_prompt import _ensure_instrumental
    styles, exclusions = _ensure_instrumental(styles, exclusions)
    # allow_bass/allow_drums=False → đảm bảo drums/bass nằm trong Exclusions
    # (Suno không có switch stem lúc tạo). Preset đã có sẵn; đây là chốt an toàn.
    cfg = {
        "source": ov.get("source", base.source),
        "preset": preset_key,
        "preset_label": preset["label"],
        "suno_idea": (ov.get("suno_idea") or "").strip(),
        "styles": styles,
        "styles_supplied": bool((ov.get("styles") or "").strip()) and
                           (ov.get("styles") or "").strip() not in
                           {p["styles"].strip() for p in vconfig.SUNO_PRESETS.values()},
        "exclusions": exclusions,
        "target_tracks": int(ov.get("target_tracks", base.target_tracks)),
        "preferred_model": ov.get("preferred_model", base.preferred_model),
        "allow_model_fallback": bool(ov.get("allow_model_fallback",
                                            base.allow_model_fallback)),
        "instrumental": bool(ov.get("instrumental", base.instrumental)),
        "allow_bass": bool(ov.get("allow_bass", base.allow_bass)),
        "allow_drums": bool(ov.get("allow_drums", base.allow_drums)),
        "generation_strategy": ov.get("generation_strategy",
                                      base.generation_strategy),
        # Max Mode LUÔN bật (yêu cầu người dùng) — bỏ qua override cũ =False.
        "max_mode": True,
        "variety": int(ov.get("variety", base.variety)),
        "style_influence": int(ov.get("style_influence", base.style_influence)),
        "weirdness": int(ov.get("weirdness", base.weirdness)),
        "instrument_qc": bool(ov.get("instrument_qc", base.instrument_qc)),
        "preferred_duration_seconds_min": int(ov.get(
            "preferred_duration_seconds_min", base.preferred_duration_seconds_min)),
        "preferred_duration_seconds_max": int(ov.get(
            "preferred_duration_seconds_max", base.preferred_duration_seconds_max)),
        "minimum_duration_seconds": int(ov.get(
            "minimum_duration_seconds", base.minimum_duration_seconds)),
        "max_create_actions": int(ov.get("max_create_actions",
                                         base.max_create_actions)),
        "max_generation_credits": int(ov.get("max_generation_credits",
                                            base.max_generation_credits)),
        "max_new_song_downloads": int(ov.get("max_new_song_downloads",
                                            base.max_new_song_downloads)),
        "max_replacement_creates": int(ov.get("max_replacement_creates",
                                              base.max_replacement_creates)),
        "auto_continue_workflow": bool(ov.get("auto_continue_workflow",
                                             base.auto_continue_workflow)),
    }
    return cfg


def upgrade_plan_v2(cfg: dict, instrument: str, music_style: str) -> bool:
    """Plan đã đóng băng còn form v1 → thay bằng form v2 cùng nội dung (giữ bản
    cũ ở prompt_plan_v1 để đối chiếu). True nếu đã nâng."""
    from .suno_prompt import upgrade_v1_plan, PROMPT_WORKFLOW_VERSION
    if int(cfg.get("prompt_workflow_version") or 1) >= PROMPT_WORKFLOW_VERSION:
        return False
    new = upgrade_v1_plan(cfg.get("prompt_plan") or [], instrument, music_style)
    if not new:
        return False
    cfg["prompt_plan_v1"] = cfg["prompt_plan"]
    cfg["prompt_plan"] = new
    cfg["exclusions"] = new[0]["exclusions"]
    cfg["prompt_workflow_version"] = PROMPT_WORKFLOW_VERSION
    return True


def prepare_batch_prompts(project: Project, cfg: dict, log=None) -> dict:
    """Freeze a project-specific, per-Create music plan before opening Suno."""
    from .prompt_catalog import project_brief, claim_prompts, prompt_hash
    from .prompt_workflow import project_context
    from .suno_prompt import (generate_suno_styles, compose_project_styles,
                              build_suno_prompt_plan, PROMPT_WORKFLOW_VERSION)
    context = project_context(project.id, project.name)
    context["music_idea"] = cfg.get("suno_idea") or context["music_idea"]
    if not cfg.get("prompt_plan"):
        # A fresh music brief must not inherit a previous batch's incompatible
        # instrument/arrangement when the user changed the idea or base Styles.
        context["music_styles"] = cfg["styles"] if cfg.get("styles_supplied") else ""
    brief = project_brief(project.id, project.name, json.dumps(context, ensure_ascii=False))
    if not cfg.get("prompt_plan"):
        styles = cfg["styles"]
        if not cfg.get("styles_supplied"):
            from backend.core_bridge import get_api_config_from_db
            from backend.database import DB_PATH
            result = generate_suno_styles(json.dumps(context, ensure_ascii=False),
                         get_api_config_from_db(str(DB_PATH)), log=log,
                         project_title=project.name, creative_brief=brief["music"])
            if not result:
                raise ValueError("Chưa viết được Styles từ hồ sơ project. Kiểm tra API hoặc "
                                 "nhập Styles đã duyệt; không chạy preset thay thế ý tưởng.")
            styles, cfg["exclusions"] = result["styles"], result["exclusions"]
        cfg["styles"] = compose_project_styles(styles, brief["music"])
        cfg["prompt_plan"] = build_suno_prompt_plan(cfg["styles"], cfg["exclusions"],
                                                   int(cfg["max_create_actions"]),
                                                   music_style=context.get("purpose", ""),
                                                   instrument=context.get("instrument", ""))
        cfg["creative_brief"] = brief
        cfg["creative_context"] = context
        cfg["prompt_workflow_version"] = PROMPT_WORKFLOW_VERSION
    elif upgrade_plan_v2(cfg, getattr(project, "instrument", "") or "",
                         getattr(project, "music_style", "") or ""):
        if log:
            log("Nâng prompt Suno đã lưu (form v1) lên form v2 — nhạc cụ chính "
                "đứng đầu, nền chỉ âm thanh thiên nhiên.")
    plan = cfg["prompt_plan"]
    if len(plan) != int(cfg["max_create_actions"]):
        raise ValueError("Prompt plan không khớp ngân sách Create đã lưu.")
    for i, item in enumerate(plan):
        if (item.get("request") != i + 1 or not isinstance(item.get("styles"), str)
                or not 1 <= len(item["styles"]) <= 1000 or not item.get("exclusions")):
            raise ValueError(f"Prompt Suno #{i + 1} không hợp lệ hoặc vượt 1000 ký tự.")
    if len({prompt_hash(item["styles"]) for item in plan}) != len(plan):
        raise ValueError("Prompt plan Suno có lượt Create trùng nguyên văn.")
    cfg["prompt_hashes"] = claim_prompts(project.id, "suno",
                                         {str(i): p["styles"] for i, p in enumerate(plan)})
    return cfg


# ══════════════════════════════════════════════════════════════════
# Truy vấn / cập nhật batch (state bền vững)
# ══════════════════════════════════════════════════════════════════
def get_active_batch(session: Session, project_id: int) -> Optional[SunoBatch]:
    """Batch chưa terminal gần nhất của project (để resume/hiển thị)."""
    rows = session.exec(
        select(SunoBatch).where(SunoBatch.project_id == project_id)
        .order_by(SunoBatch.created_at.desc())).all()
    for b in rows:
        if b.phase not in _TERMINAL:
            return b
    return rows[0] if rows else None


def batch_status(project_id: int) -> Optional[dict]:
    """Trạng thái batch Suno gần nhất (chưa terminal ưu tiên) — cho endpoint status.
    None nếu project chưa có batch nào."""
    with Session(engine) as session:
        batch = get_active_batch(session, project_id)
        if batch is None:
            return None
        return _summary(session, batch, dry_run=False, credits=None)


def mark_cancelled(project_id: int) -> Optional[dict]:
    """Đánh dấu batch đang dở là CANCELLED khi KHÔNG có job đang chạy (batch đang
    ở trạng thái blocking/paused). Nếu job đang chạy, router gọi request_cancel +
    job_manager.cancel thay vì hàm này."""
    with Session(engine) as session:
        batch = get_active_batch(session, project_id)
        if batch is None or batch.phase in _TERMINAL:
            return None
        _touch(session, batch, CANCELLED, "Đã huỷ batch Suno.", None)
        _release_lock(session, batch)
        return _summary(session, batch, dry_run=False, credits=None)


def _touch(session: Session, batch: SunoBatch, phase: Optional[str] = None,
           message: Optional[str] = None, error: Optional[str] = None) -> None:
    if phase is not None:
        batch.phase = phase
    if message is not None:
        batch.message = message
    if error is not None:
        batch.error_message = error
    batch.updated_at = _now()
    session.add(batch)
    session.commit()
    session.refresh(batch)


def _acquire_lock(session: Session, batch: SunoBatch,
                  ttl_sec: int = 3600) -> str:
    """Chốt để 1 batch không bị 2 worker chạy song song. Trả lock_token."""
    now = _now()
    if batch.lock_token and batch.lock_expires_at:
        exp = batch.lock_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp > now:
            raise SunoError(
                "Batch Suno này đang được xử lý bởi tiến trình khác (đang khoá). "
                "Chờ nó xong hoặc thử lại sau.")
    token = uuid.uuid4().hex
    batch.lock_token = token
    batch.lock_expires_at = now + timedelta(seconds=ttl_sec)
    session.add(batch)
    session.commit()
    return token


def _release_lock(session: Session, batch: SunoBatch) -> None:
    batch.lock_token = None
    batch.lock_expires_at = None
    session.add(batch)
    session.commit()


# ══════════════════════════════════════════════════════════════════
# Import handoff — DÙNG hàm import thật của app (tracks._persist)
# ══════════════════════════════════════════════════════════════════
def _import_validated_tracks(session: Session, batch: SunoBatch,
                             log: _LOG) -> int:
    """Chỉ chạy khi CẢ batch đã sẵn sàng (đủ 15 WAV hợp lệ). Copy WAV từ staging
    vào thư mục media của project rồi đăng ký Track (dedup theo path đã resolve)
    — giống nút Add file. Idempotent: candidate đã imported thì bỏ qua."""
    from backend.routers.tracks import _persist, _existing_paths
    from backend import core_bridge

    project = session.get(Project, batch.project_id)
    dest_dir = (vconfig.project_dir(batch.project_id, project.name if project else None)
                / "suno_tracks")
    dest_dir.mkdir(parents=True, exist_ok=True)

    cands = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.selected == True,           # noqa: E712
            SunoCandidate.validation_status == "valid",
        )).all()

    existing = _existing_paths(session, batch.project_id)
    imported = 0
    for c in cands:
        if c.imported:
            continue
        if not c.wav_path or not Path(c.wav_path).exists():
            log(f"Bỏ qua import {c.song_id}: thiếu file WAV đã xác thực.")
            continue
        # Copy vào media project (giữ nguyên bản gốc trong staging).
        final_path = dest_dir / Path(c.wav_path).name
        if str(final_path.resolve()) not in existing:
            if not final_path.exists():
                # Hardlink: staging + media dùng chung 1 bản trên đĩa.
                link_or_copy(c.wav_path, final_path)
            core_track = core_bridge.probe_file(str(final_path))
            if core_track is None:
                log(f"Không đọc được WAV khi import: {final_path.name}")
                continue
            track = _persist(session, batch.project_id, core_track)
            session.commit()
            session.refresh(track)
            c.track_id = track.id
            existing.add(str(final_path.resolve()))
        c.imported = True
        session.add(c)
        imported += 1
    batch.imported_tracks = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.imported == True)).all().__len__()   # noqa: E712
    session.commit()
    log(f"Đã import {imported} track mới (tổng imported={batch.imported_tracks}).")
    return imported


# ══════════════════════════════════════════════════════════════════
# Điểm vào chính — chạy/resume batch
# ══════════════════════════════════════════════════════════════════
def run_suno_step0(project_id: int, progress_cb: Callable,
                   *, dry_run: bool = True,
                   overrides: Optional[dict] = None,
                   resume: bool = False) -> dict:
    """
    Entry cho video_job_manager.submit(project_id, "suno", run_suno_step0, ...).

    dry_run=True: PREFLIGHT + điền form + đọc credits/plan, KHÔNG Create/tải.
    dry_run=False: chạy đầy đủ tới IMPORTED (+ auto_continue nếu bật).
    resume=True: tiếp tục batch chưa terminal thay vì tạo mới.
    """
    # job_manager cb là cb(message, percent). Giữ percent hiện tại để log() không
    # tụt thanh tiến trình về 0. report()/log() để JobCancelled truyền lên (pause).
    _pct = {"v": 0.0}

    def report(msg: str, percent: Optional[float] = None) -> None:
        if percent is not None:
            _pct["v"] = float(percent)
        progress_cb(msg, _pct["v"])

    def log(msg: str) -> None:
        report(msg)

    _cancel_requested.discard(project_id)   # bắt đầu lượt chạy mới
    # Huỷ/Tạm dừng có hiệu lực NGAY trong các vòng chờ Playwright/sleep dài
    # (trước đây phải đợi tới checkpoint progress_cb kế tiếp → vài phút).
    set_cancel_check(getattr(progress_cb, "is_cancelled", None))
    try:
        return _run_step0(project_id, report, log, dry_run=dry_run,
                          overrides=overrides, resume=resume)
    except OperationCancelled:
        from backend.video.job_manager import JobCancelled
        raise JobCancelled() from None
    finally:
        set_cancel_check(None)


def _run_step0(project_id: int, report: Callable, log: Callable, *, dry_run: bool,
               overrides: Optional[dict], resume: bool) -> dict:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} không tồn tại.")

        # ── Lấy/khởi tạo batch ──
        batch: Optional[SunoBatch] = None
        if resume:
            batch = get_active_batch(session, project_id)
            if batch is None or batch.phase in _TERMINAL:
                raise SunoError("Không có batch Suno nào đang dở để resume.")
        if batch is None:
            cfg = resolve_batch_config(overrides)
            cfg = prepare_batch_prompts(project, cfg, log)
            staging = vconfig.SUNO_STAGING_ROOT / f"project_{project_id}" / uuid.uuid4().hex[:8]
            staging.mkdir(parents=True, exist_ok=True)
            batch = SunoBatch(
                project_id=project_id,
                idempotency_key=f"suno:{project_id}:{int(time.time())}",
                phase=PREFLIGHT,
                preset=cfg["preset"], model=cfg["preferred_model"],
                target_tracks=cfg["target_tracks"],
                config_json=json.dumps(cfg, ensure_ascii=False),
                staging_dir=str(staging),
            )
            session.add(batch)
            session.commit()
            session.refresh(batch)
        cfg = json.loads(batch.config_json or "{}")

        if not cfg.get("prompt_plan") and batch.create_actions_used:
            raise ValueError("Batch cũ đã tạo nhạc nhưng chưa có prompt plan. Không đổi bản sắc "
                             "giữa batch; hoàn tất/kiểm tra batch cũ trước khi chạy project mới.")
        cfg = prepare_batch_prompts(project, cfg, log)
        batch.config_json = json.dumps(cfg, ensure_ascii=False)
        session.add(batch)
        session.commit()

        token = _acquire_lock(session, batch)
        try:
            return _drive_batch(session, batch, cfg, dry_run, report, log)
        except SunoLoginRequired as e:
            _touch(session, batch, WAITING_FOR_LOGIN, str(e), str(e))
            raise
        except SunoCaptchaRequired as e:
            # Captcha chưa ai giải → chờ NGƯỜI (tool không tự giải/vượt captcha).
            _touch(session, batch, WAITING_FOR_HUMAN, f"CAPTCHA: {e}", str(e))
            raise
        except SunoSubmissionUncertain as e:
            _touch(session, batch, SUBMISSION_UNCERTAIN, str(e), str(e))
            raise
        except SunoUIChanged as e:
            _touch(session, batch, UI_CHANGED, str(e), str(e))
            raise
        except SunoBudgetError as e:
            _touch(session, batch, INSUFFICIENT_GENERATION_CREDITS, str(e), str(e))
            raise
        except SunoDownloadLimit as e:
            _touch(session, batch, INSUFFICIENT_DOWNLOAD_ALLOWANCE, str(e), str(e))
            raise
        except (Exception, OperationCancelled) as e:
            # JobCancelled/OperationCancelled (pause/cancel) hoặc lỗi khác — giữ
            # state resume được.
            if type(e).__name__ in ("JobCancelled", "OperationCancelled"):
                if project_id in _cancel_requested:
                    _touch(session, batch, CANCELLED, "Đã huỷ batch Suno.", None)
                else:
                    _touch(session, batch, PAUSED,
                           "Đã tạm dừng — resume để tiếp tục.", None)
            else:
                _touch(session, batch, FAILED, f"Lỗi: {e}", str(e))
            raise
        finally:
            _cancel_requested.discard(project_id)
            _release_lock(session, batch)


_CLOSED_MARKERS = ("has been closed", "targetclosederror", "target closed",
                   "browser has disconnected", "connection closed")


def _is_browser_closed(e: BaseException) -> bool:
    """Lỗi do trình duyệt/tab đã chết (crash, bị đóng) — KHÔNG phải lỗi của bài."""
    txt = f"{type(e).__name__}: {e}".lower()
    return any(m in txt for m in _CLOSED_MARKERS)


class _BrowserClosed(Exception):
    """Trình duyệt Suno đã chết giữa chừng → cần mở lại phiên mới."""


class _BrowserGaveUp(SunoError):
    """Mở lại quá số lần cho phép — dừng ở WAITING_FOR_HUMAN (resume được)."""


def _file_logger(path: Path, log: _LOG) -> _LOG:
    """Ghi thêm log ra file trong staging (để chẩn đoán sau) rồi chuyển tiếp."""
    def _log(msg: str) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass
        log(msg)
    return _log


class _SunoBrowser:
    """Giữ 1 BrowserSession Suno và cho phép MỞ LẠI khi trình duyệt sập.
    Playwright sync chỉ chạy 1 instance/thread → đóng hẳn phiên cũ trước khi mở."""

    def __init__(self, dry_run: bool, log: _LOG, debug_dir: Optional[Path] = None):
        self.dry_run = dry_run
        self.log = log
        self.debug_dir = debug_dir
        self.bs: Optional[BrowserSession] = None

    def open(self) -> SunoDriver:
        self.close()
        bs = BrowserSession(profile_dir=vconfig.SUNO_PROFILE_DIR)
        bs.__enter__()
        self.bs = bs
        driver = SunoDriver(bs.new_page(), dry_run=self.dry_run,
                            selectors=vconfig.get_selectors("suno"), log=self.log)
        driver.debug_dir = self.debug_dir
        return driver

    def reopen(self) -> SunoDriver:
        """Mở lại sau khi sập. Ngay sau crash, tiến trình Cốc Cốc cũ có thể chưa
        thoát hẳn khỏi profile → phiên mới vừa mở đã chết theo (đã gặp LIVE:
        'sẵn sàng' rồi sập lại cùng giây). Nên xác nhận phiên SỐNG ỔN ĐỊNH vài
        giây rồi mới trả về; chết thì chờ lâu hơn và tự thử lại tại chỗ."""
        self.log("Trình duyệt Suno đã đóng/sập — mở lại phiên mới…")
        last: Optional[BaseException] = None
        for i in range(3):
            self.close()
            cancellable_sleep(10 + 10 * i)
            try:
                driver = self.open()
                driver.open_create_page()          # xác nhận vẫn đăng nhập
                driver.page.wait_for_timeout(4000)
                driver.page.evaluate("1")          # còn sống sau 4s?
                self.log("Đã mở lại trình duyệt Suno (ổn định).")
                return driver
            except Exception as e:                 # noqa: BLE001
                if not _is_browser_closed(e):
                    raise
                last = e
        raise _BrowserClosed(f"Không mở lại được trình duyệt Suno: {last}")

    def close(self) -> None:
        if self.bs is not None:
            try:
                self.bs.__exit__(None, None, None)
            except Exception:
                pass
            self.bs = None

    def __enter__(self) -> "_SunoBrowser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _slider_values(cfg: dict) -> dict:
    """Thanh trượt Suno của batch; batch cũ (config_json chưa có khoá) dùng mặc
    định hiện hành."""
    base = vconfig.SUNO
    return {"variety": cfg.get("variety", base.variety),
            "style_influence": cfg.get("style_influence", base.style_influence),
            "weirdness": cfg.get("weirdness", base.weirdness)}


def _configure_create_form(driver: SunoDriver, cfg: dict, dry_run: bool) -> None:
    """Advanced / model / instrumental / Exclude / Duration / Max Mode trên trang
    /create. Dùng cho preflight VÀ cho lượt Create thay thế (driver lúc đó đang
    đứng ở trang bài nên phải cấu hình lại form)."""
    driver.select_advanced_mode()
    driver.open_more_options()
    # Người dùng chốt: Duration LUÔN Auto. Độ dài được lọc SAU khi tải (≥120 s
    # bắt buộc, ưu tiên 3–5 phút — _prefer_duration / _replace_dead_creates).
    # Kiểm TRƯỚC các bước khác: nếu phải tải lại trang thì không mất gì đã điền.
    driver.ensure_duration_auto()
    driver.select_model(cfg.get("preferred_model", "v6"),
                        allow_fallback=cfg.get("allow_model_fallback", False))
    driver.ensure_creative_sliders(_slider_values(cfg))
    if cfg.get("instrumental", True):
        driver.assert_instrumental()
    # LIVE: Styles được điền riêng cho từng lượt Create (prompt_plan) ngay trước
    # khi bấm → điền Styles chung ở đây chỉ là thao tác thừa bị ghi đè. Dry-run
    # vẫn điền để kiểm tra form.
    if dry_run:
        driver.fill_styles(cfg["styles"])
    driver.fill_exclusions(cfg["exclusions"])
    # Max Mode LUÔN bật (người dùng chốt 2026-10-08) — kể cả batch cũ lưu False.
    driver.set_max_mode(True)


def _drive_batch(session: Session, batch: SunoBatch, cfg: dict,
                 dry_run: bool, report: Callable, log: _LOG) -> dict:
    """Thân điều phối. Mỗi phase lưu state trước khi sang phase kế → resume an toàn."""
    target = int(cfg.get("target_tracks", 15))
    staging = Path(batch.staging_dir) if batch.staging_dir else \
        (vconfig.SUNO_STAGING_ROOT / f"project_{batch.project_id}")
    staging.mkdir(parents=True, exist_ok=True)

    log = _file_logger(staging / "_suno.log", log)

    with _SunoBrowser(dry_run, log, debug_dir=staging / "_debug") as sb:
        driver = sb.open()

        # ── PREFLIGHT ──
        report("Mở Suno, kiểm tra đăng nhập…", 5)
        _touch(session, batch, PREFLIGHT, "Preflight: mở Suno…")
        driver.open_create_page()
        allowance: Optional[int] = None
        credits = driver.read_credits()
        if credits is not None:
            log(f"Credits còn lại: {credits}.")
            if not dry_run and credits < vconfig.SUNO.credits_per_create:
                raise SunoBudgetError(
                    f"Suno còn {credits} credit, 1 lượt Create (Max Mode) cần "
                    f"{vconfig.SUNO.credits_per_create} — không tự mua thêm. Nạp credit rồi resume.")

        # Hạn mức TẢI (Suno từ 03/09/2026) chỉ áp cho tải THƯỜNG từ trang bài.
        # Production tải WAV qua Suno Studio (Export → Full Song → Download) — gói
        # Premier dùng Studio KHÔNG giới hạn lượt tải (chính sách Suno; dữ liệu
        # thật: 276 WAV Studio 30/09–07/10 không trừ lượt nào, «Downloads
        # Remaining» chỉ về 0 sau 60 MP3 A/B). Vì vậy KHÔNG chặn Create theo con
        # số ở /account; chỉ bật lại (check_download_allowance=True) nếu đổi sang
        # tải thường. Suno vẫn báo «Out of Downloads» thì driver đóng hộp thoại
        # (không Buy) và dừng lô an toàn (INSUFFICIENT_DOWNLOAD_ALLOWANCE).
        if not dry_run and cfg.get("check_download_allowance", False):
            need = max(0, target - _count(session, batch, validation_status="valid",
                                         selected=True))
            if need:
                allowance = driver.read_download_allowance()
                if allowance is not None:
                    log(f"Lượt tải Suno còn {allowance}, project cần thêm {need}.")
                if allowance is not None and allowance < need:
                    raise SunoDownloadLimit(
                        f"Suno chỉ còn {allowance} lượt tải, cần {need} bài cho "
                        f"project — dừng TRƯỚC khi Create để không tốn credit. "
                        f"Chờ hạn mức làm mới hoặc tự mua thêm lượt tải trên "
                        f"suno.com/account rồi Resume.")
                driver.open_create_page()

        # ── Cấu hình form (an toàn cho dry-run) ──
        report("Cấu hình Advanced / model / styles…", 12)
        _configure_create_form(driver, cfg, dry_run)

        if dry_run:
            report("DRY-RUN xong: đã điền form & đọc trạng thái, KHÔNG tạo/tải.",
                   100)
            _touch(session, batch, PREFLIGHT,
                   f"DRY-RUN OK (credits={credits}). Chưa tiêu credit.")
            return _summary(session, batch, dry_run=True, credits=credits)

        # ══════════ LIVE ══════════
        # ── GENERATING ──
        _generate_until_target(session, batch, cfg, driver, target,
                               report, log)

        # ── DOWNLOADING + VALIDATING ──
        try:
            _download_and_validate(session, batch, cfg, driver, target,
                                   staging, report, log, reopen=sb.reopen,
                                   allowance=allowance)
        except _BrowserGaveUp as e:
            log(f"DỪNG: {e}")
            _touch(session, batch, WAITING_FOR_HUMAN, str(e))
            return _summary(session, batch, dry_run=False, credits=credits)

        # Đủ 15 hợp lệ chưa?
        valid = _count(session, batch, validation_status="valid", selected=True)
        if valid < target:
            msg = (f"Mới có {valid}/{target} WAV hợp lệ — bấm Resume để tải lại "
                   f"bài lỗi (miễn phí) hoặc kiểm tra thủ công.")
            log(f"DỪNG: {msg}")
            _touch(session, batch, WAITING_FOR_HUMAN, msg)
            return _summary(session, batch, dry_run=False, credits=credits)

        # ── READY_FOR_IMPORT → IMPORTED (publish cả batch 1 lần) ──
        report("Đủ 15 WAV hợp lệ — import vào project…", 90)
        _touch(session, batch, READY_FOR_IMPORT, "Sẵn sàng import.")
        _import_validated_tracks(session, batch, log)
        _touch(session, batch, IMPORTED, "Đã import 15 track vào project.")

    # ── auto-continue workflow (ngoài browser session) ──
    # 15 WAV đã import → tự động chạy Mix (Audio) trên chính project này, và ép
    # render Video sau khi mix xong (Suno → Mix → Video, không cần bật auto_video
    # riêng). Mix chạy ở job_manager (worker riêng), Video ở video_job_manager —
    # job Suno này kết thúc ngay sau khối này nên worker video sẽ rảnh cho bước
    # dựng video. Bọc try/except để lỗi downstream không làm hỏng STEP 0 đã xong.
    if cfg.get("auto_continue_workflow", True):
        _touch(session, batch, DOWNSTREAM_RUNNING,
               "Tự chạy tiếp: Mix 15 track → render Video…")
        try:
            from backend.routers.mixes import start_mix_for_project
            mix_id = start_mix_for_project(batch.project_id, force_video=True)
            log(f"Đã bắt đầu Mix #{mix_id}. Video sẽ tự render sau khi mix xong.")
        except Exception as e:      # noqa: BLE001 — downstream là bước phụ
            log(f"Không tự chạy tiếp workflow: {e}. Bạn có thể chạy Mix thủ công.")
    _touch(session, batch, COMPLETED, "Hoàn tất STEP 0.")
    return _summary(session, batch, dry_run=False, credits=None)


# Suno chỉ nhận ~10 lượt Create đang render cùng lúc: 2026-09-28 bấm 11 lượt
# trong 45 giây → lượt 11 không được nhận (không bài mới, không trừ credit) mà
# vẫn bị tính vào ngân sách → lô dừng ở 14/15 bài. Giữ tối đa N lượt trong cửa
# sổ render (một bài Suno render ~2–4 phút).
# 2026-10-02 người dùng chốt: mỗi quãng nghỉ CHỈ 60s (240s làm 1 lô chờ ~15'
# trông như treo). Lượt nào Suno bỏ qua vẫn được bù bằng _lost_requests.
_MAX_INFLIGHT_CREATES = 8
_INFLIGHT_WINDOW_SEC = 60


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recent_create_times(session, batch) -> list[datetime]:
    """Thời điểm của các lượt Create gần đây (theo candidate đầu tiên của lượt)."""
    first: dict[str, datetime] = {}
    for c in session.exec(select(SunoCandidate)
                          .where(SunoCandidate.batch_id == batch.id)).all():
        t = _as_utc(c.created_at)
        if c.request_id not in first or t < first[c.request_id]:
            first[c.request_id] = t
    return sorted(first.values())


def _countdown(seconds: float, label: str, report=None, step: float = 15.0) -> None:
    """Ngủ (huỷ được) nhưng cập nhật thanh tiến độ mỗi `step` giây với số giây
    còn lại — chờ có chủ đích vài phút mà dòng trạng thái đứng yên thì người
    dùng tưởng tool bị treo. Chỉ gửi lên thanh tiến độ, không ghi _suno.log."""
    end = time.time() + max(0.0, seconds)
    while True:
        left = end - time.time()
        if left <= 0:
            return
        if report is not None:
            report(f"{label} — còn ~{int(left)}s (đang chờ có chủ đích, không phải treo).")
        cancellable_sleep(min(step, left))


# Giãn nhịp Create cho MỌI đường (lượt chính, lượt bù, QC tạo lại): 2 lượt liên
# tiếp cách ≥ SUNO.create_min_gap_sec. Chỉ ghi mốc khi bấm Create THẬT (SunoDriver,
# không dry_run) → driver giả trong test không phải chờ.
_LAST_CREATE_MONO = 0.0


def _pace_create(report=None) -> None:
    """Gọi TRƯỚC khi tăng create_actions_used (huỷ lúc chờ không làm lệch ngân sách)."""
    gap = max(0, int(vconfig.SUNO.create_min_gap_sec))
    if gap <= 0 or not _LAST_CREATE_MONO:
        return
    left = gap - (time.monotonic() - _LAST_CREATE_MONO)
    if left > 0:
        _countdown(left, "Giãn nhịp giữa 2 lượt Create (không dồn dập)", report, step=10)


def _throttle_inflight(session, batch, run_times: list[datetime], report, log) -> None:
    """Chờ tới khi số lượt Create trong cửa sổ render < _MAX_INFLIGHT_CREATES."""
    _pace_create(report)
    announced = False
    while True:
        now = _now()
        times = [t for t in (run_times + _recent_create_times(session, batch))
                 if (now - t).total_seconds() < _INFLIGHT_WINDOW_SEC]
        # run_times và candidate trùng nhau cho cùng 1 lượt → gộp theo giây.
        uniq = sorted({int(t.timestamp()) for t in times})
        if len(uniq) < _MAX_INFLIGHT_CREATES:
            return
        wait = _INFLIGHT_WINDOW_SEC - (now.timestamp() - uniq[0]) + 2
        if not announced:
            log(f"Đã có {len(uniq)} lượt Create đang render — chờ ~{int(wait)}s cho "
                f"Suno render bớt rồi mới Create tiếp (tránh lượt bị Suno bỏ qua).")
            announced = True
        report(f"Chờ Suno render bớt ({len(uniq)} lượt đang chạy) — còn ~{int(wait)}s "
               f"(đang chờ có chủ đích, không phải treo).")
        cancellable_sleep(min(15.0, max(1.0, wait)))


def _lost_requests(session, batch, max_creates: int) -> list[str]:
    """Lượt Create cơ bản (req1..reqN) đã tính vào ngân sách nhưng KHÔNG có bài
    nào được ghi nhận (Suno không nhận lượt đó / tool bị ngắt giữa chừng) và
    chưa được bù bằng lượt 'reqX:pK'."""
    rows = session.exec(select(SunoCandidate)
                        .where(SunoCandidate.batch_id == batch.id)).all()
    have = {c.request_id for c in rows}
    compensated = {int(r.rsplit(":p", 1)[1]) for r in have if ":p" in r}
    used = min(batch.create_actions_used, max_creates)
    return [f"req{i}" for i in range(1, used + 1)
            if f"req{i}" not in have and i not in compensated]


def _create_and_wait(session, batch, driver: SunoDriver, before: set[str],
                     credits_before: Optional[int], expected: int,
                     report, log) -> list[NewSong]:
    """Bấm Create rồi chờ bài mới — kèm kiểm tra lượt Create có được Suno NHẬN.

    Từ 2026-10-12 Suno chặn Create bằng captcha (Turnstile ẩn → hCaptcha ghép
    hình). Captcha hiện → báo người dùng TỰ giải trong cửa sổ Suno (tool không tự
    giải/vượt). Gọi SAU khi đã tăng create_actions_used (chống bấm lại khi lỗi).
    Lượt không được nhận (credit giữ nguyên + không bài mới sau khi tải lại trang)
    → trả lại lượt cho ngân sách rồi ném lỗi; tải lại thấy bài mới → dùng luôn."""
    def _human(msg: str) -> None:
        try:
            _touch(session, batch, message=msg)
        except Exception:                   # noqa: BLE001 — chỉ là thông báo
            pass
        report(msg)

    global _LAST_CREATE_MONO
    driver.click_create()
    if isinstance(driver, SunoDriver) and not driver.dry_run:
        _LAST_CREATE_MONO = time.monotonic()
    try:
        return driver.wait_for_new_songs(
            before, expected=expected, timeout_sec=300,
            accept_sec=vconfig.SUNO.create_accept_sec,
            captcha_wait_sec=vconfig.SUNO.captcha_wait_sec, on_human=_human,
            captcha_beep_sec=vconfig.SUNO.captcha_beep_sec)
    except (SunoCaptchaRequired, SunoSubmissionUncertain) as e:
        log(f"Lượt Create #{batch.create_actions_used} chưa xác nhận: {e}")
        songs = _recheck_unconfirmed_create(session, batch, driver, before,
                                            credits_before, expected, log)
        if songs:
            return songs
        raise


def _recheck_unconfirmed_create(session, batch, driver: SunoDriver, before: set[str],
                                credits_before: Optional[int], expected: int,
                                log) -> list[NewSong]:
    """Tải lại /create, đọc lại credit + danh sách bài sau lượt Create chưa xác
    nhận. Có bài mới → trả về (lượt được nhận, chỉ chậm hiện). Credit KHÔNG đổi
    và không có bài → lượt không được nhận ⇒ trả lại lượt cho ngân sách."""
    try:
        driver.open_create_page()
        driver.page.wait_for_timeout(5000)
        fresh = [i for i in driver.snapshot_song_ids() if i not in before]
        credits_now = driver.read_credits()
    except Exception as e:                  # noqa: BLE001
        if type(e).__name__ == "JobCancelled":
            raise
        log(f"Không kiểm tra lại được lượt Create: {e}")
        return []
    if fresh:
        log(f"Tải lại trang thấy {len(fresh)} bài mới — lượt Create ĐÃ được nhận.")
        out = []
        for sid in fresh[:max(expected, 1)]:
            title, dur = driver._song_meta(sid)
            out.append(NewSong(song_id=sid, title=title, duration_seconds=dur))
        return out
    if credits_before is not None and credits_now == credits_before:
        batch.create_actions_used = max(0, batch.create_actions_used - 1)
        _touch(session, batch)
        log(f"Credit giữ nguyên {credits_now}, không có bài mới → Suno KHÔNG nhận "
            f"lượt Create này; không tính vào ngân sách "
            f"(create_actions_used={batch.create_actions_used}).")
    else:
        log(f"Credit {credits_before} → {credits_now}, không thấy bài mới — vẫn tính "
            f"lượt này (không chắc Suno đã nhận hay chưa).")
    return []


def _generate_until_target(session, batch, cfg, driver: SunoDriver, target,
                           report, log) -> None:
    _touch(session, batch, GENERATING, "Đang tạo bài trên Suno…")
    max_creates = int(cfg.get("max_create_actions", 15))
    max_extra = int(cfg.get("max_replacement_creates", 5))
    plan = cfg.get("prompt_plan") or []
    songs_per_req = _ASSUMED_SONGS_PER_REQUEST
    run_times: list[datetime] = []
    # Mỗi Create chỉ lấy 1 bài (2 bài na ná) → đếm theo bài ĐÃ CHỌN, không đếm
    # tổng candidate. Cần đủ `target` lượt Create khác nhau mới đủ `target` bài.
    while _count(session, batch, selected=True) < target:
        if batch.create_actions_used < max_creates:
            plan_idx = batch.create_actions_used
            req_id = f"req{batch.create_actions_used + 1}"
        else:
            # Hết lượt cơ bản: bù các lượt "mất" (không có bài) bằng CHÍNH prompt
            # của lượt đó, tính vào hạn mức lượt thay thế (không đốt vô hạn).
            lost = _lost_requests(session, batch, max_creates)
            extra_used = batch.create_actions_used - max_creates
            if not lost or extra_used >= max_extra:
                raise SunoBudgetError(
                    f"Chạm trần max_create_actions={max_creates} (+{extra_used}/"
                    f"{max_extra} lượt bù) nhưng mới chọn được "
                    f"{_count(session, batch, selected=True)}/{target} bài. "
                    f"Dừng để không tốn thêm.")
            plan_idx = _plan_index(lost[0])
            req_id = f"req{batch.create_actions_used + 1}:p{plan_idx + 1}"
            log(f"Lượt Create {lost[0]} không có bài nào (Suno không nhận) — "
                f"Create bù {req_id} ({extra_used + 1}/{max_extra}).")
        if not 0 <= plan_idx < len(plan):
            raise SunoBudgetError(f"Không có prompt cho lượt {req_id} — dừng.")
        credits = driver.read_credits()
        if credits is not None and credits < vconfig.SUNO.credits_per_create:
            raise SunoBudgetError(
                f"Còn {credits} credit, không đủ {vconfig.SUNO.credits_per_create} cho 1 lượt "
                f"Create (Max Mode) — dừng, resume sau khi nạp.")

        _throttle_inflight(session, batch, run_times, report, log)
        before = driver.snapshot_song_ids()
        item = plan[plan_idx]
        # Suno có thể trả thanh trượt về mặc định (tải lại trang/đổi form) → kiểm
        # lại ngay trước mỗi Create, chỉ kéo thanh lệch.
        driver.ensure_creative_sliders(_slider_values(cfg))
        # Max Mode LUÔN bật: chỉ đọc trạng thái, lệch (Suno trả về Off) mới bấm.
        driver.set_max_mode(True)
        driver.fill_styles(item["styles"])
        driver.fill_exclusions(item["exclusions"])
        # LƯU submit-intent TRƯỚC khi Create (chống re-click khi timeout).
        batch.create_actions_used += 1
        _touch(session, batch, GENERATING,
               f"Create #{batch.create_actions_used} "
               f"(đã chọn {_count(session, batch, selected=True)}/{target})…")

        run_times.append(_now())
        new_songs = _create_and_wait(session, batch, driver, before, credits,
                                     songs_per_req, report, log)
        # Đo số bài/req thật ở lần đầu.
        if batch.create_actions_used == 1 and new_songs:
            songs_per_req = max(1, len(new_songs))
            log(f"Đo được: 1 request trả {songs_per_req} bài.")

        for ns in new_songs:
            _record_candidate(session, batch, ns, req_id)
        _mark_selection(session, batch, target)
        report(f"Đã chọn {_count(session, batch, selected=True)}/{target} bài "
               f"(qua {batch.create_actions_used} lượt Create).",
               min(60, 20 + _count(session, batch, selected=True) * 3))


def _download_and_validate(session, batch, cfg, driver: SunoDriver, target,
                           staging: Path, report, log,
                           reopen: Optional[Callable[[], SunoDriver]] = None,
                           allowance: Optional[int] = None) -> None:
    """Tải + xác thực WAV cho các bài đã chọn.

    Chạy NHIỀU LƯỢT: ngay sau khi Create xong, phần lớn bài vẫn đang render nên
    lượt đầu có thể hỏng vài bài; nghỉ rồi thử lại thay vì bỏ cuộc (trước đây 1
    bài hỏng là coi như mất luôn → cả lô dừng ở WAITING_FOR_HUMAN). Hết các lượt
    mà vẫn thiếu thì ĐÔN bài dự phòng (spare) của chính request đó — spare đã
    được tạo sẵn cùng lượt Create nên KHÔNG tốn thêm credit."""
    _touch(session, batch, DOWNLOADING, "Tải WAV các bài đã chọn…")
    minimum = float(cfg.get("minimum_duration_seconds", 120))
    max_dl = int(cfg.get("max_new_song_downloads", 15))
    passes = max(1, int(cfg.get("download_passes", 3)))
    retry_wait = float(cfg.get("download_retry_wait_sec", 60))

    known_hashes = {c.sha256 for c in
                    session.exec(select(SunoCandidate).where(
                        SunoCandidate.sha256 != None)).all() if c.sha256}  # noqa: E711
    # max_dl: trần tải cho bài ĐÃ CHỌN. Bài dự phòng/bài của lượt Create thay
    # thế được cộng thêm trần riêng khi dùng tới (trước đây 15 bài chọn tải xong
    # là chạm trần 15 → bài dự phòng không bao giờ tải được).
    # Chỉ đếm bài ĐANG ĐƯỢC CHỌN đã tải. Trước đây đếm MỌI candidate đã tải
    # (cả bản probe so độ dài, bản dự phòng, bài lỗi ngắn) → resume project 9
    # (17 file/15 bài chọn) chạm trần ngay, không tải thêm được bài nào.
    state = {"downloaded": _count(session, batch, download_status="downloaded",
                                  selected=True),
             "driver": driver, "reopens": 0, "probes": 0, "max_dl": max_dl,
             # Hạn mức tải Suno (None = không rõ): mọi lần tải (cả probe) trừ 1.
             "allowance": allowance, "used": 0,
             "tried": set(), "qc": _make_instrument_qc(session, batch, cfg, log)}
    max_reopens = int(cfg.get("max_browser_reopens", 5))
    gap = cfg.get("download_gap_sec", (8.0, 20.0))

    # Bài vừa Create xong vẫn đang render — tải ngay thì Studio chưa có
    # 'Open in Studio' / bản đầy đủ. Chờ tới khi bài MỚI NHẤT đủ tuổi.
    _wait_newest_song_age(session, batch, cfg, log, report)

    def _too_short(c: SunoCandidate) -> bool:
        """WAV đã tải đủ (bài render xong) mà vẫn < minimum → tải lại vô ích."""
        return (c.validation_status == "invalid"
                and c.verified_duration_seconds is not None
                and c.verified_duration_seconds < minimum)

    def _pending(retryable_only: bool = False) -> list[SunoCandidate]:
        sel = session.exec(
            select(SunoCandidate).where(
                SunoCandidate.batch_id == batch.id,
                SunoCandidate.selected == True)).all()      # noqa: E712
        out = []
        for c in sel:
            done = (c.validation_status == "valid" and c.wav_path
                    and Path(c.wav_path).exists())
            if not done and not (retryable_only and _too_short(c)):
                out.append(c)
        return out

    def _try_one(c: SunoCandidate, probe: bool = False) -> bool:
        """True nếu bài này đã có WAV hợp lệ sau lần thử này.
        probe=True: tải bản thứ 2 của cùng lượt Create chỉ để so độ dài — tính
        vào trần riêng (tối đa 1 bản/lượt Create), không ăn vào max_dl."""
        left = (None if state["allowance"] is None
                else state["allowance"] - state["used"])
        if probe:
            if state["probes"] >= target + int(cfg.get("max_replacement_creates", 5)):
                return False
            # Bản so sánh chỉ là "tốt hơn nếu được" → không bao giờ ăn vào lượt tải
            # cần cho bài chính, và hết lượt thì chỉ bỏ qua (không dừng cả lô).
            if left is not None and left - 1 < len(_pending()):
                log(f"Bỏ tải bản so sánh {c.song_id[:8]}: còn {left} lượt tải "
                    f"Suno — giữ cho bài chính.")
                return False
            state["probes"] += 1
        elif left is not None and left <= 0:
            raise SunoDownloadLimit(
                "Suno: đã dùng hết lượt tải của gói trong lần chạy này — dừng, "
                "chờ hạn mức làm mới hoặc tự mua thêm lượt tải rồi Resume.")
        elif state["downloaded"] >= state["max_dl"]:
            log(f"Chạm trần max_new_song_downloads={state['max_dl']} — dừng tải thêm.")
            return False
        safe = "".join(ch for ch in (c.title or c.song_id)
                       if ch.isalnum() or ch in " _-")[:60].strip()
        dest = staging / f"{safe or c.song_id}_{c.song_id[:8]}.wav"
        state["tried"].add(c.song_id)
        c.download_status = "downloading"
        session.add(c); session.commit()
        drv: SunoDriver = state["driver"]
        try:
            path = drv.download_wav(c.song_id, dest, timeout_sec=240)
            state["used"] += 1
            if not probe:
                state["downloaded"] += 1
        except (OperationCancelled, SunoDownloadLimit):
            # Hết lượt tải: lỗi của TÀI KHOẢN, không phải của bài → giữ pending
            # và dừng cả lô (INSUFFICIENT_DOWNLOAD_ALLOWANCE), resume sau.
            c.download_status = "pending"
            session.add(c); session.commit()
            raise
        except Exception as e:      # noqa: BLE001 — lỗi 1 bài không được giết cả lô
            if type(e).__name__ == "JobCancelled":
                c.download_status = "pending"
                session.add(c); session.commit()
                raise
            if _is_browser_closed(e):
                # Lỗi của TRÌNH DUYỆT, không phải của bài → không tính là hỏng.
                c.download_status = "pending"
                c.download_error = None
                session.add(c); session.commit()
                raise _BrowserClosed(str(e)) from e
            c.download_status = "failed"
            c.download_error = str(e)
            session.add(c); session.commit()
            log(f"Tải WAV lỗi ({c.song_id}): {e}")
            return False
        finally:
            drv.close_extra_tabs()

        # Xác thực kỹ thuật (KHÔNG tiêu credit) — WAV thật, đủ dài, không im lặng.
        res = suno_verify.verify_wav(path, minimum_duration_seconds=minimum,
                                     known_hashes=known_hashes)
        c.wav_path = path
        c.download_status = "downloaded"
        c.sha256 = res.sha256
        c.verified_duration_seconds = res.duration_seconds
        c.verified_sample_rate = res.sample_rate
        c.verified_channels = res.channels
        c.content_qc_status = "not_verified"   # không có analyzer → không bịa
        if res.valid:
            c.validation_status = "valid"
            c.validation_error = None
            if res.sha256:
                known_hashes.add(res.sha256)
        else:
            c.validation_status = "invalid"
            c.validation_error = res.reason
            log(f"WAV không hợp lệ ({c.song_id}): {res.reason}")
        session.add(c); session.commit()
        _sync_counters(session, batch)
        report(f"Tải+kiểm {state['downloaded']} WAV "
               f"(hợp lệ {_count(session, batch, validation_status='valid')}).",
               min(88, 62 + state["downloaded"] * 2))
        return res.valid

    def _attempt(c: SunoCandidate, probe: bool = False) -> bool:
        """_try_one + tự mở lại trình duyệt nếu nó sập giữa chừng (rồi thử lại
        đúng bài đó). Hết lượt mở lại → dừng ở WAITING_FOR_HUMAN (không đốt bài)."""
        while True:
            try:
                ok = _try_one(c, probe=probe)
            except _BrowserClosed as e:
                state["reopens"] += 1
                if reopen is None or state["reopens"] > max_reopens:
                    raise _BrowserGaveUp(
                        f"Trình duyệt Suno sập {state['reopens']} lần khi tải WAV "
                        f"— dừng để kiểm tra. ({e})") from e
                log(f"Trình duyệt sập khi tải {c.song_id[:8]} "
                    f"(lần {state['reopens']}/{max_reopens}): {e}")
                try:
                    state["driver"] = reopen()
                except _BrowserClosed as e2:
                    raise _BrowserGaveUp(f"{e2} — dừng để kiểm tra.") from e2
                continue
            # Giãn nhịp giữa các bài như người thao tác.
            cancellable_sleep(random.uniform(*gap))
            return ok

    for attempt in range(1, passes + 1):
        todo = _pending(retryable_only=True)
        if not todo:
            break
        if attempt > 1:
            log(f"Lượt tải lại #{attempt}: còn {len(todo)} bài chưa có WAV hợp lệ "
                f"— nghỉ {int(retry_wait)}s cho Suno render xong.")
            _touch(session, batch, DOWNLOADING,
                   f"Chờ render rồi tải lại {len(todo)} bài (lượt {attempt}/{passes})…")
            _countdown(retry_wait, f"Chờ render rồi tải lại {len(todo)} bài "
                                   f"(lượt {attempt}/{passes})", report)
        for c in todo:
            _attempt(c)

    # ── Vẫn thiếu → đôn spare cùng request (đã tạo sẵn, không tốn credit) ──
    still = _pending()
    if still:
        for c in still:
            spare = session.exec(
                select(SunoCandidate).where(
                    SunoCandidate.batch_id == batch.id,
                    SunoCandidate.request_id == c.request_id,
                    SunoCandidate.selected == False)).first()   # noqa: E712
            if _skip_spare(spare, state["tried"]):
                continue
            log(f"Đôn bài dự phòng {spare.song_id[:8]} thay cho "
                f"{c.song_id[:8]} (cùng lượt Create {c.request_id}).")
            c.selected, c.is_spare = False, True
            spare.selected, spare.is_spare = True, False
            session.add(c); session.add(spare); session.commit()
            if spare.download_status != "downloaded":
                state["max_dl"] += 1
            _attempt(spare)

    _prefer_duration(session, batch, cfg, _attempt, log, tried=state["tried"],
                     qc=state["qc"])

    # ── Cả 2 bài của 1 lượt Create đều lỗi → bỏ lượt đó, Create bài thay thế ──
    try:
        _replace_dead_creates(session, batch, cfg, state, _attempt, report, log)
    finally:
        if state["qc"] is not None:
            state["qc"].release()

    _sync_counters(session, batch)
    _touch(session, batch, VALIDATING, "Đã tải/kiểm tra xong đợt này.")


def _wait_newest_song_age(session, batch, cfg, log, report=None) -> None:
    """Chờ tới khi bài MỚI NHẤT của batch đủ `download_min_song_age_sec` tuổi —
    bài vừa Create còn đang render, tải sớm thì Studio chưa có bản đầy đủ."""
    min_age = float(cfg.get("download_min_song_age_sec", 60))
    newest = session.exec(
        select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
        .order_by(SunoCandidate.created_at.desc())).first()
    if newest is None or newest.created_at is None:
        return
    created = newest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    wait = min_age - (datetime.now(timezone.utc) - created).total_seconds()
    if wait > 0:
        log(f"Chờ {int(wait)}s cho Suno render xong các bài vừa tạo rồi mới tải.")
        _touch(session, batch, DOWNLOADING,
               f"Chờ {int(wait)}s cho Suno render xong rồi tải…")
        _countdown(wait, "Chờ Suno render xong các bài vừa tạo rồi mới tải", report)


def _plan_index(request_id: str) -> int:
    """'req7' → 6; lượt thay thế 'req16:p7' → 6 (dùng lại prompt của bài số 7)."""
    rid = request_id or ""
    if ":p" in rid:
        return int(rid.rsplit(":p", 1)[1]) - 1
    return int(rid.removeprefix("req")) - 1


def _request_groups(session, batch) -> dict[str, list[SunoCandidate]]:
    """Candidate theo lượt Create (bỏ qua lượt đã loại), giữ thứ tự tạo."""
    groups: dict[str, list[SunoCandidate]] = {}
    for c in session.exec(
            select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
            .order_by(SunoCandidate.created_at)).all():
        groups.setdefault(c.request_id, []).append(c)
    return {r: g for r, g in groups.items()
            if not any(c.download_status == "discarded" for c in g)}


def _dead_requests(session, batch) -> list[str]:
    """Lượt Create mà CẢ 2 bài đều đã tải về và KHÔNG hợp lệ (ngắn vài giây/1
    phút, im lặng, không phải WAV…). Bài chỉ lỗi tải (UI) KHÔNG tính — thử lại
    tải là đủ, không cần tốn credit Create lại."""
    return [r for r, g in _request_groups(session, batch).items()
            if g and all(c.validation_status == "invalid" for c in g)]


def _replace_dead_creates(session, batch, cfg, state, attempt, report, log) -> None:
    """Bỏ hẳn lượt Create hỏng cả 2 bài và Create bài THAY THẾ bằng đúng prompt
    của lượt đó (giữ vị trí bài trong album). Tối đa `max_replacement_creates`
    lượt thêm/batch (đếm bền vững = create_actions_used − max_create_actions)
    để không đốt credit vô hạn nếu Suno lỗi liên tục."""
    max_extra = int(cfg.get("max_replacement_creates", 5))
    base_budget = int(cfg.get("max_create_actions", 15))
    plan = cfg.get("prompt_plan") or []
    while True:
        dead = _dead_requests(session, batch)
        if not dead:
            return
        req = dead[0]
        used = max(0, batch.create_actions_used - base_budget)
        if used >= max_extra:
            log(f"Đã dùng hết {max_extra} lượt Create thay thế — còn "
                f"{len(dead)} lượt Create lỗi cả 2 bài, cần kiểm tra thủ công.")
            return
        left = (None if state.get("allowance") is None
                else state["allowance"] - state.get("used", 0))
        if left is not None and left < 1:
            # Bài thay thế sẽ không tải về được → không đốt credit Create.
            raise SunoDownloadLimit(
                "Suno: hết lượt tải — không Create bài thay thế (bài mới không tải "
                "về được). Chờ hạn mức làm mới hoặc tự mua thêm lượt tải rồi Resume.")
        group = _request_groups(session, batch)[req]
        reasons = "; ".join(
            f"{c.song_id[:8]} {c.verified_duration_seconds or 0:.0f}s "
            f"({c.validation_error or 'không hợp lệ'})" for c in group)
        log(f"Lượt Create {req}: cả {len(group)} bài đều lỗi [{reasons}] — "
            f"loại lượt này, Create bài thay thế ({used + 1}/{max_extra}).")
        for c in group:
            c.selected, c.is_spare = False, False
            c.download_status = "discarded"
            session.add(c)
        session.commit()

        idx = _plan_index(req)
        if not 0 <= idx < len(plan):
            log(f"Không tìm thấy prompt cho {req} — bỏ qua Create thay thế.")
            return
        drv: SunoDriver = state["driver"]
        try:
            credits = drv.read_credits()
            if credits is not None and credits <= 0:
                log("Hết credit Suno — không Create thay thế (không tự mua thêm).")
                return
            drv.open_create_page()
            _configure_create_form(drv, cfg, dry_run=False)
            _pace_create(report)
            before = drv.snapshot_song_ids()
            drv.fill_styles(plan[idx]["styles"])
            drv.fill_exclusions(plan[idx]["exclusions"])
            batch.create_actions_used += 1
            new_req = f"req{batch.create_actions_used}:p{idx + 1}"
            _touch(session, batch, GENERATING,
                   f"Create thay thế cho {req} ({new_req})…")
            new_songs = _create_and_wait(session, batch, drv, before, credits,
                                         _ASSUMED_SONGS_PER_REQUEST, report, log)
        except Exception as e:      # noqa: BLE001
            if type(e).__name__ == "JobCancelled" or isinstance(e, SunoCaptchaRequired):
                raise
            if _is_browser_closed(e):
                raise _BrowserGaveUp(
                    f"Trình duyệt Suno sập khi Create thay thế — dừng để kiểm tra. ({e})"
                ) from e
            log(f"Create thay thế cho {req} lỗi: {e}")
            return
        if not new_songs:
            log(f"Create thay thế {new_req}: chưa thấy bài mới sau 300s.")
            return
        cands = [_record_candidate(session, batch, ns, new_req) for ns in new_songs]
        for i, c in enumerate(cands):
            c.selected, c.is_spare = (i == 0), (i != 0)
            session.add(c)
        session.commit()
        _sync_counters(session, batch)
        report(f"Create thay thế {new_req}: {len(cands)} bài mới, chờ render…", 80)

        _touch(session, batch, DOWNLOADING, f"Tải bài thay thế {new_req}…")
        _wait_newest_song_age(session, batch, cfg, log, report)
        # Vẫn chỉ lấy 1/2: thử bài đầu, lỗi thì bài còn lại; cả 2 hợp lệ thì
        # _prefer_duration chọn bản đúng nhạc cụ chính / gần 3–5 phút hơn.
        first, rest = cands[0], cands[1:]
        state["max_dl"] += 1
        if not attempt(first) and rest:
            spare = rest[0]
            first.selected, first.is_spare = False, True
            spare.selected, spare.is_spare = True, False
            session.add(first); session.add(spare); session.commit()
            state["max_dl"] += 1
            attempt(spare)
        _prefer_duration(session, batch, cfg, attempt, log, requests={new_req},
                         qc=state.get("qc"))
        # Nếu cả 2 bài thay thế cũng lỗi → vòng lặp tự thấy lượt chết mới.


def _duration_off(d: float, lo: float, hi: float, tol: float = 5.0) -> float:
    """0 nếu d nằm trong [lo, hi] (±tol), ngược lại = số giây lệch khỏi khoảng."""
    if lo - tol <= d <= hi + tol:
        return 0.0
    return (lo - d) if d < lo else (d - hi)


def _skip_spare(spare: Optional[SunoCandidate], tried: Optional[set] = None) -> bool:
    """Không dùng bản còn lại nếu: không có / đã loại / đã tải mà KHÔNG hợp lệ /
    lỗi tải NGAY TRONG lượt chạy này. Lỗi tải ở lượt chạy TRƯỚC (thường do menu
    Suno chập chờn, không phải lỗi bài) → cho thử lại 1 lần (miễn phí)."""
    if spare is None or spare.download_status == "discarded" \
            or spare.validation_status == "invalid":
        return True
    if spare.download_status == "failed":
        return tried is None or spare.song_id in tried
    return False


def _make_instrument_qc(session, batch, cfg, log):
    """Hàm chấm "đúng nhạc cụ chính" cho WAV đã tải (PANNs, KHÔNG tốn credit),
    có cache theo song_id. None nếu tắt / nhạc cụ project chưa có trong bảng họ
    nhạc cụ / thiếu model — khi đó chọn bài thuần theo độ dài như trước."""
    if not cfg.get("instrument_qc", True):
        return None
    project = session.get(Project, batch.project_id)
    instrument = (getattr(project, "instrument", "") or "") if project else ""
    style = (getattr(project, "music_style", "") or "") if project else ""
    try:
        from backend.video import instrument_qc
    except Exception as e:      # noqa: BLE001 — thiếu numpy/librosa… không chặn lô
        log(f"Bỏ qua chấm nhạc cụ (không nạp được module: {e}).")
        return None
    if not instrument_qc.lead_families(instrument):
        return None
    if not instrument_qc.available():
        log("Bỏ qua chấm nhạc cụ: thiếu trọng số PANNs (~/panns_data) hoặc "
            "thư viện torch/panns_inference — chọn bài theo độ dài.")
        return None
    cache: dict[str, dict] = {}
    broken = {"on": False}

    def run(c: SunoCandidate) -> Optional[dict]:
        if c.song_id in cache:
            return cache[c.song_id]
        if broken["on"] or not c.wav_path or not Path(c.wav_path).exists():
            return None
        try:
            r = instrument_qc.analyze(c.wav_path, instrument, style)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "JobCancelled":
                raise
            broken["on"] = True
            log(f"Chấm nhạc cụ lỗi ({type(e).__name__}: {e}) — từ giờ chọn bài "
                f"theo độ dài.")
            return None
        cache[c.song_id] = r
        c.content_qc_status = "clean" if r["clean"] else "leaky"
        session.add(c); session.commit()
        log(f"Chấm nhạc cụ {c.song_id[:8]}: {instrument_qc.describe(r)}")
        return r

    run.release = instrument_qc.release
    return run


def _swap_reason(sd: float, sq: Optional[dict], d: float, q: Optional[dict],
                 lo: float, hi: float) -> Optional[str]:
    """Lý do đổi sang bản còn lại (None = giữ bản đang chọn). Thứ tự ưu tiên:
    1) đúng nhạc cụ chính (sạch) — đúng điều người dùng yêu cầu nhất;
    2) cả 2 cùng lẫn mà bản còn lại lẫn ÍT HƠN RÕ (≥ 0.25);
    3) gần khoảng 3–5 phút hơn (hi=0 → bỏ tiêu chí này); 4) hoà → lẫn ít hơn."""
    from backend.video.instrument_qc import badness
    if sq is not None and q is not None:
        if sq["clean"] != q["clean"]:
            return "đúng nhạc cụ chính" if sq["clean"] else None
        if not q["clean"]:
            gap = badness(q) - badness(sq)
            if abs(gap) >= 0.25:
                return "ít lẫn nhạc cụ khác hơn" if gap > 0 else None
    if hi:
        so, co = _duration_off(sd, lo, hi), _duration_off(d, lo, hi)
        if so != co:
            return f"gần khoảng {lo:.0f}-{hi:.0f}s hơn" if so < co else None
    if sq is not None and q is not None and badness(sq) < badness(q) - 0.05:
        return "ít lẫn nhạc cụ khác hơn"
    return None


def _prefer_duration(session, batch, cfg, attempt, log,
                     requests: Optional[set[str]] = None,
                     tried: Optional[set] = None, qc=None) -> None:
    """Chọn 1 trong 2 bản của mỗi lượt Create: bắt buộc ≥ minimum (đã lọc ở
    verify); có `qc` thì ưu tiên bản ĐÚNG NHẠC CỤ CHÍNH (chấm PANNs), rồi tới độ
    dài THẬT gần [preferred_min, preferred_max] (mặc định 3–5 phút). Bản đã chọn
    lẫn nhạc cụ / lệch độ dài → tải bản còn lại của cùng lượt (không tốn credit)
    rồi giữ bản tốt hơn theo `_swap_reason`."""
    lo = float(cfg.get("preferred_duration_seconds_min") or 0)
    hi = float(cfg.get("preferred_duration_seconds_max") or 0)
    if not (lo and hi and hi >= lo):
        if qc is None:
            return
        lo = hi = 0.0
    chosen = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.selected == True,                  # noqa: E712
            SunoCandidate.validation_status == "valid")).all()
    for c in chosen:
        if requests is not None and c.request_id not in requests:
            continue
        d = c.verified_duration_seconds or 0.0
        q = qc(c) if qc else None
        off = _duration_off(d, lo, hi) if hi else 0.0
        if off == 0 and (q is None or q["clean"]):
            continue
        spare = session.exec(
            select(SunoCandidate).where(
                SunoCandidate.batch_id == batch.id,
                SunoCandidate.request_id == c.request_id,
                SunoCandidate.selected == False)).first()    # noqa: E712
        # Bản còn lại đã tải mà lỗi → không tải lại lần nữa.
        if _skip_spare(spare, tried):
            continue
        have = (spare.validation_status == "valid" and spare.wav_path
                and Path(spare.wav_path).exists())
        if not have:
            why = (f"dài {d:.0f}s (ngoài {lo:.0f}-{hi:.0f}s)" if off
                   else "lẫn nhạc cụ khác")
            log(f"Bản {c.song_id[:8]} {why} — tải bản còn lại "
                f"{spare.song_id[:8]} để so.")
            if not attempt(spare, probe=True):
                continue
            session.refresh(spare)
        if spare.validation_status != "valid":
            continue
        sd = spare.verified_duration_seconds or 0.0
        sq = qc(spare) if qc else None
        reason = _swap_reason(sd, sq, d, q, lo, hi)
        if reason:
            log(f"Đổi sang bản {spare.song_id[:8]} ({sd:.0f}s) thay "
                f"{c.song_id[:8]} ({d:.0f}s) — {reason}.")
            c.selected, c.is_spare = False, True
            spare.selected, spare.is_spare = True, False
            session.add(c); session.add(spare); session.commit()
        else:
            log(f"Giữ bản {c.song_id[:8]} ({d:.0f}s); bản còn lại {sd:.0f}s "
                f"không tốt hơn.")


# ── helpers manifest ──────────────────────────────────────────────
def _record_candidate(session, batch, ns: NewSong, req_id: str) -> SunoCandidate:
    existing = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.song_id == ns.song_id)).first()
    if existing:
        return existing
    c = SunoCandidate(
        batch_id=batch.id, project_id=batch.project_id,
        song_id=ns.song_id, request_id=req_id,
        title=ns.title or "", duration_seconds=ns.duration_seconds,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    _sync_counters(session, batch)
    return c


def _mark_selection(session, batch, target: int) -> None:
    """Mỗi lượt Create (request) CHỈ chọn 1 bài — 2 bài cùng 1 Create thường na
    ná nhau nên chỉ lấy bài đầu, bài còn lại là spare (không tải/import). Do đó
    cần đủ `target` lượt Create để có `target` bài khác nhau."""
    groups: dict[str, list[SunoCandidate]] = {}
    for c in session.exec(
            select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
            .order_by(SunoCandidate.created_at)).all():
        groups.setdefault(c.request_id, []).append(c)
    selected_count = 0
    for group in groups.values():
        if any(c.download_status == "discarded" for c in group):
            continue                     # lượt Create đã loại (cả 2 bài lỗi)
        keep = None
        if selected_count < target:
            # Giữ lựa chọn đã có (đôn dự phòng / ưu tiên độ dài) — chỉ bài
            # chưa chọn gì mới mặc định lấy bài ĐẦU.
            keep = next((c for c in group if c.selected), group[0])
            selected_count += 1
        for c in group:
            want = c is keep
            if c.selected != want or c.is_spare == want:
                c.selected = want
                c.is_spare = not want
                session.add(c)
    session.commit()
    _sync_counters(session, batch)


def _count(session, batch, **filters) -> int:
    stmt = select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
    for k, v in filters.items():
        stmt = stmt.where(getattr(SunoCandidate, k) == v)
    return len(session.exec(stmt).all())


def _sync_counters(session, batch) -> None:
    batch.generated_candidates = _count(session, batch)
    batch.selected_tracks = _count(session, batch, selected=True)
    batch.downloaded_tracks = _count(session, batch, download_status="downloaded")
    batch.validated_tracks = _count(session, batch, validation_status="valid")
    batch.updated_at = _now()
    session.add(batch)
    session.commit()


def _summary(session, batch, *, dry_run: bool, credits: Optional[int]) -> dict:
    _sync_counters(session, batch)
    return {
        "batch_id": batch.id,
        "project_id": batch.project_id,
        "phase": batch.phase,
        "message": batch.message,
        "dry_run": dry_run,
        "credits_remaining": credits,
        "preset": batch.preset,
        "model": batch.model,
        "target_tracks": batch.target_tracks,
        "generated_candidates": batch.generated_candidates,
        "selected_tracks": batch.selected_tracks,
        "downloaded_tracks": batch.downloaded_tracks,
        "validated_tracks": batch.validated_tracks,
        "imported_tracks": batch.imported_tracks,
        "create_actions_used": batch.create_actions_used,
        "staging_dir": batch.staging_dir,
    }
