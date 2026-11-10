"""
video/muse_video.py — Tạo CLIP bằng Muse.ai (trợ lý AI dạng chat, có "máy riêng")
qua profile GPMLogin (connect_over_cdp), rồi tải về + hậu kỳ.

Muse KHÔNG có ô thông số (tỉ lệ / độ phân giải / thời lượng) → mọi yêu cầu nằm
TRONG prompt tiếng Anh (MUSE_VIDEO_SPEC): 16:9, 1920x1080, đúng 8 giây, không
tiếng, giữ nguyên nhân vật theo ảnh đính kèm. Bảng tham chiếu nhân vật + phân
tích nhân vật VẪN do Gemini làm (bước ảnh không đổi) — ở đây chỉ đính kèm
images/{i}.png của clip như Flow/Gemini.

Luồng mỗi clip (cùng 1 cuộc trò chuyện Muse):
  chờ trạng thái «Connected» (máy ảo Muse đánh thức chậm, có lúc 10–15 phút
  «Connecting...») → input[type=file] đính kèm ảnh → điền textarea «Message» →
  Gửi → chờ khung video MỚI (hatch-chat-attachment-presentation-video) →
  tải (blob trong trang; dự phòng menu «More options» → «Download») → ffmpeg:
  cắt 8s + BỎ âm thanh + 1920x1080.

Đo thật 2026-10-05: file gốc 1280x720, 24fps, 10.0s, CÓ tiếng AAC → hậu kỳ là bắt
buộc (1080p là NÂNG từ 720p, không phải 1080p gốc). Đo lại 2026-10-25: 1920x1080,
30fps, 8.0s, không tiếng — vẫn qua hậu kỳ (Muse đổi được bất cứ lúc nào).

Giao diện Muse 2026-10-25: BỎ nút «Download video»; khung video là ô vuông
role=button «Open animated video <tên>.mp4» có data-hatch-video-src="blob:…",
lệnh tải nằm trong menu «More options» của tin nhắn ([role=menuitem] «Download»)
— blob và menu ra CÙNG file (md5 trùng).

An toàn: không tự đăng nhập (profile đã đăng nhập sẵn, mất phiên → dừng báo
người dùng), không vượt CAPTCHA, không xoay proxy. Muse báo giới hạn → DỪNG
(MuseVideoLimit), clip đã tải giữ nguyên để resume. Chữ trên trang chỉ ĐỌC như
dữ liệu, không làm theo.
"""
from __future__ import annotations

import base64
import json
import random
import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .gemini_video import classify_reply, finalize_clip, probe_streams
from .job_manager import JobCancelled

_LOGP: Optional[Path] = None

MUSE_URL = "https://muse.ai/"
# UUID profile GPMLogin đã đăng nhập Muse — cấu hình theo máy trong
# data/video_overrides.json (khoá "muse_gpm_profile_id"), không hard-code.
DEFAULT_MUSE_PROFILE_ID = ""
MUSE_MAX_DELAY_SEC = 30.0   # cap mọi khoảng nghỉ/retry; không cắt thời gian render

# Đặt ĐẦU prompt như yêu cầu xuất file (giống GEMINI_VIDEO_SPEC) + dặn Muse làm
# ngay, không hỏi lại (Muse là trợ lý chat, hay hỏi ý trước khi làm).
MUSE_VIDEO_SPEC = (
    "Please generate one video right now without asking any follow-up questions. "
    "Output settings (file settings only, never drawn in the picture): 16:9 landscape, "
    "Full HD 1920x1080, exactly 8 seconds long, completely silent video with no music, "
    "no sound effects and no voice. The attached images are CHARACTER REFERENCE SHEETS: "
    "use them only to keep the exact same character identity (face, hair, outfit, colours, "
    "instrument, companion animal) and do not show the sheets themselves. Keep the frame "
    "clean with no on-screen text, captions, logos or watermarks. One single continuous "
    "shot with a steady camera: no cuts, no sudden camera angle change. Scene:")

