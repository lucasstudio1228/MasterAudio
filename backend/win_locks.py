"""Tìm tiến trình đang giữ file (Windows Restart Manager) — chỉ để BÁO người dùng.

Không bao giờ đóng/kill tiến trình; lỗi bất kỳ → trả list rỗng (best-effort).
Lưu ý: Restart Manager chỉ thấy handle FILE (VLC, trình phát, ffmpeg…), không thấy
cửa sổ File Explorer đang mở thư mục.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _rm_lockers(files: list[str]) -> list[str]:
    import ctypes
    from ctypes import wintypes

    rstrtmgr = ctypes.WinDLL("rstrtmgr")

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS),
                    ("strAppName", wintypes.WCHAR * 256),
                    ("strServiceShortName", wintypes.WCHAR * 64),
                    ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG),
                    ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)]

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key):
        return []
    try:
        arr = (wintypes.LPCWSTR * len(files))(*files)
        if rstrtmgr.RmRegisterResources(session, len(files), arr, 0, None, 0, None):
            return []
        needed, count, reason = wintypes.UINT(), wintypes.UINT(0), wintypes.DWORD()
        rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None,
                           ctypes.byref(reason))
        if not needed.value:
            return []
        info = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        if rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                              info, ctypes.byref(reason)):
            return []
        own = os.getpid()
        out = []
        for i in range(count.value):
            pid = info[i].Process.dwProcessId
            name = info[i].strAppName or f"pid {pid}"
            out.append("chính tool này (đang phát/đọc file)" if pid == own
                       else f"{name} (pid {pid})")
        return out
    finally:
        rstrtmgr.RmEndSession(session)


def lockers(paths, limit_files: int = 400) -> list[str]:
    """Tên các tiến trình đang mở file trong `paths` (file hoặc thư mục)."""
    if sys.platform != "win32":
        return []
    files: list[str] = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            files.append(str(p))
        elif p.is_dir():
            for root, _dirs, names in os.walk(p):
                files.extend(os.path.join(root, n) for n in names)
        if len(files) >= limit_files:
            break
    found: list[str] = []
    try:
        for i in range(0, min(len(files), limit_files), 200):
            for name in _rm_lockers(files[i:i + 200]):
                if name not in found:
                    found.append(name)
    except Exception:       # noqa: BLE001 — chẩn đoán phụ, không được làm hỏng luồng xoá
        return found
    return found
