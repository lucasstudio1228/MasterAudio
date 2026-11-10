"""Mở cửa sổ Muse/Flow/Gemini/ChatGPT bằng ĐÚNG profile tool dùng — để người dùng theo
dõi, tự đăng nhập, kiểm tra tài khoản. Chạy như 1 job video (worker duy nhất) nên
không bao giờ tranh profile với tác vụ ảnh/clip. KHÔNG tự đăng nhập, không
nhập mật khẩu, không thao tác gì trên trang."""
import time
from typing import Callable

from . import config
from .browser_base import BrowserSession

SITES = {
    "muse":    ("Muse.ai", "https://muse.ai/"),
    "flow":    ("Flow",    config.FLOW_URL),
    "gemini":  ("Gemini",  config.GEMINI_URL),
    "chatgpt": ("ChatGPT", config.CHATGPT_URL),
}

_CHATGPT_HINT = {
    "logged_in":  "Đã đăng nhập ChatGPT ✓ — tool sẽ dùng tài khoản này vẽ thumbnail. "
                  "Bấm 'Đóng trình duyệt' khi xong.",
    "logged_out": "Chưa đăng nhập ChatGPT — hãy TỰ bấm «Log in» và đăng nhập trong cửa "
                  "sổ vừa mở (tool KHÔNG nhập email/mật khẩu).",
    "login_flow": "Đang ở trang đăng nhập OpenAI — hãy tự hoàn tất (tool không nhập gì).",
    "challenge":  "ChatGPT yêu cầu xác minh (Cloudflare) — hãy tự xác minh; tool KHÔNG "
                  "bấm vào ô xác minh.",
}


_RECORD_EVERY = 30.0   # giây — ghi lại trạng thái đăng nhập khi đổi, hoặc định kỳ


def _chatgpt_state(page, sels: dict, ready_since: list,
                   recorded: list | None = None) -> str:
    """page_state + coi «ready» ≥ 6s (có ô nhập, không có nút Log in) là đã đăng nhập.
    `recorded` = [trạng thái đã ghi, lúc ghi] — chỉ ghi file khi đổi hoặc sau 30s."""
    from .chatgpt_thumbnail import page_state, record_login
    st = page_state(page, sels)
    now = time.time()
    if st == "ready":
        ready_since[0] = ready_since[0] or now
        if now - ready_since[0] >= 6:
            st = "logged_in"
    else:
        ready_since[0] = None
    if st in ("logged_in", "logged_out"):
        rec = recorded if recorded is not None else [None, 0.0]
        if rec[0] != st or now - rec[1] >= _RECORD_EVERY:
            record_login(st == "logged_in")
            rec[0], rec[1] = st, now
    return st


def run_monitor_browser(project_id: int, progress_cb: Callable[[str, float], None],
                        site: str = "flow", hold_seconds: int = 1800) -> dict:
    """Giữ cửa sổ mở tới khi bấm 'Đóng trình duyệt' (huỷ hợp tác), người dùng
    tự đóng cửa sổ, hoặc hết hold_seconds."""
    from .job_manager import JobCancelled

    label, url = SITES[site]
    if site == "muse":
        return _run_muse_monitor(progress_cb, hold_seconds)
    sels = config.get_selectors("chatgpt") if site == "chatgpt" else {}
    ready_since: list = [None]
    recorded: list = [None, 0.0]
    state = ""
    progress_cb(f"Đang mở trình duyệt {label} (profile của tool)…", 5.0)
    with BrowserSession(headless=False, site=site) as bs:
        # ChatGPT: dùng tab sẵn có của persistent context (không để lại tab trống)
        page = (bs.context.pages[0] if site == "chatgpt" and bs.context.pages
                else bs.new_page())
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception:
            pass
        deadline = time.time() + hold_seconds
        try:
            while time.time() < deadline:
                left = int(deadline - time.time()) // 60
                hint = (f"Cửa sổ {label} đang mở — tự đăng nhập/kiểm tra nếu cần "
                        f"(tool KHÔNG nhập mật khẩu). Bấm 'Đóng trình duyệt' khi xong")
                if site == "chatgpt" and state in _CHATGPT_HINT:
                    hint = _CHATGPT_HINT[state]
                progress_cb(f"{hint} (tự đóng sau ~{left} phút).", 50.0)
                try:
                    if page.is_closed() and not bs.context.pages:
                        progress_cb(f"Cửa sổ {label} đã bị đóng tay.", 95.0)
                        return {"site": site, "closed_by": "user", "state": state}
                    live = page if not page.is_closed() else bs.context.pages[0]
                    live.wait_for_timeout(1500)
                    page = live
                    if site == "chatgpt":
                        state = _chatgpt_state(page, sels, ready_since, recorded)
                except JobCancelled:
                    raise
                except Exception:
                    progress_cb(f"Cửa sổ {label} đã đóng.", 95.0)
                    return {"site": site, "closed_by": "user", "state": state}
        except JobCancelled:
            progress_cb("Đang đóng trình duyệt…", 95.0)
            raise
    return {"site": site, "closed_by": "timeout", "state": state}


def _run_muse_monitor(progress_cb: Callable[[str, float], None],
                      hold_seconds: int) -> dict:
    """Mở đúng profile GPM được chọn cho Muse và giữ nguyên để người dùng đăng nhập.

    Đây là phần trước đây còn thiếu: đăng nhập Muse ở Chrome/Cốc Cốc hoặc một
    profile GPM khác không tạo cookie cho profile mà worker ảnh/clip thực sự dùng.
    """
    from .job_manager import JobCancelled
    from .muse_video import _GPMSession, _state, profile_id

    pid = profile_id()
    progress_cb("Đang mở đúng profile GPMLogin đã chọn cho Muse.ai…", 5.0)
    state = ""
    with _GPMSession(pid) as session:
        page = session.page
        deadline = time.time() + hold_seconds
        try:
            while time.time() < deadline:
                left = int(deadline - time.time()) // 60
                if page.is_closed():
                    progress_cb("Cửa sổ Muse.ai đã bị đóng tay.", 95.0)
                    return {"site": "muse", "closed_by": "user", "state": state,
                            "profile_id": pid}
                st = _state(page)
                if st.get("login"):
                    state = "logged_out"
                    hint = ("Đúng profile Muse của tool đang CHƯA đăng nhập — hãy tự bấm "
                            "Log in và hoàn tất đăng nhập trong cửa sổ này")
                elif st.get("kind") == "connected":
                    state = "logged_in"
                    hint = "Muse đã đăng nhập và sẵn sàng ✓"
                else:
                    state = "open"
                    hint = ("Cửa sổ Muse của đúng profile đang mở — kiểm tra tài khoản/"
                            "trang chat; tool không tự nhập mật khẩu")
                progress_cb(f"{hint}. Bấm 'Đóng trình duyệt' khi xong "
                            f"(tự đóng sau ~{left} phút).", 50.0)
                page.wait_for_timeout(1500)
        except JobCancelled:
            progress_cb("Đang đóng cửa sổ Muse.ai…", 95.0)
            raise
        except Exception:
            progress_cb("Cửa sổ Muse.ai đã đóng.", 95.0)
            return {"site": "muse", "closed_by": "user", "state": state,
                    "profile_id": pid}
    return {"site": "muse", "closed_by": "timeout", "state": state,
            "profile_id": pid}
