"""
cover/lyrics_web.py — Tìm và đối chiếu lời trên Zing MP3, LRCLIB và Google.

Cách làm (dùng chung trình duyệt của hàng đợi nhaccuatui, mở tab riêng):
1. Zing MP3: tìm đúng tên + ca sĩ → dấu ba chấm/Khác → «Lời bài hát».
2. Google «<tên bài> <ca sĩ> lyrics». Google thường hiện sẵn khung «Lời bài hát»
   (nguồn LyricFind/Musixmatch) → lấy thẳng từ đó, sạch nhất.
3. Không có khung đó → mở lần lượt vài trang lời bài hát trong kết quả, lấy khối
   chữ trông giống lời nhất, rồi:
   - có API AI (Cài đặt) → nhờ AI giữ lại ĐÚNG phần lời (và xác nhận đúng bài);
   - không có → lọc bằng quy tắc.
4. Giữ nhãn cấu trúc trong ngoặc vuông ([Verse], [Chorus], [Outro]…), nhưng bỏ
   hợp âm [Am], ký hiệu lặp và dòng nguồn/nhạc sĩ/bản quyền.

Google hiện captcha («unusual traffic») → tool KHÔNG giải, chuyển sang Bing để lấy
danh sách trang.
"""
from __future__ import annotations

import difflib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from backend.video.browser_base import OperationCancelled, query_first, wait_ms

from .nct import fold, pick_best

GOOGLE_URL = "https://www.google.com/search?hl=vi&q={q}"
BING_URL = "https://www.bing.com/search?setlang=vi&q={q}"
MIN_LINES = 8
MAX_PAGES = 4
MAX_AI_CHARS = 9000
LRCLIB_SEARCH = "https://lrclib.net/api/search"
ZING_SEARCH_URL = "https://zingmp3.vn/tim-kiem/tat-ca?q={q}"

# Trang lời bài hát hay gặp — mở trước. Trang khác vẫn thử nếu còn lượt.
PREFERRED_SITES = (
    "lyricvn.com", "lyricsvn.com", "loibaihat.me", "cainhaccho.net", "hibro.vn",
    "hopamguitar.com.vn", "highresolutionmusic.com", "loibaihat", "lyrics", "lyric",
    "hopamviet.vn", "hopamchuan.com", "sonnystudio.com",
    "vietjack", "hoatieu.vn", "chiasenhac.vn", "genius.com", "azlyrics.com",
    "letras.", "musixmatch.com", "lyricstranslate.com", "kkbox.com",
)
# Trang không có lời dạng chữ / chặn máy / chính là nhaccuatui.
SKIP_SITES = (
    "google.", "youtube.com", "youtu.be", "nhaccuatui.com", "zingmp3.vn", "facebook.com",
    "tiktok.com", "spotify.com", "apple.com", "instagram.com", "twitter.com", "x.com/",
    "soundcloud.com", "lyrics.lyricfind.com/", "wikipedia.org", "shazam.com", "deezer.com",
    "bing.com", "microsoft.com", "pinterest.", "threads.net",
)

_CAPTCHA_RE = re.compile(r"unusual traffic|lưu lượng truy cập bất thường|không phải là rô ?bốt|"
                         r"not a robot|recaptcha", re.I)

# ── Lọc chữ ───────────────────────────────────────────────────────
_LABEL_WORDS = (
    r"pre[\s-]?chorus|post[\s-]?chorus|chorus|verse|hook|bridge|intro|outro|interlude|"
    r"instrumental|refrain|rap|coda|break|drop|solo|repeat|"
    r"điệp\s*khúc|đk|dk|phiên\s*khúc|pk|đoạn\s*\d*|lời\s*\d+|lời\s*rap|nhạc\s*dạo|"
    r"dạo\s*nhạc|kết|vào\s*bài|lặp\s*lại|hát\s*lại|nam|nữ|song\s*ca|tất\s*cả|all|"
    r"male|female|both"
)
_OPEN, _CLOSE = r"[\[\(\{]", r"[\]\)\}]"
# Cả dòng chỉ là nhãn: «[Chorus]», «[Verse 2: Thành Đạt]», «(Điệp khúc x2)», «ĐK:»,
# «Verse 1:», «*ĐK*», «Rap», «Chorus (Mỹ Tâm):». Không ngoặc thì phải đúng nhãn
# (+ số / ×2 / tên trong ngoặc) → không xoá nhầm câu «Nam ơi em đi…».
_LABEL_LINE_RE = re.compile(
    rf"^[\s\*\-–—#>•]*(?:{_OPEN}\s*(?:{_LABEL_WORDS})\b[^\]\)\}}\n]{{0,40}}{_CLOSE}"
    rf"|(?:{_LABEL_WORDS})\s*\d*\s*(?:[x×]\s*\d)?\s*(?:\([^)\n]{{0,30}}\))?)"
    rf"\s*[:：.\-–]?[\s\*]*$", re.I)
# Nhãn đứng đầu câu: «ĐK: Anh đợi em…», «[Chorus] Anh đợi em…» → «Anh đợi em…»
_LABEL_PREFIX_RE = re.compile(
    rf"^[\s\*\-–—#>•]*(?:{_OPEN}\s*(?:{_LABEL_WORDS})\b[^\]\)\}}\n]{{0,40}}{_CLOSE}\s*[:：]?"
    rf"|(?:{_LABEL_WORDS})\s*\d*\s*[:：])\s*", re.I)
# Nhãn cấu trúc cần giữ trong dữ liệu lời gốc trước bước chuẩn hóa Suno. Chỉ
# nhận đúng tên đoạn đã biết nên
# không nhầm hợp âm [Am], [C#m7] hoặc một câu hát đặt trong ngoặc vuông.
_SECTION_LABEL_RE = re.compile(
    rf"^\s*\[\s*((?:{_LABEL_WORDS})\b[^\]\n]{{0,40}})\s*\]\s*$", re.I)
