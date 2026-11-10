"""
video/gemini_driver.py — Tự động hoá Gemini web (gemini.google.com) để tạo ảnh.

Chọn model 3.1 Pro (2 bước: '3.1 Pro' rồi mức suy luận 'Cao' — trước
2026-10-13 là 'Tư duy mở rộng'), sinh N ảnh:
  0 = ảnh nền thumbnail (KHÔNG chữ); 1..N-1 = các shot liên kết,
  đồng bộ nhân vật, bối cảnh và diễn tiến ánh sáng.
Lưu vào media/<project>/images/{i}.png.

Nhận & TẢI ảnh (bền, không phụ thuộc selector CSS mong manh):
  - Chờ tới khi có 1 ảnh THẬT mới (naturalWidth/Height >= MIN_IMAGE_DIM) & ổn định.
  - Chờ Gemini TRẢ LỜI XONG rồi mới tải: hover vào ảnh để hiện thanh công cụ,
    thử tải bằng menu ⋯ → 'Tải hình ảnh xuống'; nếu chưa được thì CHUỘT PHẢI
    vào ảnh → 'Tải hình ảnh xuống'; cuối cùng fallback tải trực tiếp từ src.
    Lặp lại tới khi tải được (vì thanh nút xuất hiện trễ sau khi ảnh render).
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (
    BrowserSession, click_first, query_first, wait_for_manual_login,
)

MIN_IMAGE_DIM = 400

# Bỏ qua ảnh ĐÍNH KÈM (chip trong ô soạn + ảnh trong tin nhắn người dùng) — cũng
# là blob: ~1024px nên nếu không lọc sẽ bị nhận nhầm là ảnh Gemini vừa tạo.
_JS_LARGE_IMAGES = """
(minDim) => {
  const skip = 'user-query, uploader-file-preview-container, uploader-file-preview, '
             + 'input-area-v2, .input-area-container';
  return [...document.images]
    .filter(im => im.naturalWidth >= minDim && im.naturalHeight >= minDim
                  && im.src
                  && (im.src.startsWith('http') || im.src.startsWith('blob:'))
                  && !im.closest(skip))
    .map(im => ({ src: im.src, w: im.naturalWidth, h: im.naturalHeight }));
}
"""

_JS_ALL_IMAGE_SRCS = "() => [...document.images].map(im => im.src).filter(Boolean)"

_JS_ATTACHMENTS_READY = """
(sel) => [...document.querySelectorAll(sel)]
  .filter(im => im.complete && im.naturalWidth > 0).length
"""

# Toạ độ tâm ảnh theo src (để hover / chuột phải đúng ảnh mới nhất).
_JS_IMAGE_RECT = """
(src) => {
  const im = [...document.images].find(i => i.src === src);
  if (!im) return null;
  const r = im.getBoundingClientRect();
  return { x: r.x + r.width/2, y: r.y + r.height/2,
           top: r.y, bottom: r.y + r.height, w: r.width, h: r.height };
}
"""


# Thu thập nhãn nút thật để chẩn đoán khi không tìm được nút tải.
_JS_DIAG = """
() => {
  const labels = new Set();
  document.querySelectorAll('button[aria-label]').forEach(b => {
    const a = b.getAttribute('aria-label'); if (a) labels.add(a);
  });
  const texts = new Set();
  document.querySelectorAll("[role='menuitem'],[role='menuitemradio'],button,a,span")
    .forEach(e => {
      const t = (e.textContent || '').trim();
      if (t && t.length < 60 &&
          (t.includes('Tải') || t.toLowerCase().includes('download'))) texts.add(t);
    });
  return { labels: [...labels].slice(0, 40), texts: [...texts].slice(0, 20) };
}
"""


# Gemini còn đang trả lời/vẽ không + chữ câu trả lời cuối (để báo lỗi rõ).
# Dò live 2026-09-30: lúc đang tạo, ô soạn có nút aria «Ngừng tạo câu trả lời»;
# khi vẽ ảnh thì hiện dòng «Creating your image» + khung xám chờ.
_JS_GEN_STATE = """
() => {
  const vis = e => !!(e && e.offsetParent !== null);
  const stop = [...document.querySelectorAll('button[aria-label]')].some(b => vis(b)
      && /ngừng tạo|dừng tạo|stop (generating|response)/i.test(b.getAttribute('aria-label')));
  const rs = [...document.querySelectorAll('model-response')];
  const last = rs[rs.length - 1];
  const txt = last ? (last.innerText || '').trim() : '';
  const creating = /creating your image|đang tạo (hình )?ảnh|generating image/i.test(txt);
  return { busy: stop || creating, reply: creating ? '' : txt.slice(0, 400) };
}
"""


_JS_SCROLL_LAST = """
() => { const rs = document.querySelectorAll('model-response');
        if (rs.length) rs[rs.length - 1].scrollIntoView({block: 'end'}); }
