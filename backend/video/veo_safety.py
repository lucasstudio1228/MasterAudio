"""Làm prompt clip GỌN + AN TOÀN trước khi gửi Veo (Flow / Gemini).

Gemini/Flow trả "I can't generate that video" khi prompt có từ khoá nhạy cảm —
bộ lọc đọc TỪ KHOÁ, không hiểu chữ "NO" đứng trước. Khối phủ định cũ ("NO fire,
head spinning, reversed head, face distortion, melting hands, extra limbs…")
cùng "warrior … sword" trên người tả thực đủ để bị từ chối (và bị tính là thao
tác bất thường). Prompt cũ còn dài ~3.200 ký tự, lẫn mã màu hex và dòng
"negative prompt:" mà Veo không có ô riêng.

Hàm `safe_clip_prompt` nhận prompt cảnh trong manifest (kể cả manifest cũ đã
nhúng câu khoá nguyên liệu + SCENE_SUFFIX) và dựng lại bản gọn:
    <style> + câu khoá nguyên liệu ngắn + Identity (đã lọc) + Scene (đã lọc)
    + đuôi mô tả TÍCH CỰC (không liệt kê điều cấm).
"""
from __future__ import annotations

import re

from .config import SCENERY_MARK, liven_scenery_motion

# Câu khoá nguyên liệu NGẮN (thay câu dài cũ trong prompt_gen._with_ingredient_lock).
INGREDIENT_LOCK = (
    "Ingredients 1 and 2 are character reference sheets: use them only for the "
    "look of the main character (1) and the companion creature (2), and show "
    "both inside one natural film scene, not the sheet layout.")
# Chế độ CHỈ CẢNH VẬT: 2 nguyên liệu là bảng bối cảnh + bảng ánh sáng (giữ dấu
# SCENERY_MARK để bộ bọc prompt vẫn nhận ra hồ sơ cảnh vật).
SCENERY_LOCK = (
    f"{SCENERY_MARK}: ingredients 1 and 2 are environment reference sheets of one "
    "place: use them only for the look of that place, and show it as one natural "
    "film shot, empty and peaceful, not the sheet layout.")

# Đuôi TÍCH CỰC thay MOTION_NEGATIVE: tả điều muốn có thay vì kể điều cấm.
SAFE_TAIL = (
    " Calm, gentle and natural: relaxed steady pose, natural hands and "
    "proportions, the flute held still with clear air around the face and "
    "instrument, smooth slow motion, one continuous shot, no on-screen text.")
SAFE_TAIL_SCENERY = (
    " Calm and natural: the place stays empty and peaceful yet clearly alive, "
    "never a still image — flowing water, moving clouds, wind through leaves and "
    "grass, drifting mist and moving light — with one smooth cinematic camera "
    "move at a normal speed, one continuous shot, no on-screen text.")

# Phần mở đầu/đuôi cũ đã nhúng sẵn trong manifest → bỏ khi dựng lại.
_OLD_HEAD_RE = re.compile(
    r"The two ingredient images are CHARACTER MODEL SHEETS.*?duplicated\s+character\."
    r"|SCENERY-ONLY FILM: the two ingredient images are ENVIRONMENT DESIGN SHEETS"
    r".*?flat\s+studio\s+backdrop\.",
    re.S | re.I)
_OLD_SUFFIX_RE = re.compile(
    r"\s*One single continuous shot, no cuts\. Do not reproduce the reference "
    r"sheets:.*?(?:identity|place) unchanged\.", re.S | re.I)
_OLD_NEGATIVE_RE = re.compile(r"\s*\|\|\s*STRICTLY FORBIDDEN.*$", re.S | re.I)
_NEG_PROMPT_RE = re.compile(r"\s*negative prompt:.*$", re.S | re.I)

_HEX_RE = re.compile(
    r"\s*\((?:\s*#[0-9A-Fa-f]{3,8}\s*[,/;]?\s*)+\)|\s*#[0-9A-Fa-f]{6}\b")

# Từ nhạy cảm → từ trung tính (giữ nghĩa cảnh thiền). GIỮ "warrior" và VŨ KHÍ
# theo mô tả (người dùng yêu cầu 2026-10-05) — chỉ đổi từ giao chiến/máu.
_WORD_MAP = [
    (r"\bbattle(?:field)?s?\b", "journey"),
    (r"\bcombat\b", "practice"),
    (r"\bfight(?:ing|s)?\b", "practice"),
    (r"\bblood(?:y)?\b", "red"),
    (r"\bwounds?\b|\bscars?\b|\binjur(?:y|ies|ed)\b", ""),
    (r"\bsexy\b|\bseductive\b", ""),
    # Tên studio/hoạ sĩ có bản quyền → mô tả chung.
    (r"\bPixar[- ]style\b|\bPixar\b|\bDisney\b", "stylized 3D"),
    (r"\bGhibli[- ]style\b|\bStudio Ghibli\b|\bGhibli\b", "hand-painted Japanese"),
    (r"\(Makoto Shinkai\)|\bMakoto Shinkai\b|\bShinkai\b", ""),
    # Khói/hơi/lửa: nhắc tới là Veo hay vẽ ra → bỏ hẳn khỏi cảnh.
    (r"[^,;.:\n]*\b(?:smoke|smoky|steam(?:ing)?|fire|flames?|embers?|burning)\b[^,;.:\n]*", ""),
]
_WORD_RES = [(re.compile(p, re.I), r) for p, r in _WORD_MAP]

