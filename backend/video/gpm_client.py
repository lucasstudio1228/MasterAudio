"""
video/gpm_client.py — Client cho GPMLogin (Global) Local Automation API.

Chức năng:
  • list_profiles()  — liệt kê profile (kênh) để đổ dropdown ở UI mapping.
  • start_profile(id) — bật 1 profile, trả về CDP endpoint (http://127.0.0.1:port)
                        để Playwright connect_over_cdp điều khiển trình duyệt đã
                        đăng nhập sẵn Google/YouTube của profile đó.
  • close_profile(id) — đóng profile sau khi xong.
  • ensure_app_running(exe) — (tùy chọn) mở GPMLoginGlobal.exe nếu app chưa chạy.

Ghi chú môi trường (đã xác minh trên máy user):
  - Local API mặc định là http://localhost:9495 (user xác nhận). Cấu hình được
    trong Settings → gpm_api_base_url nếu máy khác dùng cổng khác.
  - setting.dat (%APPDATA%/GPMLoginGlobal/setting.dat) có thể ghi cổng khác (vd
    19996) nhưng đó không phải cổng đang phục vụ API — chỉ dùng local_storage_path
    trong đó để tìm database.db cho fallback liệt kê.
  - API dùng phiên bản v1: GET /api/v1/profiles, /api/v1/groups,
    /api/v1/profiles/start/{id} (trả remote_debugging_port + websocket_debugging_url),
    /api/v1/profiles/stop/{id}. Route KHÔNG hợp lệ (vd /api/v3/*) → server trả
    câu chào {"data":"GPMLogin Global API"} — code phát hiện và báo user.
  - Nếu HTTP API chưa bật/không kết nối được, list_profiles() TỰ fallback đọc
    trực tiếp file database.db trong local_storage_path (chỉ để LIỆT KÊ; việc
    bật profile lấy CDP thì bắt buộc phải có HTTP API bật trong app GPMLogin).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


class GPMError(RuntimeError):
    """Lỗi khi gọi GPMLogin Local API (kèm hướng dẫn khắc phục cho user)."""


# ── Tiến trình Chrome SÓT của 1 profile ─────────────────────────
# 2026-10-06: GPMLogin có lúc «mất dấu» trình duyệt của profile (báo ProfileInUse,
# stop trả OK nhưng chrome.exe vẫn sống, giữ khoá thư mục profile — log GPM:
# «Delete failed … IO_SharingViolation»). Mọi lần bật sau đó Chrome mới chỉ chuyển
# lệnh cho bản cũ rồi thoát → cổng debug không bao giờ mở (GPM: «Stuck at:
# Starting», tool: «connect ECONNREFUSED»). Chỉ nhắm chrome.exe có UUID profile
# trong dòng lệnh (--user-data-dir=…\<uuid>) → không đụng Chrome thường/profile khác.
_PROFILE_ID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")


def _ps(script: str, timeout: float = 30.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=timeout,
                       creationflags=flags)
    return r.stdout or ""


def find_profile_browser_pids(profile_id: str) -> list[int]:
    """PID các chrome.exe đang chạy với thư mục dữ liệu của profile này."""
    if sys.platform != "win32" or not _PROFILE_ID_RE.match(profile_id or ""):
        return []
    try:
        out = _ps("Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{profile_id}*' }} | "
                  "ForEach-Object { $_.ProcessId }")
    except Exception:
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def kill_profile_browsers(profile_id: str) -> int:
    """Tắt cưỡng bức các chrome.exe sót của profile. Trả số PID đã tắt."""
    pids = find_profile_browser_pids(profile_id)
    if not pids:
        return 0
    try:
        _ps("Stop-Process -Force -ErrorAction SilentlyContinue -Id "
            + ",".join(str(p) for p in pids))
    except Exception:
        return 0
    return len(pids)


# ── Đọc cấu hình GPMLogin trên máy (best-effort) ────────────────
def _setting_dat_path() -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    p = Path(appdata) / "GPMLoginGlobal" / "setting.dat"
    return p if p.exists() else None


def read_gpm_settings() -> dict:
    """Đọc setting.dat → {'port': int|None, 'local_storage_path': str|None}.
    Trả dict rỗng nếu không đọc được (không raise)."""
    p = _setting_dat_path()
    if not p:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    api = data.get("api") or {}
    return {
        "port": api.get("port"),
        "local_storage_path": data.get("local_storage_path"),
    }


DEFAULT_API_BASE_URL = "http://localhost:9495"


def default_base_url() -> str:
    """URL API mặc định của GPMLogin Local API."""
    return DEFAULT_API_BASE_URL


def _gpm_db_path() -> Optional[Path]:
    lsp = (read_gpm_settings() or {}).get("local_storage_path")
    if not lsp:
        return None
    db = Path(lsp) / "database.db"
    return db if db.exists() else None


class GPMClient:
    def __init__(self, base_url: str | None = None, timeout: float = 20.0):
        base = (base_url or "").strip() or default_base_url()
        self.base_url = base.rstrip("/")
        self.timeout = timeout

    # ── HTTP thô ────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            raise GPMError(
                f"Không gọi được GPMLogin API ({url}). Hãy mở app GPMLogin, "
                f"vào Cài đặt → API và BẬT Local API (cổng khớp {self.base_url}). "
                f"Chi tiết: {type(e).__name__}: {e}"
            ) from e
        try:
            data = json.loads(raw)
        except Exception as e:
            raise GPMError(
                f"GPMLogin API trả về không phải JSON ({url}): {raw[:200]!r}. "
                f"Có thể sai cổng — kiểm tra lại URL API trong Cài đặt."
            ) from e
        if isinstance(data, dict) and data.get("success") is False:
            raise GPMError(f"GPMLogin API lỗi: {data.get('message') or data}")
        # Cổng sống nhưng route chưa hoạt động: server trả câu chào ở MỌI path.
        if isinstance(data, dict) and isinstance(data.get("data"), str) \
                and "GPMLogin" in data.get("data", ""):
            raise GPMError(
                f"GPMLogin API ({self.base_url}) đang chạy nhưng chưa phục vụ "
                f"route {path} (chỉ trả câu chào). Hãy mở app GPMLogin → Cài đặt "
                f"→ API và BẬT Local API, rồi thử lại."
            )
        return data

    # ── Liệt kê profile ─────────────────────────────────────────
    def list_profiles(self) -> list[dict]:
        """Trả [{id, name, group_id}]. HTTP trước; nếu lỗi → fallback đọc DB."""
        try:
            data = self._get("/api/v1/profiles",
                             {"page": 1, "per_page": 200, "group_id": "all"})
            profiles = self._extract_profiles(data)
            if profiles:
                return self._attach_group_names(profiles)
        except GPMError:
            pass
        # Fallback: đọc trực tiếp database.db (chỉ liệt kê được, không bật được).
        db_profiles = self.list_profiles_from_db()
        if db_profiles:
            return db_profiles
        # Không có gì — gọi lại HTTP để ném lỗi rõ ràng cho user.
        data = self._get("/api/v1/profiles",
                         {"page": 1, "per_page": 200, "group_id": "all"})
        return self._attach_group_names(self._extract_profiles(data))

    def _attach_group_names(self, profiles: list[dict]) -> list[dict]:
        """API v1 profiles KHÔNG kèm group_name → tra thêm từ /api/v1/groups."""
        if all(p.get("group_name") for p in profiles):
            return profiles
        try:
            gdata = self._get("/api/v1/groups", {"page": 1, "per_page": 200})
            payload = gdata.get("data") if isinstance(gdata, dict) else {}
            rows = payload.get("data") if isinstance(payload, dict) else payload
            gmap = {r.get("id"): r.get("name", "")
                    for r in (rows or []) if isinstance(r, dict)}
        except GPMError:
            gmap = {}
        for p in profiles:
            if not p.get("group_name"):
                p["group_name"] = gmap.get(p.get("group_id"), "")
        return profiles

    @staticmethod
    def _extract_profiles(data: dict) -> list[dict]:
        """API có thể trả data=[...] hoặc data={'data':[...]}. Chuẩn hoá."""
        payload = data.get("data") if isinstance(data, dict) else data
        rows = None
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("data") or payload.get("profiles") or []
        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            out.append({
                "id": r.get("id") or r.get("profile_id") or "",
                "name": r.get("name") or r.get("profile_name") or "",
                "group_id": r.get("group_id") or "",
                "group_name": r.get("group_name") or "",
            })
        return [p for p in out if p["id"]]

    def list_profiles_from_db(self) -> list[dict]:
        """Fallback: đọc profile + tên group từ database.db của GPMLogin."""
        db = _gpm_db_path()
        if not db:
            return []
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            try:
                groups = {r["id"]: r["name"]
                          for r in con.execute("SELECT id,name FROM groups")}
                rows = con.execute(
                    "SELECT id,name,group_id FROM profiles "
                    "WHERE is_deleted=0 ORDER BY name"
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return []
        return [{
            "id": r["id"], "name": r["name"], "group_id": r["group_id"],
            "group_name": groups.get(r["group_id"], ""),
        } for r in rows]

    # ── Bật / tắt profile ───────────────────────────────────────
    def start_profile(self, profile_id: str,
                      extra_params: dict | None = None) -> str:
        """Bật profile → trả CDP endpoint cho Playwright connect_over_cdp.

        API v1 trả data={remote_debugging_port, websocket_debugging_url, ...}.
        Ưu tiên websocket_debugging_url (ws://...), fallback http://127.0.0.1:PORT.
        """
        params = dict(extra_params or {})
        try:
            data = self._get(f"/api/v1/profiles/start/{profile_id}", params or None)
        except GPMError as exc:
            # ProfileInUse = profile còn mở từ lần chạy trước (hoặc job trước bị
            # dừng đột ngột). Đóng rồi bật lại 1 lần thay vì chết cả job.
            if "profileinuse" not in str(exc).lower():
                raise
            # GPMLogin giải phóng profile CHẬM sau khi stop (quan sát: >6 giây) —
            # đóng rồi thử lại nhiều lần trong ~60 giây thay vì bỏ cuộc ngay.
            self.close_profile(profile_id)
            self.kill_leftover_browsers(profile_id)
            data = None
            for _ in range(6):
                time.sleep(10)
                try:
                    data = self._get(f"/api/v1/profiles/start/{profile_id}",
                                     params or None)
                    break
                except GPMError as exc2:
                    if "profileinuse" not in str(exc2).lower():
                        raise
                    exc = exc2
            if data is None:
                raise GPMError(
                    f"GPMLogin vẫn báo ProfileInUse sau ~60 giây cho profile "
                    f"{profile_id}. Hãy đóng profile này trong GPMLogin rồi thử "
                    f"lại. ({exc})")
        payload = data.get("data") if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        ws = str(payload.get("websocket_debugging_url") or "").strip()
        port = payload.get("remote_debugging_port") or 0
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 0
        # Playwright connect_over_cdp nhận cả ws:// lẫn http://host:port.
        if ws:
            return ws
        if port > 0:
            return f"http://127.0.0.1:{port}"
        # Một số bản trả sẵn địa chỉ đầy đủ.
        addr = str(payload.get("remote_debugging_address")
                   or payload.get("remote_debugging_port") or "").strip()
        if addr:
            if addr.isdigit():
                addr = f"127.0.0.1:{addr}"
            if not addr.startswith(("http", "ws")):
                addr = f"http://{addr}"
            return addr
        raise GPMError(
            f"GPMLogin bật profile nhưng không trả cổng debug. "
            f"Phản hồi: {payload}. message={data.get('message')!r}"
        )

    def close_profile(self, profile_id: str) -> None:
        try:
            self._get(f"/api/v1/profiles/stop/{profile_id}")
        except GPMError:
            # Đóng thất bại không nên làm hỏng cả job — chỉ bỏ qua.
            pass

    def kill_leftover_browsers(self, profile_id: str, wait_sec: float = 10.0) -> int:
        """Gọi SAU close_profile: chờ GPM tự tắt trình duyệt (bình thường <2 giây);
        còn chrome.exe của profile sau wait_sec → tắt cưỡng bức. Trả số đã tắt."""
        deadline = time.time() + wait_sec
        while find_profile_browser_pids(profile_id):
            if time.time() >= deadline:
                n = kill_profile_browsers(profile_id)
                time.sleep(2.0)
                return n
            time.sleep(1.0)
        return 0

    # ── Mở app nếu chưa chạy (tùy chọn) ─────────────────────────
    def ensure_app_running(self, exe_path: str | None = None,
                          wait_sec: float = 20.0) -> bool:
        """Nếu API chưa phản hồi và có exe_path → mở app rồi chờ API sống dậy."""
        if self._api_alive():
            return True
        if not exe_path:
            return False
        exe = Path(exe_path)
        if not exe.exists():
            raise GPMError(f"Không thấy GPMLogin exe tại: {exe_path}")
        try:
            import subprocess
            subprocess.Popen([str(exe)], close_fds=True)
        except Exception as e:
            raise GPMError(f"Không mở được GPMLogin: {e}") from e
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if self._api_alive():
                return True
            time.sleep(1.0)
        return self._api_alive()

    def _api_alive(self) -> bool:
        try:
            self._get("/api/v1/profiles",
                     {"page": 1, "per_page": 1, "group_id": "all"})
            return True
        except GPMError:
            return False