# Dòng chỉ là [ … ] (nhãn kiểu Suno/Genius) hoặc toàn ký hiệu.
_BRACKET_LINE_RE = re.compile(r"^\s*\[[^\]]{0,60}\]\s*$")
_SYMBOL_LINE_RE = re.compile(r"^[\W_]+$")
# Lặp lại cuối câu: «x2», «(x2)», «[x3]», «×2», «2x», «(2 lần)»
_REPEAT_RE = re.compile(
    r"\s*(?:[\[\(]\s*(?:[x×]\s*\d|\d\s*[x×]|\d\s*lần)\s*[\]\)]|\b[x×]\s*\d|\b\d\s*[x×])\s*$", re.I)
# Hợp âm: [Am] [C#m7] [G/B] [Dsus4] — kiểu hợp âm Việt (hopamviet/hopamchuan).
_CHORD_RE = re.compile(
    r"\[\s*[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:sus\d|add\d|maj\d)?"
    r"(?:/[A-G](?:#|b)?)?\s*\]")
# Dòng thông tin: «Nhạc sĩ: …», «Sáng tác: …», «Nguồn tin: LyricFind» (phải có «:»)
_META_RE = re.compile(
    r"^\s*(?:nguồn(?:\s*tin)?|source|nhạc\s*sĩ|sáng\s*tác|tác\s*giả|ca\s*sĩ|trình\s*bày|"
    r"thể\s*hiện|composers?|songwriters?|writers?|lời(?:\s*bài\s*hát)?|bài\s*hát|album|"
    r"hợp\s*âm|tone|điệu|nhịp|capo|đăng\s*bởi|cập\s*nhật|lượt\s*xem|lyrics?|"
    r"producers?|released?|phát\s*hành|thể\s*loại|genre)\s*[:：]", re.I)
# Dòng chữ của trang web (cả dòng): «Xem thêm», «12 Contributors», «Embed»…
_UI_LINE_RE = re.compile(
    r"^\s*(?:xem\s*thêm|thu\s*gọn|chia\s*sẻ|tải\s*về|tải\s*xuống|embed|share|"
    r"you\s+might\s+also\s+like|\d+\s+contributors?.*|translations?|"
    r"(?:written|lyrics?|composed)\s+by\b.*|see\s+.*\s+live|get\s+tickets.*)\s*$", re.I)
# Nhãn nằm giữa câu: «… lấy chồng. ==> (Dạo Nhạc) Trở lại 2.»
_INLINE_LABEL_RE = re.compile(
    rf"{_OPEN}\s*(?:{_LABEL_WORDS})\b[^\]\)\}}\n]{{0,30}}{_CLOSE}", re.I)
_ARROW_TAIL_RE = re.compile(r"\s*(?:=+>|-+>|→).*$")
# Số đoạn đầu câu: «1. Ai tìm ai…», «2) Có phải…», «1. . . . Ai tìm…»
_STANZA_NO_RE = re.compile(r"^\d{1,2}\s*[.)]\s*(?:\.\s*)*")
# Dòng của trang web, không phải câu hát (dùng khi KHÔNG có AI để cắt đầu/đuôi trang).
_PAGE_WORDS_RE = re.compile(
    r"lời bài hát|\blyrics?\b|karaoke|\bmv\b|official|video|tải về|download|nâng cấp|"
    r"quảng cáo|bài hát khác|mời (?:các )?bạn|xem thêm|chuyên mục|hợp âm|\btone\b|"
    r"\d{1,2}/\d{1,2}/\d{2,4}|\d{3,}|\d+đ\b|\bpro\b|bình luận|đăng nhập", re.I)
_COPYRIGHT_RE = re.compile(r"©|\(c\)\s|copyright|all rights reserved|bản quyền", re.I)
_URL_RE = re.compile(r"https?://|www\.", re.I)


