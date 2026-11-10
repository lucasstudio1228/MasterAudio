"""Bước 6: chuẩn hoá lời bài hát cho ô Lyrics của Suno — chạy ngay trong tool.

Quy tắc (người dùng chốt 2026-11-08), làm theo thứ tự:

1. Bỏ nhãn cấu trúc (Intro, Verse 1, [Chorus], ĐK:, Phiên khúc, Lời 1, Nhạc
   dạo, Kết…). Nhãn đứng đầu một dòng có lời («ĐK: Đừng buồn») → chỉ bỏ nhãn.
   Chỗ có nhãn trở thành ranh giới đoạn.
2. Font nghệ thuật Unicode (𝓐 𝐀 Ａ ⓐ 🅐 ᴀ…) → chữ thường tương ứng; «_» → dấu
   cách; xoá ký tự ẩn, NBSP → dấu cách; xoá emoji, ký tự trang trí, markdown;
   tiếng Việt về NFC.
3. Dấu câu: giữ , . ? ! …; gộp chuỗi dấu vô nghĩa; dấu dính chữ trước, sau là
   một dấu cách. Không thêm dấu mới.
4. Dòng dài hơn 12 chữ có dấu phẩy ở giữa → tách hai dòng tại dấu phẩy gần giữa
   nhất (bỏ dấu phẩy đó).
5. Giữa các đoạn đúng một dòng trống; không dòng trống ở đầu/cuối.
6. Không thêm/bớt/sửa/sắp xếp lại chữ nào; giữ chữ hoa/thường của chữ thường.
"""
from __future__ import annotations

import re
import unicodedata

from .lyrics import strip_lyrics_header

MAX_SYLLABLES = 12        # dòng dài hơn số chữ này mới tách tại dấu phẩy
MIN_SPLIT_SIDE = 3        # mỗi nửa sau khi tách phải có ít nhất ngần này chữ

# ───────────────────────────── ký tự ─────────────────────────────
_INVISIBLE = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad",
              "\u200e", "\u200f", "\u180e"}
_SPACES = {"\u00a0", "\u202f", "\u2007", "\u3000", "\t"} | {
    chr(c) for c in range(0x2000, 0x200b)}
# gạch chân / gạch ngang / gạch trên dạng dấu kết hợp
_OVERLAY_MARKS = {chr(c) for c in range(0x0331, 0x0339)} | {"\u0305", "\u033f"}
_VARIATION = {chr(c) for c in range(0xFE00, 0xFE10)}
_ASCII_PUNCT_KEEP = set(",.!?:;'\"()[]{}-")
_LETTER_NAME = re.compile(
    r"LATIN (?:CAPITAL |SMALL )?LETTER (?:SMALL CAPITAL )?([A-Z])$")


def _art_char(ch: str) -> str | None:
    """Ký tự «font nghệ thuật» → chữ thường ASCII (None = không phải)."""
    k = unicodedata.normalize("NFKC", ch)
    if k != ch and k.isascii() and k.isalnum():
        return k.lower()
    if ord(ch) > 0x024F:        # chừa chữ Việt / Latin mở rộng thật
        try:
            m = _LETTER_NAME.search(unicodedata.name(ch))
        except ValueError:
            m = None
        if m:
            return m.group(1).lower()
    return None


