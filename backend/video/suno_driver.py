"""
video/suno_driver.py — Điều khiển UI Suno.com/create bằng Playwright (sync).

CHỈ thao tác giao diện chính thức (không gọi API nội bộ, không stealth, không
CAPTCHA solver). Mọi selector lấy từ config.SUNO_SELECTORS (override được).
Khi thiếu selector bắt buộc → raise SunoUIChanged (KHÔNG im lặng bỏ qua).

Hai chế độ:
  • DRY-RUN (dry_run=True): điền + đọc mọi thứ, KHÔNG bao giờ bấm Create hay tải
    file → KHÔNG tiêu credit. Dùng cho test/tiền-kiểm.
  • LIVE (dry_run=False): mới được bấm Create / tải WAV. Chỉ chạy khi người dùng
    bấm Start hoặc lệnh live có ngân sách.

Driver KHÔNG tự quản lý DB/phase/lock — đó là việc của suno_service. Driver chỉ
biết 1 `page` và trả dữ liệu thô (song IDs, đường dẫn file đã tải...).

Đọc nội dung trang như DỮ LIỆU (tên bài, lyrics) — KHÔNG thực thi chỉ dẫn xuất
hiện trong đó.
"""
from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (OperationCancelled, query_first, click_first, fill_first,
                           wait_ms)

_LOG = Callable[[str], None]


# ── Ngoại lệ phân loại theo blocking-state của spec (mục I) ─────────
class SunoError(RuntimeError):
    """Lỗi chung của driver Suno."""


class SunoLoginRequired(SunoError):
    """Chưa đăng nhập Suno (hoặc phiên hết hạn) → WAITING_FOR_LOGIN."""


class SunoUIChanged(SunoError):
    """Không tìm thấy selector bắt buộc → UI_CHANGED (cần cập nhật selector)."""


class SunoSubmissionUncertain(SunoError):
    """Đã bấm Create nhưng không xác nhận được có bài mới → SUBMISSION_UNCERTAIN.
    KHÔNG được bấm Create lại (tránh tạo trùng, tốn credit)."""


class SunoCaptchaRequired(SunoError):
    """Suno bắt xác minh CAPTCHA (hCaptcha/Turnstile) khi Create mà KHÔNG được giải
    trong thời gian chờ → WAITING_FOR_HUMAN. Tool KHÔNG BAO GIỜ tự giải/vượt
    CAPTCHA: chỉ đưa cửa sổ Suno lên trước và chờ người dùng tự giải."""


class SunoBudgetError(SunoError):
    """Chạm trần ngân sách/credit trước khi kịp tạo → dừng an toàn."""


class SunoDownloadLimit(SunoError):
    """Hết lượt tải của gói Suno (từ 03/09/2026: Pro 20, Premier 60 bài/tháng) →
    INSUFFICIENT_DOWNLOAD_ALLOWANCE. KHÔNG bao giờ tự mua 'download packs'."""


class SunoAssetMissing(SunoError):
    """Suno hiện popup tải nhưng backend trả “can't locate the asset”."""


class SunoGenerationFailed(SunoError):
    """Suno đã tạo ID nhưng kết quả cuối cùng báo 'failed to generate'.

    Đây không phải lỗi selector/tải: bài hỏng không thể mở Studio. Caller có
    thể bỏ các ID hỏng và cho người dùng chủ động chạy một lượt Create mới.
    """


@dataclass
class NewSong:
    song_id: str
    title: str = ""
    duration_seconds: Optional[float] = None
    request_index: int = 0


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


_JS_DURATION_STATE = r"""() => {
  const s = [...document.querySelectorAll('span')].find(e => e.textContent.trim() === 'Duration');
  if (!s) return null;
  const row = s.parentElement.parentElement;
  if (row.querySelector('[role=slider]')) return 'custom';
  const auto = [...row.querySelectorAll('button')].find(b => b.innerText.trim() === 'Auto');
  if (!auto) return null;
  return auto.getAttribute('data-selected') === 'true' ? 'auto' : 'custom';
}"""


# Từ 2026-10-12 Suno chặn mỗi lượt Create bằng captcha (POST /api/c/check →
# Turnstile ẩn «interaction-only»; không qua thì bật hCaptcha ghép hình «Drag the
# letter…»). Chỉ ĐỌC trạng thái: khung captcha có HIỆN không + toast lỗi tạo bài.
# Menu thả xuống đang mở (Filters, ⋯, Earn Credits…). Menu Base UI/Radix của
# Suno là modal: cú click ra ngoài CHỈ đóng menu, không tới nút bên dưới.
# LIVE 2026-11-09: menu Filters còn mở → bấm Create chỉ đóng menu, Suno không
# gửi /api/generate (3 lần «Nặng Tình Hay Nhẹ Lòng», 1 lần «Em Là Nhất Miền Tây»).
# Trang /song/<id> bị Suno gỡ (LIVE 2026-11-09: lyrics dính bản quyền → một bản
# «failed to generate», bản kia trả trang 404 «Page not found»).
_JS_SONG_404 = r"""() => { const t = (document.body && document.body.innerText) || '';
  return /page not found/i.test(t) && /(^|\s)404(\s|$)/.test(t); }"""

_JS_OPEN_MENUS = r"""() => [...document.querySelectorAll('[role=menu],[role=listbox]')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .length"""

# Phần tử nằm trên cùng tại tâm nút Create: "" nếu chính là nút (hoặc con của
# nút), ngược lại mô tả ngắn phần tử đang che.
_JS_CREATE_BLOCKER = r"""(el) => {
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  if (!hit || hit === el || el.contains(hit)) return '';
  const t = (hit.innerText || hit.getAttribute('aria-label') || '').trim()
    .replace(/\s+/g, ' ').slice(0, 60);
  return (hit.tagName.toLowerCase() + (hit.getAttribute('role') ? '[role=' +
    hit.getAttribute('role') + ']' : '') + (t ? ' «' + t + '»' : '')).slice(0, 100);
}"""

_JS_GEN_STATE = r"""() => {
  const shown = (e, w, h) => {
    const r = e.getBoundingClientRect();
    if (r.width < w || r.height < h) return false;
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return false;
    if (e.checkVisibility && !e.checkVisibility({opacityProperty: true, visibilityProperty: true})) return false;
    return true;
  };
  const frames = [...document.querySelectorAll('iframe')].filter(f => {
    const src = f.getAttribute('src') || '', t = f.getAttribute('title') || '';
    return /hcaptcha|turnstile|challenges\.cloudflare/i.test(src) || /captcha|challenge/i.test(t);
  }).filter(f => shown(f, 50, 50));
  // Turnstile gắn iframe trong shadow DOM ĐÓNG → querySelectorAll('iframe') không
  // thấy (2026-10-12: ô «Verify you are human» hiện giữa trang mà frames rỗng).
  // Đo chính phần tử chứa — khung hiện ~300×65, lúc ẩn 0×0.
  const hosts = [...document.querySelectorAll(
      "#generation-turnstile-container,[id*=turnstile],[class*=turnstile],.cf-turnstile")]
    .filter(e => shown(e, 120, 30));
  const toast = [...document.querySelectorAll(
      "[data-sonner-toast],[role=alert],[role=status],[class*=toast],[class*=Toast]")]
    .map(e => (e.innerText || '').trim().replace(/\s+/g, ' '))
    .find(t => /generation failed|failed to generate|couldn.?t generate|copyrighted material|verification failed|captcha|verify you are human/i.test(t)) || '';
  const f = frames[0], h = hosts[0];
  const kind = f ? ((f.getAttribute('title') || '') + ' ' +
                    (f.getAttribute('src') || '').split('?')[0]).trim()
                 : h ? 'Cloudflare Turnstile «Verify you are human» #' + (h.id || h.className) : '';
  return {captcha: frames.length > 0 || hosts.length > 0,
          kind: kind.slice(0, 120),
          toast: toast.slice(0, 200)};
}"""


_AUTH_URL_RE = re.compile(
    r"suno\.com/(auth|sign-?in|sign-?up|login)|accounts\.google\.com|clerk\.",
    re.I)
# Chỉ có khi ĐĂNG XUẤT (trang chủ/landing). Đã đo LIVE: trang /create khi đã
# đăng nhập không có phần tử nào khớp.
_LOGGED_OUT_SEL = ("button:text-is('Log in'), a:text-is('Log in'), "
                   "button:text-is('Sign in'), a:text-is('Sign in'), "
                   "button:has-text('Join Suno for free'), "
                   "a:has-text('Join Suno for free')")


def _url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _short_url(page) -> str:
    return _url(page).split("?")[0][:80] or "about:blank"


def _is_timeout(e: BaseException) -> bool:
    return "timeout" in type(e).__name__.lower()


def suno_logged_out(page) -> bool:
    """True nếu trang đang ở luồng đăng nhập hoặc hiện nút «Log in» — tức phiên
    Suno chưa/không còn đăng nhập (kể cả khi trang có nút «Create»)."""
    if _AUTH_URL_RE.search(_url(page)) and "session-recovery" not in _url(page):
        return True
    try:
        loc = page.locator(_LOGGED_OUT_SEL)
        return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 5)))
    except Exception:
        return False


