"""
video/flow_manual.py — Chế độ "Tạo video thủ công" (BÁN TỰ ĐỘNG) cho Google Flow.

Dùng khi profile tự động bị gắn cờ «hoạt động bất thường». Phân vai:
  • TOOL: mở Flow (đúng profile của tool), tạo dự án, cài đặt Thành phần/16:9/
    model/8s/x1, upload ảnh, đưa HAI bảng phân tích nhân vật (+ ảnh bìa cho
    clip 00) vào ô soạn; phát hiện clip người dùng vừa tạo xong → tải về và đặt
    tên clip_NN.mp4 → chuẩn bị clip kế.
  • NGƯỜI DÙNG: copy prompt từ UI của tool, dán vào ô soạn Flow và TỰ bấm Tạo.

Tool KHÔNG điền prompt, KHÔNG bấm Tạo, KHÔNG bấm Retry. Nếu chính cửa sổ này
cũng bị chặn, người dùng tạo clip ở trình duyệt riêng rồi "Nhập file" cho đúng
clip (import_clip_file) — vòng chờ nhận ra file đó và chuyển sang clip kế.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from . import flow_driver as fd
from .gpm_session import site_session
from .browser_base import BrowserSession, query_first, wait_for_manual_login
from .gemini_driver import _is_closed_error

# Chờ người dùng tạo 1 clip tối đa bao lâu (giây) trước khi dừng phiên.
MANUAL_CLIP_WAIT_SEC = 2 * 3600
# Poll lưới clip mỗi POLL_MS; cần STREAK lần liên tiếp thấy clip mới để chắc
# không bắt nhầm tile "đang tạo" nhấp nháy 1–2s ngay sau khi bấm Tạo.
_POLL_MS = 3000
_STREAK = 3

_STATE: dict[int, dict] = {}
_LOCK = threading.Lock()


def manual_state(project_id: int) -> Optional[dict]:
    with _LOCK:
        st = _STATE.get(project_id)
        return dict(st) if st else None


def _set(project_id: int, **kw) -> None:
    with _LOCK:
        st = _STATE.setdefault(project_id, {})
        st.update(kw)
        st["updated_at"] = time.time()


def _mark_done_in_plan(out_dir: Path, idx: int) -> None:
    """Clip vừa có (tải/nhập tay) là clip thật → bỏ khỏi tập 'fallback' của
    _plan.json để chế độ tự động resume không tạo lại nó."""
    plan_file = out_dir / "_plan.json"
    try:
        raw = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    fb = raw.get("fallback")
    if isinstance(fb, list) and idx in fb:
        raw["fallback"] = [x for x in fb if x != idx]
    clips = raw.get("clips")
    if isinstance(clips, dict):
        plan = config.generate_ingredient_plan(config.PARAMS.total_clips)
        if idx < len(plan):
            clips[f"clip_{idx:02d}.mp4"] = plan[idx]
    try:
        plan_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def import_clip_file(project_id: int, project_name: str | None, index: int,
                     src: Path) -> Path:
    """Nhập 1 file mp4 người dùng tự tạo/tải → clips/clip_NN.mp4 (ghi đè)."""
    from .assembler import probe_duration
    total = config.PARAMS.total_clips
    if not (0 <= index < total):
        raise ValueError(f"Chỉ số clip {index} không hợp lệ (0..{total - 1}).")
    if src.stat().st_size <= 10_000:
        raise ValueError("File quá nhỏ — không phải clip video hợp lệ.")
    try:
        dur = float(probe_duration(str(src)) or 0)
    except Exception as exc:
        raise ValueError(f"Không đọc được file video ({type(exc).__name__}).") from exc
    if dur < 1.0:
        raise ValueError(f"Video quá ngắn ({dur:.1f}s).")
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"clip_{index:02d}.mp4"
    fd_, tmp = tempfile.mkstemp(prefix=".import-", suffix=".mp4", dir=out_dir)
    os.close(fd_)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()
    _mark_done_in_plan(out_dir, index)
    fd._LOGP = out_dir / "_flow.log"
    fd._log(f"[THỦ CÔNG] nhập file cho {dest.name} ({dest.stat().st_size // 1024} KB, "
            f"{dur:.1f}s)")
    return dest


def _stamp(p: Path) -> Optional[tuple[int, float]]:
    try:
        st = p.stat()
        return (st.st_size, st.st_mtime) if st.st_size > 10_000 else None
    except OSError:
        return None


def _open_project(page, sels, params, img_paths, p) -> None:
    """Mở Flow → dự án mới (hoặc dự án trống sẵn có) → cài đặt → upload ảnh."""
    p("Mở Google Flow (profile của tool)…", 2.0)
    page.goto(config.FLOW_URL, wait_until="domcontentloaded")
    fd._click(page, sels.get("flow_entry", []), 5000)
    wait_for_manual_login(page, sels["new_project"] + sels["add_media_menu"],
                          "Flow", timeout_sec=600)
    p("Tạo dự án Flow mới…", 4.0)
    if not fd._click(page, sels["new_project"], 8000):
        empty = page.evaluate(
            "() => !document.querySelector('flow-video-tile, flow-image-tile')")
        if not (empty and query_first(page, sels["add_media_menu"], timeout_ms=3000)):
            fd._dump_buttons(page, "manual-new-project")
            raise RuntimeError("Không bấm được New project/Dự án mới trên Flow.")
        fd._log("    đã ở sẵn trong dự án trống → dùng luôn")
    page.wait_for_timeout(2500)
    p(f"Cài đặt Thành phần · {params.aspect_ratio} · {params.flow_model} · "
      f"{params.clip_seconds}s · x1…", 6.0)
    fd._configure_settings(page, sels)
    p(f"Upload {len(img_paths)} ảnh vào Flow…", 8.0)
    fd._upload_images(page, sels, img_paths, None)
    fd._log(f"[THỦ CÔNG] upload xong {len(img_paths)} ảnh")


def _prepare_composer(page, sels, ings: list[int]) -> None:
    """Ô soạn sạch (xoá chữ + nguyên liệu lượt trước) rồi thêm đúng nguyên liệu."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    fd._clear_ingredients(page, sels)
    for img in ings:
        fd._add_ingredient(page, sels, img)


