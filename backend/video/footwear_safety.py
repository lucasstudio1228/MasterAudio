"""Bộ lọc «chân trần» cho prompt ảnh/cảnh.

Gemini (tạo ảnh) chặn NGẦM bảng nhân vật có cận cảnh bàn chân trần ("barefoot,
clean feet, calloused soles…"): không báo vi phạm mà trả lời chung chung «I
encountered an error… try again» / «I'm having a hard time…» → tool gửi lại y
nguyên prompt nên lần nào cũng hỏng (project #23, 2026-10-07).

Cách xử lý: nhân vật LUÔN mang dép/giày đơn giản. Hàm ở đây thay mọi cụm chân
trần bằng dép rơm — tất định + idempotent, nên khối khoá continuity
(character_sheet) đổi GIỐNG HỆT nhau ở mọi prompt chứa nó (validate_manifest
vẫn đạt). Câu bố cục chung «Never crop the character's feet» KHÔNG bị đụng.
"""
from __future__ import annotations

import re

SANDALS = "simple woven straw sandals"
FOOTWEAR_INSET = "FOOTWEAR — Simple woven straw sandals with dark cloth straps."

# Ô cận cảnh của bảng nhân vật: "– FOOTWEAR — Barefoot, elegant clean feet…".
_INSET = re.compile(r"(?im)^([ \t]*(?:[–•\-*][ \t]*)?)(?:FOOTWEAR|FEET|SHOES?)[ \t]*[—–:\-][^\n]*$")
_INSET_BAD = re.compile(r"(?i)\b(bare|barefoot\w*|feet|foot|soles?|toes?|shoeless|no shoes)\b")

_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?i)\bno (?:shoes|footwear)\s*\(\s*bare[- ]?foot(?:ed)?\s*\)"), SANDALS),
    (re.compile(r"(?i)\b(?:no shoes|without shoes|shoeless|no footwear)\b"), SANDALS),
    (re.compile(r"(?i)\bbare[- ]?foot(?:ed)?\b"), f"in {SANDALS}"),
    (re.compile(r"(?i)\bbare[- ]?feet\b"), f"feet in {SANDALS}"),
    (re.compile(r"(?i)\bbare (?:toes|soles?|ankles?)\b"), SANDALS),
    (re.compile(r"(?i)\b(?:calloused|callused|dusty|dirty|clean|elegant) (?:soles?|toes)\b"), SANDALS),
)


def mentions_bare_feet(text: str) -> bool:
    return isinstance(text, str) and sanitize(text) != text


def sanitize(text: str) -> str:
    """Thay cụm chân trần bằng dép rơm đơn giản (idempotent)."""
    if not isinstance(text, str) or not text:
        return text

    def _inset(m: re.Match) -> str:
        line = m.group(0)
        return m.group(1) + FOOTWEAR_INSET if _INSET_BAD.search(line) else line

    out = _INSET.sub(_inset, text)
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    # "in in simple…" khi câu gốc là "in barefoot" / "walks in bare feet".
    out = re.sub(r"(?i)\bin (?:feet )?in " + re.escape(SANDALS), f"in {SANDALS}", out)
    return out


def sanitize_data(value):
    """Áp sanitize cho mọi chuỗi trong dict/list lồng nhau (JSON AI trả về)."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, list):
        return [sanitize_data(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_data(v) for k, v in value.items()}
    return value


def sanitize_manifest(saved: dict) -> dict:
    """Trả BẢN SAO đã thay chân trần ở prompts / motions / continuity; trả CHÍNH
    `saved` nếu không có gì cần đổi. KHÔNG đụng context/idea (đầu vào chữ ký
    input_signature — đổi sẽ khiến prepare() tưởng hồ sơ đã lỗi thời)."""
    out = dict(saved)
    changed = False
    for field in ("prompts", "motions", "continuity"):
        values = saved.get(field)
        if isinstance(values, dict):
            new = sanitize_data(values)
            if new != values:
                out[field] = new
                changed = True
    return out if changed else saved
