"""
video/browser_base.py — Tiện ích Playwright (sync API) dùng chung cho ChatGPT
và Flow. Dùng persistent profile để GIỮ ĐĂNG NHẬP giữa các lần chạy.

Chạy trong worker thread (ThreadPoolExecutor) — không có asyncio loop nên sync
API hợp lệ (giống stem_service/demucs).

⚠️  Lần đầu: trình duyệt mở ra (headless=False), bạn TỰ ĐĂNG NHẬP ChatGPT/Flow
trong cửa sổ đó. Session được lưu vào .browser_profile nên các lần sau khỏi
đăng nhập lại.
"""
from __future__ import annotations
import threading
import time
import sys
from pathlib import Path
from typing import Optional

from . import config
from .config import BROWSER, PROFILE_DIR

# Backend trên Windows có thể chạy bằng Python hệ thống khi virtualenv cũ bị
# lệch interpreter. Nạp Playwright 3.11 được đóng cục bộ cùng project.
_LOCAL_RUNTIME = Path(__file__).resolve().parents[2] / ".runtime_packages311"
if _LOCAL_RUNTIME.exists() and str(_LOCAL_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_LOCAL_RUNTIME))


def _chromium_installed(pw) -> bool:
    """True nếu Chromium bundled của Playwright đã tải về."""
    try:
        exe = pw.chromium.executable_path
        return bool(exe) and Path(exe).exists()
    except Exception:
        return False


def _install_chromium() -> None:
    """Tự tải Chromium cho Playwright (lần đầu). Chạy Chromium RIÊNG, độc lập
    với trình duyệt mặc định (Cốc Cốc) của máy."""
    import subprocess
    import sys
    print("  ⬇️  Lần đầu: đang tải Chromium riêng cho tool (~150MB)...")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    print("  ✅ Đã cài Chromium riêng cho tool.")


def _profile_variant(exe: str) -> str:
    """Tên hậu tố profile theo trình duyệt (mỗi .exe 1 profile RIÊNG)."""
    low = str(exe).lower()
    for known in ("coccoc", "chrome", "msedge", "edge", "brave"):
        if known in low:
            return known
    return "ext"


# Thư mục nặng/không cần khi nhân bản profile sang trình duyệt khác — bỏ đi để
# chép nhanh (436MB → vài chục MB) mà vẫn giữ cookie/đăng nhập.
_PROFILE_SKIP = {
    "cache", "code cache", "gpucache", "shadercache", "grshadercache",
    "gpupersistentcache", "dawngraphitecache", "dawnwebgpucache",
    "browsermetrics", "crashpad", "component_crx_cache",
    "extensions_crx_cache", "safe browsing", "service worker",
}


def _seed_profile(src: Path, dst: Path) -> None:
    """Nhân bản profile sang thư mục riêng của trình duyệt khác (1 lần đầu).

    ⚠️ KHÔNG dùng chung 1 user_data_dir cho 2 .exe khác phiên bản: Cốc Cốc
    152 sẽ nâng cấp profile và Chromium 151 bundled sau đó từ chối mở."""
    import shutil
    if dst.exists() or not src.exists():
        return
    print(f"  ⧉ Nhân bản profile sang {dst.name} (giữ đăng nhập sẵn có)...")
    try:
        shutil.copytree(
            src, dst,
            ignore=lambda d, names: [n for n in names
                                     if n.lower() in _PROFILE_SKIP],
            dirs_exist_ok=True, symlinks=True,
            ignore_dangling_symlinks=True)
    except Exception as e:                      # copy lỗi → vẫn chạy, chỉ phải
        print(f"  (không nhân bản được profile: {e} — sẽ cần đăng nhập lại)")
        dst.mkdir(parents=True, exist_ok=True)


# ── Playwright sync: tối đa 1 phiên sống / luồng ─────────────────
# sync_playwright().start() đặt event loop của nó làm «loop đang chạy» của CẢ
# LUỒNG cho tới khi stop(). Phiên nào bị RÒ (vd __enter__ lỗi sau khi đã start
# → __exit__ không chạy) sẽ đầu độc luồng: mọi lần start sau trong luồng đó ném
# «It looks like you are using Playwright Sync API inside the asyncio loop».
# Worker Video/Suno chỉ có 1 luồng dùng chung → 1 lần rò làm hỏng MỌI job sau
# (2026-10-25: lô 17 hỏng cả 3 sản phẩm, clip Muse + Suno). Mọi chỗ mở
# Playwright đi qua start_playwright(): ghi sổ phiên sống theo luồng và dọn
# phiên rò trước khi mở phiên mới (2 phiên cùng luồng vốn không chạy được).
_pw_local = threading.local()
# Phiên dừng dở (stop lỗi giữa chừng): giữ tham chiếu mãi để greenlet treo của
# nó KHÔNG bị GC — GC ném GreenletExit vào nó, chạy finally của run_forever và
# gỡ «loop đang chạy» của phiên MỚI cùng luồng.
_zombies: list = []