def _wait_user_clip(page, project_id: int, idx: int, dest: Path, prev_count: int,
                    prev_top: Optional[str], before: Optional[tuple], p, pct: float,
                    total: int) -> str:
    """Chờ người dùng tạo clip. Trả "generated" (thấy clip mới trên lưới Flow)
    hoặc "imported" (người dùng đã Nhập file cho đúng clip này)."""
    t0 = time.time()
    streak = 0
    last_tick = 0.0
    last_block = 0.0
    blocked: Optional[str] = None
    while time.time() - t0 < MANUAL_CLIP_WAIT_SEC:
        now_stamp = _stamp(dest)
        if now_stamp and now_stamp != before:
            return "imported"
        cnt = fd._clip_play_count(page)
        top = fd._top_done_clip(page)
        if cnt > prev_count or (top is not None and top != prev_top):
            streak += 1
            if streak >= _STREAK:
                fd._log(f"    ✓ thấy clip mới sau {time.time() - t0:.0f}s "
                        f"(clip xong={cnt})")
                return "generated"
        else:
            streak = 0
        if time.time() - last_block > 10:
            last_block = time.time()
            reason = fd._flow_block_reason(page)
            if reason != blocked:
                blocked = reason
                _set(project_id, blocked=reason)
                if reason:
                    fd._log(f"    ⚠ Flow báo chặn trong cửa sổ này: {reason}")
        if time.time() - last_tick > 10:
            last_tick = time.time()
            waited = int(time.time() - t0)
            msg = (f"[THỦ CÔNG {idx + 1}/{total}] Chờ bạn dán prompt clip_{idx:02d} vào Flow "
                   f"và bấm Tạo ({waited // 60}:{waited % 60:02d})")
            if blocked:
                msg += " — ⚠ Flow đang báo chặn: tạo ở trình duyệt khác rồi bấm 'Nhập file'"
            p(msg, pct)          # cũng là điểm nhận lệnh Huỷ/Tạm dừng
        page.wait_for_timeout(_POLL_MS)
    raise RuntimeError(
        f"Đã chờ {MANUAL_CLIP_WAIT_SEC // 60} phút mà chưa thấy clip_{idx:02d}. "
        f"Dừng phiên thủ công — các clip đã tải vẫn giữ nguyên, bấm lại "
        f"'Tạo video thủ công' để làm tiếp.")