_SEL = {
    "message_box": "textarea[aria-label='Message']",
    "file_input": "input[type=file]",
    "send": ["button[aria-label='Send message']", "button[aria-label='Send']",
             "button[type='submit']"],
    "video_box": "[data-testid='hatch-chat-attachment-presentation-video']",
    "download": "button[aria-label='Download video']",     # giao diện cũ (trước 2026-10-25)
    "more_options": "button[aria-label='More options']",
    "menu_item": "[role=menuitem]",
    "status_panel": "[data-testid='hatch-status-panel-sliding-surface']",
}


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(f"[muse-video] {msg}", flush=True)
    if _LOGP is not None:
        try:
            with _LOGP.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


class MuseVideoLimit(RuntimeError):
    """Muse báo hết lượt / giới hạn → dừng, resume sau."""


class MuseUnavailable(RuntimeError):
    """Muse không kết nối được (kẹt «Connecting...») hoặc mất đăng nhập."""


def profile_id() -> str:
    v = str(config.load_overrides().get("muse_gpm_profile_id") or "").strip()
    v = v or DEFAULT_MUSE_PROFILE_ID
    if not v:
        raise MuseUnavailable(
            "Chưa chọn profile GPMLogin cho Muse — vào ⚙️ Settings → «🎬 Máy tạo "
            "video qua GPMLogin» chọn profile đã đăng nhập muse.ai.")
    return v


def _setting(key: str, default: float) -> float:
    try:
        return float(config.load_overrides().get(key) or default)
    except (TypeError, ValueError):
        return default


def _capped_delay(seconds: float) -> float:
    """Giới hạn khoảng NGHỈ chủ động; không dùng cho timeout render/download."""
    cap = max(1.0, _setting("muse_max_delay_sec", MUSE_MAX_DELAY_SEC))
    return min(max(0.0, float(seconds)), cap)


# "ingredient 1" (ngôn ngữ của Flow) → "attached image 1" (Muse nhận file đính kèm).
_ING_RE = re.compile(r"\bingredient(s)?\b", re.I)


def with_spec(prompt: str) -> str:
    """Gắn yêu cầu xuất (16:9, 1080p, 8s, không tiếng, giữ nhân vật) vào ĐẦU
    prompt (1 lần); bỏ dòng thông số kiểu Gemini nếu có để không lặp."""
    p = (prompt or "").strip()
    if p.startswith(config.GEMINI_VIDEO_SPEC):
        p = p[len(config.GEMINI_VIDEO_SPEC):].strip()
    if MUSE_VIDEO_SPEC in p:
        return p
    p = _ING_RE.sub(lambda m: "attached images" if m.group(1) else "attached image", p)
    return f"{MUSE_VIDEO_SPEC} {p}".strip()


# ── Đọc trạng thái trang ────────────────────────────────────────
_JS_STATE = r"""([sel, panelSel]) => {
  const boxes = [...document.querySelectorAll(sel)];
  const ready = boxes.filter(b => { const v = b.querySelector('video');
      return !!(v && v.videoWidth > 0 && (v.currentSrc || v.src)); }).length;
  const panel = document.querySelector(panelSel);
  const status = panel ? (panel.innerText || '') : '';
  let t = document.body.innerText || '';
  if (status) t = t.replace(status, '');          // bỏ bảng trạng thái khỏi chữ chat
  // Chat ẢO HOÁ (chỉ giữ ~4 video trong DOM): video mới vào thì video cũ nhất bị
  // gỡ ⇒ đếm số khung không tin được. Video mới = khung nằm trong tin nhắn
  // assistant SAU tin nhắn user cuối cùng (data-message-role / data-message-id).
  const msgs = [...document.querySelectorAll("[data-message-item='true']")];
  let lu = -1;
  msgs.forEach((m, i) => { if (m.getAttribute('data-message-role') === 'user') lu = i; });
  const after = lu >= 0 ? msgs.slice(lu + 1).flatMap(m => [...m.querySelectorAll(sel)]) : [];
  const isReady = b => { const v = b.querySelector('video');
      return !!(v && v.videoWidth > 0 && (v.currentSrc || v.src)); };
  return {boxes: boxes.length, ready, status: status.slice(0, 400),
          has_msgs: msgs.length > 0,
          last_user: lu >= 0 ? msgs[lu].getAttribute('data-message-id') : null,
          after_boxes: after.length, after_ready: after.filter(isReady).length,
          undelivered: t.includes('Delivery not confirmed'),
          login: !document.querySelector("textarea[aria-label='Message']") &&
                 /\b(Log in|Sign in|Continue with)\b/.test(t),
          text: t};
}"""