def _live_sessions() -> list:
    live = getattr(_pw_local, "live", None)
    if live is None:
        live = _pw_local.live = []
    return live


def _clear_stale_loop(loop) -> bool:
    """Gỡ «loop đang chạy» của luồng nếu đó là loop của phiên Playwright đã
    dừng/rò (không đụng loop khác, vd loop của uvicorn)."""
    import asyncio
    try:
        cur = asyncio._get_running_loop()
    except Exception:
        return False
    if cur is None or cur is not loop:
        return False
    asyncio.events._set_running_loop(None)
    return True


def reap_leaked_playwright(reason: str = "") -> int:
    """Dừng mọi phiên Playwright còn sống trong luồng hiện tại. Trả số phiên đã
    dọn. Gọi trước khi mở phiên mới và khi 1 job worker kết thúc."""
    live = _live_sessions()
    n = len(live)
    for pw, _loop in reversed(list(live)):
        try:
            pw.stop()          # wrapper của start_playwright → tự rời sổ + gỡ loop
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ Không dừng gọn được phiên Playwright rò: {e}")
    live.clear()
    if n:
        print(f"  🧹 Đã dọn {n} phiên Playwright bị bỏ ngỏ trong luồng "
              f"{threading.current_thread().name}" + (f" ({reason})" if reason else ""))
    return n


def start_playwright():
    """sync_playwright().start() an toàn cho luồng worker dùng chung."""
    reap_leaked_playwright("trước khi mở phiên mới")
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    cm_stop = pw.stop                    # = PlaywrightContextManager.__exit__
    loop = getattr(getattr(cm_stop, "__self__", None), "_loop", None)
    live = _live_sessions()
    entry = (pw, loop)
    live.append(entry)

    def stop(*_args):
        try:
            cm_stop()
        except BaseException:
            _zombies.append(entry)
            raise
        finally:
            if entry in live:
                live.remove(entry)
            _clear_stale_loop(loop)

    pw.stop = stop
    return pw


