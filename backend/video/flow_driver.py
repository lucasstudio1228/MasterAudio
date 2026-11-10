"""
video/flow_driver.py — Tự động hoá Google Flow (flow.google.com) tạo 15 video
bằng chế độ INGREDIENTS/THÀNH PHẦN → clip 8s, Veo 3.1 Fast, 16:9, x1.

Luồng đã kiểm chứng trực tiếp trên Flow (giao diện tiếng Anh/Việt):
  1. Vào Flow → tạo dự án mới.
  2. Mở Settings → Ingredients, 16:9, Veo 3.1 - Fast, 8s, x1.
  3. Upload TẤT CẢ ảnh 0..n vào project qua nút "+" (Trình đơn thêm nội dung
     nghe nhìn) → "Tải lên" (file chooser).
  4. Với từng clip trong kế hoạch 20 clip 1:1, chạy TUẦN TỰ:
       - Xóa prompt cũ → thêm đúng một ảnh nguyên liệu.
       - Nhập prompt chuyển động → "Bắt đầu tạo".
       - Chờ clip render xong → tải xuống media/<project>/clips/clip_XX.mp4.

Không gửi dồn nhiều lượt: Flow chỉ bảo đảm nhận lượt kế khi clip trước đã xong.
Mọi selector nằm trong config.get_selectors('flow').
"""
from __future__ import annotations
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config
from .gpm_session import site_session
from .browser_base import (
    BrowserSession, query_first, click_first, fill_first, wait_for_manual_login,
)
# Dùng chung cách nhận diện lỗi «trang/trình duyệt đã đóng» với bước tạo ảnh.
from .gemini_driver import _is_closed_error

# ── LOG RA FILE (để giám sát được vì không nhìn thấy cửa sổ Chromium/console) ──
_LOGP: Optional[Path] = None


