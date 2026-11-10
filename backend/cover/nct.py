"""
cover/nct.py — Lái trình duyệt trên nhaccuatui.com: tìm bài, mở trang bài, lấy
lời, bấm Download → High quality.

Trang là Vue SPA (không có thẻ <a>), API riêng có ký số → thao tác GIAO DIỆN như
người dùng. Tool KHÔNG đăng nhập hộ: tải nhạc cần tài khoản, người dùng tự đăng
nhập 1 lần trong cửa sổ «Mở nhaccuatui để đăng nhập». Không bao giờ bấm vào hộp
mua VIP / thanh toán.

Các hàm không cần trình duyệt (chuẩn hoá chữ, chấm điểm kết quả, đổi HTML lời →
chữ, chọn chất lượng) tách riêng để test.
"""
from __future__ import annotations

import html as _html
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from backend.video.browser_base import check_cancel, query_first, wait_ms

BASE_URL = "https://www.nhaccuatui.com/"
SEARCH_URL = BASE_URL + "search?key={q}&t=all"
SONG_URL_RE = re.compile(r"^https?://(?:www\.|m\.)?nhaccuatui\.com/(?:song|bai-hat)/\S+$", re.I)

# Kết quả tìm kiếm / trang bài (đã soi DOM 2026-10-28)
SEL_RESULT = ".song-wrap .song-item"
SEL_SONG_READY = [".detail-wrap .info-wrap .name", ".lyrics-wrap", ".operate-wrap"]
SEL_DOWNLOAD_BTN = [
    ".operate-wrap .el-dropdown .play-btn:has-text('Download')",
    ".operate-wrap .el-dropdown .play-btn:has-text('Tải')",
    ".operate-wrap .el-dropdown .play-btn",
    ".operate-wrap .el-dropdown",
]
# Menu chất lượng (soi lại 2026-10-28): <ul.el-dropdown-menu> chứa các
# <div.download-item> (không phải <li.el-dropdown-menu__item>); mục Lossless có
# huy hiệu VIP là ảnh/svg nên innerText không có chữ «VIP».
SEL_MENU_ITEM = (".el-dropdown__popper .download-item, "
                 ".el-popper.el-dropdown__popper .el-dropdown-menu__item, "
                 ".nct-dropdown .el-dropdown-menu__item, "
                 ".el-popper .download-item, .download-item, "
                 "[role='menuitem']")

# Chỉ được tải bản phát hành gốc/official. Đây là dấu hiệu của bản biến thể, không
# phải thể loại dự án mà người dùng sẽ dùng về sau để tạo cover trên Suno.
VARIANT_WORDS = (
    "remix", "slowed", "reverb", "speed up", "sped up", "lofi", "lo fi", "cover",
    "karaoke", "beat", "instrumental", "live", "acoustic", "mashup", "nightcore",
    "8d", "tiktok", "vinahouse", "version", "ver", "radio edit", "extended mix",
)
_VARIANT_RE = re.compile(
    r"\b(?:remix|cover|karaoke|instrumental|mashup|nightcore|vinahouse|lo\s*fi|"
    r"slowed|reverb|sped\s*up|speed\s*up|8d|tiktok|radio\s*edit|extended\s*mix|"
    r"acoustic(?:\s+version)?|live(?:\s+version)?|beat(?:\s+version)?|"
    r"version|ver)\b", re.I)

QUALITY_ORDER = (
    ("high", ("high quality", "chat luong cao", "320", "hq")),
    ("normal", ("normal", "standard", "128", "chat luong thuong", "thuong",
                "tieu chuan")),
    ("lossless", ("lossless", "flac", "sq")),
)

_LOGIN_RE = re.compile(r"login with password|log in|login|sign in|đăng nhập|dang nhap", re.I)
_PAYWALL_RE = re.compile(r"\bvip\b|nâng cấp|nang cap|upgrade|thanh toán|thanh toan|"
                         r"purchase|mua gói|mua goi|subscribe", re.I)
_NO_LYRICS_RE = re.compile(
    r"chưa có lời|hiện chưa có lời|đang (được )?cập nhật|lyrics (are )?(being )?updat|"
    r"no lyrics|chưa cập nhật lời|lyrics for this song will be available", re.I)
_UPLOADER_LINE_RE = re.compile(
    r"^\s*(uploaded by|lời đăng bởi|đăng bởi|người đăng|cung cấp bởi|lyrics by)\b", re.I)