def clean_lines(text: str, title: str = "", artist: str = "") -> str:
    """Chữ copy từ web → lời sạch, giữ nhãn [Verse/Chorus/…].

    Bỏ hợp âm, ×2, nguồn/nhạc sĩ/bản quyền và tên bài ở đầu; giữ một dòng trống
    giữa các đoạn để cấu trúc gốc còn nguyên trước bước chuẩn hóa Suno.
    """
    head_keys = {fold(x) for x in (title, f"{title} - {artist}", f"{title} – {artist}",
                                   f"lời bài hát {title}", f"{title} lyrics") if x.strip()}
    out: list[str] = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = _CHORD_RE.sub("", raw).replace(" ", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        section = _SECTION_LABEL_RE.match(line)
        if section:
            label = re.sub(r"\s+", " ", section.group(1)).strip()
            out.append(f"[{label}]")
            continue
        if (_LABEL_LINE_RE.match(line) or _BRACKET_LINE_RE.match(line)
                or _SYMBOL_LINE_RE.match(line) or _META_RE.match(line)
                or _UI_LINE_RE.match(line) or _COPYRIGHT_RE.search(line) or _URL_RE.search(line)):
            continue
        if not any(x for x in out) and (fold(line) in head_keys or (
                fold(title) and fold(title) in fold(line) and len(line) <= len(title) + 40
                and re.search(r"lời|lyrics|-|–|\|", line, re.I))):
            continue           # dòng tên bài ở đầu trang («Lời bài hát X – Y»)
        line = _ARROW_TAIL_RE.sub("", _INLINE_LABEL_RE.sub("", line)).strip()
        line = _STANZA_NO_RE.sub("", line).strip()
        if not line or line.endswith(":"):
            continue
        line = _LABEL_PREFIX_RE.sub("", line)
        line = _REPEAT_RE.sub("", line).strip()
        line = re.sub(r"^[\*•\-–—]+\s*|\s*[\*]+$", "", line).strip()
        if line and not _REPEAT_RE.fullmatch(line):
            out.append(line)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _lyric_like(line: str) -> bool:
    n = len(line.split())
    return 2 <= n <= 22 and len(line) <= 110 and not _PAGE_WORDS_RE.search(line)


def core_block(text: str) -> str:
    """Không có AI: trang web còn dính tiêu đề/quảng cáo/«bài hát khác» ở đầu và
    cuối → giữ khối câu hát liền nhau dài nhất (cho phép lẫn 1 dòng lạ giữa chừng)."""
    lines = (text or "").split("\n")
    blocks: list[list[int]] = []     # mỗi khối: chỉ số các câu giống lời
    cur: list[int] = []
    bad_run = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        if _lyric_like(ln):
            cur.append(i)
            bad_run = 0
            continue
        bad_run += 1
        if bad_run > 1 and cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    if not blocks:
        return ""
    best = max(blocks, key=len)
    keep = [ln for ln in lines[best[0]:best[-1] + 1] if not ln.strip() or _lyric_like(ln)]
    return "\n".join(keep).strip()


def line_count(text: str) -> int:
    return sum(1 for ln in (text or "").splitlines() if ln.strip())


@dataclass(frozen=True)
class LyricsCandidate:
    lyrics: str
    source: str
    priority: int = 0


def _fingerprint(text: str) -> tuple[str, set[str]]:
    """Dấu vân tay chỉ dùng đối chiếu; không thay đổi lyrics được lưu."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip() or _SECTION_LABEL_RE.match(line.strip()):
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", fold(line)).strip()
        if normalized:
            lines.append(normalized)
    return " ".join(lines), set(lines)


def lyrics_similarity(left: str, right: str) -> float:
    """So khớp cả thứ tự câu lẫn tập câu để chịu được chorus bị lặp/không lặp."""
    a, aset = _fingerprint(left)
    b, bset = _fingerprint(right)
    if not a or not b:
        return 0.0
    sequence = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    union = aset | bset
    jaccard = len(aset & bset) / len(union) if union else 0.0
    return max(sequence, jaccard)


def choose_consensus(candidates: list[LyricsCandidate], threshold: float = 0.86
                     ) -> tuple[LyricsCandidate | None, list[str]]:
    """Chỉ duyệt khi ít nhất hai nguồn độc lập khớp nhau."""
    best: LyricsCandidate | None = None
    best_sources: list[str] = []
    best_rank = (-1, -1, -1)
    for candidate in candidates:
        agreeing = [other for other in candidates
                    if other.source != candidate.source
                    and lyrics_similarity(candidate.lyrics, other.lyrics) >= threshold]
        sources = [candidate.source, *[x.source for x in agreeing]]
        # Nhiều nguồn thắng; sau đó ưu tiên nguồn metadata rõ; cuối cùng lời dài hơn.
        rank = (len(set(sources)), candidate.priority, line_count(candidate.lyrics))
        if rank > best_rank:
            best, best_sources, best_rank = candidate, list(dict.fromkeys(sources)), rank
    if best is None or best_rank[0] < 2:
        return None, []
    return best, best_sources


def _plain_lrc(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines():
        line = re.sub(r"^(?:\[\d{1,3}:\d{2}(?:\.\d{1,3})?\])+", "", raw).strip()
        if line and not re.match(r"^\[(?:ar|al|ti|by|offset|length):", line, re.I):
            lines.append(line)
    return "\n".join(lines)


def lrclib_candidate(title: str, artist: str) -> LyricsCandidate | None:
    """Nguồn API có metadata rõ; không dùng kết quả nếu tên bài/ca sĩ không khớp."""
    if not title.strip() or not artist.strip():
        return None
    params = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
    request = urllib.request.Request(
        f"{LRCLIB_SEARCH}?{params}",
        headers={"User-Agent": "CoverNhac/1.0 (local lyrics verification)"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    target_title, target_artist = fold(title), fold(artist)
    matches = []
    for row in rows if isinstance(rows, list) else []:
        row_title = fold(str(row.get("trackName") or row.get("name") or ""))
        row_artist = fold(str(row.get("artistName") or ""))
        title_score = difflib.SequenceMatcher(None, target_title, row_title).ratio()
        artist_score = difflib.SequenceMatcher(None, target_artist, row_artist).ratio()
        if title_score < 0.92 or artist_score < 0.72 or row.get("instrumental"):
            continue
        raw = row.get("plainLyrics") or _plain_lrc(row.get("syncedLyrics") or "")
        lyrics = clean_lines(raw, title, artist)
        if line_count(lyrics) >= MIN_LINES:
            matches.append((title_score + artist_score, lyrics, row.get("id")))
    if not matches:
        return None
    _, lyrics, record_id = max(matches, key=lambda item: item[0])
    return LyricsCandidate(lyrics, f"LRCLIB #{record_id} (khớp tên + ca sĩ)", 30)


# ── Zing MP3 (giao diện: kết quả → dấu ba chấm → Lời bài hát) ──────────────
_JS_ZING_RESULTS = r"""() => {
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  // Nút «…» của ALBUM/PLAYLIST (cột trái trang album: chỉ có Thêm vào danh sách
  // phát / Sao chép link / Chia sẻ) và của thẻ album/nghệ sĩ KHÔNG có mục
  // «Lời bài hát» → không bao giờ dùng.
  const NOT_SONG = '.playlist-header,.header-thumbnail,.zm-card,.zm-carousel-item';
  const out = [];
  const used = new Set();
  // 1) Dòng bài hát chuẩn của Zing: nút «…» ở CỘT PHẢI (.media-right), chỉ hiện
  //    khi rê chuột vào dòng → không lọc theo visible; Python sẽ hover trước khi bấm.
  for (const row of document.querySelectorAll('.media-item,.song-item,.list-item')) {
    if (row.closest(NOT_SONG) || !visible(row)) continue;
    if ([...used].some(r => r.contains(row) || row.contains(r))) continue;
    const icon = row.querySelector(
      '.media-right .ic-more,.media-right [class*="ellipsis"],.hover-items .ic-more');
    if (!icon) continue;
    const button = icon.closest('button,[role="button"]') || icon;
    const i = out.length;
    const titleEl = row.querySelector('.item-title,.title,.song-title,a[href*="/bai-hat/"]');
    const artistEl = row.querySelector('.subtitle,.artist,.artists,[class*="artist"]');
    const lines = (row.innerText || '').split('\n').map(x => x.trim()).filter(Boolean);
    const link = row.querySelector('a[href*="/bai-hat/"]');
    button.setAttribute('data-tool-zing-more', String(i));
    row.setAttribute('data-tool-zing-row', String(i));
    used.add(row);
    out.push({index: i, title: ((titleEl && titleEl.innerText) || lines[0] || '').trim(),
      artist: ((artistEl && artistEl.innerText) || lines[1] || '').trim(),
      url: link ? link.href : '', hidden: false, song_row: true});
  }
  // 2) Giao diện khác (dự phòng): mọi nút «…» đang hiện, trừ nút album/thẻ và
  //    nút nằm trong dòng bài đã lấy ở bước 1 (vd ba chấm ẩn trên ảnh bìa nhỏ).
  const raw = [...document.querySelectorAll(
    'button,[role="button"],.more,.ic-more,[class*="ellipsis"],[class*="more"]')];
  const buttons = [...new Set(raw.map(el => el.closest('button,[role="button"]') || el))]
    .filter(el => {
      if (el.closest(NOT_SONG) || [...used].some(r => r.contains(el))) return false;
      const label = (el.getAttribute('aria-label') || el.getAttribute('title')
        || el.getAttribute('data-title') || el.innerText || '').trim();
      return visible(el) && (/^(khác|more|options?|\.\.\.)$/i.test(label)
        || !!el.querySelector('.ic-more,[class*="ellipsis"]'));
    });
  buttons.forEach(button => {
    const i = out.length;
    let row = button;
    for (let n = 0; n < 7 && row && row.parentElement; n++, row = row.parentElement) {
      const text = (row.innerText || '').trim();
      if (text.split('\n').filter(Boolean).length >= 2 && text.length < 500) break;
    }
    if (!row) return;
    const titleEl = row.querySelector('.title,.song-title,[class*="title"] a,a[href*="/bai-hat/"]');
    const artistEl = row.querySelector('.subtitle,.artist,.artists,[class*="artist"]');
    const lines = (row.innerText || '').split('\n').map(x => x.trim()).filter(Boolean)
      .filter(x => !/^(premium|vip|khác|more|\.\.\.)$/i.test(x));
    const title = ((titleEl && titleEl.innerText) || lines[0] || '').trim();
    const artist = ((artistEl && artistEl.innerText) || lines[1] || '').trim();
    button.setAttribute('data-tool-zing-more', String(i));
    row.setAttribute('data-tool-zing-row', String(i));
    const link = row.querySelector('a[href*="/bai-hat/"]');
    out.push({index: i, title, artist, url: link ? link.href : '',
      hidden: button.classList.contains('is-hidden'), song_row: false});
  });
  return out;
}"""

_JS_ZING_LYRICS = r"""() => {
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const selectors = [
    '[role="dialog"] textarea', '.modal textarea', '[class*="modal"] textarea',
    '[role="dialog"] .lyrics', '[role="dialog"] [class*="lyric"]',
    '.modal .lyrics', '.modal [class*="lyric"]', '.lyrics-content',
    '.lyric-content', '.lyrics', '[class*="lyrics"]'
  ];
  let best = '';
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue;
      const text = (el.value || el.innerText || '').trim();
      const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
      if (lines.length >= 8 && text.length > best.length) best = text;
    }
  }
  if (!best) {
    for (const dialog of document.querySelectorAll('[role="dialog"],.modal,[class*="modal"]')) {
      if (!visible(dialog) || !/lời\s*bài\s*hát|lyrics/i.test(dialog.innerText || '')) continue;
      const text = (dialog.innerText || '').replace(/^\s*(lời\s*bài\s*hát|lyrics)\s*/i, '').trim();
      if (text.split('\n').filter(Boolean).length >= 8 && text.length > best.length) best = text;
    }
  }
  return best;
}"""

_JS_DISMISS_ZING_POPUPS = r"""() => {
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const lyricText = /lời\s*bài\s*hát|lyrics/i;
  let closed = 0;
  const selectors = [
    '[role="dialog"] button', '.modal button', '[class*="modal"] button',
    '[class*="popup"] button', '[class*="overlay"] button',
    'button[aria-label*="close" i]', 'button[title*="close" i]',
    'button[aria-label*="đóng" i]', 'button[title*="đóng" i]'
  ];
  for (const button of document.querySelectorAll(selectors.join(','))) {
    if (!visible(button)) continue;
    const container = button.closest('[role="dialog"],.modal,[class*="modal"],[class*="popup"],[class*="overlay"]');
    // Không bao giờ đóng chính hộp lyrics sau khi đã mở thành công.
    if (container && lyricText.test(container.innerText || '')) continue;
    const label = (button.getAttribute('aria-label') || button.getAttribute('title')
      || button.innerText || '').trim();
    const iconOnly = !label && button.querySelector('svg,[class*="close"],.ic-close');
    if (/^(x|×|close|đóng|bỏ qua|skip|không,? cảm ơn)$/i.test(label) || iconOnly) {
      try { button.click(); closed++; } catch (_) {}
    }
  }
  return closed;
}"""


def dismiss_zing_popups(page, log=print) -> int:
    """Đóng quảng cáo/đăng nhập/nâng cấp đang che giao diện, nhưng giữ hộp lyrics."""
    total = 0
    for _ in range(3):
        try:
            closed = int(page.evaluate(_JS_DISMISS_ZING_POPUPS) or 0)
        except Exception:
            break
        total += closed
        if not closed:
            break
        wait_ms(page, 250)
    if total:
        log(f"đã đóng {total} popup Zing MP3 trước khi tiếp tục.")
    return total


def _first_visible(page, selectors: list[str]):
    """query_first chỉ xét `.first`; Zing thường giữ nhiều nút ẩn trước nút thật."""
    for selector in selectors:
        try:
            found = page.locator(selector)
            for index in range(min(found.count(), 80)):
                item = found.nth(index)
                if item.is_visible():
                    return item
        except Exception:
            continue
    return None


def _zing_lyrics_menu_item(page):
    """Chọn đúng dòng chữ, tránh `text=` khớp cả khung menu cha."""
    try:
        items = page.get_by_text("Lời bài hát", exact=True)
        for index in range(min(items.count(), 20)):
            item = items.nth(index)
            if item.is_visible():
                clickable = item.locator(
                    "xpath=ancestor-or-self::*[self::button or @role='menuitem' "
                    "or contains(concat(' ',normalize-space(@class),' '),' menu-list--item ')][1]")
                return clickable if clickable.count() else item
    except Exception:
        pass
    return query_first(page, [
        "[role='menuitem']:has-text('Lời bài hát')",
        ".menu-list--item:has-text('Lời bài hát')",
    ], timeout_ms=4_000)


def _zing_detail_lyrics(page, url: str, title: str, artist: str, log=print) -> str:
    """Mở trang bài và đọc textarea lyrics; ổn định hơn menu ngay trang search."""
    if not url:
        return ""
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    wait_ms(page, 1800)
    dismiss_zing_popups(page, log=log)
    rows = page.evaluate(_JS_ZING_RESULTS) or []
    interactive = [row for row in rows if not row.get("hidden")]
    best = (pick_zing_result(title, artist, interactive)
            or pick_zing_result(title, artist, rows))
    if not best:
        log("Zing MP3: trang bài không nhận diện được đúng dòng bài hát.")
        return ""
    log(f"Zing MP3: mở menu dòng «{best.get('title')}» / {best.get('artist')} "
        f"(index {best.get('index')}).")
    row = page.locator(f'[data-tool-zing-row="{int(best["index"])}"]').first
    more = page.locator(f'[data-tool-zing-more="{int(best["index"])}"]').first
    if row.count():
        row.hover()
        wait_ms(page, 250)
    if not more.count():
        log("Zing MP3: trang bài không có nút ba chấm tương ứng.")
        return ""
    more.click(force=True)
    item = _zing_lyrics_menu_item(page)
    if item is None:
        log("Zing MP3: trang bài không hiện mục Lời bài hát sau khi bấm ba chấm.")
        return ""
    log("Zing MP3: đã bấm mục «Lời bài hát».")
    item.click(force=True)
    wait_ms(page, 1200)
    raw = page.evaluate(_JS_ZING_LYRICS) or ""
    if not raw:
        try:
            count = page.locator(".modal textarea,[role='dialog'] textarea").count()
        except Exception:
            count = 0
        log(f"Zing MP3: modal đã mở nhưng bộ đọc thấy {count} textarea và chưa có chữ.")
    return raw


def pick_zing_result(title: str, artist: str, rows: list[dict]) -> dict | None:
    """Chỉ lấy lời Zing của bài ĐÚNG TÊN + ĐÚNG CA SĨ (bài trùng tên của ca sĩ
    khác thường là bài khác hẳn). Ưu tiên official; không có thì dùng lời bản
    Remix/Cover của chính ca sĩ đó."""
    from . import song_match
    query = " - ".join(x for x in (title, artist) if x)
    row, _ = song_match.choose(query, rows, min_tier=song_match.EXACT_VARIANT)
    return row


def zing_candidate(page, title: str, artist: str, log=print) -> LyricsCandidate | None:
    """Lấy lyrics qua UI Zing MP3; không gọi/chèn khoá ký số của API riêng."""
    query = " ".join(x for x in (title.strip(), artist.strip()) if x)
    if not query:
        return None
    try:
        log(f"tìm lời chuẩn trên Zing MP3 «{query}»…")
        page.goto(ZING_SEARCH_URL.format(q=urllib.parse.quote(query)),
                  wait_until="domcontentloaded", timeout=30_000)
        wait_ms(page, 2500)
        dismiss_zing_popups(page, log=log)
        rows = page.evaluate(_JS_ZING_RESULTS) or []
        best = pick_zing_result(title, artist, rows)
        if best is None:
            log(f"Zing MP3: không chọn được kết quả khớp trong {len(rows)} dòng.")
            return None
        more = page.locator(f'[data-tool-zing-more="{int(best["index"])}"]').first
        # Không đóng popup lần nữa ở đây: Zing re-render danh sách sau khi đóng,
        # làm mất marker data-tool-zing-more vừa gắn vào nút chính xác.
        more.scroll_into_view_if_needed()
        try:
            row = page.locator(f'[data-tool-zing-row="{int(best["index"])}"]').first
            if row.count():
                row.hover()
                wait_ms(page, 250)
            more.hover()
            more.click(force=True, timeout=5_000)
        except Exception:
            # Một số layout chỉ hiện nút Khác tại trang bài, không cho bấm ở kết quả.
            if not best.get("url"):
                raise
            page.goto(best["url"], wait_until="domcontentloaded", timeout=30_000)
            wait_ms(page, 1800)
            dismiss_zing_popups(page, log=log)
            detail_rows = page.evaluate(_JS_ZING_RESULTS) or []
            interactive_rows = [r for r in detail_rows if not r.get("hidden")]
            detail_best = (pick_zing_result(title, artist, interactive_rows)
                           or pick_zing_result(title, artist, detail_rows))
            more = (page.locator(
                f'[data-tool-zing-more="{int(detail_best["index"])}"]').first
                if detail_best else None)
            if more is None or not more.count():
                # Chỉ nút «…» CỘT PHẢI của dòng bài hát — không phải nút
                # album ở cột trái (menu đó không có «Lời bài hát»).
                more = _first_visible(page, [
                    ".media-item .media-right button:has(.ic-more)",
                    ".song-item .media-right button:has(.ic-more)",
                ])
            if more is None:
                log("Zing MP3: không thấy nút Khác/ba chấm trên trang bài.")
                return None
            if detail_best:
                row = page.locator(
                    f'[data-tool-zing-row="{int(detail_best["index"])}"]').first
                if row.count():
                    row.hover()
                    wait_ms(page, 250)
            more.click(force=True)
        item = _zing_lyrics_menu_item(page)
        if item is None and best.get("url") and best["url"] not in page.url:
            # Nút ẩn ở trang search đôi khi nhận click nhưng không mở menu.
            # Chuyển thẳng sang trang bài, hover đúng dòng bài rồi thử lại.
            page.goto(best["url"], wait_until="domcontentloaded", timeout=30_000)
            wait_ms(page, 1800)
            dismiss_zing_popups(page, log=log)
            detail_rows = page.evaluate(_JS_ZING_RESULTS) or []
            interactive_rows = [r for r in detail_rows if not r.get("hidden")]
            detail_best = (pick_zing_result(title, artist, interactive_rows)
                           or pick_zing_result(title, artist, detail_rows))
            if detail_best:
                row = page.locator(
                    f'[data-tool-zing-row="{int(detail_best["index"])}"]').first
                more = page.locator(
                    f'[data-tool-zing-more="{int(detail_best["index"])}"]').first
                if row.count():
                    row.hover()
                    wait_ms(page, 250)
                if more.count():
                    more.click(force=True)
                    item = _zing_lyrics_menu_item(page)
        if item is None:
            log("Zing MP3: menu đã mở nhưng không thấy mục «Lời bài hát».")
            return None
        item.click(force=True)
        wait_ms(page, 1200)
        raw = page.evaluate(_JS_ZING_LYRICS) or ""
        lyrics = clean_lines(raw, title, artist)
        if line_count(lyrics) < MIN_LINES and best.get("url"):
            raw = _zing_detail_lyrics(
                page, best["url"], title, artist, log=log)
            lyrics = clean_lines(raw, title, artist)
        if line_count(lyrics) < MIN_LINES:
            log(f"Zing MP3: hộp lyrics chỉ đọc được {line_count(lyrics)} câu.")
            return None
        return LyricsCandidate(
            lyrics,
            f"Zing MP3 (menu Lời bài hát; {best.get('title')} – {best.get('artist')})",
            50,
        )
    except OperationCancelled:
        raise
    except Exception as exc:
        detail = re.sub(r"\s+", " ", str(exc))[:180]
        log(f"Zing MP3 chưa lấy được lời ({type(exc).__name__}: {detail}) — thử nguồn tiếp theo…")
        return None


def unwrap_link(url: str) -> str:
    """Link chuyển hướng của Bing (bing.com/ck/a?…&u=a1<base64>) → link thật."""
    try:
        parts = urllib.parse.urlsplit(url or "")
        if "bing.com" in parts.netloc and parts.path.startswith("/ck/"):
            u = urllib.parse.parse_qs(parts.query).get("u", [""])[0]
            if u.startswith("a1"):
                import base64
                b64 = u[2:] + "=" * (-len(u[2:]) % 4)
                return base64.urlsafe_b64decode(b64).decode("utf-8", "replace")
    except Exception:
        pass
    return url or ""


def rank_links(links: list[str]) -> list[str]:
    """Bỏ trang vô ích, trùng lặp; trang lời bài hát quen thuộc lên trước."""
    seen: set[str] = set()
    good: list[str] = []
    for url in links:
        u = unwrap_link(url).split("#")[0]
        low = u.lower()
        if not low.startswith("http") or any(s in low for s in SKIP_SITES):
            continue
        host = urllib.parse.urlsplit(low).netloc
        if u in seen or not host:
            continue
        seen.add(u)
        good.append(u)
    pref = [u for u in good if any(s in u.lower() for s in PREFERRED_SITES)]
    return pref + [u for u in good if u not in pref]


def looks_like_song(text: str, title: str) -> bool:
    """Không có AI: trang phải nhắc tới tên bài (bỏ dấu) mới tin là đúng bài."""
    t = fold(title)
    return bool(t) and t in fold(text)


# ── Đọc trang ─────────────────────────────────────────────────────
# Khung «Lời bài hát» của Google: mỗi đoạn 1 div[jsname=U8S5sf], mỗi câu 1 span.
_JS_EXPAND_GOOGLE_LYRICS = r"""() => {
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const exact = /^(lời bài hát đầy đủ|xem toàn bộ lời bài hát|full lyrics|show full lyrics)$/i;
  for (const el of document.querySelectorAll('button,a,[role="button"],span')) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (visible(el) && exact.test(text)) {
      (el.closest('button,a,[role="button"]') || el).click();
      return true;
    }
  }
  return false;
}"""

_JS_GOOGLE_PANEL = r"""() => {
  let box = document.querySelector('[data-lyricid],[data-attrid*="lyrics" i]');
  // Google thay selector thường xuyên. Các câu provider hiện vẫn mang jsname này.
  if (!box) {
    const spans = [...document.querySelectorAll('span[jsname="YS01Ge"]')]
      .filter(x => (x.innerText || '').trim());
    if (spans.length >= 4) return spans.map(x => x.innerText.trim()).join('\n');
  }
  if (!box) {
    const headings = [...document.querySelectorAll('h1,h2,h3,div,span')]
      .filter(x => /^(lời bài hát|lyrics)$/i.test((x.innerText || '').trim()));
    let best = null, bestLines = 0;
    for (const heading of headings) {
      let node = heading.parentElement;
      for (let i=0; i<7 && node; i++, node=node.parentElement) {
        const text = (node.innerText || '').trim();
        const lines = text.split('\n').map(x=>x.trim()).filter(Boolean);
        if (text.length < 12000 && lines.length > bestLines) {
          best = node; bestLines = lines.length;
        }
      }
    }
    box = best;
  }
  if (!box) return '';
  const stanzas = [...box.querySelectorAll('div[jsname="U8S5sf"]')];
  if (stanzas.length) {
    return stanzas.map(st => {
      const spans = [...st.querySelectorAll('span[jsname="YS01Ge"]')];
      return (spans.length ? spans.map(s => s.innerText) : st.innerText.split('\n')).join('\n');
    }).join('\n\n');
  }
  return box.innerText || '';
}"""

_JS_RESULT_LINKS = r"""(sel) => [...document.querySelectorAll(sel)]
  .map(a => a.href).filter(h => h && h.startsWith('http'))"""

# Khối chữ giống lời nhất: nhiều dòng ngắn, ít link. Ưu tiên khối "sạch" (ít dòng
# lạ) và khối sâu nhất khi điểm bằng nhau.
_JS_BEST_BLOCK = r"""() => {
  const known = document.querySelector(
    '[data-lyrics-container], #fulllyric, .lyric, .lyrics, #lyrics, .song-lyric, .song-lyrics, '
    + '#song-lyric, .lyric-content, .lyrics-content, .entry-content');
  let best = null, bestScore = 0;
  for (const el of document.body.querySelectorAll('div,p,pre,article,section,td')) {
    if (el.closest('nav,header,footer,aside,form,script,style')) continue;
    const t = el.innerText || '';
    if (t.length < 150 || t.length > 25000) continue;
    const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length < 8) continue;
    // câu hát: 3–20 chữ, không link, không ngày giờ (bình luận: «Thích», «0», «12/03/2024»)
    const good = lines.filter(s => s.length >= 8 && s.length <= 110
      && s.split(/\s+/).length >= 3 && s.split(/\s+/).length <= 22
      && !/https?:|www\.|©|\d{1,2}\/\d{1,2}\/\d{2,4}|\d{1,2}:\d{2}/.test(s)).length;
    if (good < 8) continue;
    let linkLen = 0;
    el.querySelectorAll('a').forEach(a => { linkLen += (a.innerText || '').length; });
    if (linkLen / t.length > 0.2) continue;
    const ratio = good / lines.length;
    if (ratio < 0.6) continue;
    let score = good * ratio * ratio * ratio;
    if (known && (el === known || known.contains(el))) score *= 1.3;
    if (score >= bestScore) { best = el; bestScore = score; }
  }
  return best ? best.innerText : '';
}"""


def _is_blocked(page) -> bool:
    try:
        if "/sorry/" in page.url:
            return True
        return bool(_CAPTCHA_RE.search(page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 3000)")))
    except Exception:
        return False


def google_search(page, query: str) -> tuple[str, list[str], bool]:
    """→ (lời trong khung Google, các link kết quả, bị chặn?)."""
    page.goto(GOOGLE_URL.format(q=urllib.parse.quote(query)), wait_until="domcontentloaded")
    wait_ms(page, 2500)
    if _is_blocked(page):
        return "", [], True
    try:
        if page.evaluate(_JS_EXPAND_GOOGLE_LYRICS):
            wait_ms(page, 900)
    except Exception:
        pass
    panel = ""
    try:
        panel = page.evaluate(_JS_GOOGLE_PANEL) or ""
    except Exception:
        pass
    links = page.evaluate(_JS_RESULT_LINKS, "#search a[href^='http']") or []
    return panel, links, False


def bing_search(page, query: str) -> list[str]:
    page.goto(BING_URL.format(q=urllib.parse.quote(query)), wait_until="domcontentloaded")
    wait_ms(page, 2500)
    if _is_blocked(page):
        return []
    return page.evaluate(_JS_RESULT_LINKS, "#b_results h2 a, #b_results .b_algo a") or []


def read_block(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    wait_ms(page, 2000)
    return page.evaluate(_JS_BEST_BLOCK) or ""


# ── AI ────────────────────────────────────────────────────────────
_AI_PROMPT = (
    "Dưới đây là chữ copy từ một trang web, có thể chứa lời bài hát «{title}» của «{artist}».\n"
    "Nhiệm vụ: trả về ĐÚNG phần lời bài hát đó, giữ nguyên ngôn ngữ, chính tả và thứ tự câu.\n"
    "- Mỗi câu hát 1 dòng; giữa các đoạn để 1 dòng trống.\n"
    "- GIỮ nguyên các nhãn cấu trúc trong ngoặc vuông như [Verse 1: Ca sĩ], "
    "[Pre-Chorus], [Chorus], [Bridge], [Outro]. XOÁ nhãn đoạn không có ngoặc vuông, "
    "hợp âm, ký hiệu lặp (x2), tên ca sĩ đầu câu, "
    "dòng nhạc sĩ/nguồn/bản quyền, quảng cáo, bình luận, tên bài.\n"
    "- Không tự thêm, không dịch, không giải thích.\n"
    "- Nếu chữ dưới đây KHÔNG phải lời bài «{title}» thì chỉ trả lời đúng: KHONG_CO_LOI\n\n"
    "-----\n{text}\n-----"
)


def _ai_client():
    from openai import OpenAI

    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    cfg = get_api_config_from_db(str(DB_PATH))
    if not cfg.api_key:
        return None, ""
    model = getattr(cfg, "model", None) or "gpt-4o-mini"
    if model.lower().startswith("claude"):
        model = "gpt-4o-mini"
    return OpenAI(**cfg.to_client_kwargs()), model


def ai_available() -> bool:
    try:
        return _ai_client()[0] is not None
    except Exception:
        return False


def ai_extract(text: str, title: str, artist: str) -> str | None:
    """→ lời đã lọc; "" nếu AI nói không phải lời bài này; None nếu không gọi được AI."""
    try:
        client, model = _ai_client()
        if client is None:
            return None
        r = client.chat.completions.create(
            model=model, temperature=0, timeout=90,
            messages=[{"role": "user", "content": _AI_PROMPT.format(
                title=title, artist=artist or "?", text=text[:MAX_AI_CHARS])}])
        ans = (r.choices[0].message.content or "").strip()
    except Exception:
        return None
    ans = re.sub(r"^```[a-z]*\n?|\n?```$", "", ans).strip()
    if "KHONG_CO_LOI" in ans[:40]:
        return ""
    return ans


# ── Ghép lại ──────────────────────────────────────────────────────
def find_lyrics(page, title: str, artist: str = "", log=print,
                existing_lyrics: str = "", existing_source: str = "NhacCuaTui") -> dict:
    """Tìm và đối chiếu lyrics. Trang web thường phải có >=2 nguồn khớp nhau;
    Google lyrics panel được coi là nguồn cấp phép có độ tin cậy cao."""
    title = (title or "").strip()
    if not title:
        return {}
    query = " ".join(x for x in (title, artist.strip(), "lyrics") if x)
    log(f"đối chiếu lyrics cho «{title}» — ưu tiên Zing MP3…")
    links: list[str] = []
    candidates: list[LyricsCandidate] = []
    existing = clean_lines(existing_lyrics, title, artist)
    if line_count(existing) >= MIN_LINES:
        candidates.append(LyricsCandidate(existing, existing_source, 20))
    api_candidate = lrclib_candidate(title, artist)
    if api_candidate:
        candidates.append(api_candidate)
    zing = zing_candidate(page, title, artist, log=log)
    if zing:
        candidates.append(zing)
        chosen, sources = choose_consensus(candidates)
        if chosen:
            return {"lyrics": chosen.lyrics,
                    "source": "Đã đối chiếu: " + " + ".join(sources),
                    "confidence": "verified"}
        # Đây là lyrics do chính giao diện Zing cung cấp cho đúng dòng bài/ca sĩ,
        # không phải DOM tự đoán. Dùng ngay thay vì tiếp tục sang Google rồi có
        # thể gặp CAPTCHA và kết luận sai là không có lời.
        return {"lyrics": zing.lyrics, "source": zing.source,
                "confidence": "provider"}
    log(f"tìm thêm trên Google «{query}»…")
    try:
        panel, links, blocked = google_search(page, query)
    except OperationCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        panel, blocked = "", True
        log(f"không mở được Google ({type(e).__name__}) — thử Bing…")
    if panel:
        lyr = clean_lines(panel, title, artist)
        if line_count(lyr) >= MIN_LINES:
            # Khung này do Google hiển thị từ nhà cung cấp lyrics; nếu có nguồn
            # thứ hai thì ghi rõ đối chiếu, nếu không vẫn ưu tiên hơn scrape DOM.
            panel_candidate = LyricsCandidate(
                lyr, "Google lyrics panel (LyricFind/Musixmatch)", 40)
            candidates.append(panel_candidate)
            chosen, sources = choose_consensus(candidates)
            return {"lyrics": (chosen or panel_candidate).lyrics,
                    "source": "Đã đối chiếu: " + " + ".join(sources)
                    if chosen else panel_candidate.source,
                    "confidence": "verified" if chosen else "provider"}
    if blocked:
        log("Google đòi xác nhận «không phải robot» — tool không tự giải, chuyển sang Bing…")
        try:
            links = bing_search(page, query)
        except OperationCancelled:
            raise
        except Exception:
            links = []
    use_ai = ai_available()
    for url in rank_links(links)[:MAX_PAGES]:
        host = urllib.parse.urlsplit(url).netloc.replace("www.", "")
        log(f"đọc lời ở {host}…")
        try:
            raw = read_block(page, url)
        except OperationCancelled:
            raise
        except Exception:
            continue
        if line_count(raw) < MIN_LINES:
            continue
        if use_ai:
            log(f"AI đang lọc lời từ {host}…")
            got = ai_extract(raw, title, artist)
            if got == "":
                continue           # AI: không phải bài này
            if got is not None:
                lyr = clean_lines(got, title, artist)
                if line_count(lyr) >= MIN_LINES:
                    candidates.append(LyricsCandidate(lyr, url, 10))
                continue
        if not looks_like_song(raw, title):
            try:
                if not looks_like_song(page.title(), title):
                    continue
            except Exception:
                continue
        lyr = core_block(clean_lines(raw, title, artist))
        if line_count(lyr) >= MIN_LINES:
            candidates.append(LyricsCandidate(lyr, url, 10))
    chosen, sources = choose_consensus(candidates)
    if chosen:
        return {"lyrics": chosen.lyrics,
                "source": "Đã đối chiếu: " + " + ".join(sources),
                "confidence": "verified"}
    if candidates:
        # Zing là nguồn lyrics trực tiếp mà người dùng yêu cầu; cho phép dùng độc
        # lập khi nguồn còn lại không có dữ liệu, tương tự Google lyrics panel.
        zing_only = max((c for c in candidates if c.source.startswith("Zing MP3")),
                        key=lambda c: c.priority, default=None)
        if zing_only:
            return {"lyrics": zing_only.lyrics, "source": zing_only.source,
                    "confidence": "provider"}
        log(f"có {len(candidates)} nguồn lyrics nhưng chưa có 2 nguồn khớp nhau — cần kiểm tra thủ công.")
        return {"candidates": len(candidates), "confidence": "needs_review"}
    return {}
