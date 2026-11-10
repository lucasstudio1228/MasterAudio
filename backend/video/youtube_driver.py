"""
video/youtube_driver.py — Điều khiển YouTube Studio (qua trình duyệt GPMLogin đã
đăng nhập sẵn) để TẢI VIDEO LÊN và LƯU BẢN NHÁP.

Kết nối tới trình duyệt của profile GPMLogin bằng Playwright connect_over_cdp
(endpoint do gpm_client.start_profile trả về). KHÔNG tự đăng nhập Google — profile
phải đã đăng nhập sẵn kênh.

⚠️  AN TOÀN: driver này TUYỆT ĐỐI KHÔNG bấm Publish/Xuất bản/Next-to-public. Nó
chỉ điền metadata rồi ĐÓNG dialog → YouTube tự lưu thành Draft (bản nháp). Người
dùng tự xuất bản sau.

Selector rất dễ đổi + phụ thuộc ngôn ngữ tài khoản → gom trong
config.YOUTUBE_SELECTORS (EN + VN). Bước không tìm thấy selector KHÔNG-bắt-buộc
sẽ được ghi vào 'warnings' và bỏ qua; bước bắt buộc (đăng nhập, ô tiêu đề, upload)
thất bại thì raise.
"""
from __future__ import annotations

import sys
import re
import time
from pathlib import Path
from typing import Callable, Optional

# Import browser_base TRƯỚC để nó chèn .runtime_packages311 vào sys.path (nếu có)
# và để tái dùng helper selector.
from . import browser_base as _bb
from . import config