def _clean_chars(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == "\r" or ch in _INVISIBLE or ch in _OVERLAY_MARKS or ch in _VARIATION:
            continue
        if ch in _SPACES or ch == "_":
            out.append(" ")
            continue
        if ord(ch) < 128 or ch == "…":
            out.append(ch)
            continue
        art = _art_char(ch)
        if art is not None:
            out.append(art)
            continue
        k = unicodedata.normalize("NFKC", ch)
        if k != ch and len(k) == 1 and k in _ASCII_PUNCT_KEEP:   # ，！？ toàn khổ
            out.append(k)
            continue
        cat = unicodedata.category(ch)
        # emoji, ký hiệu trang trí (♪ ❤ ★ ●), dấu bao, ký tự định dạng/riêng tư
        if cat in ("So", "Cs", "Co", "Cf", "Me", "Cn") or (cat == "Sk" and ch != "´"):
            continue
        if ch in "•‣◦⁃∙·▪":
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


# ───────────────────────────── nhãn đoạn ─────────────────────────────
_LABEL = (r"(?:pre[\s-]?chorus|post[\s-]?chorus|intro|verse|chorus|hook|bridge|rap|outro|"
          r"interlude|instrumental|refrain|breakdown|solo|coda|"
          r"tiền\s+điệp\s+khúc|điệp\s+khúc|phiên\s+khúc|"
          r"nhạc\s+dạo(?:\s+(?:đầu|giữa|cuối))?|đoạn\s+kết|kết\s+bài|kết|"
          r"đk|dk|pk|lời\s*\d+|lời\s+(?:một|hai|ba|bốn))")
_LABEL_HEAD = re.compile(r"^" + _LABEL + r"(?!\w)", re.I)
_ABBREV_HEAD = re.compile(r"^(?:đk|dk|pk)(?!\w)", re.I)
# phần đuôi nhãn: số, số La Mã, x2 / (x2) / 2 lần, dấu : - . –
_LABEL_TAIL = re.compile(
    r"(?:\s*(?:\d+|[ivx]{1,4}(?!\w)|[x×]\s*\d+|\(\s*[x×]?\s*\d+\s*(?:lần)?\s*\)|"
    r"\d+\s*lần|lần\s*\d+|[:：.\-–—]))*\s*", re.I)
_BRACKETED = re.compile(r"^\[\s*([^\]]*?)\s*\]\s*(.*)$")
_PARENS = re.compile(r"^[(\{]\s*([^)\}]*?)\s*[)\}]\s*(.*)$")


_REPEAT_ONLY = re.compile(
    r"^[\[(]?\s*(?:[x×]\s*\d+|\d+\s*lần|lặp\s+lại(?:\s*\d+\s*lần)?|repeat(?:\s*[x×]?\s*\d+)?)"
    r"\s*[\])]?\s*[:.]?$", re.I)


def _split_label(line: str) -> tuple[bool, str]:
    """(dòng có nhãn?, phần lời còn lại sau khi bỏ nhãn)."""
    s = line.strip()
    if _REPEAT_ONLY.match(s):                # dòng chỉ ghi «(x2)» / «2 lần» — không phải lời
        return True, ""
    m = _BRACKETED.match(s)                  # [Chorus] / [Verse 1: Ca sĩ] / [ĐK] lời…
    if m and _LABEL_HEAD.match(m.group(1)):
        return True, m.group(2)
    m = _PARENS.match(s)                     # (ĐK) lời… — trong ngoặc phải CHỈ là nhãn
    if m:
        head = _LABEL_HEAD.match(m.group(1))
        if head and _LABEL_TAIL.fullmatch(m.group(1)[head.end():]):
            return True, m.group(2)
    head = _LABEL_HEAD.match(s)
    if not head:
        return False, s
    rest = s[head.end():]
    if _LABEL_TAIL.fullmatch(rest):          # cả dòng chỉ là nhãn
        return True, ""
    m = re.match(r"^\s*(?:\d+|[ivx]{1,4})?\s*(?:\(\s*[x×]?\s*\d+\s*\))?\s*[:：]\s*(.+)$",
                 rest, re.I)
    if m:                                    # «ĐK: Đừng buồn em ơi»
        return True, m.group(1)
    if _ABBREV_HEAD.match(s):                # «ĐK. lời» / «ĐK lời» (viết tắt, không phải chữ)
        m = re.match(r"^\s*\d*\s*[.\-–)]?\s+(\S.*)$", rest)
        if m:
            return True, m.group(1)
    return False, s


# ───────────────────────────── dấu câu ─────────────────────────────
_PUNCT_RUN = re.compile(r"\s*([,.!?…](?:\s*[,.!?…])*)")
_CLOSERS = set(")]}\"'”’»")


def _merge_run(run: str) -> str:
    r = re.sub(r"\s+", "", run)
    ellipsis = "…" in r or "..." in r
    qe = "".join(dict.fromkeys(c for c in r if c in "?!"))
    if qe:
        return ("…" if ellipsis else "") + qe
    if ellipsis:
        return "…"
    return "." if "." in r else ","


def _fix_punct(line: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _PUNCT_RUN.finditer(line):
        start, end = m.start(), m.end()
        before, after = line[start - 1:start] if start else "", line[end:end + 1]
        if m.group(1) in ",." and before.isdigit() and after.isdigit():
            continue                            # 1.000 / 2,5 — giữ nguyên
        out.append(line[pos:start])
        prefix = "".join(out).rstrip()
        out = [prefix, _merge_run(m.group(1))]
        rest = line[end:]
        if rest.strip() and rest.lstrip()[0] not in _CLOSERS:
            out.append(" ")
        pos = end
        while pos < len(line) and line[pos] == " ":
            pos += 1
    out.append(line[pos:])
    return "".join(out)


def _clean_line(line: str) -> str:
    s = re.sub(r"[*#]+", "", line)                       # **đậm**, # tiêu đề
    s = re.sub(r"^\s*(?:>+|[-–—]+)(?:\s+|$)", "", s)      # > trích dẫn, - gạch đầu dòng
    s = re.sub(r"^\s*[-–—]{2,}\s*$", "", s)               # --- đường kẻ
    s = re.sub(r"[ ]{2,}", " ", s).strip()
    s = _fix_punct(s)
    return re.sub(r"[ ]{2,}", " ", s).strip()


# ───────────────────────────── ngắt dòng ─────────────────────────────
def _words(s: str) -> int:
    return sum(1 for t in s.split() if re.search(r"\w", t))


def _split_long(line: str) -> list[str]:
    total = _words(line)
    if total <= MAX_SYLLABLES:
        return [line]
    best = None
    for m in re.finditer(r",(?= )", line):
        left, right = line[:m.start()].strip(), line[m.end():].strip()
        lw, rw = _words(left), _words(right)
        if lw < MIN_SPLIT_SIDE or rw < MIN_SPLIT_SIDE:
            continue
        score = abs(lw - total / 2)
        if best is None or score < best[0]:
            best = (score, left, right)
    if best is None:
        return [line]
    return _split_long(best[1]) + _split_long(best[2])


# ───────────────────────────── chính ─────────────────────────────
def normalize_for_suno(raw: str) -> str:
    """Lời thô → lời chuẩn cho ô Lyrics của Suno (văn bản thuần)."""
    text = strip_lyrics_header(_clean_chars(raw or "")).strip("\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[*#]+", "", line).strip()
        line = re.sub(r"^(?:>+|[-–—•]+)\s+", "", line)
        is_label, rest = _split_label(line)
        if is_label:
            lines.append("")                   # nhãn → ranh giới đoạn
            line = rest
        line = _clean_line(line)
        if not re.search(r"\w", line):         # dòng trống / chỉ còn dấu câu
            if not line.strip() or not is_label:
                lines.append("")
            continue
        lines.extend(_split_long(line))
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
