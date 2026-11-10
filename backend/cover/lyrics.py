"""
cover/lyrics.py — Xử lý lời bài hát dạng chữ (không cần GPU).

- clean_lyrics: lời thô (dán từ Suno/Internet) → danh sách câu hát. Bỏ nhãn
  [Verse]/[Chorus]/(Intro)…, dòng trống, khoảng trắng thừa.
- parse_lrc / to_lrc: nhập / xuất file .lrc (định dạng lời có mốc thời gian phổ
  biến) để người dùng sửa tay bằng phần mềm khác nếu muốn.
- normalize_lines: chuẩn hoá danh sách {text,start,end} (sắp xếp, vá end thiếu,
  không cho câu chồng lên câu sau).
"""
from __future__ import annotations

import json
import re

# [Verse 1], [Chorus], [Bridge: ...], (Intro), {Outro}… — nhãn đoạn của Suno.
_SECTION_RE = re.compile(r"^\s*[\[\({][^\]\)}]{0,40}[\]\)}]\s*$")
_LRC_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_META_RE = re.compile(r"^\s*\[(ar|ti|al|by|offset|length|re|ve):", re.I)

# Câu dài quá → khó đọc trên video; tách đôi tại dấu phẩy gần giữa nhất.
MAX_LINE_CHARS = 70
# Câu cuối không có câu sau → hiển thị tối đa bấy nhiêu giây.
DEFAULT_LAST_LINE_SEC = 5.0


# Dòng tiêu đề mà nhaccuatui / trang lời nhạc chèn ĐẦU lời: "Bài hát: Tên - Ca sĩ",
# "Ca sĩ: …", "Sáng tác: …", "Lời bài hát Tên"… Không phải câu hát.
_HEADER_RE = re.compile(
    r"^\s*(?:(?:bài hát|ca khúc|tên bài(?: hát)?|ca sĩ|trình bày|thể hiện|sáng tác|"
    r"nhạc sĩ|tác giả|nhạc và lời|nhạc|lời|song|title|artist|singer|composer|"
    r"lyrics?|album|thể loại)\s*[:：]|lời bài hát\b)", re.I)
_HEADER_SCAN = 4          # chỉ xét vài dòng có chữ đầu tiên


def strip_lyrics_header(text: str) -> str:
    """Bỏ các dòng tiêu đề (Bài hát:/Ca sĩ:/Sáng tác:…) ở ĐẦU lời. Dòng như vậy
    nằm giữa bài thì giữ nguyên (có thể là lời thật)."""
    lines = (text or "").replace("\r", "").split("\n")
    i = seen = 0
    while i < len(lines) and seen < _HEADER_SCAN:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Bước 6 đổi space → underscore, nên "Bài hát: …" có thể đã thành
        # "Bài_hát:_…". Chỉ chuẩn hoá để NHẬN DIỆN; dòng trả về giữ nguyên.
        header_probe = re.sub(r"_+", " ", line)
        if not _HEADER_RE.match(header_probe):
            break
        seen += 1
        i += 1
    if not seen:
        return text or ""
    return "\n".join(lines[i:]).lstrip("\n")


def clean_lyrics(raw: str) -> list[str]:
    """Lời thô → danh sách câu (mỗi phần tử 1 câu hiển thị)."""
    out: list[str] = []
    for line in strip_lyrics_header(raw).split("\n"):
        line = line.strip()
        if not line or _SECTION_RE.match(line):
            continue
        # Nếu người dùng dán file .lrc vào ô lời → bỏ mốc thời gian.
        if _LRC_META_RE.match(line):
            continue
        line = _LRC_TIME_RE.sub("", line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue
        out.extend(_split_long(line))
    return out


def _split_long(line: str) -> list[str]:
    if len(line) <= MAX_LINE_CHARS:
        return [line]
    mid = len(line) // 2
    cuts = [m.end() for m in re.finditer(r"[,;:]\s", line)]
    if cuts:
        cut = min(cuts, key=lambda c: abs(c - mid))
    else:
        spaces = [m.start() for m in re.finditer(r" ", line)]
        if not spaces:
            return [line]
        cut = min(spaces, key=lambda c: abs(c - mid))
    a, b = line[:cut].strip(), line[cut:].strip()
    if not a or not b:
        return [line]
    return _split_long(a) + _split_long(b)


def looks_like_lrc(raw: str) -> bool:
    return len(_LRC_TIME_RE.findall(raw or "")) >= 3


def parse_lrc(raw: str) -> list[dict]:
    """Đọc .lrc → [{text,start,end}]. 1 dòng có nhiều mốc → nhân bản câu."""
    items: list[tuple[float, str]] = []
    for line in (raw or "").replace("\r", "").split("\n"):
        if _LRC_META_RE.match(line):
            continue
        stamps = list(_LRC_TIME_RE.finditer(line))
        if not stamps:
            continue
        text = re.sub(r"\s+", " ", _LRC_TIME_RE.sub("", line)).strip()
        for m in stamps:
            mm, ss, frac = m.group(1), m.group(2), m.group(3) or "0"
            t = int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))
            items.append((t, text))
    items.sort(key=lambda x: x[0])
    lines = [{"text": t, "start": s, "end": None} for s, t in items if t]
    return normalize_lines(lines)


def _lrc_ts(sec: float) -> str:
    sec = max(float(sec), 0.0)
    m = int(sec // 60)
    s = sec - m * 60
    return f"[{m:02d}:{s:05.2f}]"


def to_lrc(lines: list[dict], title: str = "", artist: str = "") -> str:
    head = []
    if title:
        head.append(f"[ti:{title}]")
    if artist:
        head.append(f"[ar:{artist}]")
    body = [f"{_lrc_ts(l['start'])}{l['text']}" for l in lines]
    return "\n".join(head + body) + "\n"


def normalize_lines(lines: list[dict], song_duration: float | None = None) -> list[dict]:
    """Sắp theo start, vá end trống/sai, không để câu chồng lên câu sau."""
    clean: list[dict] = []
    for l in lines or []:
        text = str(l.get("text") or "").strip()
        try:
            start = float(l.get("start"))
        except (TypeError, ValueError):
            continue
        if not text or start < 0:
            continue
        end = l.get("end")
        try:
            end = float(end) if end is not None else None
        except (TypeError, ValueError):
            end = None
        clean.append({"text": text, "start": round(start, 3),
                      "end": None if end is None else round(end, 3)})
    clean.sort(key=lambda x: x["start"])
    for i, l in enumerate(clean):
        nxt = clean[i + 1]["start"] if i + 1 < len(clean) else None
        end = l["end"]
        if end is None or end <= l["start"]:
            end = nxt if nxt is not None else l["start"] + DEFAULT_LAST_LINE_SEC
        if nxt is not None and end > nxt:
            end = nxt
        if song_duration and end > song_duration:
            end = max(song_duration, l["start"] + 0.5)
        l["end"] = round(end, 3)
    return clean


def load_lines(lyrics_json: str | None) -> list[dict]:
    try:
        data = json.loads(lyrics_json or "[]")
    except Exception:
        return []
    return normalize_lines(data if isinstance(data, list) else [])


def dump_lines(lines: list[dict]) -> str:
    return json.dumps(normalize_lines(lines), ensure_ascii=False)