def _status_kind(status: str) -> str:
    """'connected' | 'connecting' | 'working' | '' — đọc bảng trạng thái Muse
    (dòng thứ 2 dưới tên trợ lý: Connected / Connecting... / is working /
    Rendering video ...)."""
    lines = [x.strip() for x in (status or "").splitlines() if x.strip()]
    if any(x.startswith("Connecting") for x in lines[:3]):
        return "connecting"
    if "Connected" in lines[:3]:
        return "connected"
    return "working" if lines else ""


def _state(page) -> dict:
    st = page.evaluate(_JS_STATE, [_SEL["video_box"], _SEL["status_panel"]])
    st["kind"] = _status_kind(st.get("status", ""))
    return st


def reply_after(text: str, prompt: str) -> str:
    """Câu trả lời của Muse SAU tin nhắn vừa gửi (bỏ nhãn ẩn «Assistant
    message:» và ô soạn «Message» cuối trang)."""
    tail = (prompt or "")[-80:].strip()
    i = text.rfind(tail) if tail else -1
    r = text[i + len(tail):] if i >= 0 else text[-1200:]
    r = r.replace("Assistant message:", "")
    lines = [x for x in r.splitlines() if x.strip()]
    while lines and lines[-1].strip() in ("Message", "Muse"):
        lines.pop()
    return "\n".join(lines)


_CLARIFY_RE = re.compile(r"\?\s*$|would you like|do you want|which (?:one|style)|"
                         r"should i|let me know (?:which|what)", re.I)   # «Let me know if you want … tweaked» = câu chào khi XONG, không phải hỏi lại


# Muse báo LỖI CÔNG CỤ (không phải từ chối nội dung / hết lượt) — vd live 2026-10-05:
# «Couldn't generate that sakura park video — the video tool hit an internal error
# on this one.» ⇒ thử lại (cùng prompt) tới khi được.
_MUSE_ERROR_RE = re.compile(
    r"(?:couldn(?:'|’)?t|could not|can(?:'|’)?t|cannot|unable to|failed to|"
    r"wasn(?:'|’)?t able to)\s+(?:\w+\s+){0,2}?"
    r"(?:generate|create|render|make|produce|finish|export|complete|deliver)|"
    r"internal (?:server )?error|something went wrong|"
    r"(?:hit|ran into|encountered|threw) (?:an? )?(?:\w+ )?(?:error|issue|problem|glitch)|"
    r"(?:tool|render(?:ing)?|generation|generator) (?:failed|errored|crashed|timed out)|"
    r"timed out|service (?:is )?(?:unavailable|overloaded)", re.I)


def classify_muse_reply(reply: str) -> Optional[str]:
    """'limit' | 'refused' | 'transient' | 'error' | 'clarify' | None."""
    kind = classify_reply(reply)
    if kind:
        return kind
    r = (reply or "").strip()
    if re.search(r"(?:usage|rate|daily|weekly) limit|limit (?:reached|exceeded)|"
                 r"upgrade to|out of credits", r, re.I):
        return "limit"
    if re.search(r"against (?:our|my) (?:policies|guidelines)|can(?:'|’)?t help with", r, re.I):
        return "refused"
    if _MUSE_ERROR_RE.search(r):
        return "error"
    lines = [x for x in r.splitlines() if x.strip()]
    if lines and _CLARIFY_RE.search(lines[-1]) and len(r) < 1500:
        return "clarify"
    return None


def wait_connected(page, timeout_sec: float, tick: Callable[[float], None]) -> None:
    t0 = time.time()
    while True:
        st = _state(page)
        if st["login"]:
            raise MuseUnavailable(
                "Muse.ai đang ở trang đăng nhập — hãy mở profile GPM này và TỰ đăng "
                "nhập Muse (tool không tự đăng nhập), rồi chạy lại.")
        if st["kind"] == "connected":
            return
        waited = time.time() - t0
        if waited > timeout_sec:
            raise MuseUnavailable(
                f"Muse chưa sẵn sàng sau {int(timeout_sec)}s (trạng thái "
                f"«{st['kind'] or '?'}» — máy ảo của Muse chưa thức / đang bận) — thử lại "
                "sau ít phút bằng 'Tạo tiếp clip thiếu' (resume).")
        tick(waited)
        # Kẹt «Connecting» lâu → tải lại trang mỗi ~5 phút (Muse tự gọi đánh thức
        # máy ảo khi nạp trang). Đang bận («working») thì chỉ đợi.
        if st["kind"] == "connecting" and waited > 60 and int(waited) % 300 < 10:
            page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10_000)