"""

# Câu lệnh đứng ĐẦU mọi prompt ảnh gửi Gemini. Dò live 2026-09-30: prompt mở
# đầu «CHARACTER MODEL SHEET…» đôi khi bị Gemini hiểu là nhờ NHẬN XÉT prompt
# («This is an exceptionally well-crafted prompt…», «I cannot generate
# images…») → không vẽ. Câu lệnh ngắn này ép gọi công cụ tạo ảnh.
IMAGE_DIRECTIVE = ("Generate an image now using your image generation tool. Do not "
                   "review, critique or rewrite the description below; reply with "
                   "the generated image. Image description:\n\n")

# Câu trả lời kiểu lỗi máy chủ Gemini → nghỉ lâu hơn trước khi thử lại.
_SERVER_ERR = ("something went wrong", "encountered an error", "try again",
               "đã xảy ra lỗi", "thử lại", "having a hard time",
               "try something else", "tool has been disabled",
               "can't generate images", "cannot generate the image")


def _diagnostic(page) -> str:
    try:
        d = page.evaluate(_JS_DIAG)
        return (f"aria-labels nút: {d.get('labels')}; "
                f"chữ có 'Tải/download': {d.get('texts')}")[:600]
    except Exception:
        return "(không đọc được nhãn nút)"


def _thumbnail_keywords() -> str:
    import random
    kws = (config.load_overrides().get("thumbnail_keywords")
           or config.THUMBNAIL_KEYWORDS)
    n = min(3, len(kws))
    return " · ".join(random.sample(list(kws), n)) if n else ""


_COMPOSITION_MARK = "THUMBNAIL COMPOSITION"   # = thumbnail_recipe.COMPOSITION_MARK


def _prompt_for(index: int, topic: str, params: config.VideoParams,
                prompts_override: Optional[dict] = None,
                style: Optional[str] = None) -> str:
    # Ưu tiên prompt do AI sinh từ ý tưởng người dùng (prompts_override),
    # rồi tới overrides file, cuối cùng là DEFAULT_PROMPTS.
    prompts = prompts_override
    if prompts is None:
        prompts = config.load_overrides().get("prompts")
    if prompts is None:
        prompts = config.DEFAULT_PROMPTS
    if not 0 <= index < params.image_count:
        raise ValueError(f"Chỉ số ảnh {index} ngoài bộ {params.image_count} ảnh.")
    tmpl = prompts.get(str(index)) if isinstance(prompts, dict) else None
    if not isinstance(tmpl, str) or not tmpl.strip():
        raise ValueError(
            f"Thiếu prompt ảnh {index}/{params.image_count - 1}. "
            "Hãy tạo lại bộ prompt đầy đủ; không dùng cảnh mặc định rời rạc "
            "vì sẽ làm mất đồng bộ nhân vật và bối cảnh.")
    fmt = {"topic": topic, "keywords": _thumbnail_keywords()}
    try:
        body = tmpl.format(**fmt)
    except Exception:
        body = tmpl.replace("{topic}", topic)
    # ÉP phong cách hoạt hình (2D/3D) vào ĐẦU + phủ định cấm ảnh thật ở CUỐI,
    # áp dụng cho MỌI prompt (AI-sinh, overrides file, DEFAULT_PROMPTS).
    # Riêng ảnh 1 & 2 là BẢNG PHÂN TÍCH NHÂN VẬT: dùng bộ bọc tài liệu, vì
    # prefix điện ảnh/bokeh ở đầu prompt sẽ khiến Gemini vẽ cảnh phim.
    # Chế độ CHỈ CẢNH VẬT: ảnh 1 & 2 là bảng thiết kế BỐI CẢNH (không nhân vật).
    if index in config.SHEET_IMAGES and config.is_scenery_prompt(body):
        wrapped = config.wrap_world_sheet_style(body, style)
    elif index in config.SHEET_IMAGES:
        wrapped = config.wrap_sheet_style(body, style)
    else:
        wrapped = config.wrap_style(body, style)
    if index == 0 and _COMPOSITION_MARK in body:
        # Prompt bìa có công thức bố cục riêng (thumbnail_recipe) → không ép
        # "chữ trái"; chỉ nhắc cấm chữ + giữ đúng vùng trống đã mô tả.
        wrapped += (
            " || MOST IMPORTANT (absolute priority): THE IMAGE MUST CONTAIN NO "
            "TEXT AT ALL. NEVER draw any letters/title/name/typography/caption/"
            "watermark/logo/numbers in the image — not a single character. "
            "Keep the THUMBNAIL COMPOSITION exactly as described, including the "
            "clean empty title area (the title is added later by software)."
        )
    elif index == 0:
        # Thumbnail: model sinh ảnh KHÔNG đánh vần được → cấm tuyệt đối vẽ chữ,
        # tiêu đề sẽ được overlay bằng font thật sau (thumbnail.render_title).
        # Đặt CUỐI cùng để ưu tiên cao nhất; yêu cầu chừa khoảng trống bên trái.
        wrapped += (
            " || MOST IMPORTANT (absolute priority): THE IMAGE MUST CONTAIN NO "
            "TEXT AT ALL. NEVER draw any letters/title/name/typography/caption/"
            "watermark/logo/numbers in the image — not a single character. "
            "Eye-catching thumbnail composition, "
            + ("the main landmark placed off-centre to the RIGHT, NO people or "
               "animals, " if config.is_scenery_prompt(body)
               else "characters placed off-centre to the RIGHT, ")
            + "LEAVE clean negative space on the LEFT HALF for the title "
            "to be added later."
        )
    return wrapped


_OPEN_MENU = ["[role='menu']", "[role='listbox']", ".cdk-overlay-backdrop-showing"]


def _close_menus(page, tries: int = 3) -> bool:
    """Đóng menu/overlay còn mở (Escape). Trả True nếu không còn menu nào.

    Menu chế độ còn mở CHE ô nhập prompt → click ô nhập chờ 30s rồi lỗi, trước
    đây bị nuốt thành «không tìm thấy ô nhập prompt» (project 35, 2026-10-13)."""
    for _ in range(tries):
        if not _visible(page, _OPEN_MENU):
            return True
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(400)
    return not _visible(page, _OPEN_MENU)


def _pick_menu_option(page, sels, key: str) -> bool:
    """Mở menu chế độ → bấm mục `key`. Không thấy mục → Escape (KHÔNG để menu
    mở che ô nhập)."""
    if not click_first(page, sels["model_selector"], timeout_ms=6000):
        return False
    page.wait_for_timeout(600)
    ok = click_first(page, sels[key], timeout_ms=6000)
    page.wait_for_timeout(800)
    _close_menus(page)
    return ok


def _select_model(page, sels) -> bool:
    """2 bước: (1) '3.1 Pro'; (2) mức suy luận 'Cao' (từ 2026-10-13 thay cho
    'Tư duy mở rộng'); rồi bật công cụ «Tạo hình ảnh». Best-effort; trả True
    nếu công cụ vẽ đã bật."""
    _pick_menu_option(page, sels, "model_option_pro")
    _pick_menu_option(page, sels, "model_option_thinking")
    return _enable_image_tool(page, sels)


def _visible(page, selectors) -> bool:
    for s in selectors or []:
        try:
            loc = page.locator(s)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _enable_image_tool(page, sels, ratio: str = "") -> bool:
    """Bật công cụ «Tạo hình ảnh» (+ tỷ lệ khung). Từ 2026-10 Gemini KHÔNG tự
    gọi công cụ vẽ nếu chưa bật → trả chữ «image generation tool has been
    disabled». Không giữ qua chat mới ⇒ gọi lại sau mỗi lần mở chat."""
    ok = _visible(page, sels.get("image_tool_chip"))
    if not ok and click_first(page, sels["upload_menu"], timeout_ms=6000):
        page.wait_for_timeout(800)
        for s in sels.get("image_tool_item", []):
            try:
                item = page.locator(s).first
                if item.count() and item.is_visible():
                    if item.get_attribute("aria-checked") != "true":
                        item.click(timeout=4000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(1000)
        ok = _visible(page, sels.get("image_tool_chip"))
        if not ok:
            page.keyboard.press("Escape")
    ratio = ratio or config.VideoParams().aspect_ratio
    if ok and ratio and click_first(page, sels.get("image_ratio_button", []),
                                    timeout_ms=3000):
        page.wait_for_timeout(600)
        if not click_first(page, [f"[role='menuitemradio'][aria-label='{ratio}']",
                                  f"[role='menuitemradio']:has-text('{ratio}')"],
                           timeout_ms=3000):
            page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    return ok


def _fill_prompt(page, sels, text: str) -> str:
    """Điền prompt vào ô soạn. Trả "" nếu được, ngược lại LÝ DO THẬT (không nuốt
    lỗi như browser_base.fill_first): ô bị menu/hộp thoại che, hay thật sự
    không thấy ô."""
    reason = "không tìm thấy ô nhập prompt"
    for round_ in range(2):
        _close_menus(page)
        loc = query_first(page, sels["prompt_box"], timeout_ms=8000)
        if loc is None:
            reason = "không tìm thấy ô nhập prompt"
        else:
            try:
                loc.click(timeout=8000)
                try:
                    loc.fill(text, timeout=15000)
                except Exception:
                    page.keyboard.insert_text(text)
                return ""
            except Exception as e:
                covered = _visible(page, _OPEN_MENU + ["[role='dialog']"])
                first = (str(e).splitlines() or [""])[0][:120]
                reason = ("ô nhập prompt bị menu/hộp thoại che" if covered
                          else f"không bấm/điền được ô nhập prompt ({type(e).__name__}: {first})")
        if round_ == 0:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(800)
    return reason


def _start_new_chat(page) -> None:
    """Bắt đầu cuộc trò chuyện MỚI để CẮT ngữ cảnh dài. Hội thoại quá dài (vd
    ảnh 37/41 trong cùng 1 chat) khiến Gemini hay trả CHỮ / từ chối / chậm →
    time-out. Mỗi prompt đã tự chứa khối danh tính nhân vật nên KHÔNG cần giữ
    mạch chat. Ưu tiên bấm nút 'Trò chuyện mới'; không có → reload thẳng
    GEMINI_URL (URL /app luôn mở chat trống)."""
    sels = config.get_selectors("gemini")
    clicked = False
    try:
        clicked = click_first(page, sels.get("new_chat", []), timeout_ms=3000)
    except Exception:
        clicked = False
    if not clicked:
        try:
            page.goto(config.GEMINI_URL, wait_until="domcontentloaded")
        except Exception:
            pass
    page.wait_for_timeout(1500)


def _attach_images(page, sels, paths: list[str], timeout_sec: int = 90) -> None:
    """Đính kèm ảnh tham chiếu vào ô soạn Gemini ('+' → Tải tệp lên → file
    chooser) rồi CHỜ tới khi đủ chip ảnh hiện ra. Lỗi → RuntimeError (không được
    lặng lẽ tạo thumbnail không có tham chiếu)."""
    if not click_first(page, sels["upload_menu"], timeout_ms=8000):
        raise RuntimeError("Không mở được menu tải tệp của Gemini (upload_menu).")
    page.wait_for_timeout(800)
    try:
        with page.expect_file_chooser(timeout=12_000) as fc:
            if not click_first(page, sels["upload_files_item"], timeout_ms=6000):
                raise RuntimeError("Không thấy mục 'Tải tệp lên' (upload_files_item).")
        fc.value.set_files(paths)
    except RuntimeError:
        page.keyboard.press("Escape")
        raise
    except Exception as e:
        page.keyboard.press("Escape")
        raise RuntimeError(f"Đính kèm ảnh tham chiếu thất bại ({type(e).__name__}: {e})") from e
    sel = ", ".join(sels["attachment_preview"])
    deadline = time.time() + timeout_sec
    ready = 0
    while time.time() < deadline:
        try:
            ready = int(page.evaluate(_JS_ATTACHMENTS_READY, sel) or 0)
        except Exception:
            ready = 0
        if ready >= len(paths):
            page.wait_for_timeout(2500)   # để upload lên server xong hẳn
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(f"Ảnh tham chiếu chưa đính kèm xong ({ready}/{len(paths)} "
                       f"sau {timeout_sec}s).")


def _gen_state(page) -> dict:
    try:
        return page.evaluate(_JS_GEN_STATE) or {}
    except Exception:
        return {}


def _wait_new_image(page, seen: set[str], timeout_sec: int,
                    max_sec: Optional[int] = None,
                    info: Optional[dict] = None) -> Optional[str]:
    """Chờ 1 ảnh THẬT mới (đủ lớn, chưa có) và đã load ổn định. Trả về src.

    Đo live 2026-09-30 (Pro · Mở rộng): 1 ảnh mất ~200s — vượt mốc cứng 180s
    cũ nên cả 3 lần thử đều bị cắt NGAY trước khi ảnh ra. Nay:
      • quá `timeout_sec` mà Gemini VẪN đang tạo (nút «Ngừng tạo câu trả lời» /
        «Creating your image») → chờ tiếp tới `max_sec`, không bỏ ảnh đang vẽ;
      • Gemini đã trả lời XONG mà không có ảnh (trả chữ / từ chối / lỗi máy
        chủ) → dừng sớm, ghi nguyên văn câu trả lời vào `info["reply"]`.
    Ảnh blob hiện CHẬM vài giây SAU khi nút «Ngừng» biến mất (dò live: chat
    «Here is the character model sheet…» có ảnh nhưng lần chờ cũ bỏ cuộc đúng
    lúc đó) → chỉ kết luận "không có ảnh" khi Gemini đã rảnh ≥ QUIET_SEC, và
    cuộn tới câu trả lời mới nhất để ảnh lazy-load được nạp.
    """
    QUIET_SEC = 30
    start = time.time()
    hard = start + max(timeout_sec, max_sec or 0)
    last: Optional[str] = None
    stable = 0
    quiet_since: Optional[float] = None
    while True:
        now = time.time()
        try:
            cands = page.evaluate(_JS_LARGE_IMAGES, MIN_IMAGE_DIM)
        except Exception:
            cands = []
        new = [c for c in cands if c.get("src") and c["src"] not in seen]
        if new:
            new.sort(key=lambda c: c["w"] * c["h"], reverse=True)
            top = new[0]["src"]
            if top == last:
                stable += 1
            else:
                last, stable = top, 1
            if stable >= 2:
                return top
        else:
            st = _gen_state(page)
            busy = bool(st.get("busy"))
            if info is not None:
                info["busy"] = busy
                if st.get("reply"):
                    info["reply"] = st["reply"]
            quiet_since = None if busy else (quiet_since or now)
            quiet = (now - quiet_since) if quiet_since else 0.0
            if quiet >= QUIET_SEC:
                # Đã trả lời xong mà chỉ có chữ, không có ảnh.
                if st.get("reply"):
                    if info is not None:
                        info["text_only"] = True
                    return None
                if now - start >= timeout_sec:
                    return None
            try:
                page.evaluate(_JS_SCROLL_LAST)
            except Exception:
                pass
        if now >= hard:
            return None
        page.wait_for_timeout(3000)


def _find_menu_item(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _find_last_visible(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                cand = loc.last
                if cand.is_visible():
                    return cand
        except Exception:
            continue
    return None


class BrowserClosed(RuntimeError):
    """Cửa sổ trình duyệt bị đóng giữa chừng (người dùng tắt / trình duyệt thoát)."""


def _is_closed_error(e: Exception) -> bool:
    """True nếu lỗi là 'trang/trình duyệt đã đóng' (đáng mở lại phiên mới).

    Playwright ném TargetClosedError/Error với thông điệp tiếng Anh; ta không
    import lớp lỗi để khỏi phụ thuộc phiên bản, chỉ soi tên lớp + nội dung."""
    if isinstance(e, BrowserClosed):
        return True
    text = f"{type(e).__name__}: {e}".lower()
    return ("targetclosed" in text
            or "has been closed" in text
            or "browser has been closed" in text
            or "target closed" in text)


def _ensure_open(page) -> None:
    """Dừng sớm với thông báo tiếng Việt nếu trang/trình duyệt đã đóng."""
    try:
        closed = page.is_closed()
    except Exception:
        closed = True
    if closed:
        raise BrowserClosed(
            "Cửa sổ trình duyệt đã bị đóng giữa chừng. Hãy để cửa sổ Gemini mở "
            "cho tới khi tạo xong ảnh, rồi bấm 'Tạo tiếp ảnh thiếu' (resume) — "
            "ảnh đã lưu không bị mất.")


def _click_menu_download(page, dest: Path, sels) -> bool:
    """Menu vừa mở → bấm 'Tải hình ảnh xuống', bắt sự kiện download."""
    item = _find_menu_item(page, sels.get("download_menu_item", []))
    if item is None:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
    try:
        with page.expect_download(timeout=60_000) as di:
            item.click()
        di.value.save_as(str(dest))
        return dest.exists() and dest.stat().st_size > 2000
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _download_new_image(page, sels, src: str, dest: Path,
                        wait_sec: int) -> bool:
    """
    Tải ảnh (theo src) đúng cách Gemini, chờ tới khi thanh công cụ sẵn sàng.
    Thử: hover → nút ⋯ → 'Tải hình ảnh xuống'; rồi chuột phải ảnh → menu;
    cuối cùng fallback tải từ src.
    """
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        rect = None
        try:
            rect = page.evaluate(_JS_IMAGE_RECT, src)
        except Exception:
            rect = None

        # Hover vào ảnh để hiện thanh nút hành động
        if rect:
            try:
                page.mouse.move(rect["x"], rect["y"])
                page.wait_for_timeout(500)
                # rê xuống mép dưới ảnh nơi thanh nút thường xuất hiện
                page.mouse.move(rect["x"], min(rect["bottom"] + 20, rect["bottom"]))
                page.wait_for_timeout(500)
            except Exception:
                pass

        # Nút ⋯ "Hiện thêm tuỳ chọn" → menu → 'Tải hình ảnh xuống'
        more = _find_last_visible(page, sels.get("more_button", []))
        if more is not None:
            try:
                more.click()
                page.wait_for_timeout(700)
                if _click_menu_download(page, dest, sels):
                    return True
            except Exception:
                pass

        # Thanh nút có thể xuất hiện trễ → chờ rồi thử lại. Nếu cửa sổ bị đóng
        # giữa chừng thì báo rõ ràng thay vì để TargetClosedError thô nổi lên.
        _ensure_open(page)
        page.wait_for_timeout(2000)

    # Fallback cuối: tải từ src (chỉ dùng được nếu là http, KHÔNG dùng cho blob:)
    if src.startswith("http"):
        try:
            resp = page.context.request.get(src)
            if resp.ok:
                dest.write_bytes(resp.body())
                if dest.stat().st_size > 2000:
                    return True
        except Exception:
            pass
    return False


def _has_image(out_dir, i: int) -> bool:
    p = out_dir / f"{i}.png"
    try:
        return p.exists() and p.stat().st_size > 2000
    except OSError:
        return False


def generate_images(
    project_id: int,
    topic: str,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
    prompts_override: Optional[dict] = None,
    style: Optional[str] = None,
    only: Optional[list[int]] = None,
    _session_impl: Optional[Callable[[list[int], Callable[[int], None]], None]] = None,
) -> list[str]:
    """
    Trả về danh sách đường dẫn ảnh đã tạo (0..n) qua Gemini web.
    resume=True: BỎ QUA ảnh đã có (chỉ tạo ảnh còn thiếu). Nếu đã đủ → không
    mở trình duyệt.
    only=[i,...]: CHỈ tạo lại đúng các ảnh trong danh sách (ghi đè), GIỮ NGUYÊN
    mọi ảnh khác — dùng cho nút "Tạo lại 1 ảnh" lẻ.
    Ảnh 0 giữ SẠCH (không chữ): tiêu đề được overlay ở bước ghép video
    (service.step_assemble) lên clip intro + thumbnail.png bằng font thật, tránh
    chữ nướng sẵn bị Veo tạo lại thành bóng ma / chồng chéo.
    """
    params = params or config.PARAMS
    sels = config.get_selectors("gemini")
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    n = params.image_count
    only_set = None
    if only is not None:
        only_set = sorted({int(i) for i in only if 0 <= int(i) < n})
        if not only_set:
            _p("Không có ảnh hợp lệ để tạo lại.", 100.0)
            return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
    # RESTART: XOÁ hết ảnh cũ trước khi tạo, để KHÔNG bao giờ trộn ảnh mới với
    # ảnh cũ/của lần render trước. Nếu run mới bị dở dang thì bước clip sẽ báo
    # "thiếu ảnh" rõ ràng thay vì âm thầm dùng ảnh cũ để ghép.
    # only-mode KHÔNG xoá gì (chỉ ghi đè đúng ảnh được chọn).
    if not resume and only_set is None:
        removed = 0
        for old in out_dir.glob("*.png"):
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            _p(f"Xoá {removed} ảnh cũ (restart)", 1.0)
    # Chạy theo IMAGE_ORDER (2 bảng phân tích trước, ảnh bìa sau); chỉ số nằm
    # ngoài bảng thứ tự (nếu sau này đổi image_count) xếp tiếp phía sau.
    def _order_key(i: int) -> tuple[int, int]:
        order = config.image_order()
        return (order.index(i), 0) if i in order else (len(order), i)

    if only_set is not None:
        todo = sorted(only_set, key=_order_key)
        _p(f"Tạo lại ảnh cụ thể: {todo}", 1.0)
    else:
        todo = sorted((i for i in range(n)
                       if not (resume and _has_image(out_dir, i))), key=_order_key)
        if resume and not todo:
            _p("Đã đủ ảnh — bỏ qua (resume)", 100.0)
            return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
        if resume:
            _p(f"Resume: còn thiếu ảnh {todo}", 1.0)

    saved: list[str] = []
    seen: set[str] = set()
    done_ok: set[int] = set()
    done_count = 0

    def _mark_done(i: int) -> None:
        done_ok.add(i)

    def _session_pass(todo: list[int], mark_done=_mark_done) -> None:
        """MỘT phiên trình duyệt: tạo các ảnh trong `todo` (đã bỏ ảnh xong).

        Tách hàm để khi cửa sổ bị đóng giữa chừng (lỗi chập chờn đã gặp thật:
        Chromium tự thoát cả lúc đang rảnh) thì mở phiên MỚI chạy tiếp phần
        còn thiếu, thay vì hỏng cả job."""
        nonlocal done_count
        # site="gemini" → Chromium bundled (Cốc Cốc tự đóng cửa sổ ở bước tải
        # ảnh); Flow vẫn dùng Cốc Cốc riêng, xem config.SITE_BROWSER.
        with BrowserSession(site="gemini") as sess:
            page = sess.new_page()
            _p("Mở Gemini...", 2.0)
            page.goto(config.GEMINI_URL, wait_until="domcontentloaded")
            wait_for_manual_login(page, sels["prompt_box"], "Gemini",
                                  timeout_sec=300)

            _p(f"Chọn model {config.GEMINI_IMAGE_MODEL} (tư duy mở rộng) + bật "
               f"công cụ «Tạo hình ảnh»...", 4.0)
            if not _select_model(page, sels):
                _p("CẢNH BÁO: không bật được công cụ «Tạo hình ảnh» của Gemini "
                   "(giao diện đổi?) — vẫn thử gửi prompt.", 4.5)

            try:
                for c in page.evaluate(_JS_LARGE_IMAGES, MIN_IMAGE_DIM):
                    if c.get("src"):
                        seen.add(c["src"])
            except Exception:
                pass

            # BỀN HOÁ: mỗi ảnh thử tối đa _IMAGE_ATTEMPTS lần; và cứ _NEW_CHAT_EVERY
            # ảnh lại mở chat mới để CẮT ngữ cảnh dài (nguyên nhân gốc khiến ảnh ~37/41
            # bị time-out: hội thoại quá dài → Gemini trả chữ/từ chối). Ảnh lưu NGAY
            # từng cái nên nếu vẫn fail thì resume tạo tiếp, KHÔNG mất ảnh đã có.
            _IMAGE_ATTEMPTS = 4   # 2026-10-06: Gemini hay «having a hard time» 1–2 lần liền
            _NEW_CHAT_EVERY = int(getattr(config.BROWSER, "image_new_chat_every", 8) or 8)
            for k, i in enumerate(todo):
                pct = 6.0 + (k / max(len(todo), 1)) * 90.0
                prompt = _prompt_for(i, topic, params, prompts_override, style)
                # Ảnh bìa BẮT BUỘC dựa trên 2 bảng nhân vật (đã tạo trước theo
                # IMAGE_ORDER): đính kèm 1.png + 2.png làm ảnh tham chiếu.
                # Tool Cover: ngược lại — bảng 1/2 vẽ TỪ ảnh bìa (config.image_refs).
                refs: list[str] = []
                need = config.image_refs(i)
                if need and i != config.IMG_THUMBNAIL:
                    refs = [str(out_dir / f"{r}.png") for r in need]
                    missing = [r for r in need if not _has_image(out_dir, r)]
                    if missing:
                        raise RuntimeError(
                            f"Ảnh {i} phải vẽ dựa trên ảnh bìa nhưng chưa có ảnh "
                            f"{missing} — tạo ảnh bìa trước rồi tạo lại ảnh {i}.")
                elif i == config.IMG_THUMBNAIL:
                    refs = [str(out_dir / f"{r}.png") for r in config.thumbnail_refs()]
                    missing = [r for r in config.thumbnail_refs() if not _has_image(out_dir, r)]
                    if missing:
                        raise RuntimeError(
                            f"Ảnh bìa phải dựa trên 2 bảng nhân vật nhưng chưa có ảnh "
                            f"{missing} — tạo 2 bảng nhân vật trước rồi tạo lại ảnh bìa.")
                    prompt += config.thumbnail_ref_note(prompt)

                # Cắt ngữ cảnh định kỳ (trước khi gửi), tránh chat phình quá dài.
                if _NEW_CHAT_EVERY > 0 and done_count > 0 \
                        and done_count % _NEW_CHAT_EVERY == 0:
                    _p(f"Mở cuộc trò chuyện mới (cắt ngữ cảnh dài) trước ảnh {i}...", pct)
                    _start_new_chat(page)
                    _select_model(page, sels)

                last_err = ""
                saved_ok = False
                text_only_hits = 0
                dest = out_dir / f"{i}.png"
                for attempt in range(1, _IMAGE_ATTEMPTS + 1):
                    tag = f"(lần {attempt}/{_IMAGE_ATTEMPTS})"
                    if attempt > 1:
                        # Thử lại: mở CHAT MỚI + chọn lại model + chờ hồi (rate-limit),
                        # rồi gửi lại prompt tự-chứa-danh-tính này từ ngữ cảnh sạch.
                        _p(f"[{k+1}/{len(todo)}] Ảnh {i} lỗi ({last_err[:70]}) — "
                           f"thử lại {tag}...", pct)
                        _start_new_chat(page)
                        _select_model(page, sels)
                        # Lỗi máy chủ Gemini («something went wrong») → nghỉ lâu hơn.
                        server_err = any(k_ in last_err.lower() for k_ in _SERVER_ERR)
                        page.wait_for_timeout(60_000 if server_err
                                              else min(6000 * attempt, 18000))

                    if refs:
                        _p(f"[{k+1}/{len(todo)}] Đính kèm {len(refs)} ảnh tham chiếu "
                           f"cho ảnh {i} {tag}...", pct)
                        try:
                            _attach_images(page, sels, refs)
                        except RuntimeError as e:
                            last_err = str(e)
                            continue
                        # Ảnh đính kèm KHÔNG phải ảnh Gemini tạo ra.
                        try:
                            seen.update(page.evaluate(_JS_ALL_IMAGE_SRCS))
                        except Exception:
                            pass

                    _p(f"[{k+1}/{len(todo)}] Gửi prompt ảnh {i} {tag}...", pct)
                    fill_err = _fill_prompt(page, sels, IMAGE_DIRECTIVE + prompt)
                    if fill_err:
                        last_err = fill_err
                        continue
                    page.wait_for_timeout(400)
                    if not click_first(page, sels["send_button"], timeout_ms=4000):
                        page.keyboard.press("Enter")
                    if refs:
                        # Tin nhắn vừa gửi hiện lại 2 ảnh đính kèm (src có thể đổi);
                        # Gemini chưa kịp vẽ trong vài giây đầu → coi mọi ảnh lớn
                        # lúc này là ảnh tham chiếu, không phải kết quả.
                        page.wait_for_timeout(6000)
                        try:
                            for c in page.evaluate(_JS_LARGE_IMAGES, MIN_IMAGE_DIM):
                                if c.get("src"):
                                    seen.add(c["src"])
                        except Exception:
                            pass

                    _p(f"[{k+1}/{len(todo)}] Đang chờ Gemini tạo ảnh {i} {tag}...", pct)
                    t_wait = time.time()
                    winfo: dict = {}
                    src = _wait_new_image(page, seen, config.BROWSER.image_wait_sec,
                                          config.BROWSER.image_wait_max_sec, winfo)
                    if not src:
                        waited = int(time.time() - t_wait)
                        reply = " ".join((winfo.get("reply") or "").split())[:220]
                        if winfo.get("text_only"):
                            text_only_hits += 1
                            last_err = (f"Gemini trả lời bằng CHỮ, không có ảnh sau {waited}s"
                                        f" — «{reply}»" if reply else
                                        f"Gemini trả lời xong nhưng không có ảnh ({waited}s)")
                        elif winfo.get("busy"):
                            last_err = (f"Gemini vẫn đang tạo ảnh sau {waited}s (quá mức "
                                        f"chờ tối đa — máy chủ Gemini đang chậm/quá tải)")
                        else:
                            last_err = (f"chờ {waited}s không thấy ảnh mới"
                                        + (f" — Gemini trả: «{reply}»" if reply else
                                           " (Gemini có thể bị giới hạn)"))
                        continue

                    _p(f"[{k+1}/{len(todo)}] Đang tải ảnh {i} {tag}...", pct + 3.0)
                    if not _download_new_image(page, sels, src, dest, wait_sec=90):
                        # Đã có ảnh nhưng tải hỏng → đánh dấu src đã thấy để lần thử
                        # sau nhận ĐÚNG ảnh mới, không "dính" lại ảnh này.
                        seen.add(src)
                        last_err = "không tải được ảnh (menu ⋯/chuột phải/src đều fail)"
                        continue

                    seen.add(src)
                    saved.append(str(dest))
                    saved_ok = True
                    mark_done(i)
                    done_count += 1
                    _p(f"[{k+1}/{len(todo)}] Đã lưu ảnh {i} ({config.image_roles(config.images_cast_mode(out_dir)).get(i, '')})", pct + 6.0)
                    break
                    # Ảnh 0 giữ SẠCH — tiêu đề overlay ở bước ghép (step_assemble).

                if not saved_ok:
                    raise RuntimeError(
                        f"Ảnh {i}: thất bại sau {_IMAGE_ATTEMPTS} lần thử "
                        f"({last_err}). Đã tạo {done_count}/{len(todo)} ảnh — bấm "
                        f"'Tạo tiếp ảnh thiếu' (resume) để làm nốt phần còn lại mà "
                        f"KHÔNG mất ảnh đã có."
                        + (f" CHẨN ĐOÁN → {_diagnostic(page)}"
                           if last_err.startswith("không tải được") else "")
                        + (" GỢI Ý: cả " f"{_IMAGE_ATTEMPTS}" " lần Gemini chỉ trả lời "
                           "chữ chung chung với CÙNG một prompt → nhiều khả năng bộ "
                           "lọc an toàn của Gemini chặn ngầm nội dung prompt (vd cận "
                           "cảnh chân trần/cơ thể). Sửa mô tả nhân vật trong prompt "
                           "rồi bấm lại." if text_only_hits >= _IMAGE_ATTEMPTS else ""))

    # Trình duyệt có thể tự đóng giữa chừng (đã gặp thật, cả khi đang rảnh).
    # Ảnh lưu NGAY từng cái, nên chỉ cần mở phiên mới và làm nốt phần còn thiếu.
    _SESSION_ATTEMPTS = 3
    for s_try in range(1, _SESSION_ATTEMPTS + 1):
        pending = [i for i in todo if i not in done_ok]
        if not pending:
            break
        try:
            # _session_impl: chỗ cắm cho test (không mở trình duyệt thật).
            (_session_impl or _session_pass)(pending, _mark_done)
            break
        except Exception as e:
            if not _is_closed_error(e) or s_try >= _SESSION_ATTEMPTS:
                raise
            left = [i for i in todo if i not in done_ok]
            _p(f"Trình duyệt đóng giữa chừng — mở lại (phiên {s_try + 1}/"
               f"{_SESSION_ATTEMPTS}) và tạo tiếp ảnh {left}...", 5.0)
            seen.clear()          # phiên mới = chat mới, ảnh cũ không còn trên trang
            time.sleep(5)

    _p("Xong tạo ảnh", 100.0)
    # Trả về TẤT CẢ ảnh hiện có (gồm ảnh cũ khi resume + ảnh mới tạo).
    return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
