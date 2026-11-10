"""
video/muse_image.py — Tạo ẢNH (2 bảng nhân vật + ảnh bìa) bằng Muse.ai — phương
án DỰ PHÒNG khi Gemini không tạo được ảnh (hết lượt ảnh / «Sorry, something went
wrong» / không bật được công cụ «Tạo hình ảnh»). Cùng hợp đồng với
gemini_driver.generate_images (resume / restart / only).

Dò live 2026-10-07 (project #23): gửi prompt bảng nhân vật KHÔNG đính kèm → Muse
làm ngay (bảng trạng thái «Generating image»), ~105s ra 1 khung
[data-testid=hatch-chat-attachment-presentation-image] trong tin nhắn assistant,
<img src="blob:…"> WebP 1920x1280 (3:2 — Muse KHÔNG ra 16:9 dù prompt yêu cầu);
nút «Open generated image image» → hộp thoại có «Download» (cùng file với blob).
⇒ tải bằng fetch(blob) trong trang (dự phòng: nút Download), rồi hậu kỳ PNG 16:9:
  • bảng nhân vật (1, 2): CHÈN LỀ màu nền hai bên (không cắt mất nhãn/ô cận cảnh);
  • ảnh bìa (0): cắt giữa 16:9 (cảnh minh hoạ) → ≥1920x1080.
Ảnh bìa đính kèm 1.png + 2.png làm tham chiếu như Gemini.

Dùng chung profile GPM + phiên với muse_video (cùng tab Muse). An toàn: không tự
đăng nhập, không vượt CAPTCHA; Muse báo hết lượt → dừng (MuseVideoLimit, câu «Muse
báo hết lượt» để lô sản xuất DỪNG thay vì đốt credit Suno). Chữ trên trang chỉ
ĐỌC như dữ liệu, không làm theo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .job_manager import JobCancelled
from .muse_video import (MuseUnavailable, MuseVideoLimit, _GPMSession, _SEL,
                         _capped_delay, _is_closed_error, _pause_ticking, _send, _setting,
                         _status_kind, classify_muse_reply, profile_id,
                         reply_after, wait_connected)

_LOGP: Optional[Path] = None

# Đặt ĐẦU prompt: yêu cầu xuất file + làm ngay, không hỏi lại. Muse có "bộ nhớ dự
# án" (tự ghi nhân vật vào memory) → dặn chỉ vẽ theo mô tả này, không lấy nhân
# vật của cuộc trò chuyện/project trước.
MUSE_IMAGE_SPEC = (
    "Please generate one still IMAGE right now without asking any follow-up questions. "
    "Output settings (file settings only, never drawn in the picture): a single still "
    "image (not a video, not an animation), 16:9 landscape, at least 1920x1080 pixels. "
    "Draw ONLY what the description below says: do not reuse characters, places or "
    "details from earlier conversations or from your memory. Image description:")

IMAGE_SEL = "[data-testid='hatch-chat-attachment-presentation-image']"
OPEN_BTN = "button[aria-label='Open generated image image']"
DIALOG_DOWNLOAD = "[role=dialog] button[aria-label='Download']"
MIN_DIM = 800                  # ảnh kết quả 1920x1280; ảnh đính kèm/xem trước nhỏ hơn

_IMAGE_ATTEMPTS = 3
_SESSION_ATTEMPTS = 2
_NOIMG_STABLE_SEC = 150        # câu trả lời đứng yên bao lâu mới coi là «không có ảnh»
_NOIMG_MIN_SEC = 240           # và tổng thời gian chờ tối thiểu
_ECHO_GRACE_SEC = 60           # tin vừa gửi chưa hiện → chưa đọc câu trả lời (khỏi đọc nhầm câu cũ)


class MuseImageDenied(RuntimeError):
    """Muse TỪ CHỐI đúng bản mô tả này (không phải lỗi công cụ) → không gửi lại y
    hệt; service chuyển máy dự phòng (Gemini). RuntimeError (KHÔNG phải
    ValueError) để _generate_images vẫn chuyển máy dự phòng."""


# Từ chối NỘI DUNG của đúng bản mô tả — đo thật project #35 (2026-10-12): 13 lần gửi
# Y HỆT đều bị «I couldn't make this particular model sheet…», «Still denied on
# this exact spec … needs to be a genuinely different version». Khác lỗi công cụ
# («the video tool hit an internal error» — gửi lại là được).
_DENIED_RE = re.compile(
    r"\bdenied\b|\bdenial\b|\bexact spec\b|\bgenuinely different\b|"
    r"(?:can(?:'|’)?t|cannot|couldn(?:'|’)?t|could not|unable to)\s+(?:\w+\s+)?"
    r"(?:produce|make|generate|create)\s+(?:this|that)\s+particular", re.I)
_TOOL_ERR_RE = re.compile(
    r"internal (?:server )?error|something went wrong|timed out|"
    r"(?:hit|ran into|encountered|threw) (?:an? )?(?:\w+ )?(?:error|glitch)", re.I)

# Bản «thiết kế lại» duy nhất được thử sau khi bị từ chối: bỏ tên studio/hoạ sĩ
# có thương hiệu → tả phong cách chung (giống veo_safety); nội dung giữ nguyên.
_BRAND_RES = [(re.compile(p, re.I), r) for p, r in (
    (r"\b(?:Studio\s+)?Ghibli(?:[- ]style)?(?:\s+anime)?", "Japanese anime"),
    (r"\bPixar(?:[- ]style)?|\bDisney(?:[- ]style)?", "stylized 3D animation"),
    (r",?\s*\bMakoto Shinkai(?:[- ]style| style)?|,?\s*\bShinkai(?:[- ]style)?", ""),
)]
_DENIED_FILE = "_muse_denied.json"


def is_spec_denial(reply: str) -> bool:
    """Muse từ chối đúng bản mô tả (gửi lại y hệt vô ích), không phải lỗi công cụ."""
    r = reply or ""
    return bool(_DENIED_RE.search(r)) and not _TOOL_ERR_RE.search(r)


def neutral_style(prompt: str) -> str:
    t = prompt or ""
    for rx, rep in _BRAND_RES:
        t = rx.sub(rep, t)
    t = re.sub(r"\(\s*[,;]?\s*\)", "", t)
    t = re.sub(r",\s*\)", ")", t)
    return re.sub(r"[ \t]{2,}", " ", t)


def _phash(prompt: str) -> str:
    return hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:16]


def _load_denied(out_dir: Path) -> dict:
    try:
        d = json.loads((out_dir / _DENIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _remember_denied(out_dir: Path, i: int, prompt: str) -> None:
    """Sổ bản mô tả Muse đã từ chối (theo ảnh) — lần chạy/resume sau KHÔNG gửi lại."""
    d = _load_denied(out_dir)
    hs = d.setdefault(str(i), [])
    if _phash(prompt) not in hs:
        hs.append(_phash(prompt))
    try:
        (out_dir / _DENIED_FILE).write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def _was_denied(out_dir: Path, i: int, prompt: str) -> bool:
    return _phash(prompt) in set(_load_denied(out_dir).get(str(i), []))


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(f"[muse-image] {msg}", flush=True)
    if _LOGP:
        try:
            with _LOGP.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def image_prompt(body: str) -> str:
    """Prompt gửi Muse = MUSE_IMAGE_SPEC + mô tả ảnh (đã bọc phong cách)."""
    b = (body or "").strip()
    return b if b.startswith(MUSE_IMAGE_SPEC) else f"{MUSE_IMAGE_SPEC}\n\n{b}"


# ── Đọc trạng thái trang ────────────────────────────────────────
_JS_STATE = r"""([sel, panelSel, minDim]) => {
  const isReady = b => { const i = b.querySelector('img');
      return !!(i && i.complete && i.naturalWidth >= minDim && (i.currentSrc || i.src)); };
  const boxes = [...document.querySelectorAll(sel)];
  const panel = document.querySelector(panelSel);
  const status = panel ? (panel.innerText || '') : '';
  let t = document.body.innerText || '';
  if (status) t = t.replace(status, '');
  // Chat ảo hoá → ảnh MỚI = khung ảnh trong tin nhắn assistant SAU tin nhắn user cuối.
  const msgs = [...document.querySelectorAll("[data-message-item='true']")];
  let lu = -1;
  msgs.forEach((m, i) => { if (m.getAttribute('data-message-role') === 'user') lu = i; });
  const after = lu >= 0 ? msgs.slice(lu + 1).flatMap(m => [...m.querySelectorAll(sel)]) : [];
  return {boxes: boxes.length, ready: boxes.filter(isReady).length,
          status: status.slice(0, 400), has_msgs: msgs.length > 0,
          last_user: lu >= 0 ? msgs[lu].getAttribute('data-message-id') : null,
          after_boxes: after.length, after_ready: after.filter(isReady).length,
          login: !document.querySelector("textarea[aria-label='Message']") &&
                 /\b(Log in|Sign in|Continue with)\b/.test(t),
          text: t};
}"""

# Tải ảnh MỚI NHẤT (sau tin user cuối) thẳng từ blob trong trang → base64.
_JS_FETCH = r"""async ([sel, minDim]) => {
  const msgs = [...document.querySelectorAll("[data-message-item='true']")];
  let lu = -1;
  msgs.forEach((m, i) => { if (m.getAttribute('data-message-role') === 'user') lu = i; });
  const scope = lu >= 0 ? msgs.slice(lu + 1) : [document];
  const imgs = scope.flatMap(m => [...m.querySelectorAll(sel + ' img')])
                    .filter(i => i.naturalWidth >= minDim);
  const img = imgs.pop();
  if (!img) return null;
  const r = await fetch(img.currentSrc || img.src);
  const b = await r.blob();
  const buf = new Uint8Array(await b.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 32768)
    s += String.fromCharCode.apply(null, buf.subarray(i, i + 32768));
  return {type: b.type, w: img.naturalWidth, h: img.naturalHeight, b64: btoa(s)};
}"""


def _state(page) -> dict:
    st = page.evaluate(_JS_STATE, [IMAGE_SEL, _SEL["status_panel"], MIN_DIM])
    st["kind"] = _status_kind(st.get("status", ""))
    return st


def _baseline(page) -> dict:
    st = _state(page)
    return {"boxes": st.get("boxes", 0), "last_user": st.get("last_user"),
            "has_msgs": bool(st.get("has_msgs"))}


def _new_image(st: dict, base: dict) -> tuple[int, int]:
    """(số khung ảnh mới, số khung mới đã nạp ảnh) so với mốc trước khi gửi."""
    if st.get("has_msgs") and base.get("has_msgs"):
        if not st.get("last_user") or st.get("last_user") == base.get("last_user"):
            return 0, 0
        return st.get("after_boxes", 0), st.get("after_ready", 0)
    n0 = base.get("boxes", 0)
    return max(0, st.get("boxes", 0) - n0), max(0, st.get("ready", 0) - n0)


def _wait_image(page, prompt: str, base: dict, timeout_sec: float,
                tick: Callable[[float], None]) -> tuple[str, dict]:
    """Chờ khung ảnh MỚI (đã nạp) hoặc câu trả lời có kết luận."""
    t0 = time.time()
    settled_text, settled_at = "", time.time()
    reply = ""
    while time.time() - t0 < timeout_sec:
        page.wait_for_timeout(6000)
        st = _state(page)
        nb, nr = _new_image(st, base)
        if nr > 0:
            page.wait_for_timeout(3000)       # Muse có thể gắn thêm ảnh cùng lượt
            return "ready", st
        if (st.get("has_msgs") and base.get("has_msgs")
                and st.get("last_user") == base.get("last_user")
                and time.time() - t0 < _ECHO_GRACE_SEC):
            # Tin vừa gửi chưa hiện: «câu sau prompt» lúc này là câu trả lời CŨ
            # (gửi lại cùng đuôi prompt) → chưa kết luận gì.
            tick(time.time() - t0)
            continue
        reply = reply_after(st["text"], prompt)
        kind = classify_muse_reply(reply)
        if kind != "limit" and is_spec_denial(reply):
            kind = "refused"
        if kind in ("limit", "refused", "transient", "error"):
            return kind, {"text": reply[-400:]}
        if "Delivery not confirmed" in reply and time.time() - t0 > 90:
            return "transient", {"text": "Delivery not confirmed"}
        stable, waited = time.time() - settled_at, time.time() - t0
        if reply != settled_text:
            settled_text, settled_at = reply, time.time()
        elif nb == 0 and st["kind"] == "connected" and (
                (kind == "clarify" and stable > 90 and waited > 120)
                or (stable > _NOIMG_STABLE_SEC and waited > _NOIMG_MIN_SEC)):
            return (kind or "noimage"), {"text": reply[-400:]}
        tick(time.time() - t0)
    return "timeout", {"text": reply[-400:]}


def _late_image(page, base: dict, checks: int = 3) -> bool:
    for i in range(checks):
        if _new_image(_state(page), base)[1] > 0:
            return True
        if i < checks - 1:
            page.wait_for_timeout(10_000)
    return False


def _download_image(page, raw: Path) -> None:
    """blob trong trang → file; hỏng thì mở hộp thoại ảnh → «Download»."""
    raw.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = page.evaluate(_JS_FETCH, [IMAGE_SEL, MIN_DIM])
    except Exception as e:
        if _is_closed_error(e):
            raise
        r = None
    if r and r.get("b64"):
        raw.write_bytes(base64.b64decode(r["b64"]))
    else:
        page.locator(f"{IMAGE_SEL} {OPEN_BTN}").last.click(timeout=10_000)
        page.wait_for_timeout(1500)
        try:
            with page.expect_download(timeout=90_000) as dl:
                page.locator(DIALOG_DOWNLOAD).last.click(timeout=10_000)
            dl.value.save_as(str(raw))
        finally:
            page.keyboard.press("Escape")
    if not raw.exists() or raw.stat().st_size < 20_000:
        raise RuntimeError("ảnh tải về rỗng/quá nhỏ")


def to_png_16x9(src: Path, dest: Path, mode: str = "pad",
                min_w: int = 1920, min_h: int = 1080) -> tuple[int, int]:
    """Ảnh Muse (WebP 3:2) → PNG 16:9. mode="pad": chèn lề màu nền (bảng nhân
    vật, không mất chi tiết); "crop": cắt giữa (ảnh cảnh). Nhỏ hơn 1920x1080 →
    phóng lên. Trả (w, h) đã lưu."""
    from PIL import Image, ImageStat

    with Image.open(src) as im0:
        im = im0.convert("RGB")
    w, h = im.size
    target = 16 / 9
    if abs(w / h - target) > 0.005:
        if mode == "crop":
            if w / h < target:
                nh = round(w / target)
                top = (h - nh) // 2
                im = im.crop((0, top, w, top + nh))
            else:
                nw = round(h * target)
                left = (w - nw) // 2
                im = im.crop((left, 0, left + nw, h))
        else:
            if w / h < target:
                cw, ch = round(h * target), h
                strips = [im.crop((0, 0, 8, h)), im.crop((w - 8, 0, w, h))]
            else:
                cw, ch = w, round(w / target)
                strips = [im.crop((0, 0, w, 8)), im.crop((0, h - 8, w, h))]
            px = [tuple(int(c) for c in ImageStat.Stat(s).median) for s in strips]
            bg = tuple(sum(c) // len(px) for c in zip(*px))
            canvas = Image.new("RGB", (cw, ch), bg)
            canvas.paste(im, ((cw - w) // 2, (ch - h) // 2))
            im = canvas
    w, h = im.size
    if w < min_w or h < min_h:
        scale = max(min_w / w, min_h / h)
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.png")
    im.save(tmp, "PNG")
    tmp.replace(dest)
    return im.size


def _has_image(out_dir: Path, i: int) -> bool:
    p = out_dir / f"{i}.png"
    try:
        return p.exists() and p.stat().st_size > 2000
    except OSError:
        return False


def generate_images_muse(
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
    """Cùng hợp đồng với gemini_driver.generate_images: restart (xoá *.png rồi
    vẽ lại 1 → 2 → 0), resume (chỉ ảnh thiếu), only=[i] (ghi đè đúng ảnh đó)."""
    global _LOGP
    from .flow_driver import assert_english_prompts
    from .gemini_driver import _prompt_for

    params = params or config.PARAMS
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = params.image_count

    def _p(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    def _order_key(i: int) -> tuple[int, int]:
        order = config.image_order()
        return (order.index(i), 0) if i in order else (len(order), i)

    if only is not None:
        todo = sorted({int(i) for i in only if 0 <= int(i) < n}, key=_order_key)
        if not todo:
            _p("Không có ảnh hợp lệ để tạo lại.", 100.0)
            return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
    else:
        if not resume:
            removed = 0
            for old in out_dir.glob("*.png"):
                try:
                    old.unlink()
                    removed += 1
                except OSError:
                    pass
            if removed:
                _p(f"Xoá {removed} ảnh cũ — Muse.ai vẽ lại toàn bộ từ đầu", 1.0)
        todo = sorted((i for i in range(n) if not (resume and _has_image(out_dir, i))),
                      key=_order_key)
        if not todo:
            _p("Đã đủ ảnh — bỏ qua (resume)", 100.0)
            return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]

    # Prompt dựng TRƯỚC khi mở trình duyệt: thiếu prompt / còn tiếng Việt → báo ngay.
    def _full(i: int) -> str:
        p = _prompt_for(i, topic, params, prompts_override, style)
        return p + (config.thumbnail_ref_note(p) if i == config.IMG_THUMBNAIL else "")

    prompts = {i: image_prompt(_full(i)) for i in todo}
    assert_english_prompts(list(prompts.items()))

    _LOGP = out_dir / "_muse_image.log"
    mode = f"ONLY {todo}" if only is not None else ("RESUME" if resume else "RESTART")
    _log(f"=== BẮT ĐẦU tạo ảnh Muse project {project_id} ({mode}) — ảnh {todo} ===")

    # Bản mô tả Muse ĐÃ từ chối ở lần chạy trước → không gửi lại y hệt: dùng bản
    # trung tính nếu chưa thử, hết cách thì chuyển máy dự phòng NGAY (chưa mở Muse).
    alt_used: set[int] = set()
    for i in todo:
        if not _was_denied(out_dir, i, prompts[i]):
            continue
        alt = neutral_style(prompts[i])
        if alt != prompts[i] and not _was_denied(out_dir, i, alt):
            prompts[i] = alt
            alt_used.add(i)
            _log(f"ảnh {i}: Muse từng từ chối bản mô tả này → dùng bản trung tính (bỏ tên studio)")
            continue
        _log(f"ảnh {i}: Muse đã từ chối cả bản gốc lẫn bản trung tính → không gửi lại")
        raise MuseImageDenied(
            f"Ảnh {i}: Muse.ai đã từ chối đúng bản mô tả này ở lần chạy trước — không "
            "gửi lại y hệt; chuyển máy dự phòng.")

    done: set[int] = set()
    raw_dir = out_dir / "_muse_raw"
    wait_sec = _setting("muse_image_wait_sec", 900)
    connect_sec = _setting("muse_connect_wait_sec", 1200)
    max_errs = int(_setting("muse_image_error_retries", 8))

    def _mark_done(i: int) -> None:
        done.add(i)

    def _session_pass(pending: list[int], mark_done=_mark_done) -> None:
        with _GPMSession(profile_id()) as sess:
            page = sess.page
            for k, i in enumerate(pending):
                pct = 3.0 + 94.0 * (len(done) / max(len(todo), 1))
                role = config.image_roles(config.images_cast_mode(out_dir)).get(i, "")
                dest = out_dir / f"{i}.png"
                need = config.image_refs(i)
                missing = [r for r in need if not _has_image(out_dir, r)]
                if missing:
                    raise RuntimeError(
                        f"Ảnh {i} phải vẽ dựa trên ảnh {list(need)} nhưng chưa có ảnh "
                        f"{missing} — tạo ảnh đó trước rồi tạo lại ảnh {i}.")
                refs: list[str] = [str((out_dir / f"{r}.png").resolve()) for r in need]
                prompt = prompts[i]
                last_err, ok = "", False
                attempt = errs = sends = 0
                while attempt < _IMAGE_ATTEMPTS and errs <= max_errs:
                    sends += 1
                    tag = f"ảnh {i} (lần {sends}" + (f", Muse lỗi {errs}" if errs else "") + ")"
                    base = None
                    try:
                        wait_connected(page, connect_sec, lambda s: _p(
                            f"[Muse {len(done)+1}/{len(todo)}] Chờ Muse kết nối "
                            f"(Connecting…) — {int(s)}s", pct))
                        base = _baseline(page)
                        how = _send(page, refs, prompt)
                        _log(f"{tag}: gửi ({how}, đính kèm {len(refs)} ảnh, {len(prompt)} ký tự)")
                        kind, st = _wait_image(
                            page, prompt, base, wait_sec,
                            lambda s: _p(f"[Muse {len(done)+1}/{len(todo)}] Muse đang vẽ "
                                         f"{tag} — {int(s)}s", pct))
                    except (MuseUnavailable, JobCancelled):
                        raise
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        last_err = f"ui: {type(e).__name__}: {e}"
                        _log(f"{tag}: lỗi giao diện — {last_err[:200]}")
                        page.wait_for_timeout(5000)
                        if base is None or not _late_image(page, base):
                            attempt += 1
                            continue
                        kind, st = "ready", {}
                    if kind == "limit":
                        _log(f"{tag}: Muse báo giới hạn — {st.get('text', '')[:200]!r}")
                        raise MuseVideoLimit(
                            f"Muse báo hết lượt/giới hạn khi tạo ảnh {i} "
                            f"(«{st.get('text', '')[:160]}»). Đã có {len(done)} ảnh mới — "
                            "khi có lượt bấm 'Tạo tiếp ảnh thiếu' (resume).")
                    if kind == "refused":
                        # Từ chối NỘI DUNG: gửi lại y hệt vô ích (#35 đã đốt 13 lần
                        # ~55 phút). Thử đúng 1 bản trung tính, rồi chuyển máy dự phòng.
                        last_err = f"refused: {st.get('text', '')[:160]}"
                        _remember_denied(out_dir, i, prompt)
                        alt = neutral_style(prompt)
                        if (i not in alt_used and alt != prompt
                                and not _was_denied(out_dir, i, alt)):
                            alt_used.add(i)
                            prompt = alt
                            _log(f"{tag}: Muse TỪ CHỐI đúng bản mô tả này — {last_err!r} → "
                                 "KHÔNG gửi lại y hệt; thử 1 bản trung tính (bỏ tên studio)")
                            page.wait_for_timeout(random.uniform(15, 25) * 1000)
                            continue
                        _log(f"{tag}: Muse TỪ CHỐI — {last_err!r} → dừng Muse (không gửi "
                             "lại y hệt), chuyển máy dự phòng")
                        raise MuseImageDenied(
                            f"Ảnh {i}: Muse.ai từ chối đúng bản mô tả này sau {sends} lần "
                            f"gửi ({last_err[:200]}) — không gửi lại y hệt; chuyển máy dự "
                            f"phòng. Đã có {len(done)}/{len(todo)} ảnh mới.")
                    if kind in ("error", "transient"):
                        errs += 1
                        last_err = f"{kind}: {st.get('text', '')[:160]}"
                        pause = _capped_delay(30 * errs + random.uniform(0, 20))
                        _log(f"{tag}: Muse báo lỗi — {last_err!r} → nghỉ {int(pause)}s rồi vẽ lại")
                        _pause_ticking(page, pause, lambda left: _p(
                            f"[Muse {len(done)+1}/{len(todo)}] Muse lỗi ảnh {i} ({errs} lần) — "
                            f"vẽ lại sau {int(left)}s", pct))
                        if not _late_image(page, base):
                            continue
                    elif kind != "ready":
                        attempt += 1
                        last_err = f"{kind}: {st.get('text', '')[:160]}"
                        _log(f"{tag}: {last_err!r}")
                        page.wait_for_timeout(
                            _capped_delay(random.uniform(10, 20) * attempt) * 1000)
                        if not _late_image(page, base):
                            continue
                    try:
                        raw = raw_dir / f"{i}.img"
                        _download_image(page, raw)
                        w, h = to_png_16x9(raw, dest,
                                           mode="crop" if i == config.IMG_THUMBNAIL else "pad")
                        raw.unlink(missing_ok=True)
                    except Exception as e:
                        if _is_closed_error(e):
                            raise
                        attempt += 1
                        last_err = f"download: {e}"
                        _log(f"{tag}: tải/hậu kỳ lỗi — {str(e)[:200]}")
                        continue
                    _log(f"{tag}: OK → {dest.name} {w}x{h}")
                    mark_done(i)
                    ok = True
                    _p(f"[Muse {len(done)}/{len(todo)}] Đã lưu ảnh {i} ({role}) — {w}x{h}",
                       3.0 + 94.0 * (len(done) / max(len(todo), 1)))
                    break
                if not ok:
                    raise RuntimeError(
                        f"Ảnh {i}: Muse.ai thất bại sau {sends} lần gửi ({attempt} lỗi thường, "
                        f"{errs} lần Muse báo lỗi) ({last_err[:200]}). Đã có {len(done)}/"
                        f"{len(todo)} ảnh mới — bấm 'Tạo tiếp ảnh thiếu' (resume).")
                if k < len(pending) - 1:
                    page.wait_for_timeout(random.uniform(5, 12) * 1000)

    for s_try in range(1, _SESSION_ATTEMPTS + 1):
        pending = [i for i in todo if i not in done]
        if not pending:
            break
        try:
            (_session_impl or _session_pass)(pending, _mark_done)
            break
        except Exception as e:
            if not _is_closed_error(e) or s_try >= _SESSION_ATTEMPTS:
                if _is_closed_error(e):
                    raise RuntimeError(
                        "Cửa sổ GPM của Muse bị ĐÓNG giữa chừng — đừng đóng cửa sổ GPM khi "
                        f"tool đang tạo ảnh. Đã có {len(done)} ảnh mới; bấm 'Tạo tiếp ảnh "
                        "thiếu' (resume).") from e
                raise
            _log(f"trình duyệt đóng giữa chừng — mở phiên {s_try + 1}/{_SESSION_ATTEMPTS}")
            _p(f"Trình duyệt đóng giữa chừng — mở lại và vẽ tiếp {pending}...", 2.0)
            time.sleep(5)

    try:
        raw_dir.rmdir()
    except OSError:
        pass
    _log(f"=== XONG: {len(done)} ảnh mới ===")
    _p("Xong tạo ảnh (Muse.ai)", 100.0)
    return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]


__all__ = ["generate_images_muse", "to_png_16x9", "image_prompt", "MUSE_IMAGE_SPEC",
           "MuseImageDenied", "is_spec_denial", "neutral_style"]