def _send(page, paths: list[str], prompt: str) -> str:
    if paths:                            # ảnh Muse (bảng nhân vật) gửi KHÔNG đính kèm
        page.locator(_SEL["file_input"]).first.set_input_files(paths)
        page.wait_for_timeout(5000)      # Muse tải ảnh lên (ô xem trước hiện ra)
    box = page.locator(_SEL["message_box"]).first
    box.click()
    box.fill(prompt)
    page.wait_for_timeout(1200)
    for sel in _SEL["send"]:
        loc = page.locator(sel)
        if loc.count() and loc.first.is_visible() and loc.first.is_enabled():
            loc.first.click()
            return sel
    box.press("Enter")
    return "Enter"


def _baseline(page) -> dict:
    """Mốc TRƯỚC khi gửi: tin nhắn user cuối + số khung video (dự phòng)."""
    st = _state(page)
    return {"boxes": st.get("boxes", 0), "last_user": st.get("last_user"),
            "has_msgs": bool(st.get("has_msgs"))}


def _new_video(st: dict, base) -> tuple[int, int]:
    """(số khung mới, số khung mới đã nạp video) so với mốc trước khi gửi."""
    if isinstance(base, int):
        base = {"boxes": base, "last_user": None, "has_msgs": False}
    if st.get("has_msgs") and base.get("has_msgs"):
        # chưa thấy tin nhắn user MỚI (vừa gửi) → các video sau tin cũ là video cũ
        if not st.get("last_user") or st.get("last_user") == base.get("last_user"):
            return 0, 0
        return st.get("after_boxes", 0), st.get("after_ready", 0)
    n0 = base.get("boxes", 0)
    return max(0, st.get("boxes", 0) - n0), max(0, st.get("ready", 0) - n0)


_NOVIDEO_STABLE_SEC = 240     # câu trả lời đứng yên bao lâu mới coi là «không có video»
_NOVIDEO_MIN_SEC = 360        # và tổng thời gian chờ tối thiểu


def _wait_video(page, prompt: str, base, timeout_sec: float,
                tick: Callable[[float], None]) -> tuple[str, dict]:
    """Chờ khung video MỚI (ready) hoặc câu trả lời có kết luận."""
    t0 = time.time()
    settled_text, settled_at = "", 0.0
    while time.time() - t0 < timeout_sec:
        page.wait_for_timeout(8000)
        st = _state(page)
        nb, nr = _new_video(st, base)
        if nr > 0 or (nb > 0 and time.time() - t0 > 60):
            # khung mới có rồi → đợi thêm cho <video> nạp (blob) trước khi tải
            for _ in range(10):
                if _new_video(_state(page), base)[1] > 0:
                    break
                page.wait_for_timeout(3000)
            return "ready", st
        reply = reply_after(st["text"], prompt)
        kind = classify_muse_reply(reply)
        if kind in ("limit", "refused", "transient", "error"):
            return kind, {"text": reply[-400:]}
        if "Delivery not confirmed" in reply and time.time() - t0 > 90:
            return "transient", {"text": "Delivery not confirmed"}
        # Muse đã RẢNH (Connected) mà câu trả lời đứng yên lâu, không có video
        # → hỏi lại («clarify», 90s) hoặc trả lời chữ («novideo»). Live 2026-10-05:
        # panel vẫn «Connected» khi Muse đang dựng, video tới ở ~130s → «novideo»
        # phải đợi lâu hơn hẳn 1 lượt dựng (~2–2.5 phút) mới kết luận.
        stable, waited = time.time() - settled_at, time.time() - t0
        if reply != settled_text:
            settled_text, settled_at = reply, time.time()
        elif st["kind"] == "connected" and (
                (kind == "clarify" and stable > 90 and waited > 120)
                or (stable > _NOVIDEO_STABLE_SEC and waited > _NOVIDEO_MIN_SEC)):
            return (kind or "novideo"), {"text": reply[-400:]}
        tick(time.time() - t0)
    return "timeout", {"text": reply_after(_state(page)["text"], prompt)[-400:]}