class BrowserSession:
    """Bọc 1 persistent browser context. Dùng như context manager."""

    def __init__(self, headless: Optional[bool] = None,
                 profile_dir: Optional[Path] = None,
                 site: Optional[str] = None):
        self._pw = None
        self.context = None
        self._old_policy = None
        self.headless = BROWSER.headless if headless is None else headless
        # site="gemini"/"flow"… → cho phép mỗi site dùng trình duyệt RIÊNG
        # (xem config.SITE_BROWSER). None = quy tắc chung như trước.
        self.site = site
        # profile_dir=None → dùng .browser_profile chung (ChatGPT/Flow, hành vi cũ).
        # Suno truyền SUNO_PROFILE_DIR để có session RIÊNG, không đụng profile khác.
        # site cũng có thể chỉ định profile riêng (config.site_profile_dir) —
        # dùng khi profile cũ đã bị Google gắn cờ và cần profile SẠCH.
        self.profile_dir = (Path(profile_dir) if profile_dir
                            else (config.site_profile_dir(site) or PROFILE_DIR))

    def __enter__(self) -> "BrowserSession":
        try:
            import playwright.sync_api  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "Chưa cài Playwright. Trong .venv chạy:\n"
                "  .venv\\Scripts\\pip.exe install playwright\n"
                "  .venv\\Scripts\\playwright.exe install chromium"
            ) from e

        # Windows: Playwright cần ProactorEventLoop để spawn subprocess trình
        # duyệt. Backend đặt WindowsSelectorEventLoopPolicy toàn cục (cho uvicorn),
        # mà Selector loop KHÔNG hỗ trợ create_subprocess_exec → NotImplementedError.
        # Ép Proactor cho loop Playwright tạo trong worker thread này; loop chính
        # của uvicorn đã tạo từ trước nên không bị ảnh hưởng.
        import asyncio as _asyncio
        import sys as _sys
        if _sys.platform == "win32":
            try:
                self._old_policy = _asyncio.get_event_loop_policy()
                _asyncio.set_event_loop_policy(
                    _asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                self._old_policy = None

        try:
            self._launch()
        except BaseException:
            # __exit__ KHÔNG chạy khi __enter__ raise → tự dọn (dừng Playwright,
            # trả policy), kẻo phiên rò đầu độc luồng worker (xem start_playwright).
            self.__exit__(None, None, None)
            raise
        return self

    def _launch(self) -> None:
        # Trình duyệt ngoài (vd Cốc Cốc): Google Flow CHẶN Chromium bundled của
        # Playwright («hoạt động bất thường») nhưng chấp nhận Cốc Cốc.
        exe = config.resolve_browser_exe(self.site)
        base_profile = self.profile_dir
        if exe:
            self.profile_dir = base_profile.with_name(
                f"{base_profile.name}_{_profile_variant(exe)}")
            _seed_profile(base_profile, self.profile_dir)
            print(f"  🌐 Trình duyệt: {exe}")

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = start_playwright()

        # Tự tải Chromium riêng nếu chưa có (chỉ 1 lần) → dự phòng khi không có
        # trình duyệt ngoài.
        if not exe and not _chromium_installed(self._pw):
            try:
                _install_chromium()
            except Exception as e:
                raise RuntimeError(
                    "Chưa tải được Chromium cho tool. Hãy chạy thủ công 1 lần:\n"
                    "  .venv\\Scripts\\playwright.exe install chromium\n"
                    f"(chi tiết: {type(e).__name__}: {e})"
                ) from e

        base_kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            slow_mo=BROWSER.slow_mo_ms,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            no_viewport=True,
            accept_downloads=True,   # cần cho Suno WAV download-wait (expect_download)
        )

        # Thử lần lượt: .exe ngoài (Cốc Cốc) → kênh cấu hình → Chromium bundled
        # → chrome → msedge
        attempts: list[dict] = []
        if exe:
            attempts.append({**base_kwargs, "executable_path": exe})
        # Dự phòng dùng LẠI profile gốc: profile bản sao đã bị Cốc Cốc 152 nâng
        # cấp thì Chromium 151 bundled không mở được nữa.
        fb = {**base_kwargs, "user_data_dir": str(base_profile)}
        if BROWSER.chrome_channel:
            attempts.append({**fb, "channel": BROWSER.chrome_channel})
        attempts.append(dict(fb))                        # bundled chromium
        attempts.append({**fb, "channel": "chrome"})
        attempts.append({**fb, "channel": "msedge"})

        last_err: Exception | None = None
        for kw in attempts:
            try:
                self.context = self._pw.chromium.launch_persistent_context(**kw)
                last_err = None
                break
            except Exception as e:               # noqa: BLE001 - thử phương án kế
                last_err = e
                continue

        if last_err is not None or self.context is None:
            raise RuntimeError(
                "Không mở được trình duyệt cho automation. Hãy tải trình duyệt "
                "cho Playwright (1 lần):\n"
                "  .venv\\Scripts\\playwright.exe install chromium\n"
                f"(chi tiết: {type(last_err).__name__}: {last_err})"
            ) from last_err

        self.context.set_default_timeout(BROWSER.action_timeout_ms)
        self.context.set_default_navigation_timeout(BROWSER.nav_timeout_ms)

    def __exit__(self, *exc):
        # Dọn dẹp KHÔNG được ném lỗi: trình duyệt đã sập/bị đóng tay thì
        # context.close() lỗi — vẫn PHẢI dừng Playwright (không thì luồng worker
        # bị đầu độc) và không che mất lỗi gốc của job.
        ctx, pw = self.context, self._pw
        self.context, self._pw = None, None
        try:
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass
        finally:
            if pw:
                try:
                    pw.stop()
                except Exception as e:      # noqa: BLE001
                    print(f"  ⚠️ Dừng Playwright lỗi: {e}")
            # Khôi phục policy Selector cho phần còn lại của tiến trình.
            try:
                import asyncio as _asyncio
                if self._old_policy is not None:
                    _asyncio.set_event_loop_policy(self._old_policy)
                    self._old_policy = None
            except Exception:
                pass

    def new_page(self):
        return self.context.new_page()


# ── Helper thao tác selector (thử nhiều selector, fallback dần) ──

