"""cover/song_match.py — So khớp «Tên bài - Ca sĩ» (dòng trong file docx/txt) với
kết quả tìm kiếm của MỌI nguồn: NhacCuaTui, Zing MP3, YouTube Music, YouTube và
bước tìm lời (Zing).

Mục tiêu: chọn bài ĐÚNG TÊN + ĐÚNG CA SĨ trước. Thứ hạng (cao → thấp):

* EXACT            đúng tên + đúng ca sĩ, bản gốc (official)
* EXACT_VARIANT    đúng tên + đúng ca sĩ nhưng là Remix/Cover/Live… của chính ca sĩ
* TITLE_ONLY       đúng tên, KHÁC ca sĩ (bản gốc) — chỉ dùng khi không còn gì hơn,
                   kèm cảnh báo cho người dùng
* TITLE_ONLY_VARIANT  đúng tên, khác ca sĩ, lại là biến thể

Karaoke/Beat/Instrumental (không có giọng hát) KHÔNG bao giờ được chọn.
Dòng không ghi ca sĩ: đúng tên bài là EXACT.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from . import nct

NONE, TITLE_ONLY_VARIANT, TITLE_ONLY, EXACT_VARIANT, EXACT = range(5)

TIER_LABEL = {
    EXACT: "đúng tên bài + ca sĩ",
    EXACT_VARIANT: "đúng tên bài + ca sĩ (bản Remix/Cover của chính ca sĩ)",
    TITLE_ONLY: "đúng tên bài nhưng KHÁC ca sĩ",
    TITLE_ONLY_VARIANT: "đúng tên bài nhưng khác ca sĩ và là bản biến thể",
    NONE: "không khớp",
}

# Chữ «trang trí» hay gặp trên YouTube/Zing, không phải một phần tên bài.
_NOISE = frozenset((
    "official", "music", "video", "mv", "m", "v", "audio", "lyric", "lyrics",
    "hd", "4k", "full", "visualizer", "topic", "vietsub", "ost", "nhac", "phim",
    "bai", "hat", "loi", "the", "with", "ft", "feat", "featuring", "x", "va",
    "official audio", "single", "teaser", "fmv", "pmv", "version",
))
# Bản không có giọng hát → không dùng làm nguồn cover.
_NO_VOCAL_RE = re.compile(r"\b(?:karaoke|kara|instrumental|beat|backing\s*track|"
                          r"minus\s*one|nhac\s*nen)\b")
_ARTIST_SEP = re.compile(
    r"\s*(?:,|;|&|\+|/|\bft\b\.?|\bfeat\b\.?|\bfeaturing\b|\bx\b|\bvs\b\.?|"
    r"\bwith\b|\bvà\b|\band\b)\s*", re.I)
_BRACKET_RE = re.compile(r"[\(\[\{【]([^\)\]\}】]*)[\)\]\}】]")
_BRACKET_NOISE_RE = re.compile(
    r"\b(?:official|mv|m/v|audio|lyrics?|video|visuali[sz]er|vietsub|hd|4k|"
    r"ft\.?|feat\.?|featuring|prod\.?|ost|from)\b", re.I)


@dataclass(frozen=True)
class Match:
    tier: int
    score: float          # tỉ lệ ca sĩ yêu cầu tìm thấy + điểm phụ để xếp hạng

    @property
    def label(self) -> str:
        return TIER_LABEL[self.tier]


def split_artists(text: str) -> list[str]:
    """«Hngle x Bảo Anh», «A ft. B», «A, B» → ['hngle', 'bao anh'] (đã bỏ dấu)."""
    out: list[str] = []
    for part in _ARTIST_SEP.split(text or ""):
        f = nct.fold(part)
        if f and f not in out:
            out.append(f)
    return out


def request_options(query: str) -> list[tuple[str, str]]:
    """Các cách hiểu dòng người dùng: (tên bài, ca sĩ). Mặc định «Tên - Ca sĩ»;
    cũng thử chiều ngược «Ca sĩ - Tên» (xếp sau)."""
    parts = nct.split_query(nct.official_query(query))
    if not parts:
        return []
    if len(parts) == 1:
        return [(parts[0], "")]
    opts = [(parts[0], ", ".join(parts[1:]))]
    rev = (parts[-1], ", ".join(parts[:-1]))
    if rev != opts[0]:
        opts.append(rev)
    return opts


def _core_title(title: str) -> str:
    """Bỏ phần ngoặc chỉ là trang trí: (Official MV), [Lyrics], (feat. X)…
    Phần ngoặc khác (Remix, Tropical…) giữ lại để không khớp nhầm bản gốc."""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        return " " if _BRACKET_NOISE_RE.search(inner) else f" {inner} "
    return nct.fold(_BRACKET_RE.sub(repl, title or ""))


def _title_rest(req_title: str, title: str, artist_phrases: list[str]) -> list[str] | None:
    """None = KHÔNG cùng tên bài. Ngược lại trả các chữ thừa trong tiêu đề (tên ca
    sĩ YouTube/Zing hay ghi kèm: «Ca sĩ - Tên bài | Official MV»)."""
    req = nct.fold(req_title)
    # Nhãn biến thể («(Đại Mèo Remix)», «Cover») được tính riêng ở evaluate().
    core = _core_title(nct.official_query(title))
    if not req or not core:
        return None
    if core == req:
        return []
    # Sai chính tả nhẹ («Nắm Tay Nhau» / «Nắm Tay Nhaư») nhưng KHÔNG nhận tên
    # dài/ngắn hơn hẳn (bài khác cùng chữ đầu).
    if abs(len(core) - len(req)) <= 2 and len(req) >= 8 and             difflib.SequenceMatcher(None, core, req).ratio() >= 0.93:
        return []
    if f" {req} " not in f" {core} ":
        return None
    rest = [t for t in f" {core} ".replace(f" {req} ", " ", 1).split() if t not in _NOISE]
    used: list[str] = []
    # Chỉ bỏ TRỌN tên ca sĩ (đủ mọi chữ), không bỏ lẻ 1 chữ — để «Anh Tìm Em»
    # không lọt chỉ vì ca sĩ yêu cầu là «Bảo Anh».
    for phrase in sorted(set(artist_phrases), key=len, reverse=True):
        words = phrase.split()
        if words and all(rest.count(w) >= words.count(w) for w in words):
            for w in words:
                rest.remove(w)
            used.extend(words)
    return used if not rest else None


def _bracket_tokens(title: str) -> set[str]:
    """Chữ trong ngoặc của tiêu đề, vd «(feat. Bảo Anh)» → {feat, bao, anh}."""
    return {t for m in _BRACKET_RE.finditer(title or "") for t in nct.fold(m.group(1)).split()}


def _artists_found(req_artists: list[str], pool: set[str], hay: str) -> int:
    """Đếm ca sĩ yêu cầu có mặt: nguyên cụm tên trong ô ca sĩ/kênh, hoặc đủ mọi
    chữ của tên trong «kho chữ ca sĩ» (ô ca sĩ, kênh, tên ghi kèm tiêu đề, ngoặc
    feat.) — không ghép chữ lấy từ chính tên bài."""
    found = 0
    for a in req_artists:
        if f" {a} " in f" {hay} " or all(t in pool for t in a.split()):
            found += 1
    return found


def evaluate(query: str, title: str, artist: str = "", extra: str = "") -> Match:
    """Chấm 1 kết quả. ``extra`` = chữ phụ (kênh YouTube, người đăng…)."""
    if not (title or "").strip():
        return Match(NONE, 0.0)
    no_vocal = _NO_VOCAL_RE.search(nct.fold(title))
    if no_vocal and not _NO_VOCAL_RE.search(nct.fold(query)):
        return Match(NONE, 0.0)       # trừ khi chính tên bài có chữ đó («Beat It»)
    variant = nct.is_variant_title(title)
    hay = " ".join(x for x in (nct.fold(artist), nct.fold(extra)) if x)
    base_pool = set(hay.split()) | _bracket_tokens(title)
    result_artists = split_artists(artist) + split_artists(extra)
    best = Match(NONE, 0.0)
    for n, (req_title, req_artist) in enumerate(request_options(query)):
        req_artists = split_artists(req_artist)
        rest = _title_rest(req_title, title, req_artists + result_artists)
        if rest is not None:
            found = _artists_found(req_artists, base_pool | set(rest), hay)                 if req_artists else 0
            if n and not found:
                continue              # đọc ngược «Ca sĩ - Tên» chỉ khi khớp ca sĩ
            artist_ok = not req_artists or found > 0
        elif not req_artists and _single_line_title_artist(req_title, title, artist):
            found, artist_ok = 0, True    # dòng gõ thiếu dấu «-»: «Tên bài Ca sĩ»
        else:
            continue
        if artist_ok:
            tier = EXACT_VARIANT if variant else EXACT
        else:
            tier = TITLE_ONLY_VARIANT if variant else TITLE_ONLY
        score = (found / len(req_artists)) if req_artists else 1.0
        if variant:
            score += nct.variant_fallback_bonus(query, title, artist)
        score -= 0.05 * n             # chiều «Ca sĩ - Tên» xếp sau
        cand = Match(tier, round(score, 3))
        if (cand.tier, cand.score) > (best.tier, best.score):
            best = cand
    return best


def _single_line_title_artist(req: str, title: str, artist: str) -> bool:
    """«Sao Mình Chưa Nắm Tay Nhau Yan Nguyễn» (không có «-»): tên bài của kết quả
    nằm trong dòng và phần còn lại đúng là ca sĩ của kết quả."""
    fr, ft, fa = nct.fold(req), _core_title(title), nct.fold(artist)
    if not ft or not fa or f" {ft} " not in f" {fr} ":
        return False
    rest = f" {fr} ".replace(f" {ft} ", " ", 1).split()
    return bool(rest) and set(rest) <= set(fa.split())


def rank(query: str, rows: list[dict], min_tier: int = TITLE_ONLY_VARIANT
         ) -> list[tuple[dict, Match]]:
    """Kết quả khớp, tốt nhất trước (thứ tự gốc của trang dùng để phân hạng hoà)."""
    scored = []
    for i, row in enumerate(rows or []):
        m = evaluate(query, row.get("title", ""), row.get("artist", ""),
                     row.get("uploader", "") or row.get("channel", ""))
        if m.tier >= min_tier:
            scored.append((m.tier, m.score, -i, row, m))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return [(row, m) for *_, row, m in scored]


def choose(query: str, rows: list[dict], min_tier: int = TITLE_ONLY_VARIANT
           ) -> tuple[dict | None, Match]:
    ranked = rank(query, rows, min_tier)
    return ranked[0] if ranked else (None, Match(NONE, 0.0))


def describe(query: str) -> str:
    opts = request_options(query)
    if not opts:
        return query
    title, artist = opts[0]
    return f"{title} - {artist}" if artist else title