def _log_fn(log):
    def _l(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _l


def _type_into(page, loc, text: str) -> bool:
    """Xoá sạch rồi gõ text vào 1 ô (contenteditable hoặc input)."""
    try:
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        loc.type(text, delay=8)
        return True
    except Exception:
        try:
            loc.fill(text)
            return True
        except Exception:
            return False


def _set_file_cdp(page, selectors: list[str], file_path: str,
                  log=None) -> bool:
    """Nạp file vào input[type=file] bằng CDP (DOM.setFileInputFiles).

    Bắt buộc với video vài GB: khi Playwright nối qua connect_over_cdp, nó coi
    trình duyệt là 'không cùng máy' và từ chối file >50 MB
    ("Cannot transfer files larger than 50Mb to a browser not co-located with
    the server"). Trình duyệt GPMLogin chạy NGAY trên máy này nên truyền đường
    dẫn qua CDP là hợp lệ và không giới hạn dung lượng.
    """
    try:
        session = page.context.new_cdp_session(page)
    except Exception as exc:
        if log:
            log(f"Không mở được CDP session: {type(exc).__name__}: {exc}")
        return False
    try:
        root = session.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        for css in selectors:
            try:
                node_id = session.send(
                    "DOM.querySelector", {"nodeId": root, "selector": css}
                ).get("nodeId") or 0
                if not node_id:
                    continue
                session.send("DOM.setFileInputFiles",
                             {"files": [file_path], "nodeId": node_id})
                return True
            except Exception as exc:
                if log:
                    log(f"CDP set file {css}: {type(exc).__name__}: {exc}")
                continue
        return False
    finally:
        try:
            session.detach()
        except Exception:
            pass


_MFK_NO_NAMES = ("VIDEO_MADE_FOR_KIDS_NOT_MFK",)
_AI_YES_NAMES = ("VIDEO_HAS_ALTERED_CONTENT_YES", "VIDEO_ALTERED_CONTENT_YES")


def _status_text(page, sel, keys=("upload_progress", "checks_status")) -> str:
    """Gộp text trạng thái upload + quét chính sách (đã lower) để đọc tiến độ."""
    parts: list[str] = []
    for key in keys:
        for css in sel.get(key, []):
            try:
                # .first có thể là 1 bản ẩn/rỗng (vd hàng trong danh sách Nội
                # dung phía sau dialog) → xét vài phần tử khớp đầu tiên.
                group = page.locator(css)
                for i in range(min(group.count(), 4)):
                    txt = (group.nth(i).inner_text(timeout=1500) or "").strip()
                    # Nhiều selector cùng trỏ 1 chỗ → lọc trùng cho log dễ đọc.
                    if txt and txt not in parts:
                        parts.append(txt)
            except Exception:
                continue
    if not parts:
        # Studio đổi markup chân dialog → quét chữ trạng thái trong dialog upload
        # ("Đang kiểm tra 55% … Còn 5 phút", "Uploading 12%", "Checks complete").
        pats = list(config.YOUTUBE_UPLOAD_BUSY_PATTERNS
                    + config.YOUTUBE_CHECK_BUSY_PATTERNS
                    + config.YOUTUBE_PROCESS_BUSY_PATTERNS
                    + config.YOUTUBE_UPLOAD_DONE_PATTERNS
                    + config.YOUTUBE_CHECK_DONE_PATTERNS)
        try:
            txt = page.evaluate(_STATUS_SCAN_JS, pats) or ""
        except Exception:
            txt = ""
        if txt:
            parts.append(txt)
    return " | ".join(parts).lower()


_STATUS_SCAN_JS = r"""
(pats) => {
  const root = document.querySelector('ytcp-uploads-dialog') || document;
  const out = [];
  for (const el of root.querySelectorAll('span, div, p, yt-formatted-string')) {
    if (el.children.length || el.closest('[contenteditable]')) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 140) continue;
    const l = t.toLowerCase();
    if (/\d{1,3}\s*%/.test(l) || pats.some(p => l.includes(p))) {
      if (!out.includes(t)) out.push(t);
    }
  }
  return out.slice(0, 6).join(' | ');
}
"""


def _radio_checked(page, name: str) -> Optional[bool]:
    """aria-checked của radio Studio theo `name`; None nếu không có trên trang."""
    try:
        loc = page.locator(f"tp-yt-paper-radio-button[name='{name}']").first
        if loc.count() == 0:
            return None
        val = loc.get_attribute("aria-checked")
        if val is not None:
            return val == "true"
        return loc.get_attribute("checked") is not None
    except Exception:
        return None


def _ensure_radio(page, selectors: list[str], names: tuple[str, ...],
                  timeout_ms: int = 6_000, attempts: int = 3) -> Optional[bool]:
    """Bấm radio rồi ĐỌC LẠI aria-checked (không tin vào cú click).
    True = đã chọn đúng · False = bấm mà vẫn chưa chọn · None = không thấy radio."""
    found = False
    for _ in range(attempts):
        state = [_radio_checked(page, n) for n in names]
        if any(state):
            return True
        found = found or any(v is not None for v in state)
        if _bb.click_first(page, selectors, timeout_ms=timeout_ms):
            found = True
            page.wait_for_timeout(500)
    if any(_radio_checked(page, n) for n in names):
        return True
    return False if found else None


def _pick_dropdown(page, trigger_sel: list[str], option_sel: list[str],
                   field_sel: str) -> tuple[bool, str]:
    """Mở dropdown → chọn mục → đọc lại chữ trên ô để xác nhận.
    Chọn hỏng thì ĐÓNG danh sách (Esc), tránh lớp phủ che các ô phía sau."""
    if not _bb.click_first(page, trigger_sel, timeout_ms=6_000):
        return False, "không mở được dropdown"
    page.wait_for_timeout(600)
    opt = _bb.query_first(page, option_sel, timeout_ms=6_000)
    label = ""
    if opt is not None:
        try:
            label = (opt.inner_text(timeout=1500) or "").strip()
            opt.click()
        except Exception:
            opt = None
    if opt is None:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(400)
        return False, "không thấy mục cần chọn"
    page.wait_for_timeout(600)
    try:
        shown = (page.locator(field_sel).first.inner_text(timeout=2000) or "")
    except Exception:
        shown = ""
    if label and label not in shown:
        return False, f"đã bấm «{label}» nhưng ô vẫn hiện «{shown.strip()[:60]}»"
    return True, label


def _norm_channel(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().casefold()


def _check_channel(page, sel, expected: str, warnings: list[str]) -> None:
    """Tài khoản Google có nhiều kênh → Studio mở kênh dùng gần nhất. Sai kênh
    thì DỪNG trước khi tải lên (chưa có gì được upload)."""
    if not (expected or "").strip():
        return
    # Trên trang /video/<id> ô #entity-name là TÊN VIDEO, không phải kênh.
    for _ in range(20):
        if "/channel/" in (page.url or ""):
            break
        page.wait_for_timeout(500)
    if "/channel/" not in (page.url or ""):
        warnings.append("Không xác nhận được kênh trong Studio — hãy tự kiểm tra "
                        f"bản nháp nằm đúng kênh «{expected}».")
        return
    loc = _bb.query_first(page, sel.get("studio_channel_name", []),
                          timeout_ms=10_000)
    actual = ""
    if loc is not None:
        try:
            actual = (loc.inner_text(timeout=2000) or "").strip()
        except Exception:
            actual = ""
    if not actual:
        warnings.append("Không đọc được tên kênh trong Studio — hãy tự kiểm tra "
                        f"bản nháp nằm đúng kênh «{expected}».")
        return
    a, e = _norm_channel(actual), _norm_channel(expected)
    if a != e and e not in a and a not in e:
        raise RuntimeError(
            f"YouTube Studio của profile này đang mở kênh «{actual}», nhưng "
            f"project cần kênh «{expected}». Chưa tải gì lên. Hãy mở profile "
            "GPMLogin, chuyển sang đúng kênh trong YouTube Studio (ảnh đại diện "
            "→ Chuyển đổi tài khoản), rồi chạy lại bước đăng nháp.")


def _has_issue(text: str) -> bool:
    """True khi Studio báo CÓ vấn đề bản quyền/chính sách.
    'No issues found' cũng chứa 'issues found' → phải loại trừ trước."""
    clean = text
    for ok in ("no issues found", "no issues", "không phát hiện vấn đề",
               "không có vấn đề", "no copyright issues"):
        clean = clean.replace(ok, " ")
    return any(p in clean for p in config.YOUTUBE_UPLOAD_ISSUE_PATTERNS)


def _wait_upload_complete(page, sel, _p, warnings: list[str],
                          timeout_s: float = 2700.0,
                          processing_grace_s: float = 1800.0) -> str:
    """Chờ TỚI KHI upload xong VÀ Studio quét bản quyền/chính sách xong.

    Đọc text trạng thái thay vì ngủ cố định, và phân biệt 3 pha:
      • 'uploading …%'      → đang tải lên, bắt buộc chờ;
      • 'checking …%'       → đang quét bản quyền/chính sách, bắt buộc chờ;
      • 'processing …'      → transcode; video thiền 2-3 tiếng xử lý rất lâu và
        KHÔNG ảnh hưởng bản nháp, nên chỉ chờ thêm tối đa `processing_grace_s`
        rồi đi tiếp kèm cảnh báo.
    Cần 3 lần đọc liên tiếp đều 'rảnh' mới chốt, tránh khoảng trống giữa 2 pha.

    Hết thời gian chờ ⇒ ghi cảnh báo và đi tiếp (video vẫn được lưu nháp), KHÔNG
    raise — tránh bỏ phí cả lượt upload đã tốn hàng chục phút.
    """
    deadline = time.time() + timeout_s
    idle_streak = 0
    last_logged = ""
    last_seen = ""
    saw_upload_busy = saw_upload_done = False
    saw_check_busy = saw_check_done = False
    proc_only_since: float | None = None

    while time.time() < deadline:
        text = _status_text(page, sel)
        if text:
            last_seen = text
        if text and text != last_logged:
            last_logged = text
            remain = int(deadline - time.time())
            # 85%: chỉ là nhịp hiển thị, tiến độ thật nằm ở text của Studio.
            _p(f"Upload/kiểm tra: {text[:160]} (còn tối đa {remain // 60} phút)",
               85.0)

        up_busy = any(p in text for p in config.YOUTUBE_UPLOAD_BUSY_PATTERNS)
        chk_busy = any(p in text for p in config.YOUTUBE_CHECK_BUSY_PATTERNS)
        proc_busy = any(p in text for p in config.YOUTUBE_PROCESS_BUSY_PATTERNS)
        saw_upload_busy |= up_busy
        saw_check_busy |= chk_busy
        if any(p in text for p in config.YOUTUBE_UPLOAD_DONE_PATTERNS) or (
                saw_upload_busy and not up_busy):
            saw_upload_done = True
        if any(p in text for p in config.YOUTUBE_CHECK_DONE_PATTERNS) or (
                saw_check_busy and not chk_busy):
            saw_check_done = True

        if up_busy or chk_busy:
            idle_streak = 0
            proc_only_since = None
        else:
            idle_streak += 1
            if idle_streak >= 3 and saw_upload_done and saw_check_done:
                break
            # Chỉ còn 'processing' (transcode) → chờ có hạn rồi đi tiếp.
            if proc_busy and saw_upload_done:
                if proc_only_since is None:
                    proc_only_since = time.time()
                elif time.time() - proc_only_since >= processing_grace_s:
                    if not saw_check_done:
                        warnings.append(
                            "YouTube vẫn đang XỬ LÝ video sau "
                            f"{int(processing_grace_s / 60)} phút và chưa báo "
                            "kết quả quét bản quyền. Bản nháp đã lưu đủ dữ liệu "
                            "— hãy xem tab 'Kiểm tra' trong Studio trước khi "
                            "xuất bản.")
                    break
            # Không đọc được text nào (selector đổi) → chốt sau vài vòng.
            if idle_streak >= 10 and not text:
                warnings.append(
                    "Không đọc được trạng thái upload (selector Studio có thể đã "
                    "đổi) — đã chờ đủ lâu rồi mới lưu nháp.")
                break
        page.wait_for_timeout(5000)
    else:
        warnings.append(
            f"Quá {int(timeout_s / 60)} phút mà YouTube chưa báo upload/kiểm tra "
            f"xong (trạng thái cuối: {last_seen[:160] or 'không đọc được'}). "
            "Video vẫn được lưu nháp — hãy kiểm tra lại trong Studio.")
        return last_seen

    if _has_issue(last_seen):
        warnings.append(
            f"YouTube báo có vấn đề bản quyền/chính sách: {last_seen[:200]}. "
            "Bản nháp vẫn được lưu — hãy xem tab 'Kiểm tra' trước khi xuất bản.")
    return last_seen


def _set_thumbnail(page, sel, thumbnail_path: str, _p,
                   warnings: list[str]) -> bool:
    """Bấm 'Chọn hình thu nhỏ / Upload file' rồi nạp ảnh thumbnail.

    Ưu tiên set thẳng vào input[type=file] RIÊNG của khung thumbnail (không chạm
    input video). Nếu input chỉ được dựng sau khi bấm nút thì bấm nút trong
    expect_file_chooser để không bật hộp thoại OS.
    """
    _p("Chọn hình thu nhỏ (thumbnail)…", 52.0)

    # Cách 1: input ảnh có sẵn trong DOM (CDP trước, Playwright dự phòng).
    if (_set_file_cdp(page, sel["thumbnail_input"], thumbnail_path)
            or _bb.upload_first(page, sel["thumbnail_input"], thumbnail_path,
                                timeout_ms=8_000)):
        page.wait_for_timeout(1500)
        if _bb.query_first(page, sel["thumbnail_selected"], timeout_ms=8_000):
            _p("Đã nạp hình thu nhỏ.", 56.0)
            return True

    # Cách 2: bấm nút 'Upload file / Tải tệp lên' và bắt file chooser.
    try:
        with page.expect_file_chooser(timeout=10_000) as fc:
            if not _bb.click_first(page, sel["thumbnail_button"],
                                   timeout_ms=8_000):
                raise RuntimeError("no thumbnail button")
        fc.value.set_files(thumbnail_path)
        page.wait_for_timeout(1500)
        if _bb.query_first(page, sel["thumbnail_selected"], timeout_ms=8_000):
            _p("Đã nạp hình thu nhỏ.", 56.0)
            return True
        warnings.append("Đã gửi file thumbnail nhưng không thấy ảnh xem trước.")
        return False
    except Exception as exc:
        warnings.append(
            f"Không đặt được hình thu nhỏ ({type(exc).__name__}) — bản nháp vẫn "
            "lưu, hãy chọn thumbnail thủ công trong Studio.")
        return False


def _set_thumbnail_ab(page, thumbnail_paths: list[str], _p,
                      warnings: list[str]) -> bool:
    """Nạp đúng 3 ảnh vào YouTube Studio «Test & compare».

    Đây là chế độ bắt buộc: nếu kênh chưa có Advanced features hoặc giao diện
    không hiện công cụ A/B thì dừng trước khi lưu nháp, không lặng lẽ dùng một
    thumbnail duy nhất.
    """
    paths = [str(Path(p).resolve()) for p in thumbnail_paths if Path(p).exists()]
    if len(paths) != 3:
        raise RuntimeError("YouTube A/B cần đúng 3 file thumbnail hợp lệ.")
    _p("Mở YouTube Test & compare cho 3 thumbnail…", 52.0)
    labels = [
        "button:has-text('Test & compare')",
        "ytcp-button:has-text('Test & compare')",
        "button:has-text('Thử nghiệm và so sánh')",
        "ytcp-button:has-text('Thử nghiệm và so sánh')",
        "button:has-text('Kiểm thử và so sánh')",
    ]
    button = _bb.query_first(page, labels, timeout_ms=12_000)
    if button is None:
        raise RuntimeError(
            "Kênh không hiện «Test & compare» thumbnail. Hãy bật Advanced features "
            "trong YouTube Studio; tool đã dừng để không đăng chỉ 1 ảnh.")
    try:
        button.click()
        page.wait_for_timeout(1200)
        dialog = page.locator("tp-yt-paper-dialog:visible, [role='dialog']:visible").last
        inputs = dialog.locator("input[type='file'][accept*='image']")
        count = inputs.count()
        if count == 1:
            inputs.first.set_input_files(paths)
        elif count >= 3:
            for i, path in enumerate(paths):
                inputs.nth(i).set_input_files(path)
        else:
            # Một số phiên bản chỉ dựng input sau khi bấm từng ô Upload.
            upload_buttons = dialog.get_by_text(re.compile(
                r"upload|tải (tệp|ảnh)|chọn (tệp|ảnh)", re.I))
            if upload_buttons.count() < 3:
                raise RuntimeError("Không thấy 3 ô tải ảnh trong hộp Test & compare.")
            for i, path in enumerate(paths):
                with page.expect_file_chooser(timeout=8_000) as fc:
                    upload_buttons.nth(i).click()
                fc.value.set_files(path)
        page.wait_for_timeout(1800)
        done = dialog.get_by_role("button", name=re.compile(
            r"^(done|save|xong|lưu|bắt đầu thử nghiệm)$", re.I))
        if done.count() == 0:
            done = dialog.locator("button:has-text('Done'), button:has-text('Xong'), "
                                  "button:has-text('Save'), button:has-text('Lưu')")
        if done.count() == 0:
            raise RuntimeError("Đã nạp ảnh nhưng không thấy nút xác nhận Test & compare.")
        done.last.click()
        page.wait_for_timeout(1500)
        _p("Đã thiết lập 3 thumbnail A/B trên YouTube.", 58.0)
        return True
    except Exception as exc:
        raise RuntimeError(f"Không thiết lập được YouTube thumbnail A/B: {exc}") from exc


class CDPConnectError(RuntimeError):
    """Không nối được CDP của profile GPMLogin (trình duyệt chết ngay sau khi
    bật / cổng debug không mở). Chưa đụng gì tới YouTube → an toàn để service
    đóng profile, bật lại (cổng MỚI) rồi thử lại."""


class _CDPPlaywright:
    """Context manager: mở Playwright + connect_over_cdp, dọn dẹp đúng cách.
    Đặt WindowsProactorEventLoopPolicy giống browser_base (cần cho worker thread)."""

    def __init__(self, cdp_endpoint: str, connect_sec: float = 60.0):
        self.cdp_endpoint = cdp_endpoint
        self.connect_sec = connect_sec
        self._pw = None
        self.browser = None
        self._old_policy = None

    def __enter__(self):
        import asyncio as _asyncio
        if sys.platform == "win32":
            try:
                self._old_policy = _asyncio.get_event_loop_policy()
                _asyncio.set_event_loop_policy(
                    _asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                self._old_policy = None
        from .browser_base import start_playwright
        try:
            self._pw = start_playwright()
            # GPMLogin trả cổng debug NGAY khi bật profile, nhưng Chrome cần vài giây
            # mới mở cổng → thử lại tối đa 60s thay vì chết ngay vì ECONNREFUSED.
            deadline = time.time() + self.connect_sec
            last_exc = None
            while True:
                try:
                    self.browser = self._pw.chromium.connect_over_cdp(
                        self.cdp_endpoint, timeout=30_000)
                    return self
                except Exception as exc:
                    last_exc = exc
                    if time.time() >= deadline:
                        break
                    time.sleep(3)
            raise CDPConnectError(
                f"Không kết nối được trình duyệt GPMLogin ({self.cdp_endpoint}) sau "
                f"{int(self.connect_sec)} giây: {last_exc}")
        except BaseException:
            self.__exit__(None, None, None)     # __exit__ không chạy khi __enter__ raise
            raise

    def _restore_policy(self):
        try:
            import asyncio as _asyncio
            if self._old_policy is not None:
                _asyncio.set_event_loop_policy(self._old_policy)
                self._old_policy = None
        except Exception:
            pass

    def __exit__(self, *exc):
        # KHÔNG close browser (đó là trình duyệt của profile — để gpm_client
        # close_profile lo). Chỉ dừng playwright + khôi phục event loop policy.
        # Không ném lỗi: kết nối CDP đã chết thì stop() có thể lỗi — vẫn phải
        # nhả luồng (xem browser_base.start_playwright).
        pw, self._pw = self._pw, None
        try:
            if pw:
                pw.stop()
        except Exception as e:      # noqa: BLE001
            print(f"  ⚠️ Dừng Playwright (CDP) lỗi: {e}")
        finally:
            self._restore_policy()


def _read_video_url(page) -> str:
    """Link video trong hộp thoại tải lên (youtu.be/… hoặc /shorts/…). Không raise."""
    try:
        url = page.evaluate(r"""() => {
          const a = [...document.querySelectorAll('ytcp-uploads-dialog a[href]')]
            .map(x => x.href).find(h => /youtu\.be\/|youtube\.com\/(shorts|watch)/.test(h));
          return a || '';
        }""")
        return str(url or "").strip()
    except Exception:
        return ""


def _dialog_text(page) -> str:
    """Chữ đang hiện trong hộp thoại tải lên (để báo đúng lý do YouTube từ chối)."""
    try:
        return " ".join(str(page.evaluate("""() => [...document.querySelectorAll(
            'ytcp-uploads-dialog, tp-yt-paper-dialog, [role=dialog]')]
            .filter(d => d.offsetParent !== null).map(d => d.innerText).join(' | ')""")
        ).split())[:400]
    except Exception:
        return ""


def _no_details_message(page) -> str:
    """2026-10-07: kênh Zen Meditation chưa BẬT «Intermediate features» (chỉ
    «Eligible») → YouTube không nhận video >15 phút, form chi tiết không mở."""
    txt = _dialog_text(page)
    msg = ("Đã chọn file nhưng YouTube không mở form chi tiết (ô Tiêu đề). Nguyên "
           "nhân hay gặp: kênh CHƯA bật tính năng trung cấp — video >15 phút & "
           "thumbnail tự chọn. Mở Studio của kênh → Settings → Channel → Feature "
           "eligibility → «Intermediate features» phải là Enabled (bạn tự xác minh "
           "số điện thoại), rồi bấm «Đăng nháp YouTube» lại.")
    return msg + (f" YouTube hiển thị: «{txt}»" if txt else "")


def upload_draft(
    cdp_endpoint: str,
    video_path: str,
    title: str,
    description: str,
    hashtags: str,
    language: str = "en-US",
    thumbnail_path: Optional[str] = None,
    thumbnail_paths: Optional[list[str]] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    log=None,
    expected_channel: str = "",
) -> dict:
    """Tải video lên YouTube Studio của profile đang mở & LƯU BẢN NHÁP.

    `thumbnail_path` (nếu có file) sẽ được nạp vào mục Hình thu nhỏ. Hàm chỉ trả
    về SAU KHI YouTube báo upload xong và quét bản quyền/chính sách xong.
    Trả {'status':'draft','warnings':[...],'upload_status':str}. Raise nếu lỗi
    bắt buộc."""
    _log = _log_fn(log)

    def _p(msg: str, pct: float):
        _log(msg)
        if progress_cb:
            try:
                progress_cb(msg, pct)
            except Exception:
                pass

    sel = config.get_selectors("youtube")
    warnings: list[str] = []

    with _CDPPlaywright(cdp_endpoint) as cdp:
        browser = cdp.browser
        # Dùng context sẵn có của profile (đã đăng nhập), tạo tab mới.
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(config.BROWSER.action_timeout_ms)
        context.set_default_navigation_timeout(config.BROWSER.nav_timeout_ms)
        page = context.new_page()

        _p("Mở YouTube Studio…", 5.0)
        page.goto(config.YOUTUBE_STUDIO_URL, wait_until="domcontentloaded")

        # 1) Kiểm tra đã đăng nhập (nút Create hiện ra).
        if not _bb.query_first(page, sel["studio_ready"], timeout_ms=30_000):
            raise RuntimeError(
                "Chưa vào được YouTube Studio (profile có thể CHƯA đăng nhập "
                "kênh, hoặc giao diện đổi). Hãy mở profile này trong GPMLogin và "
                "đăng nhập YouTube 1 lần, rồi thử lại.")
        _check_channel(page, sel, expected_channel, warnings)

        # 2) Create → Upload videos
        _p("Bấm Tạo → Tải video lên…", 12.0)
        if not _bb.click_first(page, sel["create_button"], timeout_ms=15_000):
            raise RuntimeError("Không thấy nút 'Tạo' (Create) trong Studio.")
        page.wait_for_timeout(800)
        _bb.click_first(page, sel["upload_menu_item"], timeout_ms=8_000)

        # 3) Set file video vào input[type=file]
        _p("Chọn file video để upload…", 18.0)
        detail: list[str] = []
        page.wait_for_timeout(2500)   # chờ dialog 'Upload videos' dựng xong
        if not _set_file_cdp(page, sel["file_input"], str(Path(video_path).resolve()),
                             log=detail.append):
            # Dự phòng: đường Playwright (chỉ chạy được với file < 50 MB).
            if not _bb.upload_first(page, sel["file_input"], video_path,
                                    timeout_ms=20_000, log=detail.append):
                raise RuntimeError(
                    "Không set được file video trong dialog Studio. "
                    + (detail[0] if detail else
                       "Không thấy ô chọn file (giao diện Studio có thể đã đổi)."))

        # 4) Chờ dialog Details (ô tiêu đề) xuất hiện
        _p("Chờ hộp thoại chi tiết…", 28.0)
        title_loc = _bb.query_first(page, sel["title_box"], timeout_ms=60_000)
        if not title_loc:
            raise RuntimeError(_no_details_message(page))

        # 5) Tiêu đề (xoá tên file mặc định trước)
        _p("Điền tiêu đề…", 40.0)
        if not _type_into(page, title_loc, title):
            warnings.append("Không điền được tiêu đề.")

        # 6) Mô tả (đã chứa hashtag ở cuối do youtube_meta ghép sẵn)
        desc_loc = _bb.query_first(page, sel["description_box"], timeout_ms=8_000)
        if desc_loc:
            _p("Điền mô tả…", 48.0)
            if not _type_into(page, desc_loc, description):
                warnings.append("Không điền được mô tả.")
        else:
            warnings.append("Không thấy ô mô tả.")

        # 6b) Hình thu nhỏ — phải BẤM chọn hình thu nhỏ khi có file thumbnail.
        if thumbnail_paths:
            _set_thumbnail_ab(page, thumbnail_paths, _p, warnings)
        elif thumbnail_path and Path(thumbnail_path).exists():
            _set_thumbnail(page, sel, str(Path(thumbnail_path).resolve()),
                           _p, warnings)
        elif thumbnail_path:
            warnings.append(f"Không thấy file thumbnail: {thumbnail_path}")

        # 7) Hiện thêm (Show more) để mở cấu hình mở rộng
        _p("Mở 'Hiện thêm'…", 55.0)
        if not _bb.click_first(page, sel["show_more"], timeout_ms=8_000):
            warnings.append("Không bấm được 'Hiện thêm' (một số mục có thể bỏ qua).")
        page.wait_for_timeout(600)

        # 8) Thẻ (tags) từ hashtags — bỏ dấu '#'
        tags_loc = _bb.query_first(page, sel["tags_box"], timeout_ms=5_000)
        if tags_loc and hashtags:
            tag_text = ", ".join(
                t.lstrip("#") for t in hashtags.split() if t.strip())
            try:
                tags_loc.click()
                tags_loc.type(tag_text[:480], delay=5)
            except Exception:
                warnings.append("Không điền được thẻ (tags).")

        # 9) Đối tượng: KHÔNG dành cho trẻ em (đọc lại aria-checked).
        _p("Chọn 'Không dành cho trẻ em'…", 62.0)
        if not _ensure_radio(page, sel["mfk_no"], _MFK_NO_NAMES):
            warnings.append("Không đặt được 'Không dành cho trẻ em' (BẮT BUỘC — "
                            "hãy kiểm tra thủ công trước khi xuất bản).")

        # 10) «Sử dụng AI» / Altered content = Có. Chỉ dùng radio TRONG mục
        # #altered-content (radio "Có" đầu trang là «dành cho trẻ em»).
        _p("Khai báo có sử dụng AI…", 68.0)
        sec = _bb.query_first(page, sel["altered_content_section"],
                              timeout_ms=3_000)
        if sec is not None:
            try:
                sec.scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
        if not _ensure_radio(page, sel["altered_content_yes"], _AI_YES_NAMES):
            warnings.append("Không đặt được 'Sử dụng AI = Có' — kiểm tra thủ công.")

        # 11) 'Không có sản phẩm được quảng cáo' = để mặc định (KHÔNG tick) → OK.

        # 12) Ngôn ngữ video (mã kênh, mặc định en-US) — chọn theo test-id.
        lang = (language or "en-US").strip()
        _p(f"Đặt ngôn ngữ video ({lang})…", 74.0)
        lang_opts = [c.replace("{lang}", lang)
                     for c in sel.get("video_language_option", [])]
        if lang == "en-US":
            lang_opts += sel["video_language_en_us"]
        ok, info = _pick_dropdown(page, sel["video_language_dropdown"], lang_opts,
                                  "ytcp-form-language-input#language-input")
        if not ok:
            warnings.append(f"Không chọn được ngôn ngữ video {lang} ({info}).")

        # 12b) Danh mục = Âm nhạc (Music). Kênh nhạc để danh mục mặc định
        # ("People & Blogs") sẽ bị đề xuất/kiếm tiền lệch tệp.
        _p("Đặt danh mục Âm nhạc…", 78.0)
        ok, info = _pick_dropdown(page, sel["category_dropdown"],
                                  sel["category_music"], "ytcp-form-select#category")
        if not ok:
            warnings.append(f"Không chọn được danh mục 'Âm nhạc' ({info}).")
        page.wait_for_timeout(400)

        # 12c) KIỂM TRA LẠI 2 lựa chọn bắt buộc sau mọi thao tác phía trên.
        if _radio_checked(page, "VIDEO_MADE_FOR_KIDS_NOT_MFK") is False:
            if not _ensure_radio(page, sel["mfk_no"], _MFK_NO_NAMES):
                warnings.append("Kiểm tra cuối: video vẫn KHÔNG ở trạng thái "
                                "'Không dành cho trẻ em' — sửa tay trong Studio.")
        if not any(_radio_checked(page, n) for n in _AI_YES_NAMES):
            if not _ensure_radio(page, sel["altered_content_yes"], _AI_YES_NAMES):
                warnings.append("Kiểm tra cuối: 'Sử dụng AI' chưa ở 'Có' — sửa "
                                "tay trong Studio.")
        _p("Đã xác nhận: Không dành cho trẻ em · Sử dụng AI = Có.", 80.0)

        # 13) CHỜ upload xong + quét bản quyền/chính sách xong mới đi tiếp.
        # Video thiền dài → file vài GB; hạn chờ co giãn theo dung lượng
        # (≈4 giây cho mỗi MB, tối thiểu 45 phút, tối đa 6 giờ).
        try:
            size_mb = Path(video_path).stat().st_size / 1_000_000
        except OSError:
            size_mb = 0.0
        wait_s = min(max(2700.0, size_mb * 4.0), 21_600.0)
        _p(f"Chờ YouTube tải lên & kiểm tra chính sách "
           f"({size_mb:,.0f} MB, tối đa {wait_s / 3600:.1f} giờ)…", 82.0)
        upload_status = _wait_upload_complete(page, sel, _p, warnings,
                                              timeout_s=wait_s)

        # 13b) Link video (để Short dẫn về bản đầy đủ) — chỉ ĐỌC.
        video_url = _read_video_url(page)

        # 14) ĐÓNG dialog → YouTube TỰ LƯU DRAFT. Không bao giờ bấm Next→Publish.
        _p("Đóng hộp thoại để lưu bản nháp…", 90.0)
        closed = _bb.click_first(page, sel["close_dialog"], timeout_ms=10_000)
        if not closed:
            warnings.append("Không thấy nút Đóng — video có thể vẫn được lưu nháp "
                            "tự động. Hãy kiểm tra tab 'Nội dung' trong Studio.")
        page.wait_for_timeout(1500)
        # Có thông báo 'saved as draft' thì tốt; không có thì Studio cũng đã
        # tự lưu. Chỉ NHẬN BIẾT, không bấm.
        _bb.query_first(page, sel["saved_draft_confirm"], timeout_ms=2_000)

        _p("Đã lưu bản nháp trên YouTube.", 98.0)
        try:
            page.close()
        except Exception:
            pass

    return {"status": "draft", "warnings": warnings,
            "upload_status": upload_status, "video_url": video_url}
