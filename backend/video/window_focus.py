"""
video/window_focus.py — Đưa CỬA SỔ trình duyệt của 1 trang Playwright lên trên cùng
(Windows), để người dùng thấy ngay và tự thao tác (vd tự tick captcha Suno).

page.bring_to_front() chỉ chuyển TAB trong trình duyệt — không kéo cửa sổ lên trên
các ứng dụng khác, không mở lại cửa sổ đang thu nhỏ. Ở đây:
  1. CDP Browser.setWindowBounds → bỏ thu nhỏ.
  2. Gắn tạm 1 mã vào document.title → tìm đúng HWND theo tiêu đề cửa sổ → trả
     tiêu đề cũ.
  3. Win32: khôi phục + AttachThreadInput vào luồng cửa sổ đang ở trước +
     SetForegroundWindow + nâng TOPMOST thoáng qua. Windows vẫn chặn (khoá
     foreground) → nháy nút trên thanh tác vụ (FlashWindowEx).
Chỉ thao tác CỬA SỔ — không bấm/gõ gì vào trang.
"""
from __future__ import annotations

import sys
import uuid
from typing import Optional

_MARK_JS = "(m) => { const old = document.title; document.title = m + ' ' + old; return old; }"
_UNMARK_JS = "([m, old]) => { if (document.title.startsWith(m)) document.title = old; }"


def _cdp_unminimize(page) -> None:
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception:
        return
    try:
        win = cdp.send("Browser.getWindowForTarget")
        if (win.get("bounds") or {}).get("windowState") in ("minimized", "fullscreen"):
            cdp.send("Browser.setWindowBounds",
                     {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
    except Exception:
        pass
    finally:
        try:
            cdp.detach()
        except Exception:
            pass


def _find_hwnd(mark: str) -> Optional[int]:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if mark in buf.value:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def _force_foreground(hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    SW_RESTORE, SW_SHOW = 9, 5
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x2, 0x1, 0x40
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW

    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me
                    and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    ok = user32.GetForegroundWindow() == hwnd
    if not ok:
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD)]
        FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC
        fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
        user32.FlashWindowEx(ctypes.byref(fi))
    return ok


def raise_page_window(page) -> str:
    """Đưa cửa sổ chứa `page` lên trên cùng. Trả "foreground" | "flashed" |
    "tab-only" (không tìm được cửa sổ / không phải Windows). Không bao giờ ném lỗi."""
    try:
        page.bring_to_front()
    except Exception:
        pass
    if sys.platform != "win32":
        return "tab-only"
    _cdp_unminimize(page)
    mark = f"[mm-{uuid.uuid4().hex[:8]}]"
    old = None
    try:
        old = page.evaluate(_MARK_JS, mark)
        hwnd = None
        for _ in range(10):                      # tiêu đề cửa sổ cập nhật trễ
            page.wait_for_timeout(150)
            hwnd = _find_hwnd(mark)
            if hwnd:
                break
    except Exception:
        hwnd = None
    finally:
        if old is not None:
            try:
                page.evaluate(_UNMARK_JS, [mark, old])
            except Exception:
                pass
    if not hwnd:
        return "tab-only"
    try:
        return "foreground" if _force_foreground(hwnd) else "flashed"
    except Exception:
        return "tab-only"


__all__ = ["raise_page_window"]