# ── Huỷ NHANH giữa các thao tác Playwright chặn lâu ──────────────
# Cờ huỷ của job chỉ được kiểm khi job gọi progress_cb → các vòng chờ dài
# (Studio build 300s, render, chờ bài…) làm nút Huỷ phải đợi vài phút. Job đăng
# ký 1 hàm kiểm tra (theo THREAD) → query_first / wait_ms / cancellable_sleep
# kiểm nó mỗi ≤0.5s và ném OperationCancelled.
class OperationCancelled(BaseException):
    """Người dùng bấm Huỷ/Tạm dừng. Kế thừa BaseException để KHÔNG bị các khối
    `except Exception` (thử lại, bỏ qua lỗi UI…) nuốt mất."""


_cancel_local = threading.local()


def set_cancel_check(fn) -> None:
    """fn() → True khi cần dừng. None để gỡ. Chỉ áp dụng cho thread hiện tại."""
    _cancel_local.fn = fn


def check_cancel() -> None:
    fn = getattr(_cancel_local, "fn", None)
    if fn is not None and fn():
        raise OperationCancelled()


def wait_ms(page, ms: float) -> None:
    """page.wait_for_timeout nhưng chia nhỏ ≤500ms và kiểm cờ huỷ giữa chừng
    (vẫn bơm sự kiện Playwright như wait_for_timeout)."""
    check_cancel()
    end = time.time() + ms / 1000.0
    while True:
        rem = (end - time.time()) * 1000.0
        if rem <= 0:
            return
        page.wait_for_timeout(min(500.0, rem))
        check_cancel()


def cancellable_sleep(seconds: float) -> None:
    """time.sleep kiểm cờ huỷ mỗi 0.5s."""
    check_cancel()
    end = time.time() + max(0.0, seconds)
    while True:
        rem = end - time.time()
        if rem <= 0:
            return
        time.sleep(min(0.5, rem))
        check_cancel()


def query_first(page, selectors: list[str], timeout_ms: int = 8000):
    """Trả về locator đầu tiên tìm thấy trong danh sách selector, hoặc None."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        check_cancel()
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


def click_first(page, selectors: list[str], timeout_ms: int = 8000) -> bool:
    loc = query_first(page, selectors, timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except Exception:
        return False


def fill_first(page, selectors: list[str], text: str,
               timeout_ms: int = 8000) -> bool:
    loc = query_first(page, selectors, timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        # contenteditable dùng type; textarea dùng fill
        try:
            loc.fill(text)
        except Exception:
            loc.type(text, delay=10)
        return True
    except Exception:
        return False


def upload_first(page, selectors: list[str], file_path: str,
                 timeout_ms: int = 8000, set_timeout_ms: int = 300_000,
                 log=None) -> bool:
    """Set file cho input[type=file] đầu tiên khớp.

    `set_timeout_ms`: hạn riêng cho set_input_files — file video thiền vài GB
    lâu hơn nhiều so với action_timeout mặc định (30s) của context.
    `log`: nhận thông báo lỗi THẬT; trước đây lỗi bị nuốt nên hỏng ở đây chỉ
    thấy 'không tìm thấy ô chọn file', rất khó lần ra nguyên nhân.
    """
    deadline = time.time() + timeout_ms / 1000.0
    last_err = ""
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.set_input_files(file_path, timeout=set_timeout_ms)
                    return True
            except Exception as exc:
                last_err = f"{sel} → {type(exc).__name__}: {exc}"
                continue
        page.wait_for_timeout(300)
    if log and last_err:
        try:
            log(f"upload_first thất bại: {last_err[:400]}")
        except Exception:
            pass
    return False


def wait_for_manual_login(page, ready_selectors: list[str],
                          site_name: str, timeout_sec: int = 300) -> None:
    """
    Chờ tới khi UI đã sẵn sàng (đã đăng nhập). Nếu quá timeout → raise với
    hướng dẫn rõ ràng.
    """
    if query_first(page, ready_selectors, timeout_ms=timeout_sec * 1000):
        return
    raise RuntimeError(
        f"Chưa đăng nhập {site_name} hoặc giao diện đã đổi. "
        f"Hãy đăng nhập trong cửa sổ trình duyệt vừa mở, rồi chạy lại. "
        f"Nếu đã đăng nhập mà vẫn lỗi → cần cập nhật selector trong "
        f"backend/video/config.py."
    )
