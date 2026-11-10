"""
keep_awake.py — Chống Windows tự NGỦ / TẮT MÀN HÌNH / KHOÁ do hết giờ chờ trong
lúc chạy job dài (render nhạc & tạo video bằng browser automation). Nếu màn hình
tắt/khoá giữa chừng, automation Flow/Veo (điều khiển cửa sổ Chrome thật) dễ hỏng.

Cơ chế: Windows API `SetThreadExecutionState` với cờ ES_CONTINUOUS |
ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED → hệ báo "đang bận, đừng ngủ/tắt màn".
Trạng thái ES_CONTINUOUS gắn theo THREAD gọi, nên PHẢI dùng trong đúng thread
worker chạy job (mỗi job_manager có 1 worker thread cố định — hợp lệ).

LƯU Ý: chỉ chặn khoá/ngủ do TIMEOUT rảnh rỗi. KHÔNG chặn được người dùng bấm
Win+L thủ công hay chính sách nhóm (group policy) ép khoá. Hệ khác Windows: no-op.
"""
from __future__ import annotations
import sys
from contextlib import contextmanager

_ES_CONTINUOUS       = 0x80000000
_ES_SYSTEM_REQUIRED  = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def _set_state(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # Trả 0 nếu lỗi; khác 0 nếu thành công.
        res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return res != 0
    except Exception:
        return False


@contextmanager
def keep_awake(log=None):
    """Trong khối `with`: máy KHÔNG tự ngủ / tắt màn hình / khoá vì rảnh.
    Rời khối → trả trạng thái bình thường (cho phép ngủ lại). Best-effort:
    lỗi/không phải Windows thì im lặng chạy tiếp, không làm hỏng job."""
    ok = _set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
    if ok and log:
        try:
            log("🔓 Giữ máy thức (chống tắt màn hình/khoá) trong lúc chạy")
        except Exception:
            pass
    try:
        yield ok
    finally:
        # Xoá yêu cầu (chỉ cần ES_CONTINUOUS đơn lẻ) → cho phép ngủ/khoá lại.
        _set_state(_ES_CONTINUOUS)