def _late_video(page, base, checks: int = 3) -> bool:
    """Sau khi đã kết luận lỗi: video vẫn có thể tới muộn — kiểm lại trước khi
    gửi lại (gửi lại = tốn thêm 1 lượt Muse)."""
    for i in range(checks):
        if _new_video(_state(page), base)[1] > 0:
            return True
        if i < checks - 1:
            page.wait_for_timeout(10_000)
    return False


def _pause_ticking(page, seconds: float, tick: Callable[[float], None]) -> None:
    """Nghỉ nhưng vẫn gọi tick mỗi ~10s (cập nhật tiến độ + cho phép huỷ job)."""
    end = time.time() + seconds
    while (left := end - time.time()) > 0:
        tick(left)
        page.wait_for_timeout(min(10_000, left * 1000))


# Video MỚI NHẤT (sau tin user cuối; chưa có cấu trúc tin nhắn → khung cuối trang):
# đánh dấu data-mm-dl (cho menu dự phòng nhắm đúng khung), cuộn tới (khung ngoài
# màn hình chỉ là skeleton, chưa có blob), rồi fetch blob → base64.
_JS_FETCH_VIDEO = r"""async (sel) => {
  const msgs = [...document.querySelectorAll("[data-message-item='true']")];
  let lu = -1;
  msgs.forEach((m, i) => { if (m.getAttribute('data-message-role') === 'user') lu = i; });
  const boxes = lu >= 0 ? msgs.slice(lu + 1).flatMap(m => [...m.querySelectorAll(sel)])
                        : [...document.querySelectorAll(sel)];
  document.querySelectorAll('[data-mm-dl]').forEach(e => e.removeAttribute('data-mm-dl'));
  const box = boxes.pop();
  if (!box) return null;
  box.setAttribute('data-mm-dl', '1');
  box.scrollIntoView({block: 'center'});
  const srcOf = () => { const w = box.querySelector('[data-hatch-video-src]'),
                              v = box.querySelector('video');
    return (w && w.getAttribute('data-hatch-video-src')) || (v && (v.currentSrc || v.src)) || ''; };
  let src = srcOf();
  for (let i = 0; i < 30 && !src; i++) { await new Promise(r => setTimeout(r, 500)); src = srcOf(); }
  if (!src) return {b64: null};
  const b = await (await fetch(src)).blob();
  const buf = new Uint8Array(await b.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 32768)
    s += String.fromCharCode.apply(null, buf.subarray(i, i + 32768));
  return {type: b.type, b64: btoa(s)};
}"""


def _download_via_menu(page, raw: Path) -> None:
    """Dự phòng: menu «More options» của tin nhắn chứa video → «Download»
    (không có nút đó → chuột phải lên video; giao diện cũ → «Download video»)."""
    box = page.locator("[data-mm-dl='1']")
    legacy = page.locator(_SEL["download"])
    if not box.count() and legacy.count():
        target = legacy.last
    else:
        msg = box.locator("xpath=ancestor::*[@data-message-item='true'][1]")
        holder = msg if msg.count() else box
        holder.hover(timeout=10_000)
        page.wait_for_timeout(800)
        more = holder.locator(_SEL["more_options"])
        if more.count():
            more.last.click(timeout=10_000)
        else:
            box.click(button="right", timeout=10_000)
        page.wait_for_timeout(1200)
        target = page.locator(_SEL["menu_item"], has_text="Download").last
    try:
        with page.expect_download(timeout=180_000) as dl:
            target.click(timeout=10_000)
        dl.value.save_as(str(raw))
    finally:
        page.keyboard.press("Escape")


