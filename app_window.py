"""Mo tool trong CUA SO UNG DUNG rieng (khong can trinh duyet).

Chay bang file .exe canh start.bat (xem launcher_exe/). Tu bat server an
(khong cua so den), doi server san sang roi hien giao dien trong cua so
Edge WebView2 co san tren Windows 10/11. Dong cua so = tat server; neu dang
co viec chay thi hoi lai truoc.

Cong + ten lay tu start.bat cung thu muc -> 1 file dung chung cho moi ban tool.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "logs"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _read_start_bat() -> tuple[int, str]:
    port, name = 8100, "Cover Tool"
    try:
        txt = (ROOT / "start.bat").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"--port\s+(\d+)", txt)
        if m:
            port = int(m.group(1))
        m = re.search(r"REM\s+(.+?)\s+-\s+START", txt)
        if m:
            name = m.group(1).strip().title()
    except OSError:
        pass
    return port, name


PORT, APP_NAME = _read_start_bat()
PORT = int(os.environ.get("COVER_APP_PORT") or PORT)  # chi de thu nghiem
BASE = f"http://127.0.0.1:{PORT}"

_server: subprocess.Popen | None = None
_closing_ok = False

SPLASH = """<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;display:flex;
align-items:center;justify-content:center;background:#111827;color:#e5e7eb;font:16px Segoe UI,sans-serif">
<div style="text-align:center"><div style="font-size:48px">🎧</div><div style="font-size:20px;margin:8px 0">
{name}</div><div id="m" style="color:#9ca3af">{msg}</div></div></body>"""


def _port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _get_json(path: str, timeout: float = 3):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _server_ready() -> bool:
    try:
        _get_json("/api/tasks/active")
        return True
    except Exception:
        return False


def _start_server() -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / "app_server.log", "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} mo bang cua so ung dung =====\n")
    log.flush()
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


def _stop_server() -> None:
    global _server
    if _server and _server.poll() is None:
        # /T: tat ca tien trinh con (ffmpeg, trinh duyet Suno...) giong nhu dong start.bat.
        subprocess.run(["taskkill", "/PID", str(_server.pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW)
    _server = None


def _log_tail(n: int = 15) -> str:
    try:
        lines = (LOG_DIR / "app_server.log").read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _set_icon(title: str) -> None:
    """Gan icon tai nghe cho cua so + taskbar (mac dinh se la icon Python)."""
    import ctypes
    ico = ROOT / "launcher_exe" / "app.ico"
    if not ico.exists():
        return
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.LoadImageW.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for _ in range(40):
        hwnd = u.FindWindowW(None, title)
        if hwnd:
            for which, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
                h = u.LoadImageW(None, str(ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if h:
                    u.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
            return
        time.sleep(0.25)


def _boot(window) -> None:
    """Chay trong luong rieng sau khi cua so hien: bat server roi nap giao dien."""
    global _server
    threading.Thread(target=_set_icon, args=(window.title,), daemon=True).start()
    if not _port_open():
        _server = _start_server()
    deadline = time.time() + 180
    while time.time() < deadline:
        if _server and _server.poll() is not None:
            break
        if _server_ready():
            window.load_url(BASE + "/")
            return
        time.sleep(0.5)
    import html
    window.load_html(SPLASH.format(
        name=APP_NAME,
        msg="Không mở được tool. Chụp màn hình này gửi người hỗ trợ.<pre style='text-align:left;"
            "font-size:12px;max-width:90vw;white-space:pre-wrap'>" + html.escape(_log_tail()) + "</pre>"))


def _busy_count() -> int:
    try:
        return len(_get_json("/api/tasks/active").get("tasks") or [])
    except Exception:
        return 0


def _on_closing(window):
    global _closing_ok
    if _closing_ok or _server is None:
        return True
    n = _busy_count()
    if n and not window.create_confirmation_dialog(
            "Đang có việc chạy",
            f"Tool đang chạy {n} việc (Suno / mix...).\n"
            "Đóng cửa sổ sẽ DỪNG các việc này.\n\nVẫn đóng?"):
        return False
    _closing_ok = True
    return True


def _msg(text: str, flags: int = 0x40) -> int:
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)


def _import_webview():
    try:
        import webview
        return webview
    except ImportError:
        pass
    if _msg("Máy chưa có thư viện cửa sổ ứng dụng (pywebview).\n"
            "Bấm OK để tự cài (cần mạng, khoảng 1 phút).", 0x41) != 1:
        return None
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
    subprocess.run([str(py), "-m", "pip", "install", "pywebview>=6,<7"],
                   creationflags=0x00000010)  # CREATE_NEW_CONSOLE: hien tien trinh cai
    try:
        import webview
        return webview
    except ImportError:
        _msg("Cài chưa được. Hãy dùng start.bat như trước, rồi gửi ảnh lỗi cho người hỗ trợ.", 0x10)
        return None


def main() -> int:
    try:  # taskbar nhom rieng, khong gop chung voi Python khac
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"LucasStudio.{APP_NAME}.{PORT}")
    except Exception:
        pass
    webview = _import_webview()
    if webview is None:
        return 1

    webview.settings["ALLOW_DOWNLOADS"] = True          # nut ⬇ tai file
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # link ngoai (nhaccuatui...) mo trinh duyet
    title = APP_NAME + (" (đã mở sẵn)" if _port_open() else "")
    window = webview.create_window(
        title, html=SPLASH.format(name=APP_NAME, msg="Đang khởi động, chờ chút…"),
        width=1440, height=900, min_size=(1000, 650), maximized=True,
        text_select=True,
    )
    window.events.closing += lambda: _on_closing(window)
    try:
        webview.start(
            _boot, window, gui="edgechromium",
            private_mode=False,  # giu localStorage (lo dang chon...) giua cac lan mo
            storage_path=str(ROOT / "data" / "webview"),
        )
    finally:
        _stop_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