class NctError(RuntimeError):
    """Lỗi có câu tiếng Việt dễ hiểu để hiện lên giao diện."""


class NctLoginRequired(NctError):
    pass


class NctNotFound(NctError):
    pass


class NctNoOfficial(NctNotFound):
    """Có kết quả nhưng tất cả đều là Remix/Cover/biến thể."""


class NctNoExactMatch(NctNotFound):
    """Chế độ chặt: nhaccuatui không có bản ĐÚNG TÊN + ĐÚNG CA SĨ (bản gốc).

    ``row``/``match`` = ứng viên tốt nhất (có thể None) để so với nguồn khác;
    ``search_query`` = chữ đã gõ vào ô tìm ra ứng viên đó (để mở lại)."""

    def __init__(self, message: str, row=None, match=None, search_query: str = ""):
        super().__init__(message)
        self.row, self.match, self.search_query = row, match, search_query


# ── chữ ──────────────────────────────────────────────────────────
def fold(text: str) -> str:
    """Bỏ dấu, chữ thường, chỉ giữ chữ/số + 1 khoảng trắng."""
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def tokens(text: str) -> list[str]:
    return [t for t in fold(text).split() if t]


def split_query(query: str) -> list[str]:
    """«Tên bài – Ca sĩ» (hay ngược lại) → các phần, bỏ rỗng."""
    parts = re.split(r"\s+[-–—|]\s+|\s*[–—|]\s*", query or "")
    return [p.strip() for p in parts if p and p.strip()]


def is_song_url(text: str) -> bool:
    return bool(SONG_URL_RE.match((text or "").strip()))


def search_fallbacks(query: str) -> list[str]:
    """Các ô tìm thay thế khi cả dòng không ra kết quả: từng phần «Tên bài – Ca sĩ»
    (phần đầu trước), bỏ trùng và bỏ chính dòng gốc."""
    out: list[str] = []
    for part in split_query(query):
        if fold(part) and fold(part) != fold(query) and part not in out:
            out.append(part)
    return out


def is_variant_title(title: str) -> bool:
    """True nếu tiêu đề là Remix/Cover/Karaoke/Live… thay vì bản official gốc."""
    return bool(_VARIANT_RE.search(fold(title)))


def official_query(query: str) -> str:
    """Bỏ nhãn biến thể khỏi ô tìm nhưng giữ tên bài và ca sĩ.

    Ví dụ ``Mở Lòng Vì Ai (Thazh x Đông Remix) - Inso`` sẽ được tìm bằng
    ``Mở Lòng Vì Ai - Inso``. URL được giữ nguyên để lớp kiểm tra trang bài báo
    lỗi rõ ràng nếu người dùng dán thẳng link một bản biến thể.
    """
    q = (query or "").strip()
    if is_song_url(q):
        return q
    q = re.sub(r"[\(\[\{][^\)\]\}]{0,100}(?:remix|cover|karaoke|instrumental|"
               r"mashup|nightcore|vinahouse|lo\s*fi|slowed|reverb|sped|speed|"
               r"acoustic|live|version|\bver\b|\bbeat\b)[^\)\]\}]{0,100}[\)\]\}]",
               " ", q, flags=re.I)
    q = _VARIANT_RE.sub(" ", q)
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"\s*([-–—|])\s*", r" \1 ", q).strip(" -–—|")
    return q or (query or "").strip()


def ensure_official(info: dict, requested: str = "", *, allow_variant: bool = False) -> dict:
    """Chặn biến thể khi strict; cho phép khi đã xác định không có official."""
    title = (info or {}).get("title", "")
    if is_variant_title(title) and not allow_variant:
        raise NctNotFound(
            f"Kết quả «{title}» là bản Remix/Cover/biến thể. Tool chỉ tải bản "
            f"official gốc của «{official_query(requested) or requested}».")
    return info


def score_result(query: str, title: str, artist: str) -> float:
    """Điểm khớp giữa ô tìm kiếm và 1 kết quả (cao = giống hơn)."""
    q_tokens = tokens(query)
    if not q_tokens:
        return 0.0
    hay = set(tokens(title)) | set(tokens(artist))
    score = sum(1 for t in q_tokens if t in hay) / len(q_tokens)

    ft, fa, fq = fold(title), fold(artist), fold(query)
    for part in split_query(query):
        fp = fold(part)
        if not fp:
            continue
        if fp == ft:
            score += 1.0            # đúng tên bài
        elif fa and (fp == fa or f" {fp} " in f" {fa} "):
            score += 0.5            # đúng ca sĩ
    if "#" in (title or ""):
        score -= 0.3                # bản người dùng tự đăng kiểu "#ABC ..."
    if fa in ("v a", "various artists", "nhieu ca si") and fa not in fq:
        score -= 0.2
    return round(score, 3)