MAX_IDENTITY_CHARS = 650
MAX_SCENE_CHARS = 900


def sanitize(text: str) -> str:
    """Bỏ mã hex, từ nhạy cảm; dọn dấu câu thừa. Vũ khí GIỮ theo mô tả
    (người dùng yêu cầu 2026-10-05)."""
    t = text or ""
    t = _HEX_RE.sub("", t)
    for rx, rep in _WORD_RES:
        t = rx.sub(rep, t)
    # dọn dấu câu/khoảng trắng sau khi xoá
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"([,;:])\s*(?:[,;]\s*)+", r"\1 ", t)
    t = re.sub(r":\s*\.", ".", t)
    t = re.sub(r"[,;]\s*\.", ".", t)
    t = re.sub(r"\.\s*[,;]", ".", t)
    t = re.sub(r"(?:^|(?<=[.:]))\s*[,;]\s*", " ", t)
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\b(a|an|the)\s+(?=[,.;])", "", t, flags=re.I)
    return t.strip(" ,;")


def _clip(text: str, limit: int) -> str:
    """Cắt ở ranh giới câu gần nhất ≤ limit."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > limit * 0.5 else cut.rsplit(" ", 1)[0] + ".").strip()


def split_manifest_prompt(text: str) -> tuple[str, str]:
    """(identity, scene) từ prompt manifest. Prompt không theo khuôn → ("", text)."""
    t = _OLD_NEGATIVE_RE.sub("", text or "")
    t = _NEG_PROMPT_RE.sub("", t)
    t = _OLD_SUFFIX_RE.sub("", t)
    t = _OLD_HEAD_RE.sub("", t).strip()
    identity = ""
    m = re.search(r"(?:IDENTITY|WORLD) TO KEEP:\s*(.*?)(?:\n\s*\n|\bSCENE:)", t, re.S)
    if m:
        identity = m.group(1).strip()
    if "SCENE:" in t:
        t = t.split("SCENE:", 1)[1]
    elif m:
        t = t[m.end():]
    return identity, t.strip()


def safe_clip_prompt(raw: str, style_motion: str = "", *,
                     with_lock: bool = True) -> str:
    """Prompt cuối cùng gửi Veo: gọn, không từ khoá nhạy cảm, tả tích cực."""
    identity, scene = split_manifest_prompt(raw)
    scenery = SCENERY_MARK in (raw or "")
    had_lock = (bool(identity) or "CHARACTER MODEL SHEETS" in (raw or "")
                or "ENVIRONMENT DESIGN SHEETS" in (raw or ""))
    if scenery:
        scene = liven_scenery_motion(scene)
    scene = _clip(sanitize(scene), MAX_SCENE_CHARS)
    parts = []
    style_motion = sanitize(style_motion)
    if style_motion and not scene.lower().startswith(style_motion.lower()):
        parts.append(style_motion.rstrip(".") + ".")
    if with_lock and had_lock:
        parts.append(SCENERY_LOCK if scenery else INGREDIENT_LOCK)
    if identity:
        parts.append(("Place: " if scenery else "Identity: ")
                     + _clip(sanitize(identity), MAX_IDENTITY_CHARS))
    parts.append(("Scene: " if had_lock else "") + scene)
    out = " ".join(p.strip() for p in parts if p.strip())
    out = out.rstrip()
    if not out.endswith("."):
        out += "."
    return out + (SAFE_TAIL_SCENERY if scenery else SAFE_TAIL)


# Từ khoá còn sót mà bộ lọc hay chặn — dùng cho test/cổng kiểm tra.
RISKY_RE = re.compile(
    r"\b(?:blood|battle|combat|smoke|fire|flames?|distortion|melting|"
    r"reversed head|head spinning|extra (?:fingers|limbs)|missing (?:fingers|limbs)|"
    r"Pixar|Disney|Ghibli|Shinkai)\b|#[0-9A-Fa-f]{6}", re.I)


def risky_terms(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in RISKY_RE.finditer(text or "")})
