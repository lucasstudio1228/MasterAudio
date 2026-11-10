"""
video/suno_fix.py — "Tạo lại Suno" cho TỪNG bài lỗi (sau khi mix xong).

Người dùng thấy bài lỗi ở tab Audio (máy rà soát track_qc hoặc tự đánh dấu) →
bấm nút → job này thay ĐÚNG bài đó, các bài khác giữ nguyên:
  1. Bài Suno ĐÃ CÓ cùng prompt mà chưa dùng — bản còn lại của cùng lượt
     Create + các bài của lượt "tạo lại" trước (fixN:pK / reqN:pK) kể cả lần
     trước tải hỏng — KHÔNG tốn credit, chỉ tải + kiểm.
  2. Không có / cũng lỗi → Create lại bằng ĐÚNG prompt của bài đó
     (prompt_plan), tối đa `max_creates_per_track` lượt/bài (~10 credit/lượt).
Bài mới phải qua CẢ suno_verify (kỹ thuật) lẫn track_qc (rè/méo/hụt tiếng) so
với các bài cùng project. Ưu tiên bài có độ dài gần bài cũ nhất ⇒ khi vá mix
theo thứ tự cũ, các đoạn phía sau lệch ít nhất.

Import: Track mới vào suno_tracks, Track cũ bị gỡ khỏi project (file cũ dời
sang suno_tracks/_rejected — không xoá). KHÔNG tự mix lại: nút "Vá mix" riêng.

AN TOÀN: không tự đăng nhập, không mua credit (hết credit → dừng), mọi lượt
Create cộng vào batch.create_actions_used (đếm bền vững). Khâu TẢI hỏng do giao
diện Suno (SunoUIChanged) mà trong job chưa tải được bài nào → DỪNG, không
Create thêm (2026-09-30: Suno đổi class menu, job cũ vẫn Create 4 lượt = 40
credit mà không tải được bài nào).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, select

from backend.database import engine
from backend.models import Project, SunoBatch, SunoCandidate, Track
from backend.storage_maintenance import link_or_copy
from backend.video import config as vconfig
from backend.video import suno_verify, track_qc
from backend.video.suno_driver import (SunoBudgetError, SunoDownloadLimit, SunoError,
                                       SunoUIChanged)

DEFAULT_MAX_CREATES_PER_TRACK = 2
# Đo thật 10/Create (9550 → 9450 cho 10 lượt); Max Mode LUÔN bật → ×2 (tooltip Suno).
CREDITS_PER_CREATE = vconfig.SUNO.credits_per_create


class FixError(SunoError):
    pass


def _safe_name(c: SunoCandidate) -> str:
    safe = "".join(ch for ch in (c.title or c.song_id)
                   if ch.isalnum() or ch in " _-")[:60].strip()
    return f"{safe or c.song_id}_{c.song_id[:8]}.wav"


def fix_plan(project_id: int, track_ids: list[int]) -> list[dict]:
    """Xem trước (không mở trình duyệt): mỗi bài có bản dự phòng miễn phí không,
    hay phải Create mới. Dùng cho hộp xác nhận ngân sách ở UI."""
    out = []
    with Session(engine) as s:
        for tid in track_ids:
            c = s.exec(select(SunoCandidate).where(
                SunoCandidate.project_id == project_id,
                SunoCandidate.track_id == tid)).first()
            if c is None:
                out.append({"track_id": tid, "suno": False, "spares": 0})
                continue
            out.append({"track_id": tid, "suno": True,
                        "request_id": c.request_id,
                        "spares": len(_spares(s, c))})
    return out


def _prompt_no(request_id: Optional[str]) -> Optional[int]:
    """'req7' → 7; 'req16:p7' / 'fix18:p7' → 7 (số thứ tự prompt trong plan)."""
    m = re.fullmatch(r"(?:req|fix)\d+:p(\d+)|req(\d+)", request_id or "")
    return int(m.group(1) or m.group(2)) if m else None


def _spares(s: Session, c: SunoCandidate) -> list[SunoCandidate]:
    """Bài Suno đã có, CÙNG prompt với bài c, chưa import, chưa bị loại vì nội
    dung: bản còn lại của cùng lượt Create + bài của các lượt tạo lại trước.
    Bài tải HỎNG (download_status=failed — thường do UI/trình duyệt) vẫn được
    thử lại vì tải là miễn phí. Bản cùng request xếp trước, rồi bài mới hơn."""
    no = _prompt_no(c.request_id)
    rows = s.exec(select(SunoCandidate).where(
        SunoCandidate.batch_id == c.batch_id,
        SunoCandidate.id != c.id)).all()
    out = [r for r in rows
           if r.track_id is None
           and (r.request_id == c.request_id
                or (no is not None and _prompt_no(r.request_id) == no))
           and r.download_status != "discarded"
           and r.validation_status != "invalid"
           and r.content_qc_status != "rejected"]
    out.sort(key=lambda r: (r.request_id != c.request_id, -(r.id or 0)))
    return out


def replace_tracks(project_id: int, progress_cb: Callable, *,
                   track_ids: list[int],
                   max_creates_per_track: int = DEFAULT_MAX_CREATES_PER_TRACK,
                   dry_run: bool = False) -> dict:
    """Job video_job_manager (kind="suno-fix"). Trả {replaced:[...], failed:[...]}."""
    from backend.video.browser_base import OperationCancelled, set_cancel_check
    from backend.video.job_manager import JobCancelled
    # Huỷ có hiệu lực ngay trong các vòng chờ Playwright (như STEP 0).
    set_cancel_check(getattr(progress_cb, "is_cancelled", None))
    try:
        return _replace_tracks(project_id, progress_cb, track_ids=track_ids,
                               max_creates_per_track=max_creates_per_track,
                               dry_run=dry_run)
    except OperationCancelled:
        raise JobCancelled() from None
    finally:
        set_cancel_check(None)


def _replace_tracks(project_id: int, progress_cb: Callable, *,
                    track_ids: list[int], max_creates_per_track: int,
                    dry_run: bool) -> dict:
    from backend.video.suno_service import (
        _SunoBrowser, _configure_create_form, _record_candidate, _is_browser_closed,
        _wait_newest_song_age, _plan_index, _file_logger, _acquire_lock,
        _release_lock, _touch, _sync_counters, _duration_off,
        _ASSUMED_SONGS_PER_REQUEST, upgrade_plan_v2, _make_instrument_qc,
        _create_and_wait,
        _pace_create,
    )
    from backend.video.instrument_qc import badness

    last = [0.0]

    def report(msg: str, pct: Optional[float] = None) -> None:
        if pct is not None:
            last[0] = pct
        progress_cb(msg, last[0])

    with Session(engine) as s:
        project = s.get(Project, project_id)
        if project is None:
            raise FixError(f"Project {project_id} không tồn tại")
        pname = project.name
        # Ngữ cảnh so sánh = trung vị các bài đang có (bài lỗi không kéo lệch trung vị).
        report_data = track_qc.load_report(project_id, pname)
        ctx = report_data.get("context") or {}
        if not ctx:
            report_data = track_qc.review_project(project_id)
            ctx = report_data.get("context") or {}

        jobs = []
        for tid in track_ids:
            c = s.exec(select(SunoCandidate).where(
                SunoCandidate.project_id == project_id,
                SunoCandidate.track_id == tid)).first()
            t = s.get(Track, tid)
            if c is None or t is None:
                raise FixError(f"Track {tid} không phải bài do Suno tạo trong "
                               "project này — không tạo lại được.")
            jobs.append((tid, c.id))
        if not jobs:
            raise FixError("Chưa chọn bài nào để tạo lại.")

        batch = s.get(SunoBatch, s.get(SunoCandidate, jobs[0][1]).batch_id)
        cfg = json.loads(batch.config_json or "{}")
        staging = Path(batch.staging_dir) if batch.staging_dir else \
            (vconfig.SUNO_STAGING_ROOT / f"project_{project_id}")
        staging.mkdir(parents=True, exist_ok=True)
        log = _file_logger(staging / "_suno.log", lambda m: report(m))
        proj = s.get(Project, project_id)
        if proj is not None and upgrade_plan_v2(cfg, proj.instrument or "",
                                                proj.music_style or ""):
            batch.config_json = json.dumps(cfg, ensure_ascii=False)
            s.add(batch); s.commit()
            log("Nâng prompt Suno đã lưu (form v1) lên form v2 trước khi tạo lại "
                "— nhạc cụ chính đứng đầu, nền chỉ âm thanh thiên nhiên.")
        plan = cfg.get("prompt_plan") or []
        # Chấm "đúng nhạc cụ chính" (PANNs, 0 credit) để chọn bài thay thế sạch hơn.
        iqc = _make_instrument_qc(s, batch, cfg, log)
        minimum = float(cfg.get("minimum_duration_seconds", 120))
        lo = float(cfg.get("preferred_duration_seconds_min") or 180)
        hi = float(cfg.get("preferred_duration_seconds_max") or 300)
        known = {r.sha256 for r in s.exec(select(SunoCandidate).where(
            SunoCandidate.project_id == project_id)).all() if r.sha256}

        # Tạo lại bài dùng chung helper của STEP 0 (_wait_newest_song_age…) vốn
        # ghi phase DOWNLOADING lên batch. Batch đã COMPLETED từ trước ⇒ phải trả
        # lại phase cũ khi xong, nếu không lô sản xuất chờ COMPLETED mãi (kẹt
        # 12 giờ — project 17, 2026-10-05).
        orig_phase = batch.phase
        _acquire_lock(s, batch)
        replaced, failed = [], []
        try:
            with _SunoBrowser(dry_run, log, debug_dir=staging / "_debug") as sb:
                driver = None
                stats = {"dl_ok": 0, "ui_fail": 0}

                def drv():
                    nonlocal driver
                    if driver is None:
                        report("Mở Suno (dùng phiên đã đăng nhập)…", 3)
                        driver = sb.open()
                        driver.open_create_page()
                    return driver

                def fetch(c: SunoCandidate) -> Optional[dict]:
                    """Tải + verify + QC 1 bài. Trả info nếu ĐẠT, None nếu không."""
                    nonlocal driver
                    if not (c.wav_path and Path(c.wav_path).exists()):
                        dest = staging / _safe_name(c)
                        c.download_status = "downloading"
                        s.add(c); s.commit()
                        path = None
                        for attempt in range(2):
                            try:
                                path = drv().download_wav(c.song_id, dest,
                                                          timeout_sec=240)
                                break
                            except Exception as e:      # noqa: BLE001
                                if type(e).__name__ == "JobCancelled":
                                    raise
                                if isinstance(e, SunoDownloadLimit):
                                    # Lỗi tài khoản, không phải của bài → dừng job.
                                    c.download_status = "pending"
                                    s.add(c); s.commit()
                                    raise FixError(str(e)) from e
                                if _is_browser_closed(e) and attempt == 0:
                                    log("Trình duyệt Suno đóng/sập — mở lại rồi tải lại.")
                                    driver = sb.reopen()
                                    continue
                                c.download_status = "failed"
                                c.download_error = str(e)
                                s.add(c); s.commit()
                                if isinstance(e, SunoUIChanged):
                                    stats["ui_fail"] += 1
                                log(f"Tải WAV lỗi ({c.song_id[:8]}): {e}")
                                return None
                            finally:
                                if driver is not None:
                                    try:
                                        driver.close_extra_tabs()
                                    except Exception:   # noqa: BLE001
                                        pass
                        stats["dl_ok"] += 1
                        c.wav_path = path
                        c.download_status, c.download_error = "downloaded", None
                    res = suno_verify.verify_wav(c.wav_path,
                                                 minimum_duration_seconds=minimum,
                                                 known_hashes=known)
                    c.sha256 = res.sha256
                    c.verified_duration_seconds = res.duration_seconds
                    c.verified_sample_rate = res.sample_rate
                    c.verified_channels = res.channels
                    if not res.valid:
                        c.validation_status, c.validation_error = "invalid", res.reason
                        s.add(c); s.commit()
                        log(f"Bài {c.song_id[:8]} không hợp lệ: {res.reason}")
                        return None
                    c.validation_status, c.validation_error = "valid", None
                    if res.sha256:
                        known.add(res.sha256)
                    ok, issues, m = track_qc.passes(c.wav_path, ctx)
                    c.content_qc_status = "passed" if ok else "rejected"
                    s.add(c); s.commit()
                    if not ok:
                        log(f"Bài {c.song_id[:8]} cũng lỗi khi rà soát: "
                            + "; ".join(i["message"] for i in issues
                                        if i["level"] == "error"))
                        return None
                    q = iqc(c) if iqc else None
                    if q is not None:      # iqc ghi clean/leaky; bài vẫn ĐẠT rà soát
                        c.content_qc_status = "passed"
                        s.add(c); s.commit()
                    return {"cand": c, "duration": m["duration"],
                            "clean": q["clean"] if q else None,
                            "bad": badness(q) if q else 0.0,
                            "warnings": [i["message"] for i in issues]}

                def better(a: dict, b: Optional[dict], old_dur: float) -> dict:
                    """Ưu tiên đúng nhạc cụ chính (chấm PANNs), rồi trong 3–5
                    phút, rồi ít lẫn hơn, rồi gần độ dài bài cũ (ít lệch mix)."""
                    if b is None:
                        return a

                    def key(x: dict) -> tuple:
                        leaky = x.get("clean") is False
                        return (leaky, _duration_off(x["duration"], lo, hi),
                                round(x.get("bad", 0.0), 2) if leaky else 0.0,
                                abs(x["duration"] - old_dur))
                    return a if key(a) <= key(b) else b

                def good(x: Optional[dict]) -> bool:
                    """Đủ tốt để dừng tìm: trong 3–5 phút và không lẫn nhạc cụ."""
                    return bool(x) and x.get("clean") is not False \
                        and _duration_off(x["duration"], lo, hi) == 0

                n = len(jobs)
                for k, (tid, cid) in enumerate(jobs):
                    base = 5 + 90 * k / n
                    old_c = s.get(SunoCandidate, cid)
                    old_t = s.get(Track, tid)
                    old_dur = float(old_t.duration_seconds or 0)
                    label = f"[{k + 1}/{n}] {old_t.filename}"
                    report(f"{label}: tìm bài thay thế…", base)
                    chosen: Optional[dict] = None
                    source = ""

                    # 1) Bài đã có cùng prompt (0 credit). Lấy bài đạt đầu tiên
                    #    nằm trong 3–5 phút; không có thì bài đạt tốt nhất.
                    for sp in _spares(s, old_c):
                        report(f"{label}: thử bài có sẵn {sp.song_id[:8]} "
                               f"({sp.request_id}, cùng prompt, 0 credit)…", base + 5)
                        got = fetch(sp)
                        if got:
                            chosen, source = better(got, chosen, old_dur), "spare"
                            if good(chosen):
                                break

                    # 2) Create lại bằng đúng prompt của bài này.
                    idx = _plan_index(old_c.request_id)
                    creates = 0
                    while chosen is None and creates < max_creates_per_track:
                        if not 0 <= idx < len(plan):
                            log(f"Không tìm thấy prompt cho {old_c.request_id}.")
                            break
                        if dry_run:
                            log(f"DRY-RUN: sẽ Create lại bằng prompt #{idx + 1}.")
                            break
                        if stats["ui_fail"] and not stats["dl_ok"]:
                            raise FixError(
                                "Khâu tải WAV từ Suno đang lỗi giao diện (không tải "
                                "được bài nào) — DỪNG, không Create thêm để khỏi tốn "
                                "credit. Các bài đã tạo sẽ được tải lại MIỄN PHÍ ở "
                                "lần chạy sau khi sửa xong.")
                        d = drv()
                        credits = d.read_credits()
                        if credits is not None and credits < CREDITS_PER_CREATE:
                            raise SunoBudgetError(
                                f"Credit Suno còn {credits} — không đủ Create "
                                "(không tự mua thêm).")
                        creates += 1
                        report(f"{label}: Create lại bằng prompt #{idx + 1} "
                               f"(lượt {creates}/{max_creates_per_track}, ~"
                               f"{CREDITS_PER_CREATE} credit)…", base + 10)
                        d.open_create_page()
                        _configure_create_form(d, cfg, dry_run=False)
                        _pace_create(report)
                        before = d.snapshot_song_ids()
                        d.fill_styles(plan[idx]["styles"])
                        d.fill_exclusions(plan[idx]["exclusions"])
                        batch.create_actions_used += 1
                        req = f"fix{batch.create_actions_used}:p{idx + 1}"
                        _touch(s, batch, message=f"Tạo lại Suno cho {old_t.filename} ({req})…")
                        new = _create_and_wait(s, batch, d, before, credits,
                                               _ASSUMED_SONGS_PER_REQUEST, report, log)
                        if not new:
                            log(f"{req}: chưa thấy bài mới sau 300s.")
                            continue
                        cands = [_record_candidate(s, batch, ns, req) for ns in new]
                        for c in cands:
                            c.selected, c.is_spare = False, True
                            s.add(c)
                        s.commit()
                        report(f"{label}: chờ Suno render xong ({req})…", base + 15)
                        _wait_newest_song_age(s, batch, cfg, log)
                        first = fetch(cands[0])
                        # Chỉ lấy 1/2. Tải bản thứ 2 khi bản đầu lỗi, lẫn nhạc
                        # cụ khác, hoặc nằm ngoài 3–5 phút (để so).
                        if len(cands) > 1 and not good(first):
                            second = fetch(cands[1])
                            first = better(second, first, old_dur) if second else first
                        if first:
                            chosen, source = first, "create"

                    if chosen is None:
                        failed.append({"track_id": tid, "filename": old_t.filename,
                                       "creates": creates})
                        log(f"{label}: KHÔNG tìm được bài thay thế đạt chuẩn "
                            f"(đã Create {creates} lượt).")
                        continue

                    new_tid = _swap_track(s, project_id, pname, old_t, old_c,
                                          chosen["cand"], log)
                    info = {"old_filename": old_t.filename,
                            "new_filename": s.get(Track, new_tid).filename,
                            "old_song_id": old_c.song_id,
                            "new_song_id": chosen["cand"].song_id,
                            "source": source, "creates": creates,
                            "old_duration": round(old_dur, 1),
                            "new_duration": chosen["duration"],
                            "warnings": chosen["warnings"]}
                    track_qc.record_replacement(project_id, pname, tid, new_tid, info)
                    replaced.append({"old_track_id": tid, "new_track_id": new_tid, **info})
                    report(f"{label}: đã thay bằng {info['new_filename']} "
                           f"({chosen['duration']:.0f}s, "
                           f"{'0 credit' if source == 'spare' else f'{creates} lượt Create'}).",
                           base + 90 / n)
        finally:
            if iqc is not None:
                iqc.release()
            _sync_counters(s, batch)
            if orig_phase and batch.phase != orig_phase:
                _touch(s, batch, orig_phase,
                       f"Đã tạo lại {len(replaced)} bài (rà soát chất lượng).")
            _release_lock(s, batch)

    try:
        track_qc.review_project(project_id)
    except Exception:       # noqa: BLE001
        pass
    msg = f"Đã thay {len(replaced)} bài"
    if failed:
        msg += f"; {len(failed)} bài chưa thay được"
    msg += ". Bấm «Vá mix» để thay vào bản mix (giữ nguyên thứ tự các bài)."
    report(msg, 100)
    return {"replaced": replaced, "failed": failed, "message": msg}


def _swap_track(s: Session, project_id: int, pname: Optional[str],
                old_t: Track, old_c: SunoCandidate, new_c: SunoCandidate,
                log) -> int:
    """Import bài mới (giống nút Add file) + gỡ Track cũ khỏi project."""
    from backend.routers.tracks import _persist, _delete_track_stems
    from backend import core_bridge

    dest_dir = vconfig.project_dir(project_id, pname) / "suno_tracks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / Path(new_c.wav_path).name
    if not final.exists():
        link_or_copy(new_c.wav_path, final)   # hardlink, không nhân đôi WAV
    core_track = core_bridge.probe_file(str(final))
    if core_track is None:
        raise FixError(f"Không đọc được WAV khi import: {final.name}")
    new_t = _persist(s, project_id, core_track)
    s.commit()
    s.refresh(new_t)

    new_c.track_id, new_c.imported = new_t.id, True
    new_c.selected, new_c.is_spare = True, False
    new_c.content_qc_status = "passed"
    old_c.track_id, old_c.selected, old_c.is_spare = None, False, False
    old_c.content_qc_status = "rejected"
    s.add(new_c); s.add(old_c)

    old_path = Path(old_t.filepath)
    _delete_track_stems(s, old_t)
    s.delete(old_t)
    s.commit()
    try:
        if old_path.exists() and old_path.parent == dest_dir:
            rej = dest_dir / "_rejected"
            rej.mkdir(exist_ok=True)
            shutil.move(str(old_path), str(rej / old_path.name))
    except OSError as e:
        log(f"Không dời được file cũ {old_path.name}: {e}")
    log(f"Đã thay {old_t.filename} → {final.name}.")
    return new_t.id