def variant_fallback_bonus(query: str, title: str, artist: str) -> float:
    """Xếp hạng khi không có bản official.

    Bản ``(<ca sĩ> Cover)`` do đúng ca sĩ phát hành gần bản gốc hơn một bản
    remix. Karaoke/beat/instrumental hoặc các bản đổi tốc độ luôn đứng cuối.
    """
    ft, fa = fold(title), fold(artist)
    bonus = 0.0
    if re.search(r"\bcover\b", ft):
        bonus += 0.30
        # Ví dụ: «Mở Lòng Vì Ai (Inso Cover)» / artist=Inso.
        requested_artists = split_query(official_query(query))[1:]
        for requested in requested_artists:
            fr = fold(requested)
            if fr and fr in fa and fr in ft:
                bonus += 0.35
                break
    if re.search(r"\b(?:acoustic|live)\b", ft):
        bonus -= 0.20
    if re.search(r"\b(?:karaoke|instrumental|beat|nightcore|8d|slowed|reverb|"
                 r"sped up|speed up)\b", ft):
        bonus -= 0.70
    # Remix là fallback hợp lệ nhưng đứng sau self-cover; không phạt quá nặng để
    # remix đúng ca sĩ vẫn thắng cover của một ca sĩ khác.
    if re.search(r"\bremix\b", ft):
        bonus -= 0.05
    return bonus


def pick_best(query: str, results: list[dict], *, allow_variants: bool = False) -> dict | None:
    """Kết quả hợp nhất; mặc định ưu tiên tuyệt đối official."""
    best, best_score = None, None
    for i, r in enumerate(results):
        if is_variant_title(r.get("title", "")) and not allow_variants:
            continue
        sc = score_result(query, r.get("title", ""), r.get("artist", ""))
        if allow_variants:
            sc += variant_fallback_bonus(
                query, r.get("title", ""), r.get("artist", ""))
        sc -= i * 0.001
        if best_score is None or sc > best_score:
            best, best_score = r, sc
    if best is None or best_score is None or best_score < 0.34:
        return None
    return best


def lyrics_from_html(raw_html: str) -> str:
    """innerHTML của khung lời (ngắt dòng bằng <br>) → chữ thường, mỗi câu 1 dòng."""
    s = raw_html or ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\xa0", " ").replace("\r", "")
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if not _UPLOADER_LINE_RE.match(ln)]
    from backend.cover.lyrics import strip_lyrics_header
    text = strip_lyrics_header("\n".join(lines))      # "Bài hát: Tên - Ca sĩ"
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text or (len(text) < 120 and _NO_LYRICS_RE.search(text)):
        return ""
    return text


def choose_quality(labels: list[str]) -> list[tuple[int, str]]:
    """Thứ tự thử các mục trong menu Download: High quality trước, rồi bản thường,
    cuối cùng lossless. Trả [(chỉ số mục, nhãn chất lượng)]."""
    folded = [fold(x) for x in labels]
    out: list[tuple[int, str]] = []
    used: set[int] = set()
    for key, pats in QUALITY_ORDER:
        for i, f in enumerate(folded):
            if i in used:
                continue
            if any(re.search(rf"\b{re.escape(p)}", f) for p in pats):
                out.append((i, key))
                used.add(i)
    return out


def safe_name(text: str, limit: int = 90) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "").strip(" .")
    s = re.sub(r"\s+", " ", s)
    return (s[:limit].rstrip(" .") or "bai_hat")


# ── trình duyệt ──────────────────────────────────────────────────
_JS_RESULTS = """() => Array.from(document.querySelectorAll('.song-wrap .song-item')).map((el, i) => {
  const name = el.querySelector('.song-name');
  const artists = Array.from(el.querySelectorAll('.artist .name-text'))
      .map(a => (a.innerText || '').trim()).filter(Boolean);
  const up = el.querySelector('.uploader-box span');
  return {index: i, title: name ? (name.innerText || '').trim() : '',
          artist: artists.join(', '), uploader: up ? (up.innerText || '').trim() : ''};
})"""