def _log(msg: str) -> None:
    """Ghi 1 dòng có timestamp vào clips/_flow.log (best-effort)."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print("FLOW:", line, flush=True)
    except UnicodeEncodeError:
        # Console Windows có thể vẫn là CP1252 khi chạy ngoài launcher.
        safe = line.encode("ascii", "backslashreplace").decode("ascii")
        print("FLOW:", safe, flush=True)
    if _LOGP is not None:
        try:
            with _LOGP.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# JS: liệt kê MỌI nút đang hiển thị (text + aria-label) để chẩn đoán ô khung.
_JS_BUTTONS = """
() => {
  const out = [];
  document.querySelectorAll('button,[role="button"],[role="radio"]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const style = getComputedStyle(b);
    if (style.visibility === 'hidden' || style.display === 'none') return;
    const t = (b.textContent || '').trim().slice(0, 40);
    const a = b.getAttribute('aria-label') || '';
    if (!t && !a) return;
    out.push({ t, a, x: Math.round(r.x), y: Math.round(r.y) });
  });
  return out.slice(0, 80);
}
"""


def _dump_buttons(page, label: str) -> None:
    """Ghi danh sách nút hiển thị vào log để soi DOM khi kẹt."""
    try:
        btns = page.evaluate(_JS_BUTTONS)
    except Exception as e:
        _log(f"[DIAG {label}] không đọc được nút: {e}")
        return
    _log(f"[DIAG {label}] {len(btns)} nút hiển thị:")
    for b in btns:
        aria = f" aria='{b['a']}'" if b["a"] else ""
        txt = f" text='{b['t']}'" if b["t"] else ""
        _log(f"    ({b['x']},{b['y']}){txt}{aria}")


# JS: soi dialog "Chọn một hình ảnh khung" — options, nút, ảnh, chữ.
_JS_FRAME_DIALOG = """
() => {
  const opts = [...document.querySelectorAll("[role='option']")]
    .map(o => (o.textContent||'').trim().slice(0,40)).filter(Boolean);
  const dlg = document.querySelector("[role='dialog']");
  const btns = (dlg ? [...dlg.querySelectorAll('button')] : [])
     .map(b => (b.getAttribute('aria-label') || (b.textContent||'').trim()).slice(0,30))
     .filter(Boolean);
  return {
    hasDialog: !!dlg,
    dlgImgs: dlg ? dlg.querySelectorAll('img').length : 0,
    dlgText: dlg ? (dlg.textContent||'').trim().slice(0,160) : '',
    opts: [...new Set(opts)].slice(0,30),
    btns: [...new Set(btns)].slice(0,25),
  };
}
"""

# JS: đếm chip ảnh ĐANG gắn trong ô soạn. PHẢI bám theo ô nhập prompt rồi trèo
# lên vài cấp cha (dừng khi khối cao quá nửa màn hình = đã ra ngoài ô soạn).
# Bản cũ đếm mọi <img> ở nửa dưới màn hình nên khi lưới đã có nhiều clip thì
# đếm nhầm cả thumbnail của lưới (project 5: báo "còn 7 nguyên liệu cũ" trong
# khi ô soạn trống) → xoá đi xoá lại vô ích, mỗi clip mất thêm ~90 giây.
_JS_COMPOSER_IMGS = """
() => {
  const box = document.querySelector("div[contenteditable='true']")
           || document.querySelector('textarea');
  if (!box) return -1;
  const small = el => [...el.querySelectorAll('img')].filter(im => {
    const r = im.getBoundingClientRect();
    return r.width > 20 && r.width < 180;
  }).length;
  let node = box.parentElement, best = 0;
  for (let i = 0; i < 6 && node; i++) {
    if (node.getBoundingClientRect().height > window.innerHeight * 0.5) break;
    best = Math.max(best, small(node));
    node = node.parentElement;
  }
  return best;
}
"""


def _dump_frame_dialog(page, which: str) -> None:
    try:
        d = page.evaluate(_JS_FRAME_DIALOG)
    except Exception as e:
        _log(f"    [dialog {which}] lỗi đọc: {e}")
        return
    _log(f"    [dialog {which}] hasDialog={d['hasDialog']} imgs={d['dlgImgs']} "
         f"opts={d['opts']} btns={d['btns']}")
    if d.get("dlgText"):
        _log(f"    [dialog {which}] text='{d['dlgText']}'")


def _composer_imgs(page) -> int:
    try:
        return page.evaluate(_JS_COMPOSER_IMGS)
    except Exception:
        return -1


def _click(page, selectors, timeout_ms=6000) -> bool:
    return click_first(page, selectors, timeout_ms=timeout_ms)


def _pace(page, kind: str = "ui", log: bool = False) -> None:
    """Nghỉ NGẪU NHIÊN giữa các thao tác cho giống người dùng thật.

    Flow gắn cờ «hoạt động bất thường» khi chuỗi thao tác quá nhanh/quá đều;
    đây là cách hợp lệ duy nhất được phép (không stealth/anti-detect/proxy).
    Khoảng nghỉ lấy từ config.FLOW_HUMAN_PACE, override được trong
    video_overrides.json qua khoá "flow_human_pace".
    """
    spans = dict(config.FLOW_HUMAN_PACE)
    try:
        spans.update({k: tuple(v) for k, v in
                      (config.load_overrides().get("flow_human_pace") or {}).items()})
    except Exception:                                   # noqa: BLE001
        pass
    lo, hi = spans.get(kind, spans["ui"])
    # Kể cả override cũ còn 90–180s, không cho bất kỳ nhịp nghỉ chủ động nào
    # vượt quá yêu cầu vận hành 30 giây.
    cap = float(config.FLOW_MAX_DELAY_SEC)
    lo, hi = min(float(lo), cap), min(float(hi), cap)
    lo, hi = min(lo, hi), max(lo, hi)
    secs = random.uniform(lo, hi)
    if log:
        _log(f"    nghỉ {secs:.0f}s cho giống thao tác người ({kind})")
    try:
        page.wait_for_timeout(int(secs * 1000))
    except Exception:                                   # noqa: BLE001
        time.sleep(secs)


def _configure_settings(page, sels) -> None:
    """Mở bảng cài đặt và set động theo config.PARAMS: THÀNH PHẦN, 16:9,
    model (vd Omni 1.1 Flash), thời lượng (vd 10s), x1. Chịu được UI EN/VN."""
    model_name = getattr(config.PARAMS, "flow_model", "Omni 1.1 Flash")
    clip_sec = int(getattr(config.PARAMS, "clip_seconds", 10))
    # Selector động cho option model + radio thời lượng (EN + VN).
    model_option_sels = [
        f"[role='menuitem']:has-text('{model_name}')",
        f"[role='option']:has-text('{model_name}')",
    ]
    duration_sels = [
        f"[role='radio']:text-is('{clip_sec}s')",
        f"[role='radio']:has-text('{clip_sec}s')",
        f"[role='radio']:has-text('{clip_sec} giây')",
    ]

    if not _click(page, sels["settings_trigger"], 8000):
        _dump_buttons(page, "settings-trigger")
        raise RuntimeError("Không mở được Settings của Flow.")
    page.wait_for_timeout(600)
    def ensure_radio(sel_list, label: str, summary_token: str = "") -> None:
        loc = query_first(page, sel_list, 2500)
        if loc is not None:
            try:
                if loc.get_attribute("aria-checked") == "true":
                    _log(f"    {label}: đã chọn sẵn")
                    return
            except Exception:
                pass
            try:
                loc.click()
                page.wait_for_timeout(300)
                return
            except Exception:
                pass

        # Duration/output thường đã là mặc định và hiện ngay trên chip Settings.
        if summary_token:
            trigger = query_first(page, sels["settings_trigger"], 1000)
            try:
                if trigger is not None and summary_token in trigger.inner_text():
                    _log(f"    {label}: xác nhận từ chip Settings")
                    return
            except Exception:
                pass
        _dump_buttons(page, f"setting-{label}")
        raise RuntimeError(f"Không chọn được {label}.")

    # Dự án Flow MỚI (profile sạch) mặc định ra ẢNH — lúc đó bảng cài đặt chỉ
    # có Hình ảnh/Video + model Nano Banana, KHÔNG có 'Thành phần' hay Veo.
    # Bấm Video trước; profile cũ đã ở Video thì bước này vô hại.
    out_video = query_first(page, sels.get("output_video", []), 1500)
    if out_video is not None:
        try:
            if out_video.get_attribute("aria-checked") != "true" \
                    and out_video.get_attribute("aria-selected") != "true":
                out_video.click()
                page.wait_for_timeout(700)
                _log("    loại đầu ra: chuyển sang Video")
            else:
                _log("    loại đầu ra: đã là Video")
        except Exception as e:                      # noqa: BLE001
            _log(f"    (không bấm được nút Video: {type(e).__name__})")

    _pace(page)
    ensure_radio(sels["mode_ingredients"], "mode Ingredients/Thành phần")
    _pace(page)
    ensure_radio(sels["aspect_option_169"], "tỉ lệ 16:9")
    _pace(page)

    model = query_first(page, sels["model_selector"], 5000)
    if model is None:
        raise RuntimeError("Không mở được danh sách model Flow.")
    try:
        current_model = model.inner_text()
    except Exception:
        current_model = ""
    if model_name not in current_model:
        model.click()
        page.wait_for_timeout(500)
        if not _click(page, model_option_sels, 6000):
            _dump_buttons(page, "model-option")
            raise RuntimeError(f"Không chọn được model {model_name}.")
        page.wait_for_timeout(500)
    else:
        _log(f"    model {model_name}: đã chọn sẵn")

    _pace(page)
    ensure_radio(duration_sels, f"thời lượng {clip_sec}s", f"{clip_sec}s")
    _pace(page)
    ensure_radio(sels["outputs_x1"], "số lượng x1", "x1")
    _pace(page, "step")
    # Đóng bảng cài đặt
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(400)


def _clear_ingredients(page, sels) -> None:
    """Xoá hết chip nguyên liệu đang có trong ô soạn (trước khi thêm ảnh mới).

    PHẢI kiểm chứng bằng số ảnh còn trong ô soạn: Flow ẨN khỏi picker chính
    những ảnh đang là nguyên liệu, nên chỉ cần sót 1 chip là clip sau báo
    “không chọn được 1.png” rồi chết cả job (đã gặp ở clip 3/40).
    """
    # Flow hiện tại có Clear prompt: ổn định hơn nút xóa chip (có thể còn ẩn
    # trong DOM sau khi bấm). Nó xóa cả chữ lẫn nguyên liệu của lượt trước.
    for _ in range(3):
        if _composer_imgs(page) <= 0:       # 0 = sạch, -1 = không đọc được
            return
        if _click(page, sels.get("clear_prompt", []), 1500):
            page.wait_for_timeout(600)
            continue
        removed = False
        for _try in range(4):
            hit = False
            for sel in sels.get("remove_ingredient", []):
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click()
                        hit = removed = True
                        page.wait_for_timeout(300)
                        break
                except Exception:
                    pass
            if not hit:
                break
        if not removed:
            break
    left = _composer_imgs(page)
    if left > 0:
        _log(f"    ⚠ còn {left} nguyên liệu cũ trong ô soạn sau 3 lần xoá")


def _match_index(text: str, index: int) -> bool:
    """True nếu text option ứng ĐÚNG {index}.png. Chặn nhầm: tìm '0' không được
    khớp '10.png'/'20.png' (số phải không có chữ số đứng ngay trước)."""
    return re.search(rf"(?<!\d){index}\.png", text or "") is not None


def _find_ready_option(page, index: int):
    """Tìm option ảnh '{index}.png' ĐÃ upload xong trong picker (bỏ trạng thái
    'Uploading'/'Đang tải'). Trả (locator|None, text_gần_nhất)."""
    last = ""
    try:
        opts = page.locator("[role='option']").filter(has_text=f"{index}.png")
        n = opts.count()
    except Exception:
        return None, last
    for k in range(n):
        o = opts.nth(k)
        try:
            txt = (o.inner_text() or "").strip()
        except Exception:
            continue
        if not _match_index(txt, index):
            continue          # ví dụ đang tìm 0 nhưng đây là 10.png
        if re.search(r"uploading|đang tải", txt, re.IGNORECASE):
            last = txt         # đúng ảnh nhưng còn đang upload → chờ tiếp
            continue
        return o, txt
    return None, last


def _wait_ready_option(page, index: int, timeout_ms: int = 90000):
    """Chờ tới khi ảnh '{index}.png' upload xong & hiện trong picker. Kéo danh
    sách để nạp thêm option nếu Flow lazy-render. Trả locator hoặc None."""
    deadline = time.time() + timeout_ms / 1000.0
    last = ""
    while time.time() < deadline:
        loc, txt = _find_ready_option(page, index)
        if loc is not None:
            return loc
        last = txt or last
        try:                               # nạp thêm option (lazy-render)
            opts = page.locator("[role='option']")
            c = opts.count()
            if c:
                opts.nth(c - 1).scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    if last:
        _log(f"    ảnh {index}.png hết {timeout_ms//1000}s vẫn ở trạng thái "
             f"'{last}'")
    return None


def _add_ingredient(page, sels, index: int) -> None:
    """
    Mode Thành phần: bấm '+' thêm thành phần → chọn ảnh '{index}.png' →
    'Thêm vào câu lệnh'. (Chỉ thêm 1 ảnh/clip.)
    """
    before = _composer_imgs(page)
    _pace(page)                       # ngó bảng soạn một nhịp rồi mới bấm '+'
    if not _click(page, sels["add_ingredient"], 6000):
        _dump_buttons(page, f"add-ingredient-{index}")
        raise RuntimeError("Không bấm được '+ Thêm thành phần' (add_ingredient).")
    page.wait_for_timeout(1000)
    _pace(page)                       # người còn phải nhìn danh sách ảnh

    name = f"{index}.png"
    # Chờ đúng ảnh upload xong: sau khi upload 20 ảnh, picker cần thời gian
    # index hóa; ảnh còn 'Uploading' hoặc chưa hiện sẽ khiến chọn thất bại.
    opt = _wait_ready_option(page, index, timeout_ms=90000)
    if opt is None:
        # Flow ẨN khỏi picker những ảnh ĐANG là nguyên liệu → nhiều khả năng
        # chip của lượt trước chưa xoá hết. Đóng picker, xoá sạch, mở lại 1 lần.
        _log(f"    (picker không có {name} — xoá nguyên liệu cũ rồi mở lại)")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass
        _clear_ingredients(page, sels)
        if _click(page, sels["add_ingredient"], 6000):
            page.wait_for_timeout(1200)
            opt = _wait_ready_option(page, index, timeout_ms=20000)
    if opt is None:
        _dump_frame_dialog(page, f"ingredient-{index}")
        raise RuntimeError(
            f"Không chọn được ảnh {name} khi thêm thành phần "
            f"(ảnh chưa upload xong / chưa hiện trong picker sau 90s).")
    try:
        opt.click()
    except Exception:
        try:
            opt.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        opt.click()
    page.wait_for_timeout(500)
    _pace(page)
    # Tùy phiên bản Flow, chọn option có thể tự thêm và đóng picker; phiên bản
    # khác vẫn hiện nút Add to prompt/Thêm vào câu lệnh.
    confirmed = _click(page, sels["frame_confirm"], 1500)
    page.wait_for_timeout(900)
    _pace(page, "step")
    got = _composer_imgs(page)
    ingredient_chip = query_first(page, sels.get("remove_ingredient", []), 1200)
    landed = (before >= 0 and got > before) or ingredient_chip is not None
    _log(f"    thêm nguyên liệu ảnh {index}: confirm={confirmed}, "
         f"ảnh trong ô soạn {before}->{got}, landed={landed}")
    if not landed:
        _dump_buttons(page, f"ingredient-not-landed-{index}")
        raise RuntimeError(f"Ảnh {name} chưa được thêm vào prompt Flow.")


def _upload_images(page, sels, img_paths: list[str],
                   progress_cb=None) -> None:
    """Upload TỪNG ảnh một, nghỉ giữa các lần như người thật (multi-select một
    phát vài ảnh ngay sau khi mở dự án là nhịp máy móc)."""
    if progress_cb:
        progress_cb(f"Upload {len(img_paths)} ảnh vào Flow...", 7.0)
    for n, path in enumerate(img_paths, 1):
        if n > 1:
            _pace(page, "upload")
        if not _click(page, sels["add_media_menu"], 6000):
            raise RuntimeError("Không mở được menu '+' (add_media_menu).")
        _pace(page)
        try:
            with page.expect_file_chooser(timeout=15_000) as fc:
                if not _click(page, sels["upload_menu_item"], 6000):
                    raise RuntimeError("Không bấm được Upload/Tải lên.")
            fc.value.set_files([path])
        except Exception as e:
            raise RuntimeError(f"Upload ảnh {n} thất bại (file chooser): {e}") from e
        _log(f"    upload ảnh {n}/{len(img_paths)}: {Path(path).name}")
        page.wait_for_timeout(3000)


def _open_frame_slot(page, sels, which: str) -> bool:
    """
    Mở dialog chọn khung cho ô Bắt đầu/Kết thúc.
    Ô này ban đầu hiện chữ 'Bắt đầu'/'Kết thúc'; SAU KHI đã chọn ảnh (từ clip
    thứ 2) nó hiển thị THUMBNAIL nên mất chữ → thử thêm các selector theo
    aria-label để bấm lại được ô đã điền.
    """
    base = (sels["start_frame_button"] if which == "start"
            else sels["end_frame_button"])
    kw = "đầu" if which == "start" else "thúc"
    extra = [
        f"button[aria-label*='{kw}']",
        f"[role='button'][aria-label*='{kw}']",
        f"button:has-text('{'Bắt đầu' if which == 'start' else 'Kết thúc'}')",
    ]
    for sel in list(base) + extra:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                return True
        except Exception:
            continue
    return False


def _select_frame(page, sels, which: str, index: int) -> None:
    """
    Bấm ô Bắt đầu/Kết thúc → chọn ảnh theo tên '{index}.png' → 'Thêm vào câu lệnh'.
    (Ảnh trong dialog là button[role=option] có text đúng tên file.)
    """
    before = _composer_imgs(page)
    if not _open_frame_slot(page, sels, which):
        _dump_buttons(page, f"open-{which}")
        raise RuntimeError(
            f"Không bấm được ô {which} (đã thử text + aria-label). "
            f"Xem _flow.log phần [DIAG open-{which}] để lấy selector đúng.")
    page.wait_for_timeout(1200)
    _dump_frame_dialog(page, which)   # soi dialog để biết cách chọn ảnh đúng

    name = f"{index}.png"
    clicked = False
    # Khớp ĐÚNG index (0 không nhầm 10.png) & chờ ảnh sẵn sàng.
    opt = _wait_ready_option(page, index, timeout_ms=45000)
    if opt is not None:
        try:
            opt.click()
            clicked = True
        except Exception:
            clicked = False
    if not clicked:
        # Dự phòng: tìm rồi chọn kết quả đầu tiên
        fill_first(page, sels["frame_search"], str(index), timeout_ms=4000)
        page.wait_for_timeout(1000)
        try:
            loc = page.locator("[role='option']").first
            if loc.count() > 0:
                loc.click()
                clicked = True
        except Exception:
            pass
    if not clicked:
        _dump_buttons(page, f"pick-{which}-{index}")
        raise RuntimeError(
            f"Không chọn được ảnh {name} trong dialog chọn khung.")
    page.wait_for_timeout(500)
    confirmed = _click(page, sels["frame_confirm"], 5000)
    page.wait_for_timeout(1000)
    after = _composer_imgs(page)
    landed = after > before if (before >= 0 and after >= 0) else None
    _log(f"    chọn khung {which}={index}: clickImg=OK confirm={confirmed} "
         f"ảnh_khung {before}->{after} (đã vào khung: {landed})")
    if landed is False:
        # Ảnh KHÔNG vào khung → dump để biết đúng cách. Không raise để còn
        # thấy toàn cảnh clip 1 (start+end) trong log.
        _dump_buttons(page, f"after-confirm-{which}")


# JS: số clip ĐÃ RENDER XONG & TẢI ĐƯỢC.
# ⚠️ Icon 'play_circle' là <mat-icon> (google-symbols), KHÔNG phải <button>.
# ⚠️ Tile ĐANG render cũng có 'play_circle' NGAY nhưng CHƯA có nút ⋯
#    'Tuỳ chọn khác' → phải đòi CẢ HAI (play_circle + ⋯) mới tính là xong.
# ⚠️ KHÔNG lọc theo alt tiếng Anh nữa: UI Flow của tài khoản này là TIẾNG VIỆT
# (alt thật = 'Hình thu nhỏ của video đã tạo') nên bộ đếm luôn ra 0 và driver
# chờ mãi dù clip đã render xong. `flow-video-tile` là thẻ riêng của tile VIDEO
# (ảnh nguyên liệu nằm trong `flow-image-tile`) nên không phụ thuộc ngôn ngữ.
_CLIP_TILE_SEL = "flow-video-tile"
_CLIP_MEDIA_SEL = ("img.thumbnail, video, "
                   "img[alt='Generated video thumbnail'], "
                   "video[aria-label='Generated video']")

_JS_DONE_CLIPS = f"""
() => [...document.querySelectorAll('{_CLIP_TILE_SEL}')]
  .filter(tile => tile.querySelector("{_CLIP_MEDIA_SEL}")).length