def _dur_to_seconds(text: str) -> Optional[float]:
    """'3:40' → 220.0 ; '1:02:03' → 3723.0. None nếu không parse được."""
    text = (text or "").strip()
    m = re.findall(r"\d+", text)
    if not m or ":" not in text:
        return None
    parts = [int(x) for x in text.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


class SunoDriver:
    def __init__(self, page, dry_run: bool = True,
                 selectors: Optional[dict] = None, log: Optional[_LOG] = None):
        self.page = page
        self.dry_run = dry_run
        self.sel = selectors or config.get_selectors("suno")
        self._log = log or (lambda m: None)
        # Ảnh chụp chẩn đoán khi Create không được nhận (service gán staging).
        self.debug_dir: Optional[Path] = None
        # Tín hiệu quanh lượt Create (console captcha + request /api/generate/).
        # KHÔNG lưu token, header, cookie hay body request.
        self._gen_events: list[str] = []
        self._gen_bad: list = []
        self._gen_logical_errors: list[str] = []
        # UUID bài mới đọc thụ động từ response /api/generate/.  Suno có thể
        # nhận Create (HTTP 200) nhưng danh sách bên phải đang có Filters hoặc
        # virtual-scroll nên DOM không hề chứa hai link /song/.  Không dùng
        # danh sách DOM làm nguồn sự thật duy nhất trong trường hợp đó.
        self._gen_response_ids: set[str] = set()
        # Mỗi response POST /generate (theo thứ tự) → danh sách ID clip của
        # đúng lượt Create đó (rỗng nếu lỗi). Dùng để phân biệt 2 bản người
        # dùng tự Create với 2 bản Suno vừa từ chối (ID tới muộn).
        self._gen_batches: list[list[str]] = []
        self._gen_watch = False

    def _wait(self, ms: float) -> None:
        """Chờ trên trang nhưng huỷ được ngay (xem browser_base.wait_ms)."""
        wait_ms(self.page, ms)

    # ── tiện ích selector ────────────────────────────────────────
    def _find(self, key: str, timeout_ms: int = 8000, required: bool = True):
        loc = query_first(self.page, self.sel.get(key, []), timeout_ms)
        if loc is None and required:
            raise SunoUIChanged(
                f"Không tìm thấy phần tử '{key}' trên Suno. Giao diện có thể đã "
                f"đổi — cập nhật SUNO_SELECTORS['{key}'] trong config hoặc "
                f"suno_overrides.json.")
        return loc

    # ── preflight / login ────────────────────────────────────────
    def open_create_page(self, login_timeout_sec: int = 8,
                         attempts: int = 3) -> None:
        """Mở /create và chờ form tạo bài sẵn sàng.

        Phiên Suno cũ (để lâu không dùng) đi qua /auth/session-recovery để làm
        mới token: bước này lúc treo >60s, lúc rơi về trang chủ «Log in», lúc
        thành công (đã đo LIVE 2026-10-01 — cùng profile, vài phút sau vào
        thẳng /create). Nên tải lại vài lần trước khi kết luận; trang chủ khi
        chưa đăng nhập CŨNG có nút «Create»/«Advanced» nên phải loại trừ trạng
        thái đăng xuất, không chỉ dò form."""
        probes = (self.sel.get("create_button", []) + self.sel.get("styles_box", [])
                  + self.sel.get("mode_advanced_tab", []))
        reasons: list[str] = []
        for i in range(attempts):
            if i:
                self._log(f"Suno chưa vào được trang Create ({reasons[-1]}) — "
                          f"chờ rồi tải lại (lần {i + 1}/{attempts})…")
                self._wait(5000 * i)
            try:
                self.page.goto(config.SUNO_URL, wait_until="domcontentloaded",
                               timeout=45000)
            except Exception as e:      # noqa: BLE001
                if not _is_timeout(e):
                    raise
                reasons.append(f"trang treo >45s ở {_short_url(self.page)}")
                continue
            self._wait(1500)
            # session-recovery tự chuyển hướng → chờ nó xong (tối đa 20s).
            end = time.time() + 20
            while "/auth/" in _url(self.page) and time.time() < end:
                self._wait(1000)
            if suno_logged_out(self.page):
                reasons.append(f"Suno hiện trang đăng nhập ({_short_url(self.page)})")
                continue
            ready = query_first(self.page, probes,
                                timeout_ms=login_timeout_sec * 1000)
            if (ready is not None and "/create" in _url(self.page)
                    and not suno_logged_out(self.page)):
                self._log("Suno create page sẵn sàng.")
                return
            reasons.append(f"không thấy form tạo bài ở {_short_url(self.page)}")
        detail = "; ".join(reasons)
        if all(r.startswith("trang treo") for r in reasons):
            raise SunoError(
                f"Trang suno.com/create không phản hồi sau {attempts} lần tải "
                f"({detail}). Có thể mạng hoặc Suno đang chậm — chờ vài phút rồi "
                "bấm chạy lại. Đừng đóng cửa sổ Cốc Cốc Suno khi tool đang chạy.")
        raise SunoLoginRequired(
            "Phiên đăng nhập Suno trong trình duyệt của tool đã hết hạn hoặc chưa "
            f"khôi phục được ({detail}). Bấm «🌐 Mở UI Suno để đăng nhập…», tự "
            "đăng nhập bằng tài khoản của bạn trong cửa sổ vừa mở, rồi bấm chạy "
            "lại. Tool KHÔNG tự nhập mật khẩu.")

    _JS_ACCOUNT_VALUES = r"""() => {
      const out = {};
      for (const e of document.querySelectorAll('span,div,p')) {
        if (e.children.length) continue;
        const t = (e.textContent || '').trim();
        if (!/^(Downloads Remaining|Credits Remaining|Current Plan)$/i.test(t)) continue;
        const v = e.nextElementSibling;
        if (v) out[t.toLowerCase()] = (v.textContent || '').trim();
      }
      return out;
    }"""

    def read_download_allowance(self) -> Optional[int]:
        """Số lượt tải còn lại (trang suno.com/account, ô 'Downloads Remaining').
        None = không đọc được (không chặn). Chỉ ĐỌC — không bấm gì trên trang."""
        try:
            self.page.goto("https://suno.com/account", wait_until="domcontentloaded")
            vals = {}
            for _ in range(10):
                self._wait(1000)
                vals = self.page.evaluate(self._JS_ACCOUNT_VALUES) or {}
                if "downloads remaining" in vals:
                    break
        except Exception as e:      # noqa: BLE001
            self._log(f"Không đọc được hạn mức tải ({type(e).__name__}).")
            return None
        raw = vals.get("downloads remaining", "")
        m = re.search(r"(\d[\d,]*)", raw)
        if not m:
            if re.search(r"unlimited|∞", raw, re.I):
                return 10**6
            return None
        n = int(m.group(1).replace(",", ""))
        self._log(f"Lượt tải Suno còn lại: {n} ({vals.get('current plan', '?')}).")
        return n

    _JS_OUT_OF_DOWNLOADS = r"""() => {
      for (const d of document.querySelectorAll('[role=dialog]')) {
        const t = (d.innerText || '').trim();
        if (/out of downloads|used all (of )?your downloads/i.test(t)) {
          const m = t.match(/Refreshes\s*\n*\s*([0-9\/.\-]+)/i);
          return {refresh: m ? m[1] : ''};
        }
      }
      return null;
    }"""

    def _raise_if_out_of_downloads(self) -> None:
        """Hộp thoại Download đổi thành 'Out of Downloads' khi hết hạn mức → đóng
        (Escape, KHÔNG bấm 'Buy download packs') rồi báo SunoDownloadLimit."""
        try:
            hit = self.page.evaluate(self._JS_OUT_OF_DOWNLOADS)
        except Exception:
            hit = None
        if not hit:
            return
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        when = f", làm mới {hit['refresh']}" if hit.get("refresh") else ""
        raise SunoDownloadLimit(
            f"Suno báo hết lượt tải{when} (hộp «Out of Downloads»). Tải WAV qua "
            "Studio của gói Premier vốn không bị tính lượt — kiểm tra gói Suno còn "
            "Premier không. Tool không tự mua thêm; xử lý xong rồi Resume.")

    def read_credits(self) -> Optional[int]:
        """Đọc số credit còn lại từ aria-label 'Credits remaining: N'. None nếu
        không đọc được (không chặn — chỉ để tiền-kiểm ngân sách)."""
        loc = self._find("credits", timeout_ms=4000, required=False)
        if loc is None:
            return None
        try:
            label = loc.get_attribute("aria-label") or loc.inner_text()
        except Exception:
            return None
        m = re.search(r"(\d[\d,]*)", label or "")
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None

    # ── cấu hình form (an toàn cho DRY-RUN) ──────────────────────
    def select_advanced_mode(self) -> None:
        loc = self._find("mode_advanced_tab", timeout_ms=8000, required=False)
        if loc is None:
            # Advanced có thể đã là tab mặc định; chỉ cảnh báo, không chặn cứng.
            self._log("Không thấy tab 'Advanced' rõ ràng — giả định đã ở Advanced.")
            return
        try:
            loc.click()
            self._wait(400)
            self._log("Đã chọn chế độ Advanced.")
        except Exception as e:
            raise SunoUIChanged(f"Không bấm được tab Advanced: {e}") from e

    # Nút model = nút có aria-haspopup (LIVE 2026-10-12: 'dialog'; trước đó
    # 'menu') nằm CÙNG HÀNG, ngay bên phải pill «Credits remaining». Định vị
    # theo hình học thay vì `:right-of(...)`: selector cũ khớp nhầm các nút ⋯
    # của danh sách bài khi Suno đổi haspopup menu→dialog (lỗi 2026-10-12).
    _JS_MARK_MODEL_BUTTON = r"""() => {
      document.querySelectorAll('[data-mm-model-btn]')
        .forEach(e => e.removeAttribute('data-mm-model-btn'));
      const cr = [...document.querySelectorAll("[aria-label^='Credits remaining']")]
        .find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
      if (!cr) return false;
      const c = cr.getBoundingClientRect(), cy = c.top + c.height / 2;
      let best = null, bestD = 1e9;
      for (const b of document.querySelectorAll('button[aria-haspopup]')) {
        const r = b.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (Math.abs(r.top + r.height / 2 - cy) > Math.max(12, c.height / 2)) continue;
        const d = r.left - c.right;
        if (d < 0 || d > 600) continue;
        if (d < bestD) { best = b; bestD = d; }
      }
      if (!best) return false;
      best.setAttribute('data-mm-model-btn', '1');
      return true;
    }"""

    def _model_button(self, timeout_ms: int = 6000):
        """Locator nút chọn model; None nếu không thấy."""
        deadline = time.time() + timeout_ms / 1000.0
        while True:
            try:
                if self.page.evaluate(self._JS_MARK_MODEL_BUTTON):
                    loc = self.page.locator("button[data-mm-model-btn='1']").first
                    if loc.count() > 0 and loc.is_visible():
                        return loc
            except Exception:
                pass
            if time.time() >= deadline:
                break
            self._wait(300)
        # Dự phòng: selector cấu hình (config.SUNO_SELECTORS['model_button']).
        return self._find("model_button", timeout_ms=1500, required=False)

    def _current_model_text(self) -> str:
        loc = self._model_button()
        if loc is None:
            return ""
        try:
            return (loc.inner_text() or "").strip()
        except Exception:
            return ""

    # LIVE 2026-10-12: nút model mở [role=dialog] chứa [role=grid][aria-label=
    # 'Model'] gồm các [role=row][data-grid-list-row] (aria-selected="true" =
    # đang chọn). Hàng đầu là «Create Custom Model» (100 credit — KHÔNG BAO GIỜ
    # bấm), rồi MY MODELS (custom) và ALL MODELS (v6 / v6-wild / v6-mini).
    # Kiểu cũ (LIVE 2026-10-08): [role=menu] chứa [role=menuitemradio]
    # (aria-checked). Cả hai: dòng đầu innerText = tên model. Khi đang ở custom
    # model, nút chỉ còn ảnh bìa (KHÔNG có chữ 'v6') và Suno NHỚ lựa chọn qua
    # lần tải lại.
    _MODEL_ITEMS = ("[role=grid][aria-label='Model'] [role=row][data-grid-list-row], "
                    "[role=menu] [role=menuitemradio]")

    def _open_model_menu(self) -> bool:
        btn = self._model_button()
        if btn is None:
            return False
        try:
            if not self._real_click(btn):
                btn.click()
            self.page.locator(self._MODEL_ITEMS).first.wait_for(
                state="visible", timeout=4000)
            return True
        except Exception:
            self._close_menu()
            return False

    def _close_menu(self) -> None:
        try:
            self.page.keyboard.press("Escape")
            self._wait(300)
        except Exception:
            pass

    def _model_items(self) -> list:
        """[(tên, đang_chọn, locator)] của menu model đang mở."""
        out = []
        items = self.page.locator(self._MODEL_ITEMS)
        for i in range(items.count()):
            it = items.nth(i)
            try:
                name = (it.inner_text() or "").strip().split("\n")[0].strip()
                if not name or name.lower().startswith("create custom model"):
                    continue          # hàng tạo custom model (tốn credit)
                on = "true" in (it.get_attribute("aria-checked"),
                                it.get_attribute("aria-selected"))
                out.append((name, on, it))
            except Exception:
                continue
        return out

    def current_model(self) -> str:
        """Tên model đang chọn (đọc aria-selected/aria-checked trong hộp model)."""
        if not self._open_model_menu():
            return self._current_model_text()
        try:
            return next((n for n, on, _ in self._model_items() if on), "")
        finally:
            self._close_menu()

    def select_model(self, preferred: str = "v6",
                     allow_fallback: bool = False) -> str:
        """Đảm bảo model đang chọn là `preferred` ('v6', 'v6-wild', hoặc tên
        custom model như 'Bamboo Flute v1'). Trả về tên model cuối cùng.
        allow_fallback=False + không chọn được preferred → raise (không chấp
        nhận model khác)."""
        want = (preferred or "v6").strip().lower()
        current = ""
        if self._open_model_menu():
            items = self._model_items()
            current = next((n for n, on, _ in items if on), "")
            if current.lower() == want:
                self._close_menu()
                self._log(f"Model đã đúng: {current!r}")
                return current
            target = next((it for n, _, it in items if n.lower() == want), None)
            if target is None:
                self._close_menu()
                self._log(f"Menu model không có {preferred!r} (có: "
                          f"{[n for n, _, _ in items]}).")
            else:
                try:
                    target.click()
                    self._wait(800)
                except Exception as e:
                    self._log(f"Chọn model gặp lỗi: {e}")
                    self._close_menu()
                current = self.current_model()
                if current.lower() == want:
                    self._log(f"Đã chọn model {current!r}.")
                    return current
        else:
            # Dự phòng kiểu cũ: đọc chữ trên nút (chỉ đúng với họ v6).
            current = self._current_model_text()
            t = current.lower()
            if (want == "v6" and "v6" in t and "mini" not in t and "wild" not in t) \
                    or (want != "v6" and want in t):
                self._log(f"Model đã đúng: {current!r}")
                return current
        if allow_fallback:
            self._log(f"CẢNH BÁO: không set được {preferred!r}, dùng {current!r} "
                      f"(allow_model_fallback=True).")
            return current
        raise SunoUIChanged(
            f"Không đặt được model '{preferred}' (hiện: {current!r}) và "
            f"allow_model_fallback=False → dừng để tránh dùng sai model.")

    def assert_instrumental(self) -> None:
        """Instrumental = Lyrics TRỐNG. Đọc editor; nếu có chữ → xóa để đảm bảo
        không có lời. KHÔNG bao giờ tự điền lyrics."""
        loc = self._find("lyrics_editor", timeout_ms=6000, required=False)
        if loc is None:
            self._log("Không thấy Lyrics editor — bỏ qua (giả định trống).")
            return
        try:
            txt = (loc.inner_text() or "").strip()
        except Exception:
            txt = ""
        if txt:
            self._log("Lyrics có nội dung — xóa để giữ instrumental (trống).")
            try:
                loc.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
                self._wait(300)
                left = (loc.inner_text() or "").strip()
            except Exception as e:
                raise SunoUIChanged(
                    f"Không xóa được Lyrics để giữ instrumental: {e}") from e
            if left:
                raise SunoUIChanged("Lyrics vẫn còn chữ sau khi xóa — dừng để không "
                                    "tạo bài có lời.")
        else:
            self._log("Lyrics trống → instrumental OK.")

    def fill_styles(self, styles: str) -> None:
        loc = self._find("styles_box", timeout_ms=8000)
        try:
            loc.click()
            loc.fill(styles)
            try:
                got = loc.input_value()
            except Exception:
                got = loc.inner_text()
            if " ".join((got or "").split()) != " ".join(styles.split()):
                raise SunoUIChanged("Styles trên form không khớp nội dung đã tạo.")
            self._log(f"Đã điền và xác nhận Styles ({len(styles)} ký tự).")
        except Exception as e:
            if isinstance(e, SunoUIChanged):
                raise
            raise SunoUIChanged(f"Không điền được Styles: {e}") from e

    def open_more_options(self) -> None:
        loc = self._find("more_options", timeout_ms=5000, required=False)
        if loc is None:
            self._log("Không thấy 'More Options' — có thể đã mở sẵn.")
            return
        try:
            loc.click()
            self._wait(400)
        except Exception:
            pass

    def fill_exclusions(self, exclusions: str) -> None:
        loc = self._find("exclude_styles", timeout_ms=6000, required=False)
        if loc is None:
            self._log("Không thấy ô Exclude styles — bỏ qua (cần More Options?).")
            return
        try:
            # Suno giữ nguyên ô này giữa các lượt Create → chỉ điền khi khác
            # (đọc giá trị THẬT trên form, không đoán) để khỏi thao tác thừa.
            try:
                if loc.input_value() == exclusions:
                    return
            except Exception:
                pass
            loc.click()
            loc.fill(exclusions)
            self._log(f"Đã điền Exclude styles ({len(exclusions)} ký tự).")
        except Exception as e:
            raise SunoUIChanged(f"Không điền được Exclude styles: {e}") from e

    def _duration_state(self) -> Optional[str]:
        """'auto' | 'custom' | None (không thấy hàng Duration — More Options đóng?).
        Hàng Duration (LIVE 2026-09-26): Auto = nút Custom/Auto, Auto có
        data-selected=true; Custom = thanh trượt role=slider + ô 'm:ss'."""
        try:
            return self.page.evaluate(_JS_DURATION_STATE)
        except Exception:
            return None

    def ensure_duration_auto(self) -> None:
        """Người dùng chốt: Duration LUÔN Auto. Tool không bao giờ bấm Custom.
        Suno KHÔNG có nút quay về Auto khi đã sang Custom (đã thử xoá ô / gõ
        'auto' / Esc — không được) nhưng mở lại /create luôn về Auto → nếu
        thấy Custom (ai đó bấm tay) thì tải lại trang."""
        for attempt in range(2):
            state = self._duration_state()
            if state == "auto":
                self._log("Duration: Auto.")
                return
            if state is None:
                self._log("Không thấy hàng Duration — bỏ qua (Suno mặc định Auto).")
                return
            if attempt == 0:
                self._log("Duration đang Custom — tải lại /create để về Auto.")
                self.open_create_page()
                self.select_advanced_mode()
                self.open_more_options()
        raise SunoUIChanged("Duration vẫn Custom sau khi tải lại /create — "
                            "hãy chuyển về Auto bằng tay rồi chạy tiếp.")

    # Max Mode (More Options; LIVE 2026-10-08): hàng <span>Max Mode</span> + 2 nút
    # Off/On mang data-selected="true|false"; tải lại /create → về Off. Selector
    # chung "button:has-text('On')" cũ khớp nhầm (tab "Songs", hàng Personalize…)
    # → chỉ bấm nút trong ĐÚNG hàng Max Mode (chuột thật) rồi đọc lại trạng thái.
    _JS_TOGGLE_ROW = r"""([label, want]) => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const txt = b => (b.innerText || b.textContent || '').trim();
      const state = b => {
        const a = b.getAttribute('data-selected') ?? b.getAttribute('aria-pressed')
              ?? b.getAttribute('aria-checked') ?? b.getAttribute('data-state');
        if (a !== null) return /^(true|on|checked|active)$/i.test(a);
        return /variant-standard/.test(String(b.className));
      };
      document.querySelectorAll('[data-tool-toggle]').forEach(e => e.removeAttribute('data-tool-toggle'));
      for (const l of document.querySelectorAll('span,div,p,label')) {
        if (l.children.length || txt(l) !== label || !vis(l)) continue;
        let row = l;
        for (let i = 0; i < 4 && row; i++) {
          row = row.parentElement;
          if (!row) break;
          const btns = [...row.querySelectorAll('button')].filter(vis);
          const off = btns.find(b => txt(b) === 'Off'), on = btns.find(b => txt(b) === 'On');
          if (off && on) {
            (want ? on : off).setAttribute('data-tool-toggle', '1');
            return {on: state(on), off: state(off)};
          }
        }
      }
      return null;
    }"""

    def max_mode_state(self) -> Optional[bool]:
        """True/False = Max Mode đang On/Off; None = không thấy hàng Max Mode."""
        st = self.page.evaluate(self._JS_TOGGLE_ROW, ["Max Mode", True])
        if st is None:
            return None
        return bool(st["on"]) and not bool(st["off"])

    def set_max_mode(self, on: bool) -> None:
        """Bật/tắt Max Mode và XÁC NHẬN đã đổi (không đoán). Không thấy hàng hoặc
        bấm không ăn → SunoUIChanged (không tạo nhạc sai cấu hình)."""
        st = None
        for attempt in range(4):
            st = self.page.evaluate(self._JS_TOGGLE_ROW, ["Max Mode", on])
            if st is None and attempt == 0:
                self.open_more_options()        # More Options có thể đang đóng
                self._wait(700)
                st = self.page.evaluate(self._JS_TOGGLE_ROW, ["Max Mode", on])
            if st is None:
                break
            if bool(st["on"]) == on and bool(st["off"]) != on:
                self._log(f"Max Mode = {'On' if on else 'Off'}.")
                return
            loc = self.page.locator("[data-tool-toggle='1']").first
            clicked = False
            if attempt == 0:
                clicked = self._real_click(loc)
            if not clicked and attempt <= 1:
                try:
                    loc.click(timeout=3000, force=True)
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                try:
                    loc.evaluate("el => el.click()")
                except Exception:
                    pass
            self._wait(800)
        if st is None:
            raise SunoUIChanged("Không thấy hàng 'Max Mode' trong More Options.")
        raise SunoUIChanged(
            f"Không {'bật' if on else 'tắt'} được Max Mode (On={st['on']}, "
            f"Off={st['off']}).")

    # ── Thanh trượt sáng tạo (More Options; LIVE 2026-10-08) ─────
    # Weirdness 0–100, Style Influence 0–100, Variety 0–4 (Off/Normal/High/
    # Extra/Max). Variety > 0 làm Suno VIẾT LẠI Styles (đảo thứ tự, rút gọn, tự
    # thêm nhạc cụ) → tool đặt Variety=0 để Suno dùng nguyên văn prompt.
    SLIDERS = {"weirdness": "Weirdness", "style_influence": "Style Influence",
               "variety": "Variety"}

    _JS_SLIDER_STATE = r"""(e) => {
      const r = e.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const top = (cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight)
          ? document.elementFromPoint(cx, cy) : null;
      return {x: r.left, y: r.top, w: r.width, h: r.height,
              onTop: !!top && (top === e || e.contains(top) || top.contains(e)),
              popout: !!e.closest('.card-popout-boundary')};
    }"""

    def _slider_all(self, key: str) -> list:
        sels = self.sel.get(f"slider_{key}") or [
            f"[role=slider][aria-label='{self.SLIDERS[key]}']"]
        for s in sels:
            try:
                loc = self.page.locator(s)
                n = loc.count()
            except Exception:
                continue
            if n:
                return [loc.nth(i) for i in range(n)]
        return []

    # Khoảng cách (số cấp cha) tới khối chứa thanh Weirdness: thanh của form More
    # Options nằm cùng khối (2–3 cấp); bản 'Variety' thứ hai ở chỗ khác → xa hơn.
    _JS_NEAR_WEIRDNESS = r"""(e) => { let a = e;
      for (let k = 0; k < 12 && a; k++) { a = a.parentElement;
        if (a && a.querySelector("[role=slider][aria-label='Weirdness']")) return k; }
      return 99; }"""

    def _slider(self, key: str):
        """Thanh trượt của khung More Options. LIVE 2026-10-08 có 2 'Variety' (1
        trong More Options cạnh Weirdness/Style Influence, 1 ở khối khác) → chọn
        cái nằm gần Weirdness nhất."""
        items = self._slider_all(key)
        if not items:
            return query_first(self.page, self.sel.get(f"slider_{key}") or [
                f"[role=slider][aria-label='{self.SLIDERS[key]}']"], timeout_ms=4000)
        if len(items) == 1:
            return items[0]
        best, best_d = items[0], 999
        for loc in items:
            try:
                dist = loc.evaluate(self._JS_NEAR_WEIRDNESS)
            except Exception:
                continue
            if dist < best_d:
                best, best_d = loc, dist
        return best

    @staticmethod
    def _read_now(loc) -> Optional[int]:
        try:
            return int(round(float(loc.get_attribute("aria-valuenow"))))
        except Exception:
            return None

    def read_slider(self, key: str) -> Optional[int]:
        loc = self._slider(key)
        return None if loc is None else self._read_now(loc)

    def set_slider(self, key: str, value: int) -> int:
        """Đặt 1 thanh trượt bằng CHUỘT THẬT rồi đọc lại aria-valuenow.
        LIVE 2026-10-08: thanh là DIV tuỳ biến (role=slider, không có thumb riêng),
        KHÔNG nhận phím (focus + Home/mũi tên) và KHÔNG nhận 1 cú click tại chỗ →
        KÉO trên rãnh tới x = trái + (giá trị−min)/(max−min)·rộng; lệch thì chỉnh
        dần theo số đọc lại.
        Không đặt được → SunoUIChanged."""
        loc = self._slider(key)
        if loc is None:
            raise SunoUIChanged(f"Không thấy thanh trượt '{self.SLIDERS[key]}' trong "
                                "More Options — giao diện Suno có thể đã đổi.")
        target = max(self._attr_int(loc, "aria-valuemin", 0),
                     min(self._attr_int(loc, "aria-valuemax", 100), int(value)))
        got = self._read_now(loc)
        if got == target:
            return got
        # LIVE 2026-11-09: kéo chuột luôn trượt trên giao diện mới (mất ~6 giây
        # mỗi thanh) còn phím mũi tên ăn ngay → thử phím trước, kéo là dự phòng.
        got = self._keyboard_slider(loc, target)
        if got != target:
            got = self._drag_slider(loc, target)
        if got != target:
            got = self._keyboard_slider(loc, target)
        if got != target:
            raise SunoUIChanged(f"Không đặt được '{self.SLIDERS[key]}' = {value} "
                                f"(đang {got}).")
        return got

    # Gửi n lần keydown vào thanh trượt, mỗi lần chờ 1 khung hình để React cập
    # nhật giá trị (gửi dồn trong 1 nhịp thì Suno chỉ nhận 1 nấc).
    _JS_SLIDER_KEYS = r"""async (e, a) => { const [key, n, budget] = a;
      const t0 = performance.now(); e.focus();
      for (let i = 0; i < n; i++) {
        if (performance.now() - t0 > budget) break;
        e.dispatchEvent(new KeyboardEvent('keydown', {key: key, code: key,
                                                      bubbles: true, cancelable: true}));
        await new Promise(r => { let done = false;
          const f = () => { if (!done) { done = true; r(); } };
          requestAnimationFrame(() => setTimeout(f, 0)); setTimeout(f, 40); });
      }
      return true; }"""

    def _keyboard_slider(self, loc, target: int) -> Optional[int]:
        """Fallback UI mới: chỉnh tương đối bằng Arrow từ aria-valuenow hiện tại.
        Suno bắt Arrow nhưng bỏ qua Home/End, nên không giả định Home về min."""
        try:
            if not self._focus_slider(loc):
                return self._read_now(loc)
            current = self._read_now(loc)
            if current is not None and current != target:
                # Nhanh: gửi phím mũi tên ngay trong trang, mỗi khung hình 1 phím
                # (LIVE 2026-11-09: 40 nấc ~0,8 giây so với ~6 giây bấm từng phím
                # qua Playwright). Giới hạn 2,5 giây; thiếu nấc thì vòng dưới bù.
                key = "ArrowRight" if target > current else "ArrowLeft"
                try:
                    loc.evaluate(self._JS_SLIDER_KEYS, [key, abs(target - current), 2500])
                except Exception:
                    pass
                for _ in range(5):
                    if self._read_now(loc) == target:
                        return target
                    self._wait(60)
            for _ in range(3):
                current = self._read_now(loc)
                if current is None or current == target:
                    break
                key = "ArrowRight" if target > current else "ArrowLeft"
                for _ in range(abs(target - current)):
                    self.page.keyboard.press(key)
                # Đọc lại ngay khi Suno cập nhật (thường <0,2 giây) thay vì chờ cố định.
                for _ in range(8):
                    if self._read_now(loc) == target:
                        return target
                    self._wait(60)
        except Exception:
            return self._read_now(loc)
        return self._read_now(loc)

    def _focus_slider(self, loc) -> bool:
        """Đưa focus bàn phím vào thanh trượt (cuộn vào giữa khung nếu cần)."""
        js_focused = "e => e === document.activeElement || e.contains(document.activeElement)"
        for attempt in range(2):
            try:
                if attempt:
                    loc.evaluate("e => e.scrollIntoView({block: 'center'})")
                    self._wait(150)
                loc.focus()
                if loc.evaluate(js_focused) is True:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _attr_int(loc, name: str, default: int) -> int:
        try:
            return int(float(loc.get_attribute(name)))
        except Exception:
            return default

    def _drag_slider(self, loc, value: int) -> Optional[int]:
        """Kéo 1 phần tử role=slider tới `value`; trả giá trị đọc lại."""
        try:
            lo = int(float(loc.get_attribute("aria-valuemin") or 0))
            hi = int(float(loc.get_attribute("aria-valuemax") or 100))
        except Exception:
            lo, hi = 0, 100
        target = max(lo, min(hi, int(value)))
        if self._read_now(loc) == target:
            return target
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        self._wait(250)
        st = loc.evaluate(self._JS_SLIDER_STATE)
        if not st["onTop"]:
            # Rãnh bị che (thanh cuộn panel/sticky footer) → cuộn vào giữa khung.
            loc.evaluate("e => e.scrollIntoView({block: 'center'})")
            self._wait(300)
            st = loc.evaluate(self._JS_SLIDER_STATE)
        x0, w = st["x"], max(1.0, st["w"])
        cy = st["y"] + st["h"] / 2
        span = max(1, hi - lo)
        mouse = self.page.mouse

        def pos(v: float) -> float:
            return x0 + (v - lo) / span * w

        # LIVE: 1 cú click (down/up tại chỗ) KHÔNG đổi giá trị; phải KÉO (down →
        # move nhiều bước → up). Kéo từ vị trí hiện tại tới đích; biên thì kéo
        # vượt rãnh 12px cho chắc chạm min/max.
        x = pos(target)
        for _ in range(5):
            cur = self._read_now(loc)
            if cur is None or cur == target:
                break
            xe = x0 - 12 if target == lo else (x0 + w + 12 if target == hi else x)
            xs = min(x0 + w - 2, max(x0 + 2, pos(cur)))
            mouse.move(xs, cy)
            self._wait(80)
            mouse.down()
            self._wait(80)
            mouse.move(xe, cy, steps=12)
            self._wait(80)
            mouse.up()
            self._wait(250)
            got = self._read_now(loc)
            if got is not None and got != target:
                x = min(x0 + w, max(x0, x + (target - got) / span * w))
        return self._read_now(loc)

    def set_creative_sliders(self, values: dict) -> dict:
        """values: {"variety": 0, "style_influence": 75, "weirdness": 30}; khoá
        None/thiếu = để nguyên. Trả giá trị đọc lại sau khi đặt."""
        out = {}
        for key in ("variety", "style_influence", "weirdness"):
            if values.get(key) is None:
                continue
            out[key] = self.set_slider(key, int(values[key]))
        if out:
            self._log("Thanh trượt Suno: " + ", ".join(
                f"{self.SLIDERS[k]}={v}" for k, v in out.items()))
        return out

    def ensure_creative_sliders(self, values: dict) -> dict:
        """Chỉ kéo những thanh đang LỆCH so với `values` (đọc lại trước mỗi Create
        rẻ hơn kéo lại cả 3). Trả `values` đã chốt."""
        want = {k: int(v) for k, v in values.items() if v is not None and k in self.SLIDERS}
        drift = {k: v for k, v in want.items() if self.read_slider(k) != v}
        if drift:
            self.set_creative_sliders(drift)
        return want

    def fill_lyrics(self, text: str) -> None:
        """Điền ô Lyrics (chỉ dùng cho THẺ CẤU TRÚC instrumental như
        '[Intro - solo flute only]' — không bao giờ điền lời hát)."""
        loc = self._find("lyrics_editor", timeout_ms=6000)
        if (loc.get_attribute("data-cowrite-input") or "").lower() == "true":
            raise SunoUIChanged("Selector Lyrics trỏ nhầm ô Cowriter (nhờ Suno viết lời).")
        try:
            # Lexical contenteditable: xoá rồi GÕ từng dòng (Enter = đoạn mới).
            loc.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Delete")
            lines = [ln.strip() for ln in (text or "").splitlines()]
            for i, ln in enumerate(lines):
                if ln:
                    self.page.keyboard.insert_text(ln)
                if i < len(lines) - 1:
                    self.page.keyboard.press("Enter")
            self._wait(300)
            got = (loc.inner_text() or "").strip()
        except Exception as e:
            raise SunoUIChanged(f"Không điền được Lyrics: {e}") from e
        want = [ln for ln in lines if ln]
        if want and want[0] not in got:
            raise SunoUIChanged("Lyrics sau khi điền không khớp nội dung mong muốn.")
        self._log(f"Đã điền thẻ cấu trúc vào Lyrics ({len(text)} ký tự).")

    # ── snapshot / monitor ───────────────────────────────────────
    def snapshot_song_ids(self) -> set[str]:
        """Tập song_id (UUID) hiện có trong danh sách — đọc từ href a[href*='/song/'].
        Dùng để so trước/sau Create → biết bài nào MỚI."""
        try:
            hrefs = self.page.eval_on_selector_all(
                "a[href*='/song/']",
                "els => els.map(e => e.getAttribute('href'))")
        except Exception:
            hrefs = []
        ids: set[str] = set()
        for h in hrefs or []:
            m = _UUID_RE.search(h or "")
            if m:
                ids.add(m.group(0))
        return ids

    def _song_meta(self, song_id: str) -> tuple[str, Optional[float]]:
        """(title, duration_seconds) best-effort cho 1 song_id."""
        title, dur = "", None
        try:
            link = self.page.locator(f"a[href*='/song/{song_id}']").first
            if link.count() > 0:
                title = (link.inner_text() or "").strip().split("\n")[0]
        except Exception:
            pass
        # duration: aria-label 'Play <title>' row thường kèm text mm:ss
        try:
            play = self.page.locator(
                f"[aria-label^='Play']").first
            _ = play  # duration đọc kém tin cậy — để None nếu không chắc.
        except Exception:
            pass
        return title, dur

    def generation_state(self) -> dict:
        """{captcha: khung captcha đang HIỆN, kind, toast: thông báo lỗi tạo bài}."""
        try:
            st = self.page.evaluate(_JS_GEN_STATE) or {}
        except Exception:
            st = {}
        return {"captcha": bool(st.get("captcha")), "kind": st.get("kind") or "",
                "toast": st.get("toast") or ""}

    def _gen_flags(self) -> dict:
        ev = list(self._gen_events)
        last_req = max((i for i, e in enumerate(ev) if e == "captcha_required"), default=-1)
        last_ver = max((i for i, e in enumerate(ev) if e.startswith("captcha_verified")),
                       default=-1)
        sent = "generate_sent" in ev
        return {"sent": sent,
                "ok": any(e.startswith("generate_http_2") for e in ev),
                "bad": ([e for e in ev if e.startswith(("generate_http_4", "generate_http_5"))]
                        + list(self._gen_logical_errors)),
                # Suno đòi captcha mà CHƯA có «captcha verified» → đang chờ xác minh.
                "captcha_pending": last_req > last_ver and not sent,
                "events": ev}

    def _gen_diag(self) -> str:
        """Tóm tắt tín hiệu lượt Create (không token/cookie) + body lỗi /generate."""
        parts = [e for e in dict.fromkeys(self._gen_events)]
        parts.extend(self._gen_logical_errors[:1])
        for resp in self._gen_bad[:1]:
            try:
                body = (resp.text() or "").strip().replace("\n", " ")[:200]
            except Exception:
                body = ""
            if body:
                parts.append(f"body: {body}")
        return "; ".join(parts) or "không có tín hiệu captcha/generate"

    def save_debug(self, tag: str) -> Optional[str]:
        if self.debug_dir is None:
            return None
        try:
            Path(self.debug_dir).mkdir(parents=True, exist_ok=True)
            p = Path(self.debug_dir) / f"create_{tag}_{time.strftime('%H%M%S')}.png"
            self.page.screenshot(path=str(p))
            return str(p)
        except Exception:
            return None

    def _alert_human(self, msg: str, on_human: Optional[Callable[[str], None]],
                     beep: bool = False) -> None:
        self._log(msg)
        if beep:
            # Kéo CỬA SỔ Suno lên trên cùng (bỏ thu nhỏ) để người dùng tự tick
            # captcha ngay — chỉ đưa cửa sổ lên, KHÔNG bấm gì vào captcha.
            from .window_focus import raise_page_window
            how = raise_page_window(self.page)
            if how != "foreground":
                self._log(f"Đưa cửa sổ Suno lên trước: {how} (Windows chặn chiếm "
                          "tiêu điểm → nháy nút trên thanh tác vụ)")
        else:
            try:
                self.page.bring_to_front()
            except Exception:
                pass
        if beep:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        if on_human is not None:
            try:
                on_human(msg)
            except Exception:
                pass

    def wait_for_new_songs(self, before_ids: set[str], expected: int,
                           timeout_sec: int = 300,
                           poll_sec: float = 3.0,
                           accept_sec: Optional[float] = None,
                           captcha_wait_sec: float = 1200,
                           on_human: Optional[Callable[[str], None]] = None,
                           captcha_grace_sec: float = 10,
                           captcha_beep_sec: float = 180
                           ) -> list[NewSong]:
        """Chờ tới khi xuất hiện >= expected bài MỚI (id không thuộc before_ids).
        Đã-bấm-Create + hết giờ mà chưa thấy → SunoSubmissionUncertain.

        accept_sec (bật kiểm tra lượt Create có được NHẬN không — bình thường bài
        mới hiện ~3s sau khi bấm):
          • khung CAPTCHA hiện, HOẶC console Suno báo «captcha required» mà sau
            captcha_grace_sec vẫn chưa «captcha verified» (Turnstile nằm trong shadow
            DOM đóng — có thể không đo được khung) → đưa cửa sổ Suno lên trước, báo
            người dùng TỰ giải (on_human), chờ thêm tới captcha_wait_sec. KHÔNG bấm
            gì vào captcha. Hết giờ mà chưa có bài → SunoCaptchaRequired.
          • Suno báo «Generation failed» / /generate trả 4xx–5xx → dừng NGAY.
          • quá accept_sec: không bài, không captcha, không request tạo bài đang
            chờ → SunoSubmissionUncertain sớm (không mù chờ 300s)."""
        start = time.time()
        deadline = start + timeout_sec
        new_ids: list[str] = []
        captcha_seen = False
        resolved_at: Optional[float] = None     # captcha vừa biến mất (người đã giải)
        last_nag = last_beep = 0.0
        while time.time() < deadline:
            current = self.snapshot_song_ids() | set(self._gen_response_ids)
            fresh = [i for i in current if i not in before_ids]
            if len(fresh) >= expected:
                new_ids = fresh[:expected] if expected > 0 else fresh
                break
            # Giữ lại tối đa những gì đã thấy để báo cáo.
            if len(fresh) > len(new_ids):
                new_ids = fresh
            flags = self._gen_flags()
            if accept_sec is not None and not new_ids:
                st = self.generation_state()
                now = time.time()
                # HTTP 200 vẫn có thể chứa lỗi nghiệp vụ/không sinh clip.
                if flags["bad"] or st["toast"]:
                    why = st["toast"] or flags["bad"][0]
                    shot = self.save_debug("failed")
                    err = (f"Suno báo lỗi sau Create: «{why}» — lượt này KHÔNG được nhận. "
                           f"[{self._gen_diag()}]" + (f" Ảnh: {shot}" if shot else ""))
                    if captcha_seen:
                        raise SunoCaptchaRequired(
                            "CAPTCHA chưa được xác minh xong. " + err)
                    raise SunoSubmissionUncertain(err)
                # Request 2xx đang xử lý nhưng chưa có clip: tiếp tục chờ; không
                # chạy nhánh captcha/not-accepted và tuyệt đối không click lại.
                if flags["ok"]:
                    self._wait(int(poll_sec * 1000))
                    continue
                pending = (flags["captcha_pending"] and now - start >= captcha_grace_sec
                           and not st["toast"] and not flags["bad"])
                if st["captcha"] or pending:
                    # Ưu tiên chờ người giải (kể cả khi toast lỗi còn đọng lại).
                    resolved_at = None
                    if not st["captcha"]:
                        st["kind"] = "Cloudflare «Verify you are human» — chưa xác minh"
                    if not captcha_seen:
                        captcha_seen = True
                        deadline = max(deadline, now + captcha_wait_sec)
                        self.save_debug("captcha")
                        self._alert_human(
                            f"⚠️ CẦN BẠN: Suno yêu cầu xác minh CAPTCHA khi Create "
                            f"({st['kind'] or 'captcha'}). Hãy TỰ giải trong cửa sổ "
                            f"Suno (Cốc Cốc) — tool KHÔNG tự giải; đang chờ tối đa "
                            f"{int(captcha_wait_sec // 60)} phút…", on_human, beep=True)
                        last_nag = last_beep = now
                    elif now - last_nag >= 60:
                        last_nag = now
                        beep = now - last_beep >= captcha_beep_sec
                        if beep:
                            last_beep = now
                        self._alert_human(
                            f"⚠️ Vẫn đang chờ bạn giải CAPTCHA trong cửa sổ Suno — "
                            f"còn ~{int((deadline - now) // 60)} phút.", on_human, beep=beep)
                elif captcha_seen and not flags["sent"]:
                    # Captcha đã tắt mà Suno chưa gửi lệnh tạo bài: cho thêm accept_sec.
                    if resolved_at is None:
                        resolved_at = now
                    elif now - resolved_at >= accept_sec:
                        shot = self.save_debug("after_captcha")
                        raise SunoCaptchaRequired(
                            f"CAPTCHA đã tắt nhưng {int(accept_sec)}s sau Suno vẫn không "
                            f"gửi lệnh tạo bài — lượt Create không được nhận. "
                            f"[{self._gen_diag()}]" + (f" Ảnh: {shot}" if shot else ""))
                elif (not captcha_seen and not flags["sent"]
                      and now - start >= accept_sec):
                    shot = self.save_debug("not_accepted")
                    raise SunoSubmissionUncertain(
                        f"Sau Create {int(accept_sec)}s: không có bài mới, không thấy "
                        f"CAPTCHA, Suno không gửi lệnh tạo bài — lượt này KHÔNG được "
                        f"nhận. [{self._gen_diag()}]" + (f" Ảnh: {shot}" if shot else ""))
            self._wait(int(poll_sec * 1000))
        if len(new_ids) < max(expected, 1):
            if captcha_seen and not new_ids:
                raise SunoCaptchaRequired(
                    f"Suno yêu cầu CAPTCHA nhưng chưa được giải trong "
                    f"{int(captcha_wait_sec // 60)} phút — lượt Create không được nhận. "
                    f"Giải CAPTCHA trên Suno rồi bấm Resume (tool không tự giải).")
            raise SunoSubmissionUncertain(
                f"Sau Create không thấy đủ bài mới ({len(new_ids)}/{expected}) "
                f"trong {timeout_sec}s. KHÔNG bấm Create lại — kiểm tra thủ công "
                f"trên Suno rồi resume. [{self._gen_diag()}]")
        out: list[NewSong] = []
        for sid in new_ids:
            title, dur = self._song_meta(sid)
            out.append(NewSong(song_id=sid, title=title, duration_seconds=dur))
        return out

    # ── tín hiệu quanh lượt Create (chỉ đọc) ─────────────────────
    def _on_console(self, msg) -> None:
        try:
            t = (msg.text or "").strip().lower()
        except Exception:
            return
        if "captcha required" in t:
            self._gen_events.append("captcha_required")
        elif "captcha verified" in t:        # KHÔNG lưu token
            self._gen_events.append("captcha_verified_null" if t.endswith("null")
                                    else "captcha_verified")

    @staticmethod
    def _is_generate(url: str) -> bool:
        return "/api/generate/" in (url or "")

    def _on_request(self, req) -> None:
        try:
            if req.method == "POST" and self._is_generate(req.url):
                self._gen_events.append("generate_sent")
        except Exception:
            pass

    def _on_response(self, resp) -> None:
        try:
            if resp.request.method != "POST" or not self._is_generate(resp.url):
                return
            self._gen_events.append(f"generate_http_{resp.status}")
            batch: list[str] = []
            self._gen_batches.append(batch)
            if resp.status >= 400:
                self._gen_bad.append(resp)
                return
            # Chỉ giữ UUID, tuyệt đối không lưu body/token/cookie.  Các API
            # generate hiện tại trả clip ở nhiều cấu trúc khác nhau. Chỉ nhận
            # ID có ngữ cảnh clip/song; UUID task ở top-level KHÔNG phải bài hát.
            try:
                body = resp.text() or ""
                # LIVE 2026-11-09 (36028d8b, 30c274fe → 404): object top-level của
                # response cũng có "metadata"/"major_model_version" nên UUID task
                # bị nhận như clip; code cũ sort() rồi lấy 2 ID đầu → lúc trúng ID
                # task, bỏ sót 1 bài thật. Nay: ID nằm TRONG danh sách clips/songs/
                # tracks là chuẩn (giữ thứ tự Suno trả); ID khác chỉ là dự phòng.
                strong: list[str] = []
                weak: list[str] = []

                class _Found:
                    @staticmethod
                    def add(uid: str, primary: bool = False) -> None:
                        bucket = strong if primary else weak
                        if uid not in bucket:
                            bucket.append(uid)
                found = _Found()

                def visit(value, key: str = "", clip_context: bool = False) -> None:
                    if isinstance(value, dict):
                        marker_keys = {str(k).lower() for k in value}
                        looks_like_clip = clip_context or bool(marker_keys & {
                            "audio_url", "video_url", "image_url", "title",
                            "display_name", "metadata", "major_model_version",
                        })
                        for child_key, child in value.items():
                            ck = str(child_key).lower()
                            if (ck in {"clip_id", "song_id", "audio_id"}
                                    or ck.endswith("_song_id")) and isinstance(child, str):
                                match = _UUID_RE.fullmatch(child.strip())
                                if match:
                                    found.add(match.group(0), primary=clip_context)
                            elif ck == "id" and looks_like_clip and isinstance(child, str):
                                match = _UUID_RE.fullmatch(child.strip())
                                if match:
                                    found.add(match.group(0), primary=clip_context)
                            else:
                                visit(child, ck, ck in {"clips", "songs", "tracks"})
                    elif isinstance(value, list):
                        for child in value:
                            visit(child, key, clip_context)

                try:
                    visit(json.loads(body))
                except Exception:
                    pass
                chosen = strong or weak
                if chosen:
                    batch.extend(chosen)
                    self._gen_response_ids.update(chosen)
                    self._gen_events.append(
                        f"generate_ids_{len(self._gen_response_ids)}")
                try:
                    payload = json.loads(body)
                    logical = ""
                    if isinstance(payload, dict):
                        for key in ("error", "detail", "error_message"):
                            value = payload.get(key)
                            if value:
                                logical = f"generate_http_200_{key}: {str(value)[:180]}"
                                break
                        if not logical and payload.get("success") is False:
                            logical = "generate_http_200_success_false"
                        if not logical and isinstance(payload.get("clips"), list) \
                                and not payload["clips"]:
                            logical = "generate_http_200_empty_clips"
                    if logical:
                        self._gen_logical_errors.append(logical)
                        self._gen_events.append("generate_logical_error")
                except Exception:
                    pass
            except Exception:
                # Network response có thể đã bị browser giải phóng; DOM vẫn là
                # fallback và lỗi đọc body không được làm hỏng lượt Create.
                pass
        except Exception:
            pass

    def _on_request_failed(self, req) -> None:
        try:
            if req.method == "POST" and self._is_generate(req.url):
                self._gen_events.append("generate_http_599 (request failed)")
        except Exception:
            pass

    def _arm_create_watch(self) -> None:
        self._gen_events.clear()
        self._gen_bad.clear()
        self._gen_response_ids.clear()
        self._gen_batches.clear()
        self._gen_logical_errors.clear()
        if self._gen_watch:
            return
        try:
            self.page.on("console", self._on_console)
            self.page.on("request", self._on_request)
            self.page.on("response", self._on_response)
            self.page.on("requestfailed", self._on_request_failed)
            self._gen_watch = True
        except Exception:
            pass

    # ── LIVE: Create (chỉ khi dry_run=False) ─────────────────────
    def click_create(self) -> None:
        if self.dry_run:
            self._log("[DRY-RUN] BỎ QUA click Create (không tiêu credit).")
            return
        loc = self._find("create_button", timeout_ms=8000)
        self._prepare_create_click(loc)
        self._arm_create_watch()
        try:
            # Nút Create mới của Suno đôi khi nhận locator.click() nhưng không
            # phát pointer events, không gửi /api/generate và vẫn đứng nguyên.
            # Bấm bằng toạ độ chuột thật trước (giống các menu Studio đã ổn
            # định); chỉ fallback về Playwright click khi không lấy được box.
            if self._real_click(loc):
                self._log("Đã bấm Create bằng chuột thật (LIVE).")
            else:
                loc.click()
                self._log("Đã bấm Create (LIVE, fallback locator).")
        except Exception as e:
            raise SunoUIChanged(f"Không bấm được Create: {e}") from e

    def _open_menu_count(self) -> int:
        try:
            n = self.page.evaluate(_JS_OPEN_MENUS)
        except Exception:
            return 0
        return int(n) if isinstance(n, (int, float)) else 0

    def _close_open_menus(self, tries: int = 3) -> int:
        """Escape tới khi không còn menu thả xuống nào mở. Trả số menu còn mở."""
        for _ in range(tries):
            if not self._open_menu_count():
                return 0
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                break
            self._wait(250)
        return self._open_menu_count()

    def _create_blocker(self, loc) -> str:
        try:
            hit = loc.evaluate(_JS_CREATE_BLOCKER)
        except Exception:
            return ""
        return hit if isinstance(hit, str) else ""

    def _prepare_create_click(self, loc) -> None:
        """Trước khi bấm Create: đóng menu đang mở và chắc nút không bị che —
        nếu không, cú click chỉ đóng menu và Suno không nhận lệnh (không tốn
        credit nhưng hàng đợi dừng vì «Không chắc Suno đã nhận lệnh Create»)."""
        left = self._close_open_menus()
        if left:
            self._log(f"Còn {left} menu đang mở trước Create — đóng bằng chuột.")
            self._dismiss_popups()
            self._close_open_menus()
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        blocker = self._create_blocker(loc)
        if blocker:
            self._log(f"Nút Create đang bị che bởi {blocker} — đóng popup rồi bấm.")
            self._dismiss_popups()
            self._close_open_menus()
            blocker = self._create_blocker(loc)
            if blocker:
                self._log(f"CẢNH BÁO: nút Create vẫn bị che bởi {blocker}.")

    # ── tiện ích: định vị nút ⋯ (More options) của 1 bài ─────────
    def _real_click(self, loc) -> bool:
        """Click bằng toạ độ CHUỘT THẬT (mouse.move → click). Một số menu Radix
        trong Studio (đặc biệt nút 'Export') chỉ mở bằng chuột thật + pointermove,
        KHÔNG phản hồi locator.click()/dispatch/keyboard. Trả True nếu đã click."""
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            box = loc.bounding_box()
        except Exception:
            box = None
        if not box:
            return False
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        try:
            self.page.mouse.move(cx, cy)
            self._wait(150)
            self.page.mouse.click(cx, cy)
            return True
        except Exception:
            return False

    # Nút ⋯ ở ĐẦU TRANG BÀI (/song/<id>, cạnh like/dislike) luôn là của chính
    # bài đó. Trước đây ưu tiên nút ⋯ theo link /song/<id> — trên trang bài thì
    # đó là nút ở THANH PHÁT NHẠC dưới cùng: LIVE 2026-09-30, ngay sau 1 lần tải
    # thành công, menu mở từ thanh phát nhạc bị menu 'Earn Credits' ở sidebar
    # chen vào → submenu Edit không bung (~1/2 số bài tải lỗi). JS đánh dấu nút
    # ⋯ nằm trong vùng nội dung, không thuộc hàng của bài KHÁC, không ở sidebar
    # trái / thanh phát nhạc, và cao nhất trang.
    _MARK_HEADER_MORE_JS = """(id) => {
      document.querySelectorAll('[data-mm-song-more]')
        .forEach(e => e.removeAttribute('data-mm-song-more'));
      if (!id || !location.pathname.includes(id)) return false;
      let best = null, by = 1e9;
      for (const b of document.querySelectorAll("button[aria-label='More options']")) {
        if (b.offsetParent === null) continue;
        let c = b, foreign = false;
        for (let i = 0; i < 6 && c; i++) {
          const ls = c.querySelectorAll ? c.querySelectorAll("a[href*='/song/']") : [];
          if (ls.length) {
            foreign = [...ls].some(a => !(a.getAttribute('href') || '').includes(id));
            break;
          }
          c = c.parentElement;
        }
        if (foreign) continue;
        const r = b.getBoundingClientRect();
        if (r.width === 0 || r.x < 250 || r.y > innerHeight - 120) continue;
        if (r.y < by) { by = r.y; best = b; }
      }
      if (!best) return false;
      best.setAttribute('data-mm-song-more', '1');
      return true;
    }"""

    def _locate_song_more(self, song_id: Optional[str]):
        """Trả locator nút ⋯ của bài `song_id`: ưu tiên nút ⋯ ở đầu trang bài;
        rồi tới nút trong hàng chứa link /song/<id>; cuối cùng nút ⋯ đầu tiên."""
        more = None
        if song_id:
            try:
                if self.page.evaluate(self._MARK_HEADER_MORE_JS, song_id):
                    cand = self.page.locator("button[data-mm-song-more='1']").first
                    if cand.count() > 0:
                        more = cand
            except Exception:
                more = None
        if more is None and song_id:
            try:
                row = self.page.locator(f"a[href*='/song/{song_id}']").first
                if row.count() > 0:
                    container = row.locator(
                        "xpath=ancestor-or-self::*[.//button[@aria-label='More options']][1]")
                    cand = container.locator("button[aria-label='More options']").first
                    if cand.count() > 0:
                        more = cand
            except Exception:
                more = None
        if more is None or more.count() == 0:
            more = query_first(self.page, self.sel.get("song_more_options", []),
                               timeout_ms=6000)
        return more

    def _dismiss_popups(self) -> None:
        """Đóng menu/popup đang mở (vd menu 'Earn Credits' ở sidebar) và đưa
        chuột về vùng trống giữa trang trước khi mở menu ⋯ của bài."""
        try:
            for _ in range(2):
                self.page.keyboard.press("Escape")
                self._wait(150)
            w, h = self.page.evaluate("() => [innerWidth, innerHeight]")
            self.page.mouse.move(w * 0.45, h * 0.55)
            self._wait(200)
        except Exception:
            pass

    def _open_song_menu(self, song_id: str, tries: int = 3) -> None:
        """Mở menu ⋯ của bài và XÁC NHẬN đúng là menu bài (có mục Edit trong
        [role=menu]) — không thì dọn popup rồi mở lại."""
        edit_sel = [f"[role=menu] {x}" for x in
                    self.sel.get("studio_edit_menu", [])[:2]]
        for i in range(tries):
            self._dismiss_popups()
            more = self._locate_song_more(song_id)
            if more is None:
                raise SunoUIChanged(
                    f"Không tìm thấy menu ⋯ (More options) cho bài {song_id}.")
            if not self._real_click(more):
                more.click()
            self._wait(600)
            if query_first(self.page, edit_sel, timeout_ms=3000) is not None:
                return
            self._log(f"Menu ⋯ của bài chưa mở đúng (lần {i + 1}/{tries}) — mở lại.")
        raise SunoUIChanged(f"Không mở được menu ⋯ của bài {song_id}.")

    def _hover_real(self, loc) -> bool:
        """Rê CHUỘT THẬT vào mục (từ mép trái vào giữa) để Radix bung submenu."""
        try:
            box = loc.bounding_box()
        except Exception:
            box = None
        if not box:
            return False
        cy = box["y"] + box["height"] / 2
        try:
            self.page.mouse.move(box["x"] + 6, cy)
            self.page.mouse.move(box["x"] + box["width"] / 2, cy, steps=4)
            return True
        except Exception:
            return False

    def _ensure_song_open(self, song_id: str, timeout_sec: int = 90) -> None:
        """Mở TRANG BÀI `suno.com/song/<id>` để thao tác ⋯ trên đúng bài.

        Danh sách ở /create được ẢO HOÁ (virtualized): khi thư viện dài, các bài
        tạo trước đó bị gỡ khỏi DOM nên dò theo `a[href*='/song/<id>']` trong
        danh sách rất hay trượt (đã đo LIVE: chỉ 19 link trong DOM, cuộn một
        nhịp là bộ link đổi hoàn toàn). Trang bài thì luôn mở được bằng id."""
        url = f"https://suno.com/song/{song_id}"
        deadline = time.time() + timeout_sec
        last_err = ""
        while time.time() < deadline:
            try:
                if song_id not in (self.page.url or ""):
                    self.page.goto(url, wait_until="domcontentloaded")
                self._wait(1500)
                has_link = self.page.locator(
                    f"a[href*='/song/{song_id}']").count() > 0
                has_more = self.page.locator(
                    "button[aria-label='More options']").count() > 0
                if has_link or has_more:
                    return
            except Exception as e:      # noqa: BLE001 — thử lại
                last_err = str(e)
            self._wait(2000)
        self._log(f"Mở thẳng trang bài {song_id} không được ({last_err}) — "
                  f"thử dò trong danh sách /create.")
        self._ensure_song_listed(song_id)

    def _scroll_song_list(self) -> None:
        """Cuộn ĐÚNG khung danh sách bài (không phải cửa sổ). `mouse.wheel` chỉ
        tác dụng lên phần tử dưới con trỏ, mà con trỏ mặc định ở (0,0) — nằm
        ngoài danh sách nên trước đây cuộn không ăn."""
        try:
            box = self.page.evaluate("""() => {
              const a = document.querySelector("a[href*='/song/']");
              let n = a && a.parentElement;
              for (let i = 0; i < 12 && n; i++) {
                if (n.scrollHeight > n.clientHeight + 120 && n.clientHeight > 260) {
                  const r = n.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }
                n = n.parentElement;
              }
              return null;
            }""")
        except Exception:
            box = None
        try:
            if box:
                self.page.mouse.move(box["x"], box["y"])
            self.page.mouse.wheel(0, 1200)
        except Exception:
            pass

    def _ensure_song_listed(self, song_id: str, timeout_sec: int = 30) -> None:
        """Quay về /create và chờ link của bài xuất hiện (cuộn nếu cần). Cần vì
        sau 'Go to Song' driver đang ở trang bài KHÁC — mỗi lần tải phải trở lại
        danh sách để định vị đúng bài theo song_id."""
        link_sel = f"a[href*='/song/{song_id}']"
        try:
            first = self.page.locator(link_sel).first
            if first.count() > 0 and first.is_visible():
                return
        except Exception:
            pass
        try:
            self.page.goto(config.SUNO_URL, wait_until="domcontentloaded")
        except Exception:
            pass
        self._wait(1500)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            loc = self.page.locator(link_sel).first
            try:
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=2000)
                    if loc.is_visible():
                        return
            except Exception:
                pass
            self._scroll_song_list()             # nạp thêm bài (lazy load)
            self._wait(800)
        raise SunoUIChanged(
            f"Không thấy bài {song_id} trong danh sách /create để mở Studio.")

    # ── LIVE bước 1: mở bài trong Studio ở chế độ Single-track ────
    def _open_song_in_studio(self, song_id: str,
                             load_timeout_sec: int = 180) -> None:
        """⋯ → Edit → Open in Studio → Single-track → chờ project load xong.
        'Single-track' (full mix) MIỄN PHÍ; KHÔNG chọn Multi-track (tốn credit)."""
        self._ensure_song_open(song_id)
        failed = query_first(self.page, [
            "text=This song failed to generate",
            "text=Song failed to generate",
            "text=Generation failed",
        ], timeout_ms=2500)
        if failed is not None:
            raise SunoGenerationFailed(
                f"Bản Suno {song_id[:8]} đã báo 'This song failed to generate' — "
                "không thể mở Studio hoặc tải WAV.")
        self._open_song_menu(song_id)

        # Submenu 'Edit' mở khi hover (Radix). Chỉ tìm TRONG [role=menu] — trước
        # đây selector dự phòng `button:has-text('Edit')` khớp nhầm nút 'Edit'
        # trên ảnh bìa khi menu ⋯ bị đóng. Thử rê chuột thật, click, phím →;
        # không được thì mở lại menu ⋯ (tối đa 3 vòng).
        edit_sel = [f"[role=menu] {x}" for x in
                    self.sel.get("studio_edit_menu", [])[:2]]
        ois_sel = [f"[role=menu] {x}" for x in
                   self.sel.get("open_in_studio", [])[:2]]
        ois = None
        for round_ in range(3):
            if round_:
                self._log("Submenu Edit chưa bung — mở lại menu ⋯ và thử lại.")
                self._open_song_menu(song_id)
            edit = query_first(self.page, edit_sel, timeout_ms=4000)
            if edit is None:
                continue
            for how in ("hover", "click", "key"):
                try:
                    if how == "hover":
                        if not self._hover_real(edit):
                            edit.hover()
                    elif how == "click":
                        edit.click()
                    else:
                        edit.focus()
                        self.page.keyboard.press("ArrowRight")
                except Exception:
                    continue
                self._wait(700)
                ois = query_first(self.page, ois_sel, timeout_ms=2000)
                if ois is not None:
                    break
            if ois is not None:
                break
        if ois is None:
            raise SunoUIChanged(
                "Không thấy 'Open in Studio' trong submenu Edit của bài.")
        # Submenu Edit của giao diện Suno hiện tại là menu nổi Radix. Locator
        # vẫn báo visible nhưng `locator.click()` có thể chờ actionability tới
        # 30 giây rồi timeout (menu dịch/chuyển focus trong lúc Playwright kiểm
        # tra). Click theo toạ độ chuột thật ổn định hơn và cũng giống thao tác
        # người dùng. Giữ force/DOM click làm dự phòng nếu không lấy được box.
        context = self.page.context
        page_before = self.page
        pages_before = set(context.pages)
        if not self._real_click(ois):
            try:
                ois.click(timeout=5000, force=True)
            except Exception:
                ois.dispatch_event("click")
        self._wait(800)

        # Suno có hai biến thể UI: điều hướng ngay trong tab hiện tại hoặc mở
        # Studio trong tab mới. Nếu là tab mới mà vẫn giữ `self.page` ở trang
        # /song/, bước tìm Single-track chắc chắn trượt dù popup đã hiện đúng.
        new_pages = [p for p in context.pages if p not in pages_before]
        if new_pages:
            self.page = new_pages[-1]
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception:
                pass
            self._log("'Open in Studio' mở tab mới → đã chuyển driver sang tab Studio.")
        elif page_before is not self.page:
            self.page = page_before

        # Hộp thoại chọn Single-track vs Multi-track.
        single = query_first(self.page, self.sel.get("studio_single_track", []),
                             timeout_ms=10000)
        if single is None:
            try:
                dialogs = self.page.locator("[role=dialog]").all_inner_texts()
                visible_buttons = self.page.locator("button:visible").all_inner_texts()
                detail = (f" URL={self.page.url}; dialogs={dialogs[:3]}; "
                          f"buttons={visible_buttons[-20:]}")
            except Exception:
                detail = f" URL={getattr(self.page, 'url', '')}"
            self.save_debug("no_single_track")
            raise SunoUIChanged(
                "Không thấy nút 'Single-track' trong hộp thoại mở Studio."
                + detail)
        single.click()
        self._log("Đã chọn Single-track, đang chờ Studio build project...")

        # 'Building your project...' có thể mất khá lâu → chờ nút Export hiện ra
        # như tín hiệu editor đã load xong.
        export = query_first(self.page, self.sel.get("studio_export_menu", []),
                             timeout_ms=load_timeout_sec * 1000)
        if export is None:
            raise SunoUIChanged(
                "Studio không load xong (không thấy nút 'Export' sau khi chờ "
                f"{load_timeout_sec}s).")
        self._log("Studio đã load xong (single-track).")

    # ── LIVE bước 2: Export → Full Song → Go to Song ─────────────
    def _studio_export_full_song(self, render_timeout_sec: int = 300) -> None:
        """Export (góc trên-phải) → Full Song → chờ 'Song Saved' → 'Go to Song'
        → chờ hết 'Preparing song for playback...'. Full Song (single-track)
        MIỄN PHÍ (chỉ Multitrack/stems mới tốn credit)."""
        export = query_first(self.page, self.sel.get("studio_export_menu", []),
                             timeout_ms=10000)
        if export is None:
            raise SunoUIChanged("Không thấy nút 'Export' trong Studio.")

        # Menu Export là Radix, CHỈ mở bằng chuột thật (locator.click không mở)
        # và đôi khi cần vài lần bấm (mỗi lần bấm toggle) → thử tối đa 6 lần,
        # dừng ngay khi thấy 'Full Song'.
        full = None
        for _ in range(6):
            self._real_click(export)
            self._wait(900)
            full = query_first(self.page, self.sel.get("studio_full_song", []),
                               timeout_ms=1500)
            if full is not None:
                break
        if full is None:
            raise SunoUIChanged(
                "Không mở được menu Export hoặc không thấy 'Full Song'.")
        # Studio render Full Song NGAY TRONG TAB rồi tự upload WAV lên Suno.
        # Theo dõi các request upload để chờ xong trước khi đóng tab/tải.
        studio_page = self.page
        uploads = self._track_studio_uploads(studio_page)
        if not self._real_click(full):
            full.click()
        self._log("Đã bấm Export → Full Song, đang render bản đầy đủ...")

        # 'Go to Song' xuất hiện cùng toast 'Song Saved!' khi render xong.
        goto = query_first(self.page, self.sel.get("studio_go_to_song", []),
                           timeout_ms=render_timeout_sec * 1000)
        if goto is None:
            raise SunoUIChanged(
                "Không thấy nút 'Go to Song' sau khi Export (render 'Song Saved' "
                f"chưa xong trong {render_timeout_sec}s?).")

        # QUAN TRỌNG: 'Go to Song' MỞ BÀI TRONG TAB MỚI (đã kiểm chứng LIVE) —
        # self.page vẫn đứng ở /studio. Phải bắt tab mới rồi chuyển self.page
        # sang đó, nếu không sẽ không thấy nút ⋯ để tải WAV.
        context = self.page.context
        new_page = None
        try:
            with context.expect_page(timeout=60000) as info:
                if not self._real_click(goto):
                    goto.click()
            new_page = info.value
        except Exception:
            # Không mở tab mới → có thể điều hướng cùng tab; thử tìm tab /song/.
            for p in context.pages:
                try:
                    if "/song/" in (p.url or ""):
                        new_page = p
                        break
                except Exception:
                    pass
        if new_page is not None and new_page is not self.page:
            self.page = new_page
            # KHÔNG đóng tab Studio ở đây: 'Go to Song' hiện ra chỉ 1–2s sau
            # Full Song, lúc Studio CÒN ĐANG upload file WAV của bản mới. Đóng
            # tab sớm làm upload bị huỷ → Suno báo mãi "we can't locate the
            # asset". Tab Studio được đóng sau khi tải xong WAV (download_wav).
            self._log("'Go to Song' mở bài ở tab mới → đã chuyển sang tab đó "
                      "(giữ tab Studio mở tới khi tải xong WAV).")
        else:
            self._log("Đã bấm 'Go to Song' (không phát hiện tab mới).")
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            self.page.wait_for_url("**/song/**", timeout=30000)
        except Exception:
            self._wait(2000)

        self._wait_preparing_done(render_timeout_sec)
        self._wait_studio_uploads(uploads, render_timeout_sec)

    # Request PUT/POST do tab Studio gửi khi lưu bản Full Song lên Suno.
    @staticmethod
    def _is_studio_upload(method: str, url: str) -> bool:
        u = (url or "").lower()
        return (method or "").upper() in ("PUT", "POST") and (
            "suno-data-uploads" in u or "amazonaws.com" in u or "/upload" in u)

    def _track_studio_uploads(self, page) -> dict:
        """Gắn listener lên tab Studio, đếm upload đang chạy. Lỗi → trả state
        rỗng (chỉ là bước chờ thêm, không chặn quy trình)."""
        st: dict = {"pending": {}, "seen": 0, "done": 0, "failed": 0}

        def _on_request(req) -> None:
            try:
                if self._is_studio_upload(req.method, req.url):
                    st["pending"][req] = req.url
                    st["seen"] += 1
                elif ((req.method or "").upper() != "GET"
                      and st.setdefault("logged", 0) < 6):
                    # Chẩn đoán: METHOD + đường dẫn (bỏ query), không ghi body.
                    st["logged"] += 1
                    self._log(f"[chẩn đoán] Studio gửi: {req.method} "
                              f"{(req.url or '').split('?')[0].split('://', 1)[-1][:120]}")
            except Exception:
                pass

        def _on_end(req, ok: bool) -> None:
            try:
                if st["pending"].pop(req, None) is not None:
                    st["done" if ok else "failed"] += 1
            except Exception:
                pass

        try:
            page.on("request", _on_request)
            page.on("requestfinished", lambda r: _on_end(r, True))
            page.on("requestfailed", lambda r: _on_end(r, False))
        except Exception:
            pass
        return st

    def _wait_studio_uploads(self, st: Optional[dict], timeout_sec: int = 300) -> None:
        """Chờ tab Studio upload xong file của bản Full Song (best-effort)."""
        if not st:
            return
        deadline = time.time() + timeout_sec
        logged = False
        while st["pending"] and time.time() < deadline:
            if not logged:
                self._log(f"Studio đang lưu file bản Full Song lên Suno "
                          f"({len(st['pending'])} upload) — chờ xong rồi mới tải WAV.")
                logged = True
            self._wait(1000)
        if st["pending"]:
            self._log("CẢNH BÁO: Studio vẫn đang upload sau khi chờ — vẫn thử tải "
                      "(tab Studio giữ mở).")
        elif st["seen"]:
            self._log(f"Studio đã lưu xong file lên Suno ({st['done']} upload xong"
                      + (f", {st['failed']} lỗi" if st["failed"] else "") + ").")

    def _wait_preparing_done(self, timeout_sec: int = 300) -> None:
        """Chờ tới khi 'Preparing song for playback...' biến mất. Đợi nó XUẤT
        HIỆN ngắn trước (nếu có) rồi chờ tan; hết giờ vẫn tiếp tục (best-effort)."""
        # Cho nó kịp xuất hiện (tối đa ~8s).
        appeared = query_first(self.page, self.sel.get("studio_preparing", []),
                               timeout_ms=8000)
        if appeared is None:
            self._log("Không thấy 'Preparing song for playback' — coi như đã sẵn sàng.")
            return
        self._log("Đang 'Preparing song for playback...', chờ hoàn tất...")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            still = query_first(self.page, self.sel.get("studio_preparing", []),
                                timeout_ms=1500)
            if still is None:
                self._log("Bản nhạc đã sẵn sàng (hết 'Preparing').")
                return
            self._wait(2000)
        self._log("CẢNH BÁO: vẫn còn 'Preparing song for playback' sau timeout — "
                  "vẫn thử tải tiếp.")

    # ── LIVE bước 3: ⋯ → Download → WAV trên trang bài đã render ──
    def _download_wav_current(self, dest: Path, timeout_sec: int = 180) -> str:
        """Tải WAV của bài đang hiển thị (sau 'Go to Song'). Bài render từ Studio
        có song_id MỚI → lấy id từ URL để định vị ⋯ đúng bài."""
        # Sau 'Go to Song' trang bài mới có thể mất kha khá thời gian mới render
        # xong nút ⋯ (đặc biệt khi còn 'Preparing song for playback...'). Chờ URL
        # ổn định rồi POLL tìm nút ⋯ thay vì bỏ cuộc ngay lần đầu.
        try:
            self.page.wait_for_url("**/song/**", timeout=45000)
        except Exception:
            pass

        # Lấy id bài mới từ URL rồi ĐIỀU HƯỚNG LẠI tới trang bài "sạch"
        # https://suno.com/song/<id> — trang này chỉ có 1-2 nút ⋯ (nút đầu tiên
        # là của chính bài, mở menu có Download), trong khi tab do 'Go to Song'
        # mở ra hiển thị cả danh sách (12 nút ⋯ dễ nhầm bài).
        cur_id = None
        try:
            m = _UUID_RE.search(self.page.url or "")
            cur_id = m.group(0) if m else None
        except Exception:
            cur_id = None
        if cur_id:
            try:
                self.page.goto(f"https://suno.com/song/{cur_id}",
                               wait_until="domcontentloaded")
                self._wait(1500)
            except Exception:
                pass

        more = None
        deadline = time.time() + max(timeout_sec, 60)
        while time.time() < deadline:
            try:
                m = _UUID_RE.search(self.page.url or "")
                cur_id = m.group(0) if m else cur_id
            except Exception:
                pass
            more = self._locate_song_more(cur_id)
            if more is not None:
                try:
                    if more.count() > 0 and more.is_visible():
                        break
                except Exception:
                    pass
            more = None
            self._wait(1500)
        if more is None:
            raise SunoUIChanged(
                "Không tìm thấy menu ⋯ trên trang bài để tải WAV.")
        confirm = self._open_download_dialog(cur_id, more)
        # KHÔNG dùng download manager (expect_download/save_as): Cốc Cốc crash
        # 0xC0000005 đúng lúc Playwright chặn download (lặp lại 100% trên Suno,
        # giống Flow). Thay vào đó bắt CHÍNH request WAV mà trang gửi đi, ghi
        # body ra đĩa rồi abort để trình duyệt không khởi động lượt tải nữa.
        self._capture_wav_click(
            confirm, dest, timeout_sec,
            reopen=lambda: self._open_download_dialog(cur_id, None),
            refresh=lambda: self._refresh_song_and_reopen(cur_id))
        self._log(f"Đã tải WAV: {dest.name}")
        return str(dest)

    def _refresh_song_and_reopen(self, cur_id):
        """Toast 'can't locate the asset': trang bài được mở khi Suno còn xử lý
        bản Studio mới → dữ liệu trên trang cũ. Bấm lại trên trang cũ vô ích —
        tải lại trang bài (vẫn là bản Studio), chờ hết Preparing, mở lại Download."""
        self._log("Tải lại trang bài Studio để Suno cập nhật file WAV, rồi mở Download lại.")
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        url = f"https://suno.com/song/{cur_id}" if cur_id else None
        try:
            if url:
                self.page.goto(url, wait_until="domcontentloaded")
            else:
                self.page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        self._wait(2500)
        self._wait_preparing_done(120)
        more = None
        for _ in range(20):
            more = self._locate_song_more(cur_id)
            try:
                if more is not None and more.count() > 0 and more.is_visible():
                    break
            except Exception:
                pass
            more = None
            self._wait(1500)
        return self._open_download_dialog(cur_id, more)

    def _open_download_dialog(self, cur_id, more):
        """⋯ của bài (bản Full Song do Studio export) → Download → chỉ WAV.
        Trả nút xác nhận 'Download' trong hộp thoại."""
        if more is None:
            more = self._locate_song_more(cur_id)
            if more is None:
                raise SunoUIChanged(
                    "Không tìm thấy menu ⋯ trên trang bài để tải WAV.")
        # Mục Download chỉ tìm TRONG [role=menu] (selector cuối '*:has-text' quá
        # lỏng); menu không mở đúng → dọn popup, mở lại (tối đa 3 lần).
        dl_sel = [f"[role=menu] {x}" for x in
                  self.sel.get("download_entry", [])[:2]]
        entry = None
        for i in range(3):
            if i:
                self._log("Menu ⋯ chưa có mục Download — mở lại.")
                more = self._locate_song_more(cur_id) or more
            self._dismiss_popups()
            if not self._real_click(more):
                more.click()
            self._wait(600)
            entry = query_first(self.page, dl_sel, timeout_ms=4000)
            if entry is not None:
                break
        if entry is None:
            entry = query_first(self.page, self.sel.get("download_entry", []),
                                timeout_ms=3000)
        if entry is None:
            raise SunoUIChanged("Không bấm được mục Download trong menu ⋯.")
        entry.click()
        self._wait(800)
        self._raise_if_out_of_downloads()

        # Hộp thoại Download = danh sách TOGGLE. Mặc định MP3 bật, WAV tắt.
        # Quy trình chuẩn (theo yêu cầu): BẬT WAV, TẮT MP3, rồi bấm 'Download'.
        # Nút toggle CHỈ nhận chuột thật → dùng _real_click, kiểm lại dấu tích.
        self._set_download_format_only_wav()

        confirm = query_first(self.page, self.sel.get("download_confirm", []),
                              timeout_ms=6000)
        if confirm is None:
            raise SunoUIChanged(
                "Không thấy nút 'Download' để xác nhận tải trong hộp thoại.")
        # Ghi lại đúng nút và trạng thái popup. Suno từng đổi nhãn nút cuối từ
        # Download sang Confirm; selector :has-text quá rộng cũng có thể chọn
        # nhầm một nút định dạng chứa chữ Download.
        try:
            label = (confirm.inner_text() or "").strip()
            disabled = confirm.is_disabled()
            self._log(f"Nút xác nhận tải: {label!r}, disabled={disabled}.")
            if disabled:
                raise SunoUIChanged(
                    f"Nút xác nhận tải {label!r} đang bị vô hiệu hoá sau khi chọn WAV.")
        except SunoUIChanged:
            raise
        except Exception:
            pass
        try:
            if self.debug_dir:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                shot = self.debug_dir / f"download_dialog_{int(time.time())}.png"
                self.page.screenshot(path=str(shot), full_page=True)
                self._log(f"Ảnh popup tải: {shot}")
        except Exception:
            pass
        return confirm

    # Bản Full Song vừa export từ Studio cần Suno sinh file WAV phía server.
    # Bấm Download quá sớm → toast "can't locate the asset". Khi đó CHỜ trên
    # chính bản Studio đó rồi bấm Download lại (không export lại — mỗi lần
    # export tạo bản mới lại phải chờ từ đầu; không bao giờ tải bài gốc).
    ASSET_RETRY_SEC = 20
    ASSET_WAIT_SEC = 240

    def _asset_missing_visible(self) -> bool:
        try:
            missing = self.page.locator(
                "text=/can't locate the asset|cannot locate the asset/i").first
            return bool(missing.count() and missing.is_visible())
        except Exception:
            return False

    def _capture_wav_click(self, button, dest: Path, timeout_sec: int,
                           reopen=None, refresh=None) -> None:
        context = self.page.context
        got: dict = {}
        diag_page = self.page
        diag: dict = {"n": 0, "seen": set()}

        def _on_diag_response(resp) -> None:
            # Chẩn đoán: chỉ ghi METHOD + đường dẫn (bỏ query) + mã lỗi của API
            # Suno trả lỗi trong lúc tải. KHÔNG ghi header/cookie/body/token.
            try:
                url = resp.url or ""
                if resp.status < 400 or "suno" not in url or diag["n"] >= 8:
                    return
                key = (resp.request.method, url.split("?")[0], resp.status)
                if key in diag["seen"]:
                    return
                diag["seen"].add(key)
                diag["n"] += 1
                self._log(f"[chẩn đoán] Suno trả lỗi: {key[0]} "
                          f"{key[1].split('://', 1)[-1][:120]} → {key[2]}")
            except Exception:
                pass

        def _is_wav(body: bytes) -> bool:
            return len(body) > 44 and body[:4] == b"RIFF" and body[8:12] == b"WAVE"

        def _url_is_wav(url: str) -> bool:
            return (url or "").lower().split("?")[0].endswith(".wav")

        def _handler(route) -> None:
            # CHỈ chặn request file .wav (S3 .../studio/uploads/<id>.wav). Trước
            # đây chặn "**/*" rồi route.fetch() lại MỌI xhr/fetch/document của
            # cả context → trình duyệt quá tải và thỉnh thoảng sập giữa lúc tải.
            # Chỉ bắt request TẢI VỀ. Tab Studio (giữ mở) có thể PUT file .wav
            # lên S3 cùng lúc — để nó đi thẳng, không chặn.
            try:
                method = (route.request.method or "GET").upper()
            except Exception:
                method = "GET"
            if got.get("done") or method != "GET":
                route.continue_()
                return
            try:
                resp = route.fetch(timeout=timeout_sec * 1000)
            except Exception:
                route.continue_()
                return
            if resp.status == 200:
                body = resp.body()
                if _is_wav(body):
                    dest.write_bytes(body)
                    got["done"] = True
                    got["url"] = route.request.url.split("?")[0]
                    route.abort()
                    return
            route.fulfill(response=resp)

        def _on_download(dl) -> None:
            # Lọt qua route (vd blob:) → huỷ ngay để trình duyệt không sập.
            got["download_event"] = True
            try:
                dl.cancel()
            except Exception:
                pass

        context.route(_url_is_wav, _handler)
        self.page.on("download", _on_download)
        try:
            diag_page.on("response", _on_diag_response)
        except Exception:
            pass
        try:
            try:
                initial_label = (button.inner_text() or "").strip()
            except Exception:
                initial_label = ""
            if not self._real_click(button):
                button.click(timeout=5000, force=True)
            # Suno mới dùng hai pha cho bài chưa mở khoá:
            #   Unlock & Download → (mở khoá / trừ 1 lượt) → Download.
            # Click đầu không phát request WAV nên phải chờ nút đổi nhãn rồi
            # click lần hai. Không bấm Buy packs hay bất kỳ giao dịch nào.
            if "unlock" in initial_label.lower():
                unlock_deadline = time.time() + 20
                unlock_retry_at = time.time() + 4
                while (time.time() < unlock_deadline and not got.get("done")
                       and not got.get("download_event")):
                    self._wait(700)
                    next_button = query_first(self.page, [
                        "[role=dialog] button:text-is('Download')",
                        "button:text-is('Download')",
                    ], timeout_ms=300)
                    if next_button is not None:
                        button = next_button
                        self._log("Suno đã mở khoá bài — bấm Download WAV lần hai.")
                        if not self._real_click(button):
                            button.click(timeout=5000, force=True)
                        break
                    if time.time() >= unlock_retry_at:
                        unlock_retry_at = time.time() + 4
                        self._log("Nút vẫn là Unlock & Download — click lại để mở khoá.")
                        try:
                            if not self._real_click(button):
                                button.click(timeout=3000, force=True)
                        except Exception:
                            pass
            # Xác nhận click thực sự được modal nhận. Một số bản Suno giữ modal
            # nguyên trạng dù mouse.click trả thành công; khi đó dùng bàn phím
            # trên chính nút đang focus (trusted key event) trước khi chờ lâu.
            self._wait(1200)
            try:
                still_open = button.is_visible()
            except Exception:
                still_open = False
            if still_open and not got.get("download_event") and not got.get("done"):
                self._log("Popup Download vẫn mở sau click — thử focus + Enter.")
                try:
                    button.focus()
                    self._wait(150)
                    self.page.keyboard.press("Enter")
                except Exception:
                    pass
                self._wait(1200)
            try:
                still_open = button.is_visible()
            except Exception:
                still_open = False
            if still_open and not got.get("download_event") and not got.get("done"):
                self._log("Popup Download vẫn mở sau Enter — thử Space.")
                try:
                    button.focus()
                    self.page.keyboard.press("Space")
                except Exception:
                    pass
                self._wait(1200)
            deadline = time.time() + max(timeout_sec, self.ASSET_WAIT_SEC)
            retry_at = time.time() + 5
            retried = False
            asset_deadline = None
            asset_retry_at = 0.0
            asset_tries = 0
            while time.time() < deadline and not got.get("done"):
                self._wait(500)
                # Toast đỏ "can't locate the asset": WAV của bản Studio chưa
                # sinh xong. Chờ rồi bấm Download lại trên CHÍNH bản đó (mở lại
                # hộp thoại nếu đã đóng). Hết hạn chờ mới báo để caller làm lại
                # Studio → Export (không bao giờ tải thẳng trang bài gốc).
                if self._asset_missing_visible() and asset_deadline is None:
                    asset_deadline = time.time() + self.ASSET_WAIT_SEC
                    asset_retry_at = time.time() + self.ASSET_RETRY_SEC
                    deadline = max(deadline, asset_deadline + 5)
                    retried = True      # không click cưỡng bức chồng lên
                    self._log("Suno chưa sinh xong WAV của bản Studio — chờ "
                              f"{self.ASSET_RETRY_SEC}s rồi bấm Download lại.")
                if asset_deadline is not None and time.time() >= asset_retry_at:
                    if time.time() >= asset_deadline:
                        raise SunoAssetMissing(
                            "Suno không tìm thấy asset WAV của bản Full Song trung gian "
                            f"(đã chờ {self.ASSET_WAIT_SEC}s, bấm lại {asset_tries} lần).")
                    asset_tries += 1
                    self._log(f"Bấm Download WAV lại (lần {asset_tries}) trên bản Studio.")
                    try:
                        visible = button.is_visible()
                    except Exception:
                        visible = False
                    if refresh is not None:
                        try:
                            button = refresh()
                        except SunoError:
                            raise
                        except Exception:
                            pass
                    elif not visible and reopen is not None:
                        try:
                            self.page.keyboard.press("Escape")
                            self._wait(400)
                            button = reopen()
                        except SunoError:
                            raise
                        except Exception:
                            pass
                    try:
                        if not self._real_click(button):
                            button.click(timeout=5000, force=True)
                    except Exception:
                        pass
                    # Tính hẹn lần sau từ SAU khi tải lại trang + bấm xong.
                    asset_retry_at = time.time() + self.ASSET_RETRY_SEC
                    continue
                # Menu nổi của Suno đôi lúc nhận mouse click nhưng không phát
                # sinh request/download. Nếu sau 8 giây chưa có bất kỳ tín hiệu
                # nào, click cưỡng bức/DOM đúng nút xác nhận một lần; thao tác
                # này chỉ tải file đã render, không liên quan credit/Create.
                if (not retried and time.time() >= retry_at
                        and not got.get("download_event")):
                    retried = True
                    self._log("Nút Download chưa phát sinh request — click cưỡng bức lại.")
                    try:
                        button.click(timeout=5000, force=True)
                    except Exception:
                        try:
                            button.dispatch_event("click")
                        except Exception:
                            pass
        except (SunoAssetMissing, SunoDownloadLimit):
            raise
        except Exception as e:
            # Cốc Cốc đôi lúc đóng page/context ngay SAU KHI route handler đã
            # ghi xong WAV. Khi đó wait_for_timeout ném TargetClosedError dù
            # file trên đĩa đã hoàn chỉnh. Không biến lượt tải thành thất bại.
            try:
                body = dest.read_bytes() if dest.is_file() else b""
            except OSError:
                body = b""
            if _is_wav(body):
                got["done"] = True
                got.setdefault("url", "file đã ghi xong trước khi browser đóng")
                self._log("Browser đã đóng sau khi WAV được ghi hoàn chỉnh — "
                          "giữ file và tiếp tục resume an toàn.")
            else:
                raise SunoError(f"Tải WAV thất bại: {type(e).__name__}: {e}") from e
        finally:
            try:
                self.page.remove_listener("download", _on_download)
            except Exception:
                pass
            try:
                diag_page.remove_listener("response", _on_diag_response)
            except Exception:
                pass
            try:
                context.unroute(_url_is_wav, _handler)
            except Exception:
                pass
        if not got.get("done"):
            extra = (" (trang tạo download dạng blob — không bắt được request)"
                     if got.get("download_event") else "")
            raise SunoError(f"Không bắt được file WAV sau {timeout_sec}s{extra}.")
        self._log(f"Bắt được WAV từ {got['url']}")

    def _format_checked(self, loc) -> bool:
        """Định dạng đang được chọn nếu bên trong <button> có dấu tích <svg>."""
        try:
            return loc.locator("svg").count() > 0
        except Exception:
            return False

    def _set_download_format_only_wav(self) -> None:
        """Bật WAV, tắt MP3 (và mọi định dạng khác đang bật) trong hộp thoại
        Download. Các nút là toggle, chỉ nhận CHUỘT THẬT; thử lại vài lần và
        xác nhận trạng thái dấu tích để chắc chắn."""
        # BẬT WAV (nếu chưa có dấu tích).
        for _ in range(4):
            wav = query_first(self.page, self.sel.get("download_format_wav", []),
                              timeout_ms=4000)
            if wav is None:
                raise SunoUIChanged(
                    "Không thấy định dạng WAV trong hộp thoại Download.")
            if self._format_checked(wav):
                break
            self._real_click(wav)
            self._wait(500)
        else:
            raise SunoUIChanged("Không bật được định dạng WAV (không có dấu tích).")

        # TẮT MP3 (nếu đang có dấu tích) — yêu cầu: chỉ tải WAV, bỏ tick MP3.
        for _ in range(4):
            mp3 = query_first(self.page, self.sel.get("download_format_mp3", []),
                              timeout_ms=3000)
            if mp3 is None or not self._format_checked(mp3):
                break
            self._real_click(mp3)
            self._wait(500)
        self._log("Đã chọn định dạng: chỉ WAV (đã bỏ MP3).")

    def close_extra_tabs(self) -> None:
        """Đóng mọi tab trừ self.page. Mỗi bài để lại 1 tab /studio + 1 tab
        'Go to Song' — tích 15 bài là ~30 tab Studio nặng, dễ làm trình duyệt sập."""
        try:
            pages = list(self.page.context.pages)
        except Exception:
            return
        for p in pages:
            if p is self.page:
                continue
            try:
                p.close()
            except Exception:
                pass

    # ── LIVE: tải WAV 1 bài qua Studio (chỉ khi dry_run=False) ────
    def download_asset_wav(self, song_id: str, dest_path: str | Path) -> dict:
        """Tải asset của đúng bài qua rights của player, không thu PCM realtime."""
        if self.dry_run:
            raise SunoError("[DRY-RUN] download_asset_wav bị chặn.")
        from backend.cover.suno_wav_downloader import download_authorized_song_as_wav
        self._log(f"Đang tải và giải mã liên tục asset {song_id[:8]} (không thu PCM)…")
        try:
            info = download_authorized_song_as_wav(self.page, song_id, Path(dest_path))
        except Exception as exc:
            # Bài 'failed to generate' (thường do Suno chặn lyrics sau khi Create)
            # vẫn có trang /song/<id> nhưng asset trả HTTP 404. Phân loại đúng
            # để caller không tải lại vô ích mà chuyển bài sang chờ xử lý tay.
            if self._song_generation_failed(song_id):
                if getattr(self, "_song_removed", False):
                    raise SunoGenerationFailed(
                        f"Bản Suno {song_id[:8]} đã bị Suno gỡ (trang bài báo 404 "
                        "«Page not found») — thường do Suno chặn lyrics/bản quyền "
                        "sau khi Create; không có audio để tải.") from exc
                raise SunoGenerationFailed(
                    f"Bản Suno {song_id[:8]} đã báo 'This song failed to generate' — "
                    f"không có audio để tải ({str(exc)[:120]}).") from exc
            raise
        self._log(f"Đã tạo WAV liền mạch {song_id[:8]}: "
                  f"{info.get('sample_rate')} Hz, {info.get('channels')} kênh, "
                  f"{float(info.get('duration_seconds') or 0):.1f}s.")
        return info

    _GENERATION_FAILED_SEL = (
        "text=This song failed to generate",
        "text=Song failed to generate",
        "text=Generation failed",
    )

    def _song_generation_failed(self, song_id: str) -> bool:
        """True nếu trang /song/<id> hiển thị 'failed to generate' HOẶC bài đã bị
        Suno gỡ (trang 404 «Page not found», đặt ``self._song_removed``).
        Chỉ đọc trang, không bấm gì, không tốn credit."""
        self._song_removed = False
        url = f"https://suno.com/song/{song_id}"
        try:
            if f"/song/{song_id.lower()}" not in (self.page.url or "").lower():
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._wait(1500)
            if query_first(self.page, list(self._GENERATION_FAILED_SEL),
                           timeout_ms=4000) is not None:
                return True
            if not self._song_page_missing():
                return False
            # Nạp lại một lần cho chắc: 404 thoáng qua thì không coi là bị gỡ.
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._wait(3000)
            if self._song_page_missing():
                self._song_removed = True
                self._log(f"Trang bài {song_id[:8]} báo 404 «Page not found» — "
                          "Suno đã gỡ bản này.")
                return True
            return query_first(self.page, list(self._GENERATION_FAILED_SEL),
                               timeout_ms=2000) is not None
        except OperationCancelled:
            raise
        except Exception:
            return False

    def _song_page_missing(self) -> bool:
        try:
            return self.page.evaluate(_JS_SONG_404) is True
        except Exception:
            return False

    def download_wav(self, song_id: str, dest_path: str | Path,
                     timeout_sec: int = 180) -> str:
        """Tải WAV chính thức của 1 bài về đúng dest_path theo quy trình Studio:
        ⋯ → Edit → Open in Studio → Single-track → (chờ load) → Export →
        Full Song → (chờ 'Song Saved') → Go to Song → (chờ hết 'Preparing song
        for playback...') → ⋯ → Download → WAV.

        KHÔNG tải thẳng trên trang bài gốc ('Unlock & Download' bị trừ lượt tải
        Suno). Studio lỗi → ném lỗi cho caller tự thử lại (vẫn qua Studio).

        Đăng ký download-wait TRƯỚC khi bấm để khỏi quét thư mục Downloads chung.
        Trả path thật. DRY-RUN → không tải, raise để tránh gọi nhầm."""
        if self.dry_run:
            raise SunoError("[DRY-RUN] download_wav bị chặn (không tải file).")
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # render_timeout dựa theo timeout_sec nhưng nới rộng cho bước render.
        render_timeout = max(timeout_sec, 300)
        self._open_song_in_studio(song_id, load_timeout_sec=render_timeout)
        try:
            self._studio_export_full_song(render_timeout_sec=render_timeout)
            return self._download_wav_current(dest, timeout_sec=timeout_sec)
        finally:
            # Tab Studio được giữ mở trong lúc tải (để Suno lưu xong file);
            # tải xong / lỗi mới đóng cho nhẹ trình duyệt.
            try:
                self.close_extra_tabs()
            except Exception:
                pass

    # Không bao giờ fallback sang MP3 và không Create lại trong nhánh tải.