_JS_SONG = """() => {
  const q = s => document.querySelector(s);
  const name = q('.detail-wrap .info-wrap .name span') || q('.detail-wrap .info-wrap .name');
  let artists = Array.from(document.querySelectorAll('.detail-wrap span.artist-name'));
  if (!artists.length) { const a = q('span.artist-name'); if (a) artists = [a]; }
  const box = q('.lyrics-wrap .cont .lyrics-box') || q('.lyrics-wrap .lyrics-box') || q('.lyrics-box');
  return {title: name ? (name.innerText || '').trim() : '',
          artist: artists.map(a => (a.innerText || '').trim()).filter(Boolean).join(', '),
          lyrics_html: box ? box.innerHTML : ''};
}"""

_JS_DIALOG_TEXT = """() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const boxes = Array.from(document.querySelectorAll(
      '.el-dialog, .el-overlay-dialog, .el-message-box, [role=dialog], .modal, .login-wrap'));
  return boxes.filter(vis).map(b => (b.innerText || '').slice(0, 400)).join('\\n');
}"""

_JS_HEADER_LOGIN = """() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const re = /^(log ?in|sign ?in|đăng nhập)$/i;
  const roots = Array.from(document.querySelectorAll('header, .header, [class*=header]'));
  if (!roots.length) return null;
  for (const r of roots) {
    for (const el of r.querySelectorAll('button, a, span, div')) {
      if (el.children.length > 2) continue;
      const t = (el.innerText || '').trim();
      if (t && t.length < 20 && re.test(t) && vis(el)) return true;
    }
  }
  return false;
}"""


def login_state(page) -> str:
    """"logged_out" (thấy nút Log in ở đầu trang) | "logged_in" | "unknown"."""
    try:
        v = page.evaluate(_JS_HEADER_LOGIN)
    except Exception:
        return "unknown"
    if v is None:
        return "unknown"
    return "logged_out" if v else "logged_in"


def _dialog_text(page) -> str:
    try:
        return page.evaluate(_JS_DIALOG_TEXT) or ""
    except Exception:
        return ""


def _close_dialogs(page) -> None:
    try:
        page.keyboard.press("Escape")
        wait_ms(page, 400)
    except Exception:
        pass


def search(page, query: str, log=print) -> list[dict]:
    """Mở trang tìm kiếm, trả danh sách kết quả bài hát."""
    page.goto(SEARCH_URL.format(q=quote(query.strip())), wait_until="domcontentloaded")
    if query_first(page, [SEL_RESULT], timeout_ms=20_000) is None:
        return []
    wait_ms(page, 800)   # để danh sách render đủ
    results = page.evaluate(_JS_RESULTS) or []
    log(f"Tìm thấy {len(results)} kết quả cho «{query}»")
    return [r for r in results if r.get("title")]


def open_result(page, index: int) -> str:
    """Bấm tên bài thứ `index` trong danh sách kết quả → URL trang bài."""
    loc = page.locator(SEL_RESULT).nth(index).locator(".song-name").first
    loc.scroll_into_view_if_needed()
    loc.click()
    deadline = time.time() + 20
    while time.time() < deadline:
        check_cancel()
        if "/song/" in page.url or "/bai-hat/" in page.url:
            break
        wait_ms(page, 300)
    else:
        raise NctError("Bấm vào bài trong kết quả tìm kiếm nhưng không mở được trang bài.")
    query_first(page, SEL_SONG_READY, timeout_ms=20_000)
    return page.url.split("?")[0]


def read_song(page) -> dict:
    """Đọc tên bài, ca sĩ, lời ở trang bài đang mở."""
    query_first(page, SEL_SONG_READY, timeout_ms=20_000)
    info: dict = {}
    for _ in range(3):
        info = page.evaluate(_JS_SONG) or {}
        if info.get("title"):
            break
        wait_ms(page, 1000)
    return {"title": info.get("title", ""), "artist": info.get("artist", ""),
            "lyrics": lyrics_from_html(info.get("lyrics_html", "")),
            "url": page.url.split("?")[0]}


def _menu_items(page):
    loc = page.locator(SEL_MENU_ITEM)
    out = []
    try:
        n = loc.count()
    except Exception:
        return out
    for i in range(n):
        it = loc.nth(i)
        try:
            if it.is_visible():
                label = (it.inner_text() or "").strip()
                if not _PAYWALL_RE.search(label) and re.search(
                        r"vip", it.evaluate("e => e.outerHTML") or "", re.I):
                    label += " [VIP]"
                out.append((it, label))
        except Exception:
            continue
    return out


