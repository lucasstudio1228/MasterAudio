"""
video/chatgpt_thumbnail.py — ChatGPT web vẽ thumbnail (ảnh + chữ tiêu đề).

MẶC ĐỊNH từ 2026-10-23 (Cài đặt `thumb_engine="chatgpt"`). Mở chatgpt.com trong
profile RIÊNG (.browser_profile_chatgpt — config.SITE_PROFILE) → chat THƯỜNG
(2026-10-25: chat tạm thời KHÔNG có công cụ vẽ ảnh — ChatGPT đáp «can't access the
image-generation tool in this temporary chat»; bật lại bằng override
`chatgpt_temporary_chat: true`, lỗi → tự chuyển chat thường) → đính kèm ảnh bìa
sạch (ảnh 0) → prompt tiếng
Anh theo công thức thumbnail (thumbnail_ai.build_prompt) → chờ ảnh → tải bằng
fetch trong trang. Cắt 1280x720 / kiểm tra chữ / cache / thử lại do
thumbnail_ai.generate lo (dùng chung với máy API).

An toàn:
  • KHÔNG tự đăng nhập, không nhập email / mật khẩu / OTP. Chưa đăng nhập → kéo
    cửa sổ lên và CHỜ người dùng tự đăng nhập (nút «Mở ChatGPT để đăng nhập»).
  • Cloudflare / captcha → kéo cửa sổ lên + chờ người tự xác minh, KHÔNG bấm.
  • Chỉ đọc cuộc chat tool vừa mở; không đọc lịch sử / chat cá nhân.
  • Không ghi link ảnh (có chữ ký truy cập) hay cookie vào log.
  • Selector best-effort: config.CHATGPT_SELECTORS (override video_overrides.json).
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote_to_bytes, urlparse

from . import config
from .thumbnail_ai import DrawEngine, ThumbAIError

SITE = "chatgpt"
MODEL_TAG = "chatgpt-web"
LABEL = "ChatGPT web"
TEMP_CHAT_URL = config.CHATGPT_URL + "?temporary-chat=true"
LOGIN_WAIT_AUTO = 180        # ghép video tự động: chờ đăng nhập / xác minh ≤ 3 phút
LOGIN_WAIT_MANUAL = 600      # người dùng bấm nút: ≤ 10 phút
GEN_TIMEOUT = 480            # ChatGPT vẽ 1–4 phút, giờ đông có thể xếp hàng
IDLE_NO_IMAGE = 25           # trả lời xong mà 25s không có ảnh → coi như không vẽ
PING_EVERY = 15              # giây — báo tiến độ (cũng là điểm kiểm cờ Huỷ)
LOGIN_FILE_NAME = "chatgpt_login.json"

NUDGE = ("Please generate the image now with your image generation tool, exactly as "
         "described above. Do not ask questions.")

_STATE_MSG = {
    "logged_out": "Chưa đăng nhập ChatGPT — hãy TỰ đăng nhập trong cửa sổ vừa bật lên "
                  "(tool không nhập email/mật khẩu). Tool chờ rồi tự chạy tiếp.",
    "login_flow": "Đang ở trang đăng nhập OpenAI — hãy tự hoàn tất đăng nhập "
                  "(tool không nhập gì). Tool chờ rồi tự chạy tiếp.",
    "challenge": "ChatGPT yêu cầu xác minh (Cloudflare / captcha) — hãy TỰ xác minh "
                 "trong cửa sổ vừa bật lên; tool KHÔNG bấm vào ô xác minh.",
}


class _NoImage(Exception):
    """ChatGPT trả lời xong nhưng không có ảnh (thông điệp = trích lời đáp)."""


# ── Trạng thái đăng nhập (đọc nhanh, không mở trình duyệt) ───────

def _login_file() -> Path:
    from .thumbnail_ai import _db_path
    return _db_path().parent / LOGIN_FILE_NAME


def login_status() -> dict:
    """Lần kiểm tra gần nhất: {logged_in: True/False/None, checked_at: iso|None}."""
    try:
        d = json.loads(_login_file().read_text(encoding="utf-8"))
    except Exception:
        return {"logged_in": None, "checked_at": None}
    li = d.get("logged_in")
    return {"logged_in": li if isinstance(li, bool) else None,
            "checked_at": d.get("checked_at")}


def record_login(logged_in: bool) -> None:
    """Chỉ lưu CÓ/KHÔNG đăng nhập + thời điểm — không lưu tài khoản / cookie."""
    try:
        p = _login_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"logged_in": bool(logged_in),
                                   "checked_at": datetime.now(timezone.utc).isoformat()}),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


# ── Nhận diện trang ──────────────────────────────────────────────

_CHALLENGE_JS = r"""() => {
  const t = (document.title || '').toLowerCase();
  if (/just a moment|attention required|một chút/.test(t)) return true;
  return !!document.querySelector(
    "iframe[src*='challenges.cloudflare.com'], iframe[src*='hcaptcha.com'], " +
    "iframe[src*='arkoselabs'], #challenge-form, #cf-challenge-running, #challenge-stage");
}"""


def _visible(page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            continue
    return False


def page_state(page, sels: dict) -> str:
    """challenge | login_flow | logged_out | logged_in | ready | loading.
    ready = có ô nhập nhưng chưa thấy nút đăng nhập lẫn nút hồ sơ."""
    try:
        if page.evaluate(_CHALLENGE_JS):
            return "challenge"
        host = (urlparse(page.url).hostname or "").lower()
    except Exception:
        return "loading"
    if not host or host == "about:blank":
        return "loading"
    if not host.endswith("chatgpt.com"):
        return "login_flow"               # auth.openai.com, Google, Microsoft…
    if _visible(page, sels.get("login_button", [])):
        return "logged_out"
    if _visible(page, sels.get("profile_button", [])):
        return "logged_in"
    if _visible(page, sels.get("prompt_box", [])):
        return "ready"
    return "loading"


# Ảnh trong cuộc chat: bỏ ảnh của lượt NGƯỜI DÙNG (ảnh bìa đính kèm), khung soạn,
# thanh bên; bỏ ảnh xem trước còn mờ. Trả cả lời đáp gần nhất (chỉ cuộc chat này).
_SNAPSHOT_JS = r"""() => {
  const userTurn = (el) => {
    if (el.closest('[data-message-author-role="user"]')) return true;
    const turn = el.closest('[data-testid^="conversation-turn"], article');
    return !!(turn && (turn.getAttribute('data-turn') === 'user' ||
                       turn.querySelector('[data-message-author-role="user"]')));
  };
  const blurred = (el) => {
    for (let n = el, i = 0; n && i < 4; n = n.parentElement, i++) {
      const f = getComputedStyle(n).filter || '';
      if (f.includes('blur')) return true;
    }
    return false;
  };
  const imgs = [];
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.src || '';
    // ChatGPT's current image viewer commonly exposes the completed image as a
    // blob: URL (and occasionally a data: URL).  Those URLs are deliberately
    // short lived, but they are readable from this page.  Do not discard them:
    // _download() runs inside the same browser context.
    if (!src) continue;
    if (img.closest('nav, aside, form, header, #thread-bottom-container, [data-testid="composer"]')) continue;
    if (userTurn(img)) continue;
    if (!img.complete || img.naturalWidth < 512 || img.naturalHeight < 340) continue;
    imgs.push({src, w: img.naturalWidth, h: img.naturalHeight, blurred: blurred(img)});
  }
  const turns = document.querySelectorAll('[data-testid^="conversation-turn"], article');
  let reply = '';
  for (let i = turns.length - 1; i >= 0; i--) {
    const t = turns[i];
    if (t.getAttribute('data-turn') === 'user' ||
        t.querySelector('[data-message-author-role="user"]')) break;
    reply = (t.innerText || '').trim();
    if (reply) break;
  }
  if (!reply) {
    const a = document.querySelectorAll('[data-message-author-role="assistant"]');
    if (a.length) reply = (a[a.length - 1].innerText || '').trim();
  }
  const low = reply.toLowerCase();
  const creating = /creating image|generating image|đang tạo (hình )?ảnh|đang tạo hình/.test(low);
  const users = document.querySelectorAll('[data-message-author-role="user"]').length;
  return {imgs, reply: reply.slice(0, 400), creating, users};
}"""

_ATTACHED_JS = r"""() => {
  const box = document.querySelector('#prompt-textarea, div[contenteditable="true"]');
  const root = (box && (box.closest('form') ||
               box.parentElement?.parentElement?.parentElement?.parentElement)) || document;
  return !!root.querySelector('img[src^="blob:"], img[alt], [data-testid*="attachment"], [data-testid*="file"]');
}"""

_FETCH_JS = r"""async (src) => {
  try {
    const r = await fetch(src, {credentials: 'include'});
    if (!r.ok) return {error: 'HTTP ' + r.status};
    const b = await r.blob();
    const data = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(b);
    });
    return {data, type: b.type, size: b.size};
  } catch (e) { return {error: String(e).slice(0, 160)}; }
}"""

_CANVAS_JS = r"""async (src) => {
  try {
    const all = Array.from(document.querySelectorAll('img'));
    const img = all.find(x => (x.currentSrc || x.src || '') === src);
    if (!img) return {error: 'image element disappeared'};
    if (!img.complete) await new Promise((resolve, reject) => {
      img.addEventListener('load', resolve, {once: true});
      img.addEventListener('error', () => reject(new Error('image load failed')), {once: true});
      setTimeout(() => reject(new Error('image load timeout')), 15000);
    });
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    if (w < 1 || h < 1) return {error: 'image has no dimensions'};
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    c.getContext('2d', {alpha: false}).drawImage(img, 0, 0, w, h);
    const data = c.toDataURL('image/png');
    return {data, type: 'image/png', size: Math.floor(data.length * .75), w, h};
  } catch (e) { return {error: String(e).slice(0, 160)}; }
}"""

_LIMIT_RE = re.compile(
    r"limit|giới hạn|hết lượt|try again later|thử lại sau|can.?t (help|create|generate)|"
    r"unable to (create|generate)|không thể (tạo|giúp)|content polic|chính sách|vi phạm",
    re.I)


REFERENCE_TASK = ("Use the attached picture only as the reference for the same "
                  "person, outfit and place, and create the NEW picture described below.")


def chatgpt_prompt(base: str, *, edit: bool = True, task: str | None = None) -> str:
    """Prompt 1 dòng (Enter trong ô soạn = gửi) — 100% tiếng Anh.
    edit=False: vẽ mới từ mô tả (ảnh bìa gốc, không đính kèm ảnh).
    task: câu giao việc riêng (vd REFERENCE_TASK — vẽ bảng tham chiếu từ ảnh bìa)."""
    task = task or ("Edit the attached picture into the finished thumbnail described below."
                    if edit else "Create the picture described below from scratch.")
    head = ("Use your image generation tool to create ONE image right now - do not ask "
            f"questions and do not answer with text only. {task} Output: a single "
            "landscape image, 1536x1024.")
    body = " ".join(line.strip() for line in base.splitlines() if line.strip())
    return f"{head} {body}"


# ── Điều khiển trình duyệt ──────────────────────────────────────

class _ChatGPT:
    """Mở trình duyệt LƯỜI (chỉ khi thật sự phải vẽ — ảnh cache thì không mở)."""

    def __init__(self, log: Callable[[str], None], login_wait_sec: int):
        self._log = log
        self.login_wait = int(login_wait_sec)
        self.sels = config.get_selectors(SITE)
        # Chat tạm thời không vẽ được ảnh (2026-10-25) → mặc định chat thường.
        self.temporary = bool(config.load_overrides().get("chatgpt_temporary_chat"))
        self._in_temp = False             # lượt đang chạy là chat tạm thời?
        self.sess = None
        self.page = None
        self.tmpdir: Optional[str] = None
        self._last_ping = 0.0

    # vòng đời
    def open(self) -> None:
        from .browser_base import BrowserSession
        self._log("Mở ChatGPT trong cửa sổ trình duyệt riêng của tool…")
        sess = BrowserSession(headless=False, site=SITE)
        sess.__enter__()
        self.sess = sess
        ctx = sess.context
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()

    def close(self) -> None:
        if self.sess is not None:
            try:
                self.sess.__exit__(None, None, None)
            except Exception:
                pass
            self.sess = None
            self.page = None
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.tmpdir = None

    # tiện ích
    def _ping(self, msg: str, force: bool = False) -> None:
        """Báo tiến độ thưa (≥ PING_EVERY s) — cũng là chỗ nút Huỷ có hiệu lực."""
        now = time.time()
        if force or now - self._last_ping >= PING_EVERY:
            self._last_ping = now
            self._log(msg)

    def _alert(self, msg: str) -> None:
        """Kéo cửa sổ lên + kêu bíp để người dùng tự xử lý. KHÔNG bấm gì trên trang."""
        self._log(msg)
        try:
            from .window_focus import raise_page_window
            raise_page_window(self.page)
        except Exception:
            pass
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    def _ensure_ready(self, url: str) -> None:
        """Chờ trang sẵn sàng + đã đăng nhập. Phải chờ người (đăng nhập / xác minh)
        thì xong mở lại `url` (sau đăng nhập trang có thể ở chỗ khác)."""
        from .browser_base import wait_ms
        deadline = time.time() + self.login_wait
        waited_human = False
        alerted = None
        ready_since = None
        while True:
            st = page_state(self.page, self.sels)
            if st == "ready":
                ready_since = ready_since or time.time()
            else:
                ready_since = None
            if st == "logged_in" or (ready_since and time.time() - ready_since >= 6):
                record_login(True)
                if not waited_human:
                    return
                waited_human = False
                alerted = None
                self._log("Đã đăng nhập / xác minh xong — mở lại chat mới…")
                self.page.goto(url, wait_until="domcontentloaded")
                ready_since = None
                continue
            if st in _STATE_MSG:
                waited_human = True
                if st == "logged_out":
                    record_login(False)
                if alerted != st:
                    alerted = st
                    self._alert(_STATE_MSG[st])
            left = int(deadline - time.time())
            if left <= 0:
                if st in ("logged_out", "login_flow"):
                    raise ThumbAIError(
                        "Chưa đăng nhập ChatGPT — mở ⚙️ Cài đặt → Thumbnail → «🤖 Mở ChatGPT "
                        "để đăng nhập» rồi tự đăng nhập (tool không nhập mật khẩu).")
                if st == "challenge":
                    raise ThumbAIError("ChatGPT đang yêu cầu xác minh (Cloudflare) — tool "
                                       "không tự bấm; hãy tự xác minh rồi tạo lại.")
                raise ThumbAIError("Không thấy ô nhập của ChatGPT (giao diện đổi?) — cần "
                                   "cập nhật config.CHATGPT_SELECTORS.")
            if st in _STATE_MSG:
                self._ping(f"Đang chờ bạn xử lý cửa sổ ChatGPT… (còn ~{left}s)")
            wait_ms(self.page, 1500)

    def _wait_attachment(self) -> None:
        from .browser_base import wait_ms
        end = time.time() + 30
        while time.time() < end:
            try:
                if self.page.evaluate(_ATTACHED_JS):
                    wait_ms(self.page, 1500)
                    return
            except Exception:
                pass
            wait_ms(self.page, 700)
        self._log("Chưa thấy ảnh xem trước trong khung soạn — vẫn thử gửi.")

    def _type(self, box, text: str) -> None:
        page = self.page
        box.click()
        try:
            box.fill(text)
        except Exception:
            page.keyboard.insert_text(text)
        got = (box.inner_text() or "").strip()
        if len(got) < len(text) * 0.8:
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(text)
            got = (box.inner_text() or "").strip()
            if len(got) < len(text) * 0.8:
                raise ThumbAIError("Không nhập được prompt vào ô chat ChatGPT.")

    def _send(self) -> None:
        from .browser_base import query_first, wait_ms
        page = self.page
        before = self._snapshot().get("users", 0)
        end = time.time() + 90
        clicked = False
        while time.time() < end:
            btn = query_first(page, self.sels["send_button"], timeout_ms=1500)
            if btn is not None:
                try:
                    if btn.is_enabled():
                        btn.click()
                        clicked = True
                        break
                except Exception:
                    pass
            self._ping("Chờ ảnh bìa tải lên ChatGPT xong để gửi…")
            wait_ms(page, 1000)
        if not clicked:
            raise ThumbAIError("Nút gửi của ChatGPT không bật (ảnh tải lên chưa xong / "
                               "giao diện đổi).")
        end = time.time() + 15
        while time.time() < end:
            wait_ms(page, 700)
            if (_visible(page, self.sels["stop_button"])
                    or self._snapshot().get("users", 0) > before):
                return
        raise ThumbAIError("Gửi prompt lên ChatGPT không thành công.")

    def _snapshot(self) -> dict:
        try:
            return self.page.evaluate(_SNAPSHOT_JS) or {}
        except Exception:
            return {}

    def _wait_image(self) -> bytes:
        from .browser_base import query_first, wait_ms
        page = self.page
        t0 = time.time()
        last_src = None
        idle_since = None
        nudged = False
        challenge_seen = False
        while time.time() - t0 < GEN_TIMEOUT:
            wait_ms(page, 2000)
            try:
                challenge = bool(page.evaluate(_CHALLENGE_JS))
            except Exception:
                challenge = False
            if challenge:
                if not challenge_seen:
                    challenge_seen = True
                    self._alert(_STATE_MSG["challenge"])
                self._ping("Đang chờ bạn tự xác minh trong cửa sổ ChatGPT…")
                continue
            snap = self._snapshot()
            busy = _visible(page, self.sels["stop_button"]) or bool(snap.get("creating"))
            imgs = snap.get("imgs") or []
            finals = [i for i in imgs if not i.get("blurred")]
            if finals and not busy:
                best = max(finals, key=lambda i: i.get("w", 0) * i.get("h", 0))["src"]
                if best == last_src:            # ổn định ≥ 2 lượt (~4s) → ảnh cuối
                    return self._download(best)
                last_src = best
                continue
            last_src = None
            if not busy and not imgs:
                idle_since = idle_since or time.time()
                if time.time() - idle_since >= IDLE_NO_IMAGE:
                    reply = (snap.get("reply") or "").strip()
                    if self._in_temp:
                        # Chat tạm thời trả chữ ("can't access the image-generation
                        # tool…") → sang chat thường ngay, không nhắc / không kết luận
                        # hết lượt (chữ "can't generate" sẽ khớp _LIMIT_RE).
                        raise _NoImage(reply[:200])
                    if reply and _LIMIT_RE.search(reply):
                        raise ThumbAIError(f"ChatGPT không vẽ (hết lượt / từ chối): "
                                           f"«{reply[:200]}»")
                    if reply and not nudged:
                        nudged = True
                        idle_since = None
                        self._log("ChatGPT trả lời bằng chữ — nhắc vẽ ảnh ngay…")
                        box = query_first(page, self.sels["prompt_box"], timeout_ms=8000)
                        if box is None:
                            raise _NoImage(reply[:200])
                        self._type(box, NUDGE)
                        self._send()
                        continue
                    raise _NoImage(reply[:200])
            else:
                idle_since = None
            self._ping(f"ChatGPT đang vẽ thumbnail… {int(time.time() - t0)}s")
        raise ThumbAIError(f"ChatGPT vẽ quá lâu (> {GEN_TIMEOUT // 60} phút).",
                           retryable=True)

    def _download(self, src: str) -> bytes:
        """Read the generated image without leaving the authenticated page.

        ChatGPT rotates between normal CDN URLs, authenticated backend URLs,
        ``blob:`` object URLs and ``data:`` URLs.  Keep all paths local to the
        active browser session; never persist the signed URL in logs.
        """
        # data: does not need a network request and may be too large to pass
        # through fetch reliably in Chromium.
        if src.startswith("data:") and "," in src:
            try:
                head, payload = src.split(",", 1)
                raw = (base64.b64decode(payload) if ";base64" in head.lower()
                       else unquote_to_bytes(payload))
                if len(raw) > 10_000:
                    return raw
            except Exception:
                pass

        errors: list[str] = []
        res = {}
        try:
            res = self.page.evaluate(_FETCH_JS, src) or {}
        except Exception as e:
            res = {"error": type(e).__name__}
        if res.get("error"):
            errors.append(f"fetch: {res['error']}")
        data = str(res.get("data") or "")
        if data.startswith("data:") and "," in data:
            try:
                raw = base64.b64decode(data.split(",", 1)[1])
                if len(raw) > 10_000:
                    return raw
            except Exception as e:
                errors.append(f"decode: {type(e).__name__}")

        # If ChatGPT's endpoint rejects an explicit fetch, the already decoded
        # <img> can still be copied losslessly at natural resolution to PNG.
        # This also handles blob URLs that expire between polling and fetch.
        try:
            canvas = self.page.evaluate(_CANVAS_JS, src) or {}
        except Exception as e:
            canvas = {"error": type(e).__name__}
        cdata = str(canvas.get("data") or "")
        if cdata.startswith("data:") and "," in cdata:
            try:
                raw = base64.b64decode(cdata.split(",", 1)[1])
                if len(raw) > 10_000:
                    return raw
            except Exception as e:
                errors.append(f"canvas decode: {type(e).__name__}")
        if canvas.get("error"):
            errors.append(f"canvas: {canvas['error']}")

        # APIRequestContext carries cookies from the browser context and is a
        # useful final path for regular https CDN/backend URLs.  It cannot read
        # blob/data URLs, hence it intentionally comes last.
        try:
            if src.startswith(("http://", "https://")):
                resp = self.page.context.request.get(
                    src, timeout=60_000,
                    headers={"Referer": "https://chatgpt.com/"})
                if resp.ok:
                    body = resp.body()
                    if len(body) > 10_000:
                        return body
                errors.append(f"request: HTTP {getattr(resp, 'status', '?')}")
        except Exception as e:
            errors.append(f"request: {type(e).__name__}")
        raise ThumbAIError(f"Không tải được ảnh ChatGPT vừa vẽ "
                           f"({' | '.join(errors[:3]) or 'dữ liệu rỗng'}).",
                           retryable=True)

    def _draw_once(self, prompt: str, ref_path: Optional[Path], temporary: bool) -> bytes:
        from .browser_base import query_first, upload_first
        page = self.page
        url = TEMP_CHAT_URL if temporary else config.CHATGPT_URL
        self._in_temp = temporary
        page.goto(url, wait_until="domcontentloaded")
        self._ensure_ready(url)
        box = query_first(page, self.sels["prompt_box"], timeout_ms=20_000)
        if box is None:
            raise ThumbAIError("Không thấy ô nhập prompt ChatGPT (giao diện đổi?).")
        if ref_path is not None:
            if not upload_first(page, self.sels["file_input"], str(ref_path),
                                timeout_ms=10_000, set_timeout_ms=60_000, log=self._log):
                raise ThumbAIError("Không đính kèm được ảnh bìa vào ChatGPT (không thấy ô "
                                   "chọn file — giao diện đổi?).")
            self._wait_attachment()
        self._type(box, prompt)
        self._send()
        self._log("Đã gửi — ChatGPT đang vẽ " + ("ảnh + chữ" if ref_path is not None
                                                 else "ảnh bìa") + " (thường 1–4 phút)…")
        return self._wait_image()

    def draw(self, prompt: str, ref: Optional[bytes], task: str | None = None) -> bytes:
        """DrawEngine.call: prompt tiếng Anh + ảnh bìa JPEG → bytes ảnh ChatGPT vẽ.
        ref=None → vẽ mới từ mô tả (không đính kèm ảnh)."""
        from .job_manager import JobCancelled
        if self.sess is None:
            try:
                self.open()
            except JobCancelled:
                raise
            except Exception as e:
                self.close()
                first = str(e).splitlines()[0][:200] if str(e) else ""
                raise ThumbAIError(f"Không mở được trình duyệt ChatGPT ({type(e).__name__}"
                                   f"{': ' + first if first else ''}) — cửa sổ ChatGPT cũ "
                                   "còn mở? Đóng nó rồi thử lại.") from None
        if self.tmpdir is None:
            self.tmpdir = tempfile.mkdtemp(prefix="mm_chatgpt_")
        ref_path: Optional[Path] = None
        if ref is not None:
            ref_path = Path(self.tmpdir) / "cover.jpg"
            ref_path.write_bytes(ref)
        text = chatgpt_prompt(prompt, edit=ref is not None, task=task)
        for temporary in ([True, False] if self.temporary else [False]):
            try:
                return self._draw_once(text, ref_path, temporary)
            except _NoImage as e:
                if temporary:
                    self._log("Chat tạm thời không trả ảnh → thử lại bằng chat thường…")
                    self.temporary = False
                    continue
                msg = f": «{e}»" if str(e) else ""
                raise ThumbAIError(f"ChatGPT không trả ảnh{msg}.", retryable=True)
            except (ThumbAIError, JobCancelled):
                raise
            except Exception as e:              # Playwright / cửa sổ bị đóng…
                raise ThumbAIError(f"Trình duyệt ChatGPT lỗi: {type(e).__name__}: "
                                   f"{str(e).splitlines()[0][:200] if str(e) else ''}")
        raise ThumbAIError("ChatGPT không trả ảnh.", retryable=True)


# ── Luồng chính ──────────────────────────────────────────────────

def generate(*, title: str, subtitle: str, recipe: dict | None, cover: Path, out: Path,
             settings=None, log: Callable[[str], None] | None = None,
             force: bool = False, interactive: bool = False,
             cancel_check: Callable[[], bool] | None = None) -> dict:
    """ChatGPT web vẽ thumbnail → `out`. Ảnh đã vẽ (cache) → không mở trình duyệt.
    interactive=True (người dùng bấm nút) → chờ đăng nhập / xác minh lâu hơn.
    cancel_check = progress_cb.is_cancelled → nút Huỷ có hiệu lực trong vòng chờ.
    Ném ThumbAIError khi không có ảnh dùng được (service chuyển sang cách kế tiếp)."""
    from . import thumbnail_ai as T
    from . import browser_base as BB
    from .browser_base import OperationCancelled
    from .job_manager import JobCancelled

    def _log(m: str) -> None:
        if log:
            try:
                log(m)
            except JobCancelled:
                raise
            except Exception:
                pass

    drv = _ChatGPT(_log, LOGIN_WAIT_MANUAL if interactive else LOGIN_WAIT_AUTO)
    engine = DrawEngine(key="chatgpt", model=MODEL_TAG, label=LABEL, native_wide=False,
                        call=drv.draw, protocol="web")
    prev_check = getattr(BB._cancel_local, "fn", None)
    if cancel_check is not None:
        BB.set_cancel_check(cancel_check)
    try:
        return T.generate(title=title, subtitle=subtitle, recipe=recipe, cover=cover,
                          out=out, settings=settings, log=log, force=force, engine=engine)
    except OperationCancelled:
        raise JobCancelled() from None
    finally:
        drv.close()
        BB.set_cancel_check(prev_check)


def draw_scene(prompt: str, *, ref: Optional[bytes] = None,
               log: Callable[[str], None] | None = None,
               attempts: int = 2, interactive: bool = False,
               cancel_check: Callable[[], bool] | None = None) -> bytes:
    """ChatGPT web vẽ MỚI 1 ảnh từ mô tả tiếng Anh → bytes ảnh gốc. ref=None: ảnh
    bìa gốc (không đính kèm); ref=JPEG ảnh bìa: vẽ bảng tham chiếu TỪ ảnh bìa.
    Lỗi tạm (không trả ảnh / tải hỏng) → thử lại tối đa `attempts` lượt. Ném
    ThumbAIError khi không có ảnh (máy ảnh chuyển sang dự phòng)."""
    from . import browser_base as BB
    from .browser_base import OperationCancelled
    from .job_manager import JobCancelled

    def _log(m: str) -> None:
        if log:
            try:
                log(m)
            except JobCancelled:
                raise
            except Exception:
                pass

    drv = _ChatGPT(_log, LOGIN_WAIT_MANUAL if interactive else LOGIN_WAIT_AUTO)
    prev_check = getattr(BB._cancel_local, "fn", None)
    if cancel_check is not None:
        BB.set_cancel_check(cancel_check)
    try:
        for attempt in range(1, max(1, attempts) + 1):
            try:
                return drv.draw(prompt, ref, REFERENCE_TASK if ref is not None else None)
            except ThumbAIError as e:
                if not e.retryable or attempt >= attempts:
                    raise
                _log(f"ChatGPT lỗi ({e}) — thử lại lượt {attempt + 1}/{attempts}…")
                time.sleep(5)
        raise ThumbAIError("ChatGPT không trả ảnh.", retryable=True)
    except OperationCancelled:
        raise JobCancelled() from None
    finally:
        drv.close()
        BB.set_cancel_check(prev_check)


__all__ = ["SITE", "MODEL_TAG", "LABEL", "login_status", "record_login", "page_state",
           "chatgpt_prompt", "generate", "draw_scene"]