def _download(page, raw: Path) -> None:
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.unlink(missing_ok=True)
    try:
        r = page.evaluate(_JS_FETCH_VIDEO, _SEL["video_box"])
    except Exception as e:
        if _is_closed_error(e):
            raise
        _log(f"tải blob lỗi — {str(e)[:160]} → thử menu Download")
        r = None
    if r and r.get("b64"):
        raw.write_bytes(base64.b64decode(r["b64"]))
    else:
        _download_via_menu(page, raw)
    if not raw.exists() or raw.stat().st_size < 20_000:
        raise RuntimeError("file tải về rỗng/quá nhỏ")


_DOWNLOAD_TRIES = 3


def _fetch_clip(page, raw: Path, dest: Path, clip_sec: float, tag: str) -> tuple[dict, dict]:
    """Tải + hậu kỳ video VỪA tạo; lỗi thì tải lại NGAY trên cùng video — không
    gửi lại prompt (gửi lại = tốn thêm 1 lượt Muse mà giao diện hỏng thì vẫn hỏng).
    Hết lượt thử → ném lỗi để dừng job."""
    err: Optional[Exception] = None
    for i in range(1, _DOWNLOAD_TRIES + 1):
        try:
            _download(page, raw)
            src_info = probe_streams(raw)
            info = finalize_clip(raw, dest, max_sec=clip_sec)
            raw.unlink(missing_ok=True)
            return src_info, info
        except Exception as e:
            if _is_closed_error(e):
                raise
            err = e
            _log(f"{tag}: tải/hậu kỳ lỗi (lần tải {i}/{_DOWNLOAD_TRIES}) — {str(e)[:200]}")
            if i < _DOWNLOAD_TRIES:
                page.wait_for_timeout(5000 * i)
    raise RuntimeError(f"{type(err).__name__}: {str(err)[:200]}")


def _pace_span() -> tuple[float, float]:
    ov = config.load_overrides().get("muse_video_pace") or {}
    span = ov.get("between_clips") if isinstance(ov, dict) else None
    try:
        lo, hi = _capped_delay(float(span[0])), _capped_delay(float(span[1]))
        return (min(lo, hi), max(lo, hi))
    except Exception:
        return (8.0, min(20.0, MUSE_MAX_DELAY_SEC))


class _GPMSession:
    """Bật profile GPM → nối CDP (thử lại khi trình duyệt chết ngay sau khi
    bật) → mở tab Muse riêng. Thoát: đóng tab + đóng profile."""

    def __init__(self, pid: str, attempts: int = 3, pause_sec: float = 15.0):
        self.pid, self.attempts, self.pause_sec = pid, attempts, pause_sec
        self.client = None
        self._cdp = None
        self.page = None

    def __enter__(self):
        from .gpm_client import GPMClient
        from .youtube_driver import CDPConnectError, _CDPPlaywright
        self.client = GPMClient()
        last = None
        for i in range(1, self.attempts + 1):
            ep = self.client.start_profile(self.pid)
            try:
                self._cdp = _CDPPlaywright(ep).__enter__()
                break
            except CDPConnectError as e:
                last = e
                _log(f"GPM: không nối được CDP (lần {i}/{self.attempts}) — đóng/bật lại")
                self.client.close_profile(self.pid)
                time.sleep(self.pause_sec)
            except BaseException:
                self.client.close_profile(self.pid)
                raise
        else:
            raise MuseUnavailable(
                f"Không mở được profile GPM {self.pid} ({last}). Nếu cửa sổ profile này "
                "đang mở TAY từ GPMLogin, hãy đóng nó rồi chạy lại.")
        try:
            ctx = self._cdp.browser.contexts[0] if self._cdp.browser.contexts else \
                self._cdp.browser.new_context()
            self.page = next((p for p in ctx.pages if "muse.ai" in (p.url or "")), None)
            if self.page is None:
                self.page = ctx.new_page()
                self.page.goto(MUSE_URL, wait_until="domcontentloaded", timeout=90_000)
            self.page.bring_to_front()
        except BaseException:
            # CDP đã nối mà mở tab Muse lỗi (cửa sổ GPM bị đóng, trang tải >90s…):
            # __exit__ KHÔNG chạy khi __enter__ raise → phải tự dừng Playwright +
            # đóng profile. Bỏ sót ở đây (2026-10-25) để lại Playwright sống trong
            # luồng worker → mọi job sau lỗi «Sync API inside the asyncio loop».
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        cdp, self._cdp = self._cdp, None
        try:
            if cdp is not None:
                cdp.__exit__(None, None, None)
        finally:
            try:
                self.client.close_profile(self.pid)
            except Exception:
                pass
        return False