def _open_download_menu(page):
    """Rê chuột rồi bấm nút Download → các mục chất lượng đang hiện."""
    btn = query_first(page, SEL_DOWNLOAD_BTN, timeout_ms=15_000)
    if btn is None:
        raise NctError("Không thấy nút Download trên trang bài (giao diện nhaccuatui "
                       "có thể đã đổi).")
    btn.scroll_into_view_if_needed()
    for action in ("hover", "click"):
        try:
            getattr(btn, action)()
        except Exception:
            continue
        for _ in range(6):
            wait_ms(page, 300)
            items = _menu_items(page)
            if items:
                return items
            if _LOGIN_RE.search(_dialog_text(page)):
                _close_dialogs(page)
                raise NctLoginRequired(
                    "nhaccuatui yêu cầu đăng nhập mới cho tải. Bấm «Mở nhaccuatui để "
                    "đăng nhập», tự đăng nhập trong cửa sổ đó rồi bấm «Thử lại».")
    if login_state(page) == "logged_out":
        raise NctLoginRequired(
            "Chưa đăng nhập nhaccuatui. Bấm «Mở nhaccuatui để đăng nhập», tự đăng "
            "nhập rồi bấm «Thử lại».")
    raise NctError("Bấm Download nhưng không hiện menu chọn chất lượng.")


_AUDIO_EXT_RE = re.compile(r"\.(mp3|m4a|flac|wav|aac)(\?|$)", re.I)


def is_audio_download_url(url: str) -> bool:
    """Link file nhạc mà nút Download của nhaccuatui gọi (vd
    https://stream.nct.vn/…/abc_hq.mp3?…&download=true)."""
    u = (url or "").split("#")[0]
    return "download=true" in u.lower() and bool(_AUDIO_EXT_RE.search(u))


def _wait_download(page, click, timeout_s: float = 90.0) -> dict:
    """Bấm (click()) rồi chờ file nhạc. Trả {"body", "url"} khi bắt được file,
    {"download"} khi trình duyệt tự tải, {"popup_url"} khi mở tab file nhạc,
    {"paywall": True} khi hiện hộp VIP, {} khi không thấy gì.

    Cách chính: nhaccuatui tải mp3 từ stream.nct.vn (…&download=true) rồi mới đổi
    thành file blob cho trình duyệt lưu — đúng lúc đó Cốc Cốc hay ĐÓNG LUÔN CẢ CỬA SỔ.
    Nên tool chặn yêu cầu mp3 đó: tự tải (cùng phiên đăng nhập), giữ nội dung, rồi
    huỷ phần trình duyệt → trình duyệt không phải tải gì, không bị đóng."""
    ctx = page.context
    got: list = []
    popups: list = []
    captured: list = []

    def on_download(d):
        got.append(d)

    def on_page(pg):
        popups.append(pg)
        pg.on("download", on_download)

    def on_route(route):
        try:
            resp = route.fetch(timeout=180_000)
            captured.append({"url": route.request.url, "status": resp.status,
                             "body": resp.body()})
        except Exception as e:  # noqa: BLE001
            captured.append({"url": route.request.url, "status": 0, "body": b"",
                             "error": f"{type(e).__name__}: {e}"})
        finally:
            try:
                route.abort()
            except Exception:
                pass

    page.on("download", on_download)
    ctx.on("page", on_page)
    ctx.route(is_audio_download_url, on_route)
    try:
        click()
        end = time.time() + timeout_s
        while time.time() < end and not got and not captured:
            wait_ms(page, 400)
            if not got and not captured and _PAYWALL_RE.search(_dialog_text(page)):
                return {"paywall": True}
    finally:
        try:
            ctx.unroute(is_audio_download_url, on_route)
        except Exception:
            pass
        try:
            page.remove_listener("download", on_download)
        except Exception:
            pass
        try:
            ctx.remove_listener("page", on_page)
        except Exception:
            pass
    if captured:
        return captured[0]
    if got:
        return {"download": got[0]}
    for pg in popups:
        try:
            url = pg.url
        except Exception:
            continue
        if _AUDIO_EXT_RE.search(url):
            return {"popup_url": url}
    return {}


