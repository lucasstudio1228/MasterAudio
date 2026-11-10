"""
video/gemini_video.py — Tạo CLIP trực tiếp trên Gemini web (chế độ Video = Veo)
rồi tải về. Thay Flow làm máy tạo clip mặc định (config.clip_engine()).

Mỗi clip = 1 cuộc trò chuyện MỚI (ngữ cảnh sạch):
  '+' → «Tạo video» → Escape lớp phủ thư viện Video → chế độ «Pro Mở rộng»
  (3.1 Pro + Tư duy mở rộng, như ảnh người dùng chốt) → khung Ngang 16:9 →
  đính kèm bảng nhân vật (clip 00 thêm ảnh bìa) → prompt TIẾNG ANH (veo_safety)
  + dòng thông số «Full HD 1080p … 8 seconds» → Gửi → chờ <generated-video> →
  «Tải video xuống» → hậu kỳ ffmpeg: BỎ HẲN âm thanh + đúng 1920x1080.

Đo thật 2026-09-29: ~90s/clip; file gốc 1280x720, 24fps, 8.0s, CÓ tiếng AAC
(prompt ghi 1080p nhưng Gemini vẫn trả 720p) → hậu kỳ là bắt buộc.

An toàn: không tự đăng nhập (chờ người dùng), không vượt CAPTCHA, không xoay
proxy. Gemini báo hết lượt/giới hạn → DỪNG ngay (GeminiVideoLimit), clip đã tải
giữ nguyên để resume. Nội dung trang chỉ được ĐỌC như dữ liệu.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (BrowserSession, click_first, fill_first,
                           wait_for_manual_login)
from .gemini_driver import BrowserClosed, _ensure_open, _is_closed_error
from .job_manager import JobCancelled

_LOGP: Optional[Path] = None


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(f"[gemini-video] {msg}", flush=True)
    if _LOGP is not None:
        try:
            with _LOGP.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


class GeminiVideoLimit(RuntimeError):
    """Gemini báo hết lượt / chạm giới hạn tạo video → dừng, resume sau."""


# ── Phân loại câu trả lời ───────────────────────────────────────
_LIMIT_RE = re.compile(
    r"reached (?:your|the) (?:daily )?limit|video generation limit|"
    r"try again tomorrow|come back tomorrow|out of (?:video )?generations|"
    r"đã đạt (?:đến )?giới hạn|hết lượt|hạn mức|thử lại vào ngày mai|quay lại vào ngày mai",
    re.I)
_REFUSED_RE = re.compile(
    r"can(?:'|’)?t (?:generate|create|make) (?:that|this|the)? ?video|"
    r"cannot (?:generate|create)|unable to (?:generate|create)|"
    r"không thể tạo|không tạo được video", re.I)
_TRANSIENT_RE = re.compile(
    r"something went wrong|try your request again|đã xảy ra lỗi|"
    r"có lỗi xảy ra|vui lòng thử lại", re.I)


def classify_reply(text: str) -> Optional[str]:
    """'limit' | 'refused' | 'transient' | None (chưa có kết luận)."""
    t = text or ""
    if _LIMIT_RE.search(t):
        return "limit"
    if _REFUSED_RE.search(t):
        return "refused"
    if _TRANSIENT_RE.search(t):
        return "transient"
    return None


# JS: trạng thái câu trả lời cuối cùng (số video đã sẵn sàng + chữ).
_JS_STATE = """() => {
  const vids = [...document.querySelectorAll('generated-video video')]
    .filter(v => v.videoWidth > 0 && (v.currentSrc || v.src));
  const m = [...document.querySelectorAll('model-response')];
  const last = m.length ? m[m.length - 1] : null;
  // Bỏ phần "tư duy" (thinking) để không bắt nhầm từ khoá trong suy luận.
  let text = '';
  if (last) {
    const mc = last.querySelector('message-content');
    text = (mc ? mc.innerText : last.innerText) || '';
  }
  return {ready: vids.length, responses: m.length, text: text.slice(0, 800),
          src: vids.length ? (vids[vids.length-1].currentSrc || vids[vids.length-1].src) : ''};
}"""


def with_spec(prompt: str) -> str:
    """Gắn yêu cầu xuất Full HD 1080p + 8s vào ĐẦU prompt (1 lần)."""
    p = (prompt or "").strip()
    if config.GEMINI_VIDEO_SPEC in p:
        return p
    return f"{config.GEMINI_VIDEO_SPEC} {p}".strip()


# ── Hậu kỳ: bỏ âm thanh + 1920x1080 ─────────────────────────────
def probe_streams(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr.strip()[:200]}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"audio": any(s.get("codec_type") == "audio" for s in streams),
            "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
            "duration": float((data.get("format") or {}).get("duration") or 0.0)}


def finalize_clip(raw: Path, dest: Path, max_sec: Optional[float] = None) -> dict:
    """ffmpeg: -an (tắt TOÀN BỘ âm thanh) + scale đúng 1920x1080 (lanczos) +
    H.264 CRF 18. max_sec: cắt còn tối đa N giây (Muse trả 10s → 8s).
    Ghi ra file tạm rồi thay dest. Trả thông số đã kiểm."""
    w, h = config.GEMINI_VIDEO_OUT_SIZE
    tmp = dest.with_name(dest.stem + ".part.mp4")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw), "-map", "0:v:0", "-an", "-sn", "-dn",
         *(["-t", f"{max_sec:g}"] if max_sec else []),
         "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)],
        capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        raise RuntimeError(f"ffmpeg hậu kỳ clip lỗi: {r.stderr.strip()[-300:]}")
    info = probe_streams(tmp)
    if info["audio"] or (info["width"], info["height"]) != (w, h):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Hậu kỳ sai thông số: {info}")
    tmp.replace(dest)
    return info


# ── Thao tác giao diện ──────────────────────────────────────────
def _close_overlays(page, tries: int = 3) -> None:
    for _ in range(tries):
        try:
            if page.locator(".cdk-overlay-backdrop-showing").count() == 0:
                return
        except Exception:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)


def _mode_label(page, vsels) -> str:
    try:
        loc = page.locator(", ".join(vsels["mode_button"])).first
        aria = loc.get_attribute("aria-label", timeout=5000) or ""
    except Exception:
        return ""
    # «Mở công cụ chọn chế độ, hiện tại là Pro Mở rộng» → «Pro Mở rộng»
    return aria.split("hiện tại là", 1)[-1].strip() if "hiện tại là" in aria else aria


# Mức suy luận cao: «Mở rộng/Extended» (cũ) hoặc «Cao/High» (từ 2026-10-13,
# menu chế độ = 3.1 Pro + mức Thấp/Trung bình/Cao/Deep Think).
_THINK_RE = re.compile(r"Mở rộng|Extended|\bCao\b|\bHigh\b", re.I)


def _ensure_mode(page, vsels) -> str:
    """Chế độ phải là 3.1 Pro + mức suy luận cao («Pro Cao»; trước 2026-10-13
    là «Pro Mở rộng»)."""
    label = _mode_label(page, vsels)
    if not re.search(r"\bPro\b", label):
        if click_first(page, vsels["mode_button"], timeout_ms=6000):
            page.wait_for_timeout(900)
            click_first(page, vsels["mode_pro"], timeout_ms=6000)
            page.wait_for_timeout(1200)
            _close_overlays(page, 2)
        label = _mode_label(page, vsels)
    if not _THINK_RE.search(label):
        if click_first(page, vsels["mode_button"], timeout_ms=6000):
            page.wait_for_timeout(900)
            click_first(page, vsels["mode_thinking"], timeout_ms=6000)
            page.wait_for_timeout(1200)
            _close_overlays(page, 2)
        label = _mode_label(page, vsels)
    if not (re.search(r"\bPro\b", label) and _THINK_RE.search(label)):
        raise RuntimeError(f"Không chọn được chế độ «Pro Cao» (đang: {label!r}).")
    return label


def _ensure_landscape(page, vsels) -> None:
    try:
        loc = page.locator(", ".join(vsels["aspect_button"])).first
        aria = loc.get_attribute("aria-label", timeout=5000) or ""
    except Exception:
        aria = ""
    if "16:9" in aria:
        return
    if click_first(page, vsels["aspect_button"], timeout_ms=5000):
        page.wait_for_timeout(800)
        click_first(page, vsels["aspect_landscape"], timeout_ms=5000)
        page.wait_for_timeout(800)
        _close_overlays(page, 2)
    try:
        aria = page.locator(", ".join(vsels["aspect_button"])).first \
            .get_attribute("aria-label", timeout=4000) or ""
    except Exception:
        aria = ""
    if "16:9" not in aria:
        raise RuntimeError(f"Không đặt được khung Ngang 16:9 (đang: {aria!r}).")


def _video_tool_disabled(page, vsels) -> bool:
    """Hết lượt tạo video trong ngày: Gemini vẫn hiện mục «Tạo video» nhưng
    aria-disabled=true (đo thật 2026-09-29 sau ~10 lượt/ngày, tài khoản Ultra)."""
    for sel in vsels["video_tool"]:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            dis = (loc.get_attribute("aria-disabled", timeout=2000) or "").lower()
            cls = loc.get_attribute("class", timeout=2000) or ""
            return dis == "true" or "mdc-list-item--disabled" in cls
        except Exception:
            continue
    return False


def _open_video_composer(page, gsels, vsels) -> str:
    """Chat MỚI ở chế độ Video, đúng chế độ + khung hình. Trả nhãn chế độ."""
    page.goto(config.GEMINI_URL, wait_until="domcontentloaded")
    wait_for_manual_login(page, gsels["prompt_box"], "Gemini", timeout_sec=300)
    page.wait_for_timeout(2500)
    _close_overlays(page)
    if page.locator(", ".join(vsels["video_chip"])).count() == 0:
        if not click_first(page, vsels["tools_menu"], timeout_ms=8000):
            raise RuntimeError("Không mở được menu công cụ '+' của Gemini.")
        page.wait_for_timeout(1000)
        if _video_tool_disabled(page, vsels):
            page.keyboard.press("Escape")
            raise GeminiVideoLimit(
                "Gemini đã KHOÁ mục «Tạo video» (mờ, không bấm được) — tài khoản hết "
                "lượt tạo video trong ngày.")
        if not click_first(page, vsels["video_tool"], timeout_ms=6000):
            page.keyboard.press("Escape")
            raise RuntimeError("Không thấy mục «Tạo video» trong menu '+'.")
        page.wait_for_timeout(2500)
        _close_overlays(page)
    if page.locator(", ".join(vsels["video_chip"])).count() == 0:
        raise RuntimeError("Chưa bật được chế độ Video (không thấy chip «Video»).")
    label = _ensure_mode(page, vsels)
    _ensure_landscape(page, vsels)
    return label


def _attach(page, vsels, paths: list[str], timeout_sec: int = 90) -> None:
    try:
        with page.expect_file_chooser(timeout=10_000) as fc:
            if not click_first(page, vsels["upload_button"], timeout_ms=6000):
                raise RuntimeError("Không thấy nút «Tải tệp lên» ở chế độ Video.")
        fc.value.set_files(paths)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Đính kèm ảnh thất bại ({type(e).__name__}: {e})") from e
    sel = ", ".join(vsels["attachment_chip"])
    deadline = time.time() + timeout_sec
    n = 0
    while time.time() < deadline:
        try:
            n = page.locator(sel).count()
        except Exception:
            n = 0
        if n >= len(paths):
            page.wait_for_timeout(3000)       # chờ upload lên server xong hẳn
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(f"Ảnh chưa đính kèm xong ({n}/{len(paths)} sau {timeout_sec}s).")


def _download(page, vsels, raw: Path, src: str) -> None:
    """«Tải video xuống» (sự kiện download); lỗi → tải thẳng từ src."""
    raw.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.locator("generated-video").last.scroll_into_view_if_needed(timeout=5000)
        btn = page.locator(", ".join(vsels["download_button"])).last
        with page.expect_download(timeout=90_000) as dl:
            btn.click(force=True, timeout=10_000)
        dl.value.save_as(str(raw))
        if raw.exists() and raw.stat().st_size > 10_000:
            return
    except Exception as e:
        _log(f"nút tải lỗi ({type(e).__name__}: {str(e)[:120]}) → tải từ src")
    if not src:
        raise RuntimeError("Không tải được video (không có src).")
    r = page.request.get(src, timeout=120_000)
    body = r.body() if r.ok else b""
    if len(body) <= 10_000:
        raise RuntimeError(f"Tải video từ src thất bại (HTTP {r.status}, {len(body)} byte).")
    raw.write_bytes(body)


def _wait_result(page, timeout_sec: int, tick: Callable[[float], None]) -> tuple[str, dict]:
    """Chờ tới khi có video / lỗi. Trả (kind, state) — kind ∈ ready|limit|
    refused|transient|timeout. Lỗi phải lặp lại 2 lần liên tiếp mới tính."""
    t0 = time.time()
    prev_kind = None
    st: dict = {}
    while time.time() - t0 < timeout_sec:
        _ensure_open(page)
        page.wait_for_timeout(6000)
        tick(time.time() - t0)
        try:
            st = page.evaluate(_JS_STATE) or {}
        except Exception as e:
            if _is_closed_error(e):
                raise
            continue
        if int(st.get("ready") or 0) > 0:
            page.wait_for_timeout(3000)
            try:
                st = page.evaluate(_JS_STATE) or st
            except Exception:
                pass
            return "ready", st
        kind = classify_reply(st.get("text", ""))
        if kind and kind == prev_kind:
            return kind, st
        prev_kind = kind
    return "timeout", st


# ── Luồng chính ─────────────────────────────────────────────────
def _pace_span() -> tuple[float, float]:
    ov = config.load_overrides().get("gemini_video_pace") or {}
    span = ov.get("between_clips") if isinstance(ov, dict) else None
    try:
        lo, hi = float(span[0]), float(span[1])
        return (lo, max(lo, hi))
    except Exception:
        return (6.0, 14.0)


_CLIP_ATTEMPTS = 3
_SESSION_ATTEMPTS = 3


def generate_clips_gemini(
    project_id: int,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
    style: str | None = None,
    only: Optional[list[int]] = None,
    _session_impl: Optional[Callable[[list[int]], None]] = None,
) -> list[str]:
    """Cùng hợp đồng với flow_driver.generate_clips (resume / restart / only)."""
    global _LOGP
    from . import flow_driver
    from .flow_manual import _mark_done_in_plan

    params = params or config.PARAMS
    gsels = config.get_selectors("gemini")
    vsels = config.get_selectors("gemini_video")
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = params.total_clips

    def _p(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    only_set = {int(k) for k in (only or []) if 0 <= int(k) < total}
    resume_eff = resume or bool(only_set)

    # Clip tĩnh dự phòng (từ Flow cũ) trong _plan.json → coi là CHƯA xong.
    fallback_idx: set[int] = set()
    try:
        raw_plan = json.loads((out_dir / "_plan.json").read_text(encoding="utf-8"))
        fallback_idx = {int(x) for x in (raw_plan.get("fallback") or [])}
    except Exception:
        pass
    if resume_eff and config.intro_clip_needs_refresh(project_id, project_name):
        fallback_idx.add(config.INTRO_CLIP)

    if not resume_eff:
        for old in out_dir.glob("clip_*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass

    items = flow_driver.clip_prompt_list(project_id, project_name, style, params)
    prompts = {it["index"]: with_spec(it["prompt"]) for it in items}
    ings = {it["index"]: it["ingredients"] for it in items}
    # Prompt dự phòng khi Gemini TỪ CHỐI prompt cảnh: câu chuyển động chung, gọn.
    wrap = flow_driver._style_wrapper(style)
    cast_mode = config.images_cast_mode(img_dir)
    variants = flow_driver._fallback_variants(flow_driver.motion_base(cast_mode))

    def _fallback(k: int) -> str:
        if config.single_image(params):
            return with_spec(wrap(flow_driver.cover_fallback_motion(k, cast_mode)))
        return with_spec(wrap(variants[k % len(variants)]))
    flow_driver.assert_english_prompts(list(prompts.items()))

    def _is_done(k: int) -> bool:
        if k in only_set:
            return False
        return flow_driver._clip_done(out_dir, k) and k not in fallback_idx

    todo = sorted(only_set) if only_set else [k for k in range(total) if not _is_done(k)]

    _LOGP = out_dir / "_gemini_video.log"
    try:
        _LOGP.write_text("", encoding="utf-8")
    except Exception:
        pass
    mode = f"ONLY {sorted(only_set)}" if only_set else ("RESUME" if resume else "RESTART")
    _log(f"=== BẮT ĐẦU tạo clip Gemini project {project_id} ({mode}) — cần {len(todo)}/{total} ===")
    if not todo:
        _p("Đã đủ clip (resume)", 100.0)
        return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)]

    need_imgs = sorted({i for k in todo for i in ings[k]})
    missing = [i for i in need_imgs if not (img_dir / f"{i}.png").exists()]
    if missing:
        raise FileNotFoundError(
            f"Thiếu ảnh {', '.join(f'{i}.png' for i in missing)} trong {img_dir} — "
            "hãy tạo ảnh trước.")

    done: set[int] = set()
    raw_dir = out_dir / "_gemini_raw"

    def _session_pass(pending: list[int]) -> None:
        with BrowserSession(site="gemini") as sess:
            page = sess.new_page()
            for n, k in enumerate(pending):
                base_pct = 2.0 + 96.0 * (len(done) / max(len(todo), 1))
                dest = out_dir / f"clip_{k:02d}.mp4"
                paths = [str(config.clip_ingredient_path(
                    project_id, project_name, i).resolve()) for i in ings[k]]
                last_err = ""
                ok = False
                for attempt in range(1, _CLIP_ATTEMPTS + 1):
                    tag = f"clip {k:02d} (lần {attempt}/{_CLIP_ATTEMPTS})"
                    use_fallback = attempt > 1 and last_err.startswith("refused")
                    prompt = (_fallback(k)
                              if use_fallback and k != config.INTRO_CLIP else prompts[k])
                    try:
                        _p(f"[{len(done)+1}/{len(todo)}] Mở Gemini Video — {tag}", base_pct)
                        label = _open_video_composer(page, gsels, vsels)
                        _attach(page, vsels, paths)
                        if not fill_first(page, gsels["prompt_box"], prompt):
                            raise RuntimeError("không tìm thấy ô nhập prompt")
                        page.wait_for_timeout(700)
                        if not click_first(page, vsels["send_button"], timeout_ms=6000):
                            raise RuntimeError("không bấm được nút Gửi")
                        _log(f"{tag}: gửi ({label}, ảnh {ings[k]}, {len(prompt)} ký tự"
                             f"{', prompt dự phòng' if use_fallback else ''})")
                        kind, st = _wait_result(
                            page, config.BROWSER.gemini_video_wait_sec,
                            lambda s: _p(f"[{len(done)+1}/{len(todo)}] Gemini đang tạo "
                                         f"{tag} — {int(s)}s", base_pct))
                    except GeminiVideoLimit as e:
                        _log(f"{tag}: {e}")
                        raise GeminiVideoLimit(
                            f"Gemini báo hết lượt/giới hạn tạo video ở clip {k:02d}: {e} "
                            f"Đã có {len(done)} clip mới — để hôm sau / khi có lượt rồi bấm "
                            "'Tạo tiếp clip thiếu' (resume).") from e
                    except JobCancelled:
                        raise          # huỷ job: dừng ngay, không tính là lỗi giao diện
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        last_err = f"ui: {e}"
                        _log(f"{tag}: lỗi giao diện — {str(e)[:200]}")
                        page.wait_for_timeout(5000)
                        continue
                    if kind == "limit":
                        _log(f"{tag}: Gemini báo giới hạn — {st.get('text', '')[:200]!r}")
                        raise GeminiVideoLimit(
                            f"Gemini báo hết lượt/giới hạn tạo video ở clip {k:02d} "
                            f"(«{st.get('text', '')[:160]}»). Đã có {len(done)} clip mới — "
                            "để hôm sau / khi có lượt rồi bấm 'Tạo tiếp clip thiếu' (resume).")
                    if kind != "ready":
                        last_err = f"{kind}: {st.get('text', '')[:160]}"
                        _log(f"{tag}: {last_err!r}")
                        page.wait_for_timeout(random.uniform(15, 30) * 1000 * attempt)
                        continue
                    try:
                        raw = raw_dir / f"clip_{k:02d}.mp4"
                        _download(page, vsels, raw, st.get("src", ""))
                        src_info = probe_streams(raw)
                        info = finalize_clip(raw, dest)
                        raw.unlink(missing_ok=True)
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        last_err = f"download: {e}"
                        _log(f"{tag}: tải/hậu kỳ lỗi — {str(e)[:200]}")
                        continue
                    _log(f"{tag}: OK gốc {src_info['width']}x{src_info['height']} "
                         f"{src_info['duration']:.1f}s audio={src_info['audio']} → "
                         f"{info['width']}x{info['height']} {info['duration']:.1f}s không tiếng")
                    _mark_done_in_plan(out_dir, k)
                    fallback_idx.discard(k)
                    done.add(k)
                    ok = True
                    _p(f"[{len(done)}/{len(todo)}] Đã lưu clip_{k:02d}.mp4 "
                       f"(1920x1080, 8s, không tiếng)",
                       2.0 + 96.0 * (len(done) / max(len(todo), 1)))
                    break
                if not ok:
                    raise RuntimeError(
                        f"Clip {k:02d}: thất bại sau {_CLIP_ATTEMPTS} lần ({last_err[:200]}). "
                        f"Đã có {len(done)}/{len(todo)} clip mới — bấm 'Tạo tiếp clip thiếu' "
                        "(resume) để làm nốt, clip đã tải không mất.")
                if n < len(pending) - 1:
                    lo, hi = _pace_span()
                    page.wait_for_timeout(random.uniform(lo, hi) * 1000)

    for s_try in range(1, _SESSION_ATTEMPTS + 1):
        pending = [k for k in todo if k not in done]
        if not pending:
            break
        try:
            (_session_impl or _session_pass)(pending)
            break
        except Exception as e:
            if not _is_closed_error(e) or s_try >= _SESSION_ATTEMPTS:
                raise
            _log(f"trình duyệt đóng giữa chừng — mở phiên {s_try + 1}/{_SESSION_ATTEMPTS}")
            _p(f"Trình duyệt đóng giữa chừng — mở lại và tạo tiếp {len(pending)} clip...", 2.0)
            time.sleep(5)

    try:
        raw_dir.rmdir()
    except OSError:
        pass
    _log(f"=== XONG: {len(done)} clip mới ===")
    _p("Xong tạo clip (Gemini)", 100.0)
    return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)
            if flow_driver._clip_done(out_dir, k)]


__all__ = ["generate_clips_gemini", "GeminiVideoLimit", "BrowserClosed",
           "classify_reply", "finalize_clip", "probe_streams", "with_spec"]