def manual_clips(project_id: int, progress_cb: Callable[[str, float], None],
                 project_name: str | None = None, style: str | None = None,
                 only: Optional[list[int]] = None) -> list[str]:
    """Phiên bán tự động: làm lần lượt các clip còn thiếu (hoặc đúng `only`)."""
    params = config.PARAMS
    total = params.total_clips
    sels = config.get_selectors("flow")
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd._LOGP = out_dir / "_flow.log"

    items = fd.clip_prompt_list(project_id, project_name, style, params)
    forced = {int(k) for k in (only or []) if 0 <= int(k) < total}
    todo = [it for it in items if it["index"] in forced or (not forced and not it["done"])]
    done_now = [it["index"] for it in items if it["done"]]
    fd.assert_english_prompts([(it["index"], it["prompt"]) for it in todo])
    _STATE[project_id] = {}
    _set(project_id, active=True, phase="opening", current=None, prompt="",
         ingredients=[], blocked=None, total=total, done=done_now,
         todo=[it["index"] for it in todo], message="")
    fd._log(f"=== [THỦ CÔNG] project {project_id}: cần {len(todo)} clip "
            f"{[it['index'] for it in todo]} ===")

    def p(msg: str, pct: float) -> None:
        _set(project_id, message=msg)
        progress_cb(msg, pct)

    if not todo:
        _set(project_id, active=False, phase="done")
        p(f"Đã đủ {total} clip — không cần tạo thêm.", 100.0)
        return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)]

    need_imgs = sorted({i for it in todo for i in it["ingredients"]})
    missing = [i for i in need_imgs if not (img_dir / f"{i}.png").exists()]
    if missing:
        raise FileNotFoundError(
            f"Thiếu ảnh {', '.join(f'{i}.png' for i in missing)} trong {img_dir} — "
            f"hãy tạo ảnh trước.")
    img_paths = [str(config.clip_ingredient_path(project_id, project_name, i))
                 for i in need_imgs]

    try:
        with site_session("flow", "Flow", BrowserSession) as sess:   # GPM nếu đã chọn ở Cài đặt
            page = sess.new_page()
            _open_project(page, sels, params, img_paths, p)
            for n, it in enumerate(todo):
                idx, ings = it["index"], it["ingredients"]
                dest = out_dir / it["name"]
                pct = 10.0 + n / len(todo) * 85.0
                _set(project_id, phase="preparing", current=idx, prompt=it["prompt"],
                     ingredients=ings, blocked=None)
                p(f"[THỦ CÔNG {n + 1}/{len(todo)}] Đưa nguyên liệu ảnh "
                  f"{'+'.join(map(str, ings))} vào ô soạn cho clip_{idx:02d}…", pct)
                fd._log(f"--- [THỦ CÔNG] clip_{idx:02d} (nguyên liệu {ings}) ---")
                _prepare_composer(page, sels, ings)
                before = _stamp(dest)
                prev_count = fd._clip_play_count(page)
                prev_top = fd._top_done_clip(page)
                _set(project_id, phase="waiting_user")
                got = _wait_user_clip(page, project_id, idx, dest, prev_count, prev_top,
                                      before, p, pct, len(todo))
                if got == "generated":
                    _set(project_id, phase="downloading")
                    p(f"[THỦ CÔNG {n + 1}/{len(todo)}] Clip xong — tải về "
                      f"{dest.name}…", pct)
                    page.wait_for_timeout(1500)
                    ok = False
                    for attempt in range(2):
                        if fd._download_newest_clip(page, sels, dest,
                                                    config.BROWSER.clip_wait_sec,
                                                    pace=False):
                            ok = True
                            break
                        page.wait_for_timeout(4000)
                    if not ok:
                        # Không tải được → chờ người dùng tự tải + Nhập file.
                        _set(project_id, phase="waiting_user",
                             blocked="Tool không tải được clip này — hãy tải tay từ "
                                     "Flow rồi bấm 'Nhập file' cho " + dest.name)
                        fd._log(f"    ✗ không tải được {dest.name} — chờ nhập file tay")
                        _wait_user_clip(page, project_id, idx, dest,
                                        fd._clip_play_count(page),
                                        fd._top_done_clip(page), before, p, pct,
                                        len(todo))
                        if not (_stamp(dest) and _stamp(dest) != before):
                            continue
                _mark_done_in_plan(out_dir, idx)
                fd._log(f"    ✓ [THỦ CÔNG] ĐÃ LƯU {dest.name} "
                        f"({dest.stat().st_size // 1024} KB, {got})")
                with _LOCK:
                    st = _STATE.get(project_id, {})
                    st["done"] = sorted(set(st.get("done", [])) | {idx})
                    st["todo"] = [k for k in st.get("todo", []) if k != idx]
    except Exception as e:
        _set(project_id, active=False, phase="error", current=None)
        if _is_closed_error(e):
            raise RuntimeError(
                "Cửa sổ Flow của tool đã bị đóng giữa chừng. Các clip đã tải vẫn "
                "giữ nguyên — bấm lại 'Tạo video thủ công' để làm tiếp "
                "(đừng đóng cửa sổ Cốc Cốc của tool).") from e
        raise
    finally:
        with _LOCK:
            st = _STATE.get(project_id)
            if st and st.get("active"):
                st.update(active=False, phase="stopped", current=None)

    missing_after = [k for k in range(total) if not fd._clip_done(out_dir, k)]
    _set(project_id, active=False, phase="done", current=None, prompt="")
    fd._log(f"=== [THỦ CÔNG] xong phiên — còn thiếu {missing_after} ===")
    p(f"Phiên thủ công xong — đã có {total - len(missing_after)}/{total} clip.", 96.0)
    return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)
            if k not in missing_after]