def _is_closed_error(e: Exception) -> bool:
    s = str(e)
    return ("has been closed" in s or "Target closed" in s
            or type(e).__name__ == "TargetClosedError")


_CLIP_ATTEMPTS = 3
_SESSION_ATTEMPTS = 2


def generate_clips_muse(
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
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = params.total_clips
    clip_sec = float(getattr(params, "clip_seconds", 8) or 8)

    def _p(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    only_set = {int(k) for k in (only or []) if 0 <= int(k) < total}
    resume_eff = resume or bool(only_set)

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

    _LOGP = out_dir / "_muse_video.log"
    try:
        _LOGP.write_text("", encoding="utf-8")
    except Exception:
        pass
    mode = f"ONLY {sorted(only_set)}" if only_set else ("RESUME" if resume else "RESTART")
    _log(f"=== BẮT ĐẦU tạo clip Muse project {project_id} ({mode}) — cần {len(todo)}/{total} ===")
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
    raw_dir = out_dir / "_muse_raw"
    wait_sec = _setting("muse_video_wait_sec", 1500)
    connect_sec = _setting("muse_connect_wait_sec", 1200)
    max_errs = int(_setting("muse_error_retries", 30))

    def _session_pass(pending: list[int]) -> None:
        with _GPMSession(profile_id()) as sess:
            page = sess.page
            for n, k in enumerate(pending):
                base_pct = 2.0 + 96.0 * (len(done) / max(len(todo), 1))
                dest = out_dir / f"clip_{k:02d}.mp4"
                paths = [str(config.clip_ingredient_path(
                    project_id, project_name, i).resolve()) for i in ings[k]]
                last_err = ""
                ok = False
                attempt = errs = sends = 0     # attempt: lỗi thường; errs: Muse báo lỗi công cụ
                while attempt < _CLIP_ATTEMPTS and errs <= max_errs:
                    sends += 1
                    tag = f"clip {k:02d} (lần {sends}" + (f", Muse lỗi {errs}" if errs else "") + ")"
                    # prompt dự phòng: sau khi Muse từ chối/hỏi lại/không ra video, hoặc
                    # luân phiên khi Muse lỗi công cụ ≥3 lần liền với cùng prompt
                    use_fallback = ((attempt > 0 and last_err.startswith(("refused", "clarify", "novideo")))
                                    or (errs >= 3 and errs % 2 == 1))
                    prompt = (_fallback(k)
                              if use_fallback and k != config.INTRO_CLIP else prompts[k])
                    base = None
                    try:
                        wait_connected(page, connect_sec, lambda s: _p(
                            f"[{len(done)+1}/{len(todo)}] Chờ Muse kết nối "
                            f"(Connecting…) — {int(s)}s", base_pct))
                        base = _baseline(page)
                        how = _send(page, paths, prompt)
                        _log(f"{tag}: gửi ({how}, ảnh {ings[k]}, {len(prompt)} ký tự"
                             f"{', prompt dự phòng' if use_fallback else ''})")
                        kind, st = _wait_video(
                            page, prompt, base, wait_sec,
                            lambda s: _p(f"[{len(done)+1}/{len(todo)}] Muse đang tạo "
                                         f"{tag} — {int(s)}s", base_pct))
                    except (MuseUnavailable, JobCancelled):
                        raise          # huỷ job / Muse chưa sẵn sàng: dừng NGAY, không gửi lại
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        last_err = f"ui: {type(e).__name__}: {e}"
                        _log(f"{tag}: lỗi giao diện — {last_err[:200]}")
                        page.wait_for_timeout(5000)
                        if base is None or not _late_video(page, base):
                            attempt += 1
                            continue
                        kind, st = "ready", {}
                        _log(f"{tag}: video đã tới — tải về, không gửi lại")
                    if kind == "limit":
                        _log(f"{tag}: Muse báo giới hạn — {st.get('text', '')[:200]!r}")
                        raise MuseVideoLimit(
                            f"Muse báo hết lượt/giới hạn tạo video ở clip {k:02d} "
                            f"(«{st.get('text', '')[:160]}»). Đã có {len(done)} clip mới — "
                            "để khi có lượt rồi bấm 'Tạo tiếp clip thiếu' (resume).")
                    if kind in ("error", "transient"):
                        # Muse lỗi công cụ / chưa nhận tin → nghỉ rồi gửi lại, KHÔNG tính
                        # vào 3 lần thường (thử tới khi được, trần muse_error_retries)
                        errs += 1
                        last_err = f"{kind}: {st.get('text', '')[:160]}"
                        pause = _capped_delay(30 * errs + random.uniform(0, 20))
                        _log(f"{tag}: Muse báo lỗi — {last_err!r} → nghỉ {int(pause)}s rồi tạo lại")
                        _pause_ticking(page, pause, lambda left: _p(
                            f"[{len(done)+1}/{len(todo)}] Muse lỗi clip {k:02d} ({errs} lần) — "
                            f"tạo lại sau {int(left)}s", base_pct))
                        if not _late_video(page, base):
                            continue
                        _log(f"{tag}: video tới muộn — tải về, không gửi lại")
                    elif kind != "ready":
                        attempt += 1
                        last_err = f"{kind}: {st.get('text', '')[:160]}"
                        _log(f"{tag}: {last_err!r}")
                        page.wait_for_timeout(
                            _capped_delay(random.uniform(15, 30) * attempt) * 1000)
                        if not _late_video(page, base):
                            continue
                        _log(f"{tag}: video tới muộn — tải về, không gửi lại")
                    try:
                        src_info, info = _fetch_clip(
                            page, raw_dir / f"clip_{k:02d}.mp4", dest, clip_sec, tag)
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        raise RuntimeError(
                            f"Clip {k:02d}: Muse ĐÃ tạo video nhưng tải về lỗi "
                            f"{_DOWNLOAD_TRIES} lần ({str(e)[:200]}) — dừng, KHÔNG gửi lại "
                            "cho khỏi tốn lượt Muse (giao diện Muse có thể vừa đổi). "
                            f"Đã có {len(done)}/{len(todo)} clip mới — bấm 'Tạo tiếp clip "
                            "thiếu' (resume).") from e
                    _log(f"{tag}: OK gốc {src_info['width']}x{src_info['height']} "
                         f"{src_info['duration']:.1f}s audio={src_info['audio']} → "
                         f"{info['width']}x{info['height']} {info['duration']:.1f}s không tiếng")
                    _mark_done_in_plan(out_dir, k)
                    fallback_idx.discard(k)
                    done.add(k)
                    ok = True
                    _p(f"[{len(done)}/{len(todo)}] Đã lưu clip_{k:02d}.mp4 "
                       f"(1920x1080, {info['duration']:.0f}s, không tiếng)",
                       2.0 + 96.0 * (len(done) / max(len(todo), 1)))
                    break
                if not ok:
                    raise RuntimeError(
                        f"Clip {k:02d}: Muse thất bại sau {sends} lần gửi ({attempt} lỗi thường, "
                        f"{errs} lần Muse báo lỗi) ({last_err[:200]}). "
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
                if _is_closed_error(e):
                    raise RuntimeError(
                        "Cửa sổ GPM của Muse bị ĐÓNG giữa chừng — đừng đóng cửa sổ GPM khi "
                        f"tool đang tạo clip. Đã có {len(done)} clip mới; bấm 'Tạo tiếp clip "
                        "thiếu' (resume).") from e
                raise
            _log(f"trình duyệt đóng giữa chừng — mở phiên {s_try + 1}/{_SESSION_ATTEMPTS}")
            _p(f"Trình duyệt đóng giữa chừng — mở lại và tạo tiếp {len(pending)} clip...", 2.0)
            time.sleep(5)

    try:
        raw_dir.rmdir()
    except OSError:
        pass
    _log(f"=== XONG: {len(done)} clip mới ===")
    _p("Xong tạo clip (Muse.ai)", 100.0)
    return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)
            if flow_driver._clip_done(out_dir, k)]


__all__ = ["generate_clips_muse", "MuseVideoLimit", "MuseUnavailable",
           "classify_muse_reply", "with_spec", "profile_id", "reply_after"]
