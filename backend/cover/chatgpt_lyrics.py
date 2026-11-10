"""Lớp tương thích cho bước chuẩn hóa lyrics Suno.

Tên module được giữ để code/test cũ vẫn import được, nhưng workflow không còn
mở ChatGPT và không đổi khoảng trắng thành ``_``. Toàn bộ xử lý diễn ra cục bộ
qua :func:`backend.cover.suno_lyrics.normalize_for_suno`.
"""
from __future__ import annotations

import unicodedata
from typing import Callable

from .suno_lyrics import normalize_for_suno

_LOG = Callable[[str], None]


class LyricsTransformError(RuntimeError):
    """Lyrics không còn nội dung hợp lệ sau khi chuẩn hóa."""


def safe_suno_transform(lyrics: str) -> str:
    """Trả lyrics Suno dạng văn bản tự nhiên, Unicode NFC và có khoảng trắng.

    Hàm cũng đọc được cache cũ dùng dấu ``_`` vì ``normalize_for_suno`` chuyển
    chúng về khoảng trắng trước khi xử lý.
    """
    result = normalize_for_suno(lyrics or "")
    return unicodedata.normalize("NFC", result)


# Giữ tên API cũ để plugin/test ngoài repo không vỡ import. Không còn font nghệ
# thuật và không còn underscore dù tên lịch sử của hàm vẫn được giữ.
local_artistic_transform = safe_suno_transform


def _canonicalize_chatgpt_reply(text: str) -> str:
    """Tương thích test/plugin cũ; chỉ chuẩn hóa Unicode, không gọi ChatGPT."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFKC", text or ""))


def _clean_reply(text: str) -> str:
    """Bỏ code fence nếu dữ liệu cũ từng được lưu từ ChatGPT."""
    text = (text or "").strip().replace("\r", "")
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _validate(source: str, result: str) -> None:
    """Xác nhận kết quả đúng bằng bộ chuẩn hóa cục bộ duy nhất."""
    expected = safe_suno_transform(source)
    actual = safe_suno_transform(result)
    if not expected:
        raise LyricsTransformError("Không có lyrics hợp lệ để gửi lên Suno.")
    if "_" in result:
        raise LyricsTransformError(
            "Lyrics còn dấu _; phải dùng khoảng trắng tự nhiên trước khi gửi Suno.")
    if actual != expected:
        raise LyricsTransformError(
            "Kết quả đã đổi nội dung hoặc thứ tự lyrics; không gửi lên Suno.")


def transform_lyrics(lyrics: str, log: _LOG) -> str:
    """Chuẩn hóa ngay trong tool; không mở web và không truyền lyrics ra ngoài."""
    result = safe_suno_transform(lyrics)
    if not result:
        raise LyricsTransformError("Không có lyrics hợp lệ để gửi lên Suno.")
    if log:
        log(
            "Bước 6: đã chuẩn hóa lyrics cục bộ bằng khoảng trắng tự nhiên; "
            "không gửi ChatGPT và không dùng dấu _."
        )
    return result