"""


def _clip_play_count(page) -> int:
    """Số container video đã render xong; không đếm trùng thumbnail/player."""
    try:
        return page.evaluate(_JS_DONE_CLIPS)
    except Exception:
        return 0


# JS: "chữ ký" media của tile video ĐÃ XONG MỚI NHẤT (tile đầu lưới).
# ⚠️ Lưới Flow ẢO HOÁ: chỉ dựng ~9 tile video cùng lúc → từ clip thứ 10 bộ đếm
#    `_clip_play_count` KẸT ở 9 dù clip mới đã xong (test thật 2026-09-26: chờ
#    oan 900s). Tile mới luôn ở ĐẦU lưới → so chữ ký tile đầu trước/sau khi tạo.
#    Chữ ký = URL media bỏ query (URL ký có Expires/Signature đổi theo lượt tải)
#    và bỏ hậu tố '=mm,..' (biến thể kích thước).
_JS_TOP_DONE_CLIP = f"""
() => {{
  const tile = [...document.querySelectorAll('{_CLIP_TILE_SEL}')]
    .find(t => t.querySelector("{_CLIP_MEDIA_SEL}"));
  if (!tile) return null;
  const m = tile.querySelector('img.thumbnail, img') || tile.querySelector('video');
  const src = m && (m.currentSrc || m.getAttribute('src') || m.getAttribute('poster'));
  if (!src) return null;
  let s = src.split('?')[0];
  const cut = s.lastIndexOf('/');
  const eq = s.indexOf('=', cut);
  return eq > 0 ? s.slice(0, eq) : s;
}}
"""


def _top_done_clip(page) -> Optional[str]:
    """Chữ ký media của clip đã xong MỚI NHẤT (None nếu chưa có/không đọc được)."""
    try:
        return page.evaluate(_JS_TOP_DONE_CLIP)
    except Exception:
        return None


# Số lần bấm Retry tối đa khi Veo báo lỗi tạo clip (thất bại/tạm chặn).
_CLIP_MAX_RETRIES = 3
# Số lần tạo LẠI TOÀN BỘ 1 clip (làm mới nguyên liệu + bấm tạo lại) khi Veo
# không ra clip động. BẮT BUỘC mỗi clip là video động thật từ ĐÚNG ảnh của nó —
# tuyệt đối KHÔNG thay bằng ảnh tĩnh hay clip của ảnh khác. Hết số lần này mà vẫn
# lỗi → coi là LỖI, dừng (không ghép video sai); resume sẽ tạo lại đúng clip đó.
_CLIP_FULL_ATTEMPTS = 3


class FlowBlocked(RuntimeError):
    """Flow TỪ CHỐI tạo clip vì lý do tài khoản/chính sách (không phải lỗi render).

    Ví dụ thật: thẻ «Không thành công — Chúng tôi nhận thấy có hoạt động bất
    thường nào đó… Bạn chưa bị tính phí cho lượt tạo này.» Bấm Retry KHÔNG cứu
    được: Google đã gắn cờ phiên/tài khoản. Phải DỪNG ngay và báo người dùng,
    nếu không mỗi clip sẽ ngồi chờ hết clip_wait_sec × _CLIP_FULL_ATTEMPTS
    (≈45 phút) rồi vẫn hỏng, và bấm Retry liên tục còn làm cờ nặng thêm."""


class _SessionQuota(Exception):
    """Phiên này đã tạo đủ hạn mức clip → đóng trình duyệt, nghỉ, mở phiên mới.

    KHÔNG phải lỗi: đây là cách CHỦ ĐỘNG tránh thẻ «hoạt động bất thường», vì
    Flow gắn cờ sau ~5–6 lượt tạo trong cùng một phiên dù đã nghỉ giống người."""

    def __init__(self, made: int):
        super().__init__(f"đã tạo {made} clip trong phiên này")
        self.made = made


# Chuỗi (thường) trong thẻ lỗi khi Flow CHẶN lượt tạo — đã chuẩn hoá lower().
# Chỉ để những cụm ĐẶC TRƯNG của thẻ lỗi, tránh bắt nhầm chữ trong footer/menu.
_BLOCK_PHRASES = (
    "hoạt động bất thường",       # "Chúng tôi nhận thấy có hoạt động bất thường"
    "unusual activity",
    "hết lượt",                   # hết credit/lượt tạo
    "out of ai credits",
    "out of credits",
    "vi phạm chính sách",
    "violates our policies",
    "content policy",
)

_JS_PAGE_TEXT = "() => (document.body ? document.body.innerText : '')"


def _flow_block_reason(page) -> Optional[str]:
    """Đọc text hiển thị của trang, trả về đoạn trích quanh thông báo CHẶN nếu
    có (None = không bị chặn). Không dùng selector vì Flow đổi DOM liên tục —
    text người dùng đọc được là thứ ổn định nhất."""
    try:
        txt = (page.evaluate(_JS_PAGE_TEXT) or "")[:20000]
    except Exception:
        return None
    # innerText trộn cả tên icon Material ('refresh', 'delete_forever',
    # 've_folder_upload') lẫn nhãn menu → lọc để chỉ giữ CÂU người dùng đọc.
    lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
    for k, line in enumerate(lines):
        low = line.lower()
        if not any(ph in low for ph in _BLOCK_PHRASES):
            continue
        near = [ln for ln in lines[max(0, k - 1): k + 3] if " " in ln]
        return " ".join(" ".join(near).split())[:400]
    return None


def _find_retry_button(page):
    """Trả về locator nút 'Retry/Thử lại' khi Veo tạo clip THẤT BẠI (tile hiện
    trạng thái lỗi: refresh/Retry + Reuse prompt + Delete). None nếu không có."""
    for sel in (
        "button[aria-label='Retry']",
        "button[aria-label='Thử lại']",
        "button[aria-label='Try again']",
        "button:has-text('Retry')",
        "button:has-text('Thử lại')",
    ):
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


_JS_TILE_VIDEO = """() => {
  const v = document.querySelector("[data-mm-tile='1'] video")
         || document.querySelector('video');
  return v ? (v.currentSrc || v.getAttribute('src')) : null;
}"""


def _fetch_tile_media(page, box, dest: Path) -> bool:
    """Tải clip THẲNG từ URL media của tile (đường tải CHÍNH).

    Hover tile → <video src="https://flow.google.com/asb/<id>=mm,22,15"> hiện
    ra; đổi hậu tố sang biến thể chất lượng cao rồi GET bằng request context
    (dùng chung cookie của phiên). Không đụng tới cơ chế download của trình
    duyệt — Cốc Cốc crash (exit 0xC0000005) ngay khi Playwright chặn download,
    làm hỏng cả phiên. Trả False để rơi về đường menu ⋯ → Tải xuống cũ.
    """
    variant = (config.load_overrides().get("flow_media_variant")
               or config.FLOW_MEDIA_VARIANT)
    try:
        page.mouse.move(box["x"], box["y"])
        page.wait_for_timeout(1500)
        src = page.evaluate(_JS_TILE_VIDEO)
        if not src or not str(src).startswith("http"):
            _log("    (không thấy <video> của tile — quay lại menu tải)")
            return False
        src = str(src)
        # <video src> có HAI dạng:
        #   1) ".../asb/<id>=mm,22,15" — đổi được hậu tố sang biến thể nét hơn.
        #   2) URL CDN ĐÃ KÝ "...?Expires=…&KeyName=…&Signature=…" — TUYỆT ĐỐI
        #      không cắt theo dấu '=' (cắt trúng query ⇒ URL rác ⇒ HTTP 403).
        # Clip vừa render xong đôi khi trả 403 cho biến thể nét hơn (Flow còn
        # đang sinh bản đó) → thử lại 1 nhịp, rồi mới hạ về đúng biến thể mà
        # trình phát đang dùng; hỏng hết mới quay lại menu ⋯ → Tải xuống.
        attempts: list[tuple[str, str, int]] = []       # (nhãn, url, chờ trước)
        if "?" not in src and "=" in src.rsplit("/", 1)[-1]:
            base = src.rsplit("=", 1)[0]
            attempts += [(variant, f"{base}={variant}", 0),
                         (variant, f"{base}={variant}", 4000)]
        attempts.append(("bản đang phát", src, 0))
        for label, url, wait_ms in attempts:
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            r = page.request.get(url, timeout=120_000)
            if r.status != 200:
                _log(f"    (tải thẳng {label}: HTTP {r.status})")
                continue
            body = r.body()
            if len(body) < 10_000:
                _log(f"    (tải thẳng chỉ {len(body)} byte)")
                continue
            dest.write_bytes(body)
            _log(f"    ✓ tải thẳng media {label} ({len(body)//1024} KB)")
            return True
        _log("    (tải thẳng không được — quay lại menu tải)")
        return False
    except Exception as e:                              # noqa: BLE001
        _log(f"    (tải thẳng lỗi: {type(e).__name__} — quay lại menu tải)")
        return False


def _wait_and_download_clip(page, sels, dest: Path, wait_sec: int,
                            prev_count: int, on_tick=None,
                            prev_top: Optional[str] = None) -> bool:
    """
    Chờ clip MỚI render xong (số nút play_circle tăng) → định vị tile clip mới
    nhất → hover → ⋯ More options/Tuỳ chọn khác → Download/Tải xuống → chọn
    độ phân giải (mặc định 720p gốc)
    → bắt sự kiện download.
    """
    deadline = time.time() + wait_sec
    t0 = time.time()
    # ⚠️ NGAY sau khi bấm tạo, Flow hiện 1 tile "đang tạo" NHẤP NHÁY có
    # play_circle + ⋯ trong ~1-2s rồi chuyển sang trạng thái render (mất ⋯).
    # → BỎ QUA giai đoạn đầu để không đếm nhầm tile nhấp nháy là clip xong.
    _log(f"    đã bấm tạo — bỏ qua 12s nhấp nháy đầu, rồi chờ render (>{prev_count})")
    page.wait_for_timeout(12000)

    def _abort_if_blocked() -> None:
        """Flow chặn lượt tạo → DỪNG CẢ PIPELINE ngay (không Retry, không chờ)."""
        reason = _flow_block_reason(page)
        if not reason:
            return
        _log(f"    ✗ Flow CHẶN lượt tạo: {reason}")
        _dump_buttons(page, "flow-blocked")
        raise FlowBlocked(
            "Google Flow từ chối tạo clip: “" + reason + "”. Đây là chặn ở phía "
            "tài khoản/phiên Google (không phải lỗi của tool) nên bấm Retry vô "
            "ích. Hãy mở Flow bằng chính profile trình duyệt đó, xử lý cảnh báo "
            "(đăng nhập lại / xác minh / đợi vài giờ / kiểm tra hạn mức), tạo "
            "thử 1 clip bằng tay cho ra kết quả, rồi bấm TẠO LẠI (resume) — các "
            "clip đã xong được giữ nguyên.")

    _abort_if_blocked()

    # Chỉ chấp nhận khi số clip xong > prev_count ỔN ĐỊNH (2 lần liên tiếp,
    # cách nhau ~4s) → tránh mọi nhấp nháy tạm thời.
    # ⚠️ Khi Veo tạo THẤT BẠI (lỗi/tạm chặn) → tile hiện nút 'Retry' và số clip
    #    xong KHÔNG bao giờ tăng. Trước đây ta chờ đủ wait_sec (900s) vô ích rồi
    #    huỷ cả pipeline. Giờ: phát hiện nút Retry SỚM → bấm Retry, gia hạn thời
    #    gian, thử lại tối đa _CLIP_MAX_RETRIES lần.
    found = False
    streak = 0
    last_log = 0.0
    retries = 0
    while time.time() < deadline:
        cnt = _clip_play_count(page)
        top = _top_done_clip(page) if prev_top else None
        # Xong = đếm tăng HOẶC tile đầu lưới đã là media MỚI (bộ đếm kẹt khi
        # lưới ảo hoá ≥ ~9 clip — xem _JS_TOP_DONE_CLIP).
        if cnt > prev_count or (top and top != prev_top):
            streak += 1
            if streak >= 2:
                _log(f"    ✓ clip render XONG sau {time.time()-t0:.0f}s "
                     f"(clip xong={cnt}"
                     f"{', tile đầu đổi' if top and top != prev_top else ''})")
                found = True
                break
        else:
            streak = 0
            # Veo báo lỗi? → bấm Retry để tạo lại thay vì chờ hết giờ.
            retry_btn = _find_retry_button(page)
            if retry_btn is not None:
                # Phân biệt "render lỗi vặt" (Retry cứu được) với "Flow CHẶN"
                # (Retry vô ích) — chỉ kiểm khi đã thấy tile lỗi để đỡ tốn lượt
                # evaluate mỗi 4s.
                _abort_if_blocked()
                if retries >= _CLIP_MAX_RETRIES:
                    # Hết lượt Retry mà tile vẫn đang lỗi → ĐỪNG nằm chờ nốt
                    # wait_sec (trước đây phí tới 15 phút mỗi lần) — thoát sớm
                    # để vòng ngoài tạo lại toàn bộ clip.
                    _log(f"    ✗ vẫn LỖI sau {retries} lần Retry — thoát sớm, "
                         f"không chờ nốt {wait_sec}s.")
                    break
                retries += 1
                _log(f"    ⚠ Veo báo LỖI tạo clip (hiện nút Retry) — bấm "
                     f"Retry tạo lại (lần {retries}/{_CLIP_MAX_RETRIES})")
                try:
                    retry_btn.click()
                except Exception as e:
                    _log(f"    (không bấm được Retry: {e})")
                    _dump_buttons(page, "retry-fail")
                    break
                # Bỏ qua ~12s nhấp nháy đầu của lần render lại + gia hạn giờ.
                page.wait_for_timeout(12000)
                deadline = time.time() + wait_sec
                t0 = time.time()
                last_log = 0.0
                continue
        if time.time() - last_log > 30:
            last_log = time.time()
            _log(f"    ... đang render ({time.time()-t0:.0f}s, clip xong={cnt})")
            # Nhịp này cũng là chỗ DUY NHẤT job nhận lệnh Huỷ/Tạm dừng trong
            # suốt lúc chờ render (callback ném JobCancelled).
            if on_tick:
                on_tick(f"đang render {time.time()-t0:.0f}s")
        page.wait_for_timeout(4000)
    if not found:
        _log(f"    ✗ HẾT {wait_sec}s chưa thấy clip xong (clip xong hiện = "
             f"{_clip_play_count(page)}, đã Retry {retries} lần). "
             f"Có thể Veo còn render hoặc bị chặn.")
        _dump_buttons(page, "wait-timeout")
        return False
    page.wait_for_timeout(1500)   # để tile ổn định
    _pace(page, "after_render")   # người còn xem thử clip rồi mới đi tải
    return _download_newest_clip(page, sels, dest, wait_sec)


# 2) Định vị tile clip MỚI NHẤT (tổ tiên của play_circle đầu tiên có chứa
#    nút ⋯) → đánh dấu nút ⋯ của đúng tile đó + lấy toạ độ để hover.
#    Thử lại vài lần vì ⋯ có thể gắn trễ sau khi render xong.
# Chỉ nhận tile VIDEO (flow-video-tile) — tile ảnh nguyên liệu cũng nằm
# trong flow-grid-tile-container nên không được đếm nhầm.
_JS_MARK_TILE = """() => {
  const tiles = [...document.querySelectorAll('flow-grid-tile-container')]
    .filter(t => t.querySelector(TILE_SEL))
    .filter(t => t.querySelector(MEDIA_SEL));
  const tile = tiles[0];
  if (!tile) return null;
  document.querySelectorAll('[data-mm-tile]').forEach(
    x => x.removeAttribute('data-mm-tile'));
  tile.setAttribute('data-mm-tile', '1');
  const r = tile.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + 24 };
}""".replace("TILE_SEL", json.dumps(_CLIP_TILE_SEL)) \
    .replace("MEDIA_SEL", json.dumps(_CLIP_MEDIA_SEL))


def _download_newest_clip(page, sels, dest: Path, wait_sec: int,
                          pace: bool = True) -> bool:
    """Tải clip ĐÃ XONG MỚI NHẤT (tile đầu lưới) về `dest`: tải thẳng URL
    media, hỏng thì qua menu ⋯ → Tải xuống. Dùng chung cho chế độ tự động và
    chế độ thủ công (người dùng tự bấm tạo, tool chỉ tải + đặt tên)."""
    box = None
    for _try in range(6):
        box = page.evaluate(_JS_MARK_TILE)
        if box:
            break
        page.wait_for_timeout(1500)
    if not box:
        _log("    ✗ không định vị được tile clip mới (nút ⋯ 'Tuỳ chọn khác').")
        _dump_buttons(page, "no-tile")
        return False

    # Đường tải CHÍNH: lấy thẳng file media (nhanh, không phụ thuộc menu/ngôn
    # ngữ, không dùng download manager của trình duyệt). Hỏng → dùng menu cũ.
    if pace:
        _pace(page, "before_download")
    if _fetch_tile_media(page, box, dest):
        return dest.exists() and dest.stat().st_size > 10_000

    res = (config.load_overrides().get("flow_download_resolution")
           or config.FLOW_DOWNLOAD_RESOLUTION)          # vd "720p", "1080p"
    res_sel = f"[role='menuitem']:has-text('{res}')"

    def open_resolution_item():
        """Mở menu của đúng tile mới và trả về item độ phân giải."""
        try:
            # Upscale làm Flow render lại tile, vì vậy data-mm-dl/box cũ có thể
            # mất. Luôn đánh dấu lại tile hoàn tất mới nhất trước mỗi lần mở.
            current_box = page.evaluate(_JS_MARK_TILE)
            if not current_box:
                return None
            page.mouse.move(current_box["x"], current_box["y"])
            page.wait_for_timeout(600)
            more = query_first(page, [
                "[data-mm-tile='1'] button[aria-label='More options']",
                "[data-mm-tile='1'] button[aria-label='Tuỳ chọn khác']",
            ], 6000)
            if more is None:
                return None
            more.click()
            page.wait_for_timeout(700)
        except Exception:
            return None

        dl = None
        for sel in sels.get("video_download", []):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    dl = loc.first
                    break
            except Exception:
                continue
        if dl is None:
            return None
        try:
            dl.hover()
            page.wait_for_timeout(800)
        except Exception:
            pass
        if page.locator(res_sel).count() == 0:
            try:
                dl.click()
                page.wait_for_timeout(800)
            except Exception:
                return None
        item = page.locator(res_sel).first
        try:
            return item if item.count() > 0 and item.is_visible() else None
        except Exception:
            return None

    def close_menus() -> None:
        for _ in range(2):
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
            except Exception:
                pass

    # 3–5) 720p tải ngay. 1080p lần đầu khởi tạo job upscale; sau khi thông báo
    # "Upscaling your video" biến mất, mở menu lại để tải file đã upscale.
    res_item = open_resolution_item()
    if res_item is None:
        _log(f"    ✗ không thấy tùy chọn tải '{res}'.")
        _dump_buttons(page, "no-resolution")
        close_menus()
        return False
    try:
        _log(f"    tải {res}...")
        # Nếu video đã upscale sẵn, download xuất hiện ngay.
        with page.expect_download(timeout=15_000) as di:
            res_item.click()
        di.value.save_as(str(dest))
    except Exception as e:
        if "1080" not in res:
            _log(f"    ✗ tải xuống lỗi: {e}")
            close_menus()
            return False
        _log("    1080p chưa có sẵn — Flow đang upscale, chờ hoàn tất...")
        close_menus()
        notice = page.get_by_text("Upscaling your video", exact=False)
        try:
            notice.wait_for(state="hidden", timeout=wait_sec * 1000)
        except Exception:
            _log(f"    ✗ upscale 1080p chưa xong sau {wait_sec}s")
            return False
        page.wait_for_timeout(1500)
        res_item = open_resolution_item()
        if res_item is None:
            _log("    ✗ upscale xong nhưng không mở lại được mục 1080p.")
            return False
        try:
            with page.expect_download(timeout=120_000) as di:
                res_item.click()
            di.value.save_as(str(dest))
        except Exception as retry_error:
            _log(f"    ✗ tải 1080p sau upscale lỗi: {retry_error}")
            close_menus()
            return False

    close_menus()

    return dest.exists() and dest.stat().st_size > 10_000


def _clip_done(out_dir, idx: int) -> bool:
    p = out_dir / f"clip_{idx:02d}.mp4"
    try:
        return p.exists() and p.stat().st_size > 10_000
    except OSError:
        return False


def _load_motions(img_dir) -> dict[str, str]:
    """Đọc prompt chuyển động AI theo từng ảnh từ images/prompts.json (do
    prompt_gen sinh). Trả {} nếu không có/không hợp lệ (→ dùng mặc định)."""
    try:
        raw = json.loads((img_dir / "prompts.json").read_text(encoding="utf-8"))
        motions = raw.get("motions")
        if isinstance(motions, dict):
            return {str(k): v.strip() for k, v in motions.items()
                    if isinstance(v, str) and v.strip()}
    except Exception:
        pass
    return {}


def _style_wrapper(style: str | None) -> Callable[[str], str]:
    """Hàm bọc prompt clip: phong cách (2D/3D/tả thực) ở ĐẦU + phủ định cứng
    MOTION_NEGATIVE ở CUỐI. Chế độ tự động và chế độ thủ công dùng CHUNG để
    prompt người dùng dán tay giống hệt prompt tool tự nhập.

    Override "clip_prompt_mode": "safe" → bản gọn veo_safety (tả tích cực, bỏ
    hex) khi Flow/Gemini từ chối "I can't generate that video"."""
    style_motion = config.style_info(style).get("motion") or ""
    if config.clip_prompt_mode() == "safe":
        from . import veo_safety

        def wrap_safe(m: str) -> str:
            m = (m or "").strip()
            return veo_safety.safe_clip_prompt(m, style_motion) if m else m
        return wrap_safe

    from .veo_safety import INGREDIENT_LOCK as short_lock
    from .veo_safety import SCENERY_LOCK as short_scenery_lock

    def wrap(m: str) -> str:
        m = (m or "").strip()
        # Hồ sơ dựng thời bản gọn mang câu khoá ngắn → đổi lại câu khoá đầy đủ.
        if m.startswith(short_lock):
            m = config.INGREDIENT_LOCK_FULL + m[len(short_lock):]
        elif m.startswith(short_scenery_lock):
            m = config.INGREDIENT_LOCK_SCENERY + m[len(short_scenery_lock):]
        # Chỉ cảnh vật → phủ định người/thú thay cho phủ định khói/tay/mặt.
        motion_negative = config.motion_negative(m)
        scenery = config.is_scenery_prompt(m)
        if scenery:
            # Cảnh vật ĐỘNG (2026-10-27): bỏ câu khoá máy/siêu chậm của hồ sơ cũ.
            m = config.liven_scenery_motion(m)
        if style_motion and not m.lower().startswith(style_motion.lower()):
            m = f"{style_motion}, {m}" if m else style_motion
        # Gắn phủ định CỨNG vào CUỐI mọi prompt (chặn khói/đầu-ngược/méo tay…);
        # khối phủ định cũ (khác bản hiện hành) đã nhúng sẵn → thay, không nhân đôi.
        cut = m.find("|| STRICTLY FORBIDDEN")
        if cut > 0 and motion_negative.strip() not in m:
            m = m[:cut].rstrip()
        if scenery and m and config.SCENERY_LIVING_MOTION not in m:
            cut = m.find("|| STRICTLY FORBIDDEN")
            head, tail = (m[:cut].rstrip(), " " + m[cut:]) if cut > 0 else (m.rstrip(), "")
            if not head.endswith((".", "!", "?")):
                head += "."
            m = f"{head} {config.SCENERY_LIVING_MOTION}{tail}"
        if m and motion_negative and motion_negative.strip() not in m:
            m = f"{m}{motion_negative}"
        return m
    return wrap


def motion_base(cast_mode: str | None) -> str:
    """Câu chuyển động dự phòng (khi thiếu prompt AI của clip). Override
    flow_motion_prompt viết cho nhân vật → bỏ qua ở chế độ chỉ cảnh vật."""
    if config.single_image():
        return config.LOOP_MOTION_PROMPT_SINGLE
    if cast_mode == "scenery":
        return config.FLOW_MOTION_PROMPT_SCENERY
    return config.load_overrides().get("flow_motion_prompt") or config.FLOW_MOTION_PROMPT


def _fallback_variants(plain_base: str) -> list[str]:
    if config.is_scenery_prompt(plain_base):
        # Cảnh vật ĐỘNG: mỗi clip dự phòng một kiểu máy quay khác nhau.
        return [
            plain_base + ", smooth dolly forward into the scene",
            plain_base + ", lateral tracking shot gliding to the right",
            plain_base + ", gentle crane rise revealing the wider view",
            plain_base + ", slow orbit around the main landmark",
            plain_base + ", aerial drift over the place",
        ]
    return [
        plain_base,
        plain_base + ", very slow pan to the right",
        plain_base + ", slow gentle push-in",
        plain_base + ", slight gentle tilt-up",
        plain_base + ", very slow pan to the left",
    ]


def cover_fallback_motion(idx: int, cast_mode: str | None) -> str:
    """Tool Cover: prompt dự phòng của clip `idx` khi hồ sơ thiếu / Muse từ chối —
    00 intro, 01 loop, 02.. góc quay nhân vật (nguyên liệu là 2 bảng, KHÔNG phải
    ảnh bìa → không dùng câu «giữ đúng khung ảnh tham chiếu»)."""
    if idx == config.INTRO_CLIP:
        return config.intro_motion_prompt(cast_mode)
    if idx in config.COVER_ANGLE_CLIPS:
        from .cover_scene import angle_motions
        return angle_motions("the place shown on the scene sheet", "",
                             cast_mode or "")[str(idx)]
    return config.LOOP_MOTION_PROMPT_SINGLE


def assert_english_prompts(items) -> None:
    """Chốt chặn cuối: KHÔNG gửi/hiện prompt còn tiếng Việt cho Flow (người
    dùng yêu cầu 100% tiếng Anh — Flow/Gemini hiểu tiếng Anh tốt nhất)."""
    from .translate import is_foreign
    bad = [idx for idx, text in items if is_foreign(text or "")]
    if bad:
        raise RuntimeError(
            f"Prompt clip {bad[:10]} còn chữ không phải tiếng Anh — dừng, không gửi Flow. "
            "Hồ sơ prompt cũ cần dịch lại (mở lại tab Video để tool tự dịch).")


def clip_prompt_list(project_id: int, project_name: str | None = None,
                     style: str | None = None,
                     params: Optional[config.VideoParams] = None) -> list[dict]:
    """Danh sách ĐỦ N clip: chỉ số, nguyên liệu (ảnh), prompt ĐÃ BỌC đúng như
    tool gửi Flow, và clip đã có trên đĩa chưa. Dùng cho chế độ thủ công."""
    params = params or config.PARAMS
    total = params.total_clips
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    wrap = _style_wrapper(style)
    ai_motions = _load_motions(img_dir)
    cast_mode = config.images_cast_mode(img_dir)
    variants = _fallback_variants(motion_base(cast_mode))
    items = []
    single = config.single_image(params)
    for idx, ings in enumerate(config.generate_ingredient_plan(total)):
        if single:
            # Tool Cover: 00 intro, 01 loop, 02.. góc quay — prompt nằm trong hồ sơ.
            raw = ai_motions.get(str(idx)) or cover_fallback_motion(idx, cast_mode)
        elif config.IMG_THUMBNAIL in ings:
            raw = config.intro_motion_prompt(cast_mode)
        else:
            raw = ai_motions.get(str(idx)) or variants[idx % len(variants)]
        items.append({"index": idx, "name": f"clip_{idx:02d}.mp4",
                      "ingredients": list(ings), "prompt": wrap(raw),
                      "done": _clip_done(out_dir, idx)})
    return items


def generate_clips(
    project_id: int,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
    style: str | None = None,
    only: Optional[list[int]] = None,
) -> list[str]:
    """
    Tạo clip (chế độ THÀNH PHẦN). Trả về danh sách path clip.

    Quy trình 3 ảnh: clip 00 (intro) dựng lại ảnh bìa — nguyên liệu ảnh 0 + 1
    + 2, prompt INTRO_MOTION_PROMPT. Mọi clip còn lại dùng CÙNG hai nguyên liệu
    — ảnh 1 (bảng nhân vật chính) + ảnh 2 (bảng linh thú); thứ phân biệt là
    PROMPT bối cảnh (images/prompts.json → motions, key = CHỈ SỐ CLIP).

    resume=True: GIỮ clip đã tải, chỉ tạo tiếp clip còn thiếu. Nếu đã đủ →
    không mở trình duyệt.
    restart (mặc định): XOÁ clip cũ + kế hoạch cũ, làm lại từ đầu.
    only=[k,...]: CHỈ tạo lại đúng các clip theo chỉ số (clip_XX), GIỮ NGUYÊN
    mọi clip khác. Nguyên liệu mỗi clip là cố định nên không cần _plan.json cũ.
    """
    global _LOGP
    params = params or config.PARAMS
    sels = config.get_selectors("flow")
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_file = out_dir / "_plan.json"
    total = params.total_clips

    # only-mode: tạo lại clip lẻ → coi như resume (giữ clip khác), nhưng ÉP tạo
    # lại đúng những clip trong danh sách.
    only_set: set[int] = set()
    if only is not None:
        only_set = {int(k) for k in only if 0 <= int(k) < total}
    single_mode = bool(only_set)
    resume_eff = resume or single_mode

    # Kế hoạch nguyên liệu: clip 00 (intro) = [0, 1, 2] dựng lại ảnh bìa, các
    # clip khác = [1, 2]. Deterministic ⇒ không phụ thuộc _plan.json (khác quy
    # trình cũ ánh xạ 1:1 ngẫu nhiên).
    plan: list[list[int]] = config.generate_ingredient_plan(total)
    cast_mode = config.images_cast_mode(img_dir)
    roles = config.image_roles(cast_mode)

    # Luôn cần HAI bảng nhân vật; ảnh 0 (thumbnail) chỉ cần khi còn phải tạo
    # clip intro (kiểm ngay dưới, sau khi biết clip nào đã xong).
    for i in sorted({i for ings in plan for i in ings} - {config.IMG_THUMBNAIL}):
        if not (img_dir / f"{i}.png").exists():
            raise FileNotFoundError(
                f"Thiếu ảnh {i}.png ({roles.get(i, '')}) trong "
                f"{img_dir} — hãy tạo ảnh trước.")

    # Clip DỰ PHÒNG tĩnh (Veo lỗi) được ĐÁNH DẤU để resume TẠO LẠI bằng Veo —
    # nếu không, file .mp4 tĩnh trông y hệt clip thật nên resume sẽ bỏ qua và ảnh
    # tĩnh "dính" mãi trong video. Đọc lại tập fallback đã lưu ở lần chạy trước.
    fallback_idx: set[int] = set()
    if resume_eff and plan_file.exists():
        try:
            raw = json.loads(plan_file.read_text(encoding="utf-8"))
            fb = raw.get("fallback") if isinstance(raw, dict) else None
            if isinstance(fb, list):
                fallback_idx = {int(x) for x in fb}
        except Exception:
            fallback_idx = set()
    if resume_eff and config.intro_clip_needs_refresh(project_id, project_name):
        fallback_idx.add(config.INTRO_CLIP)

    def _is_done(k: int) -> bool:
        """RESUME coi clip k là XONG khi có file hợp lệ VÀ không phải clip tĩnh
        dự phòng (clip tĩnh cần Veo tạo lại thành video thật). only-mode: clip
        được chọn luôn coi là CHƯA xong để ép tạo lại (ghi đè)."""
        if k in only_set:
            return False
        return _clip_done(out_dir, k) and k not in fallback_idx

    def _save_plan() -> None:
        try:
            plan_file.write_text(json.dumps(
                {"plan": plan, "clips": plan_map,
                 "fallback": sorted(fallback_idx)}, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    if not resume_eff:
        # RESTART: xoá clip cũ + kế hoạch + log
        for old in out_dir.glob("clip_*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass

    _LOGP = out_dir / "_flow.log"
    try:                                    # bắt đầu log mới mỗi lần chạy
        _LOGP.write_text("", encoding="utf-8")
    except Exception:
        pass
    _mode_label = (f"ONLY {sorted(only_set)}" if single_mode
                   else "RESUME" if resume else "RESTART")
    _log(f"=== BẮT ĐẦU tạo clip project {project_id} ({_mode_label}) ===")

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    # Lưu kế hoạch đầy đủ ngay để resume lần sau đọc lại tập fallback.
    plan_map: dict[str, list[int]] = {f"clip_{k:02d}.mp4": list(ings)
                                      for k, ings in enumerate(plan)
                                      if _is_done(k)}
    _save_plan()

    # Resume mà đã đủ clip → khỏi mở trình duyệt.
    if resume_eff and all(_is_done(k) for k in range(total)):
        _log("Đã đủ clip — bỏ qua (resume)")
        _p("Đã đủ clip (resume)", 100.0)
        return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)]
    # Prompt chuyển động: nhẹ nhàng, chậm, mượt (video sẽ còn được làm chậm 0.7x
    # và blend khi ghép). Xoay vòng vài biến thể cho đa dạng.
    plain_base = motion_base(cast_mode)
    # Phong cách (2D/3D) sẽ được gắn lên ĐẦU MỌI prompt chuyển động để Veo giữ
    # đúng nét hoạt hình của ảnh nguồn, không "làm thật hoá" khi tạo chuyển động.
    _style_wrap = _style_wrapper(style)

    # Prompt BỐI CẢNH riêng cho từng CLIP do AI sinh. Đọc từ images/prompts.json
    # (motions, key = CHỈ SỐ CLIP — không còn là chỉ số ảnh). Thiếu → mặc định.
    ai_motions = _load_motions(img_dir)
    if ai_motions:
        _log(f"dùng {len(ai_motions)} prompt bối cảnh AI theo từng clip")
    # Biến thể camera mặc định (dự phòng khi clip không có prompt AI).
    fallback_variants = _fallback_variants(plain_base)
    # Mọi clip dùng chung hai bảng nhân vật → chỉ upload đúng hai ảnh đó, một
    # lần, kể cả khi tạo 40 clip.
    todo_images = sorted({
        img for clip_idx, ings in enumerate(plan) for img in ings
        if not (resume_eff and _is_done(clip_idx))
    })
    missing = [i for i in todo_images if not (img_dir / f"{i}.png").exists()]
    if missing:
        raise FileNotFoundError(
            f"Thiếu ảnh {', '.join(f'{i}.png' for i in missing)} "
            f"({', '.join(roles.get(i, '') for i in missing)}) trong "
            f"{img_dir} — clip intro 00 dựng từ ảnh bìa; hãy tạo ảnh trước.")
    img_paths = [str(config.clip_ingredient_path(project_id, project_name, i))
                 for i in todo_images]

    _log(f"kế hoạch nguyên liệu: clip {config.INTRO_CLIP:02d} (intro) × ảnh "
         f"{list(config.INTRO_INGREDIENTS)}, còn lại {total - 1} clip × ảnh "
         f"{list(config.SHEET_IMAGES)}")
    if resume_eff:
        done_idx = [k for k in range(total) if _is_done(k)]
        _log(f"resume: đã có {len(done_idx)} clip {done_idx}, tạo tiếp phần thiếu"
             + (f" (gồm {len(fallback_idx)} clip tĩnh cần làm lại: "
                f"{sorted(fallback_idx)})" if fallback_idx else ""))
    saved: list[str] = []
    failed_idx: list[int] = []      # clip Veo KHÔNG ra clip động

    def _session_pass(quota: int) -> None:
        """MỘT phiên trình duyệt: tạo hết clip còn thiếu.

        Tách hàm để khi cửa sổ trình duyệt tự đóng giữa chừng (lỗi môi
        trường, đã gặp cả ở Gemini) thì mở phiên MỚI chạy tiếp: clip đã tải
        nằm sẵn trên đĩa nên vòng lặp chỉ làm nốt phần thiếu.

        quota = số clip TỐI ĐA tạo trong phiên này (Flow gắn cờ sau ~5–6 lượt
        trong cùng một phiên). Tạo đủ quota mà vẫn còn clip thiếu → ném
        _SessionQuota để người gọi đóng trình duyệt, nghỉ dài rồi mở phiên mới.
        """
        saved.clear()
        failed_idx.clear()
        made = 0
        with site_session("flow", "Flow", BrowserSession) as sess:   # GPM nếu đã chọn ở Cài đặt
            page = sess.new_page()
            _p("Mở Google Flow...", 2.0)
            page.goto(config.FLOW_URL, wait_until="domcontentloaded")
            # Một số tài khoản vào trang /about trước dashboard.
            _click(page, sels.get("flow_entry", []), 5000)
            wait_for_manual_login(
                page, sels["new_project"] + sels["add_media_menu"], "Flow",
                timeout_sec=300)
            _pace(page, "open", log=True)   # người vào trang còn nhìn quanh

            _p("Tạo dự án Flow mới...", 4.0)
            _log("tạo dự án mới")
            if not _click(page, sels["new_project"], 8000):
                # Profile vừa xoá cache: Flow mở thẳng vào 1 dự án TRỐNG (có
                # menu '+' nhưng không có tile nào) → dùng luôn dự án đó.
                empty = page.evaluate(
                    "() => !document.querySelector("
                    "'flow-video-tile, flow-image-tile')")
                if not (empty and query_first(page, sels["add_media_menu"],
                                              timeout_ms=3000)):
                    _dump_buttons(page, "new-project")
                    raise RuntimeError("Không bấm được New project/Dự án mới.")
                _log("    đã ở sẵn trong dự án trống → dùng luôn")
            page.wait_for_timeout(2500)
            _pace(page, "step")

            _p(f"Cài đặt Thành phần · {params.aspect_ratio} · "
               f"{params.flow_model} · {params.clip_seconds}s · x1...", 6.0)
            _log(f"cài đặt Thành phần/{params.aspect_ratio}/"
                 f"{params.flow_model}/{params.clip_seconds}s/x1")
            _configure_settings(page, sels)

            _p("Upload ảnh vào Flow...", 8.0)
            _log(f"upload {len(img_paths)} ảnh cần dùng: {todo_images}")
            _upload_images(page, sels, img_paths, progress_cb)
            _log("upload ảnh xong")
            _pace(page, "step")

            for clip_idx, ings in enumerate(plan):
                pct = 10.0 + (clip_idx / max(total, 1)) * 88.0
                dest = out_dir / f"clip_{clip_idx:02d}.mp4"
                imgs_label = "+".join(str(i) for i in ings)
                # RESUME: clip đã có sẵn (là clip Veo thật) → bỏ qua.
                if resume_eff and _is_done(clip_idx):
                    _log(f"--- CLIP {clip_idx+1}/{total} — ĐÃ CÓ, bỏ qua")
                    saved.append(str(dest))
                    plan_map[f"clip_{clip_idx:02d}.mp4"] = list(ings)
                    continue
                # TUẦN TỰ: clip trước đã render + tải xong (_wait_and_download_clip
                # chặn đồng bộ) mới sang clip này — KHÔNG nghỉ thêm giữa các clip.
                _log(f"--- CLIP {clip_idx+1}/{total}  (nguyên liệu {imgs_label}) ---")

                # BẮT BUỘC ra clip ĐỘNG thật. Tạo lại TOÀN BỘ tối đa
                # _CLIP_FULL_ATTEMPTS lần (mỗi lần làm mới nguyên liệu + bấm tạo, bên
                # trong còn tự bấm Retry). KHÔNG thay bằng ảnh tĩnh/clip khác.
                success = False
                for attempt in range(1, _CLIP_FULL_ATTEMPTS + 1):
                    tag = f"(lần {attempt}/{_CLIP_FULL_ATTEMPTS})"
                    _p(f"[{clip_idx+1}/{total}] Thêm nguyên liệu {imgs_label} "
                       f"{tag}...", pct)
                    # Đảm bảo không còn menu/overlay & xoá nguyên liệu vòng trước
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                    except Exception:
                        pass
                    _clear_ingredients(page, sels)
                    # CẢ HAI bảng nhân vật vào mỗi clip → giữ đúng nhân vật + linh
                    # thú ở mọi bối cảnh.
                    for img in ings:
                        _add_ingredient(page, sels, img)
                    # Clip intro: làm sống động ĐÚNG ảnh bìa. Các clip khác: prompt
                    # bối cảnh AI của CHÍNH clip đó; thiếu → mặc định.
                    if config.single_image(params):
                        raw_motion = (ai_motions.get(str(clip_idx))
                                      or cover_fallback_motion(clip_idx, cast_mode))
                    elif config.IMG_THUMBNAIL in ings:
                        raw_motion = config.intro_motion_prompt(cast_mode)
                    else:
                        raw_motion = (ai_motions.get(str(clip_idx))
                                      or fallback_variants[clip_idx % len(fallback_variants)])
                    motion = _style_wrap(raw_motion)
                    assert_english_prompts([(clip_idx, motion)])
                    if motion:
                        _pace(page, "before_prompt")
                        _log(f"    prompt clip {clip_idx} {tag}: {motion[:90]}"
                             + ("…" if len(motion) > 90 else ""))
                        fill_first(page, sels["prompt_box"], motion, 5000)
                    prev_count = _clip_play_count(page)   # số clip xong trước khi tạo
                    prev_top = _top_done_clip(page)       # tile xong mới nhất trước khi tạo
                    # Người thật còn đọc lại prompt trước khi bấm Tạo.
                    _pace(page, "before_generate", log=True)
                    _log(f"    bấm tạo {tag} (clip xong hiện tại={prev_count})")
                    _p(f"[{clip_idx+1}/{total}] Đang tạo clip {tag}...", pct)
                    if not _click(page, sels["generate_button"], 8000):
                        _dump_buttons(page, "no-generate")
                        raise RuntimeError("Không bấm được 'Bắt đầu tạo' (generate_button).")
                    if _wait_and_download_clip(
                            page, sels, dest, config.BROWSER.clip_wait_sec, prev_count,
                            on_tick=lambda s, _i=clip_idx, _t=tag, _p2=pct: _p(
                                f"[{_i+1}/{total}] Đang tạo clip {_t} — {s}", _p2),
                            prev_top=prev_top):
                        success = True
                        break
                    _log(f"    ⚠ clip {clip_idx} chưa ra clip động {tag}"
                         + (" — thử tạo lại toàn bộ…" if attempt < _CLIP_FULL_ATTEMPTS
                            else " — HẾT lượt."))

                if not success:
                    # KHÔNG thay thế bằng bất cứ thứ gì (ảnh tĩnh / clip khác đều
                    # sai yêu cầu). Đánh dấu lỗi → cuối vòng DỪNG để không ghép video
                    # sai; resume sẽ tạo lại đúng clip này. Xoá file rác nếu có.
                    _log(f"    ✗ Veo KHÔNG tạo được clip động cho clip {clip_idx} "
                         f"sau {_CLIP_FULL_ATTEMPTS} lần tạo lại.")
                    try:
                        if dest.exists():
                            dest.unlink()
                    except OSError:
                        pass
                    failed_idx.append(clip_idx)
                    fallback_idx.add(clip_idx)   # đánh dấu → resume Veo làm lại
                    _save_plan()
                    continue
                _log(f"    ✓ ĐÃ LƯU {dest.name} "
                     f"({dest.stat().st_size//1024} KB)")
                saved.append(str(dest))
                plan_map[f"clip_{clip_idx:02d}.mp4"] = list(ings)
                fallback_idx.discard(clip_idx)   # đã là clip Veo thật → hết fallback
                only_set.discard(clip_idx)       # đã tạo lại xong → thôi ép lại
                _save_plan()                     # giữ 'plan'+'fallback' để resume
                made += 1
                # Hết hạn mức phiên mà vẫn còn clip thiếu → ĐÓNG trình duyệt,
                # nghỉ dài, mở phiên mới (xem chú thích FLOW_CLIPS_PER_SESSION).
                if made >= quota and any(not _is_done(k) for k in range(total)):
                    raise _SessionQuota(made)
                # Nghỉ giữa hai clip: chuỗi lượt liên tiếp không nghỉ chính
                # là thứ khiến Flow gắn cờ «hoạt động bất thường».
                if clip_idx + 1 < total:
                    _p(f"[{clip_idx+1}/{total}] Nghỉ giữa hai clip...", pct)
                    _pace(page, "between_clips", log=True)

    # CHẠY NHIỀU PHIÊN. Mỗi phiên: mở trình duyệt → dự án Flow mới → tạo tối đa
    # `quota` clip → đóng → nghỉ dài → phiên sau làm nốt phần thiếu (clip đã lưu
    # nằm sẵn trên đĩa). Ba lý do sang phiên mới:
    #   • hết hạn mức (_SessionQuota) — CHỦ ĐỘNG, tránh bị gắn cờ;
    #   • Flow đã gắn cờ (FlowBlocked) — nghỉ lâu hơn rồi thử lại;
    #   • cửa sổ tự đóng (_is_closed_error, Cốc Cốc/Gemini đều từng gặp).
    ov = config.load_overrides()
    quota = max(1, int(ov.get("flow_clips_per_session")
                       or config.FLOW_CLIPS_PER_SESSION))
    max_sessions = max(1, int(ov.get("flow_max_sessions")
                              or config.FLOW_MAX_SESSIONS))
    max_block = max(0, int(ov.get("flow_max_block_retries")
                           or config.FLOW_MAX_BLOCK_RETRIES))

    def _span(key: str, default: tuple[float, float]) -> tuple[float, float]:
        try:
            v = ov.get(key)
            raw = (float(v[0]), float(v[1])) if v else default
        except Exception:                       # noqa: BLE001
            raw = default
        cap = float(config.FLOW_MAX_DELAY_SEC)
        lo, hi = min(raw[0], cap), min(raw[1], cap)
        return (min(lo, hi), max(lo, hi))

    def _cooldown(span: tuple[float, float], why: str, s_next: int) -> None:
        """Nghỉ dài GIỮA HAI PHIÊN (trình duyệt đã đóng). Gọi progress_cb mỗi
        20 giây để lệnh Huỷ/Tạm dừng vẫn ăn trong lúc chờ."""
        secs = random.uniform(*span)
        left_n = len([k for k in range(total) if not _is_done(k)])
        _log(f"⏸ {why} — nghỉ {secs:.0f} giây rồi mở phiên {s_next} "
             f"(còn {left_n} clip)")
        end = time.time() + secs
        while True:
            remain = end - time.time()
            if remain <= 0:
                break
            _p(f"{why} — nghỉ {remain:.0f} giây nữa rồi chạy tiếp "
               f"(còn {left_n} clip)", 10.0)
            time.sleep(min(20.0, remain))

    blocked = 0
    closed = 0
    _CLOSED_RETRIES = 3     # cửa sổ chết liên tiếp 3 lần = lỗi môi trường thật
    for s_try in range(1, max_sessions + 1):
        try:
            _session_pass(quota)
            break
        except _SessionQuota as q:
            # Phiên này có tiến độ thật → xoá bộ đếm hỏng của các phiên trước.
            resume_eff, blocked, closed = True, 0, 0
            _cooldown(_span("flow_session_cooldown", config.FLOW_SESSION_COOLDOWN),
                      f"Đã tạo {q.made} clip trong phiên {s_try}", s_try + 1)
        except FlowBlocked as e:
            # CHỈ nghỉ-rồi-thử-lại với thẻ «hoạt động bất thường» (cờ chống tự
            # động hoá, tự hết sau ~20–30 phút). Hết lượt/hết credit thì nghỉ
            # bao lâu cũng vô ích → ném ra ngay cho người dùng xử lý.
            txt = str(e).lower()
            if not ("hoạt động bất thường" in txt or "unusual activity" in txt):
                raise
            blocked += 1
            if blocked > max_block or s_try >= max_sessions:
                raise
            resume_eff = True
            _log(f"⚠ Flow gắn cờ (lần {blocked}/{max_block}): {e}")
            _cooldown(_span("flow_block_cooldown", config.FLOW_BLOCK_COOLDOWN),
                      f"Flow gắn cờ «hoạt động bất thường» (lần {blocked})",
                      s_try + 1)
        except Exception as e:                  # noqa: BLE001
            closed += 1
            if (not _is_closed_error(e) or closed >= _CLOSED_RETRIES
                    or s_try >= max_sessions):
                raise
            resume_eff = True   # giữ clip đã tải, phiên sau chỉ làm phần thiếu
            left = [k for k in range(total) if not _is_done(k)]
            _log(f"⚠ trình duyệt đóng giữa chừng — mở lại (phiên {s_try + 1}), "
                 f"còn {len(left)} clip: "
                 f"{left[:10]}{'…' if len(left) > 10 else ''}")
            _p(f"Trình duyệt đóng giữa chừng — mở lại (phiên {s_try + 1}), "
               f"còn {len(left)} clip...", 10.0)
            time.sleep(5)
    else:
        raise RuntimeError(
            f"Đã chạy {max_sessions} phiên Flow mà vẫn chưa đủ {total} clip.")

    # BẮT BUỘC mỗi clip là video động thật từ đúng ảnh của nó. Nếu còn clip Veo
    # KHÔNG tạo được → coi là LỖI: DỪNG, KHÔNG ghép video (tránh video sai/thiếu
    # chuyển động). Các clip lỗi đã đánh dấu fallback nên bấm TẠO LẠI (resume) sẽ
    # chỉ tạo lại đúng những clip này, giữ nguyên các clip đã xong.
    if failed_idx:
        fb = [f"clip_{k:02d}" for k in failed_idx]
        msg = (f"Veo KHÔNG tạo được {len(failed_idx)} clip động (mỗi clip đã thử "
               f"tạo lại {_CLIP_FULL_ATTEMPTS} lần): {', '.join(fb)}. ĐÃ DỪNG, "
               f"không ghép video với clip thay thế. Hãy bấm TẠO LẠI (resume) để "
               f"tạo lại đúng các clip này (các clip khác giữ nguyên).")
        _log("✗ " + msg)
        _p("✗ " + msg, 99.0)
        raise RuntimeError(msg)

    _p("Xong tạo clip", 100.0)
    return saved
