"""Phiên trình duyệt qua profile GPMLogin (connect_over_cdp) — dùng chung cho
các máy tạo video chọn ở Cài đặt (Flow; Muse có _GPMSession riêng trong
muse_video). Cùng giao diện với BrowserSession: `.context`, `.new_page()`.

Profile cho từng site lưu trong data/video_overrides.json:
    "<site>_gpm_profile_id": "<UUID profile GPMLogin>"
(vd flow_gpm_profile_id). Để trống → site đó dùng trình duyệt riêng của tool
như trước (BrowserSession)."""
from __future__ import annotations

import time

from . import config
from .browser_base import BROWSER, BrowserSession


def gpm_profile_id(site: str) -> str:
    """UUID profile GPM đã chọn ở Cài đặt cho site ('' = chưa chọn)."""
    return str(config.load_overrides().get(f"{site}_gpm_profile_id") or "").strip()


class GPMBrowserSession:
    """Bật profile GPM → nối CDP (thử lại khi trình duyệt chết ngay sau khi bật).
    Thoát: dừng Playwright + đóng profile."""

    def __init__(self, pid: str, label: str = "", attempts: int = 3,
                 pause_sec: float = 15.0):
        self.pid, self.label = pid, label or "GPM"
        self.attempts, self.pause_sec = attempts, pause_sec
        self.client = None
        self._cdp = None
        self.context = None

    def __enter__(self) -> "GPMBrowserSession":
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
                print(f"  ⚠️ {self.label}: không nối được CDP profile GPM "
                      f"(lần {i}/{self.attempts}) — đóng/bật lại", flush=True)
                self.client.close_profile(self.pid)
                time.sleep(self.pause_sec)
            except BaseException:
                self.client.close_profile(self.pid)
                raise
        else:
            raise RuntimeError(
                f"{self.label}: không mở được profile GPM {self.pid} ({last}). Nếu cửa sổ "
                "profile này đang mở TAY từ GPMLogin, hãy đóng nó rồi chạy lại.")
        try:
            br = self._cdp.browser
            self.context = br.contexts[0] if br.contexts else br.new_context()
            self.context.set_default_timeout(BROWSER.action_timeout_ms)
            self.context.set_default_navigation_timeout(BROWSER.nav_timeout_ms)
        except BaseException:
            # __exit__ KHÔNG chạy khi __enter__ raise → tự dọn.
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        cdp, self._cdp, self.context = self._cdp, None, None
        try:
            if cdp is not None:
                cdp.__exit__(None, None, None)
        finally:
            try:
                self.client.close_profile(self.pid)
            except Exception:
                pass
        return False

    def new_page(self):
        page = self.context.new_page()
        page.bring_to_front()
        return page


def site_session(site: str, label: str = "", fallback=None):
    """Phiên trình duyệt cho site: profile GPM nếu đã chọn ở Cài đặt, ngược lại
    trình duyệt riêng của tool (hành vi cũ). fallback = lớp phiên dự phòng
    (mặc định BrowserSession; driver truyền tên của chính nó để test thay được)."""
    pid = gpm_profile_id(site)
    if pid:
        return GPMBrowserSession(pid, label=label or site)
    return (fallback or BrowserSession)(site=site)