def download(page, dest_dir: Path, stem: str, log=print) -> tuple[Path, str]:
    """Trang bài đang mở → tải file, ưu tiên High quality. Trả (đường dẫn, chất lượng)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    items = _open_download_menu(page)
    labels = [t for _, t in items]
    log("Menu tải: " + " | ".join(labels))
    order = choose_quality(labels)
    if not order:
        # Không nhận ra nhãn nào — thử mục đầu tiên không có chữ VIP.
        order = [(i, "?") for i, t in enumerate(labels) if not _PAYWALL_RE.search(t)][:1]
    if not order:
        raise NctError("Menu tải chỉ có mục VIP — tool không bấm mua gói.")

    last_err = ""
    for idx, quality in order:
        items = _menu_items(page) or _open_download_menu(page)
        if idx >= len(items):
            continue
        item, label = items[idx]
        if _PAYWALL_RE.search(label) and quality != "high":
            continue
        log(f"Chọn «{label}»…")
        res = _wait_download(page, item.click)
        if "body" in res:
            body = res["body"] or b""
            if len(body) > 50_000 and 200 <= int(res.get("status") or 0) < 300:
                ext = Path(res["url"].split("?")[0]).suffix.lower() or ".mp3"
                path = _unique(dest_dir / f"{stem}{ext}")
                path.write_bytes(body)
                return path, quality
            last_err = (f"bấm «{label}» nhưng file nhạc tải không được "
                        f"({res.get('error') or 'HTTP ' + str(res.get('status'))})")
            continue
        dl = res.get("download")
        if dl is not None:
            ext = Path(dl.suggested_filename or "").suffix.lower() or ".mp3"
            path = _unique(dest_dir / f"{stem}{ext}")
            try:
                dl.save_as(str(path))
            except Exception as e:  # noqa: BLE001
                # Tải từ tab phụ tự đóng → Playwright báo «Target … closed» dù file đã
                # bắt đầu tải. Lấy lại thẳng từ link tải bằng phiên đã đăng nhập.
                log(f"Lưu file qua trình duyệt lỗi ({type(e).__name__}) — tải lại từ link…")
                if not _fetch_to(page, dl.url, path):
                    last_err = f"bấm «{label}» có file nhưng không lưu được"
                    continue
            if path.stat().st_size < 50_000:
                path.unlink(missing_ok=True)
                last_err = "file tải về quá nhỏ (không phải bài nhạc)"
                continue
            return path, quality
        if res.get("paywall"):
            _close_dialogs(page)
            last_err = f"mục «{label}» cần tài khoản VIP"
            continue
        if res.get("popup_url"):
            extra = res["popup_url"]
            ext = Path(extra.split("?")[0]).suffix.lower() or ".mp3"
            path = _unique(dest_dir / f"{stem}{ext}")
            if _fetch_to(page, extra, path):
                return path, quality
        if _LOGIN_RE.search(_dialog_text(page)):
            _close_dialogs(page)
            raise NctLoginRequired(
                "nhaccuatui yêu cầu đăng nhập mới cho tải. Bấm «Mở nhaccuatui để đăng "
                "nhập», tự đăng nhập rồi bấm «Thử lại».")
        last_err = f"bấm «{label}» nhưng không thấy file tải về"
    raise NctError(f"Không tải được: {last_err or 'không có mục tải phù hợp'}.")


def _fetch_to(page, url: str, path: Path) -> bool:
    """Tải `url` bằng cookie của trình duyệt (đã đăng nhập) → ghi `path`.
    True nếu ra file nhạc thật (> 50 KB)."""
    if not (url or "").startswith("http"):
        return False
    try:
        resp = page.context.request.get(url, timeout=120_000)
        body = resp.body() if resp.ok else b""
    except Exception:  # noqa: BLE001
        return False
    if len(body) <= 50_000:
        return False
    path.write_bytes(body)
    return True


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
    return path


def find_and_open(page, query: str, log=print, strict: bool = False) -> dict:
    """Ô nhập (tên bài – ca sĩ, hoặc link bài) → mở đúng trang bài, trả thông tin
    {title, artist, lyrics, url, candidate}.

    ``strict=True`` (luồng tải nhạc gốc): chỉ mở bài ĐÚNG TÊN + ĐÚNG CA SĨ bản
    gốc; không có thì ném ``NctNoExactMatch`` kèm ứng viên tốt nhất để so với
    Zing MP3 / YouTube Music / YouTube."""
    q = (query or "").strip()
    if is_song_url(q):
        page.goto(q, wait_until="domcontentloaded")
        return ensure_official(read_song(page), q, allow_variant=True)
    lookup = official_query(q)
    if lookup != q:
        log(f"Bỏ nhãn Remix/Cover — chỉ tìm bản official: «{lookup}»")
    if strict:
        return _find_and_open_strict(page, q, lookup, log)
    results = search(page, lookup, log=log)
    if not results:
        # Ô tìm của nhaccuatui khắt khe: «Tìm Em - Hngle Bảo Anh» ra 0 kết quả trong khi
        # «Tìm Em» thì có → tìm lại theo từng phần (tên bài trước), chấm điểm vẫn theo cả dòng.
        for part in search_fallbacks(lookup):
            results = search(page, part, log=log)
            if results and pick_best(lookup, results) is not None:
                break
    if not results:
        raise NctNotFound(f"nhaccuatui không có kết quả cho «{q}». Thử gõ lại tên "
                          "bài/ca sĩ, hoặc dán thẳng link bài hát.")
    best = pick_best(lookup, results)
    variant_fallback = False
    if best is None:
        names = "; ".join(f"{r['title']} – {r['artist']}" for r in results[:3])
        best = pick_best(lookup, results, allow_variants=True)
        if best is None:
            raise NctNotFound(f"Không chắc bài nào đúng «{q}» (đầu danh sách: {names}). "
                              "Hãy dán thẳng link bài hát nhaccuatui.")
        variant_fallback = True
        log(f"Không có bản official — dùng bản Remix/Cover phù hợp nhất: "
            f"{best['title']} – {best['artist']}")
    log(f"Chọn: {best['title']} – {best['artist']}")
    open_result(page, int(best["index"]))
    info = ensure_official(read_song(page), q, allow_variant=variant_fallback)
    info["candidate"] = best
    info["variant_fallback"] = variant_fallback
    return info


def _find_and_open_strict(page, q: str, lookup: str, log) -> dict:
    from . import song_match
    best_row, best_match, best_q = None, None, ""
    for search_q in [lookup, *search_fallbacks(lookup)]:
        results = search(page, search_q, log=log)
        row, match = song_match.choose(lookup, results)
        if row is not None and (best_match is None
                                or (match.tier, match.score)
                                > (best_match.tier, best_match.score)):
            best_row, best_match, best_q = row, match, search_q
        if best_match is not None and best_match.tier == song_match.EXACT:
            break
    if best_match is None or best_match.tier != song_match.EXACT:
        found = (f" (tốt nhất: «{best_row['title']} – {best_row.get('artist', '')}», "
                 f"{best_match.label})") if best_row else ""
        raise NctNoExactMatch(
            f"nhaccuatui không có bản đúng «{song_match.describe(lookup)}»{found}.",
            row=best_row, match=best_match, search_query=best_q)
    log(f"Chọn (đúng tên + ca sĩ): {best_row['title']} – {best_row.get('artist', '')}")
    open_result(page, int(best_row["index"]))
    info = ensure_official(read_song(page), q)
    info.update(candidate=best_row, variant_fallback=False, match_tier=best_match.tier)
    return info


def open_candidate(page, search_query: str, row: dict, requested: str = "",
                   log=print) -> dict:
    """Mở lại 1 ứng viên nhaccuatui đã chấm trước đó (sau khi đã so với nguồn
    khác). Tìm lại đúng ô tìm cũ, khớp lại theo tên + ca sĩ (vị trí có thể đổi)."""
    results = search(page, search_query, log=log)
    pick = None
    for r in results:
        if fold(r.get("title", "")) == fold(row.get("title", "")) and \
                fold(r.get("artist", "")) == fold(row.get("artist", "")):
            pick = r
            break
    if pick is None:
        raise NctNotFound(f"Không mở lại được «{row.get('title')}» trên nhaccuatui.")
    open_result(page, int(pick["index"]))
    info = ensure_official(read_song(page), requested, allow_variant=True)
    info["candidate"] = pick
    info["variant_fallback"] = is_variant_title(info.get("title") or pick.get("title", ""))
    return info


def save_debug(page, folder: Path, name: str) -> str:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{safe_name(name, 40)}_{int(time.time())}.png"
        page.screenshot(path=str(p), full_page=False)
        return str(p)
    except Exception:
        return ""
