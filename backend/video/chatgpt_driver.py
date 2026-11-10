"""
video/chatgpt_driver.py — Tự động hoá ChatGPT web để tạo ảnh.

Sinh image_count ảnh cho 1 project:
  0 = thumbnail (theo topic), 1..n = cảnh thiên nhiên/thành phố/mây (hoạt hình, 16:9).
Lưu vào media/<project>/images/{i}.png.

⚠️  Best-effort: selector ChatGPT hay đổi — chỉnh trong config.get_selectors('chatgpt').
Chỉ chạy được khi bạn đã đăng nhập ChatGPT trong .browser_profile (lần đầu tự login).
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (
    BrowserSession, query_first, fill_first, click_first, wait_for_manual_login,
)


def _prompt_for(index: int, topic: str, params: config.VideoParams) -> str:
    prompts = config.load_overrides().get("prompts") or config.DEFAULT_PROMPTS
    tmpl = prompts.get(str(index))
    if tmpl:
        return tmpl.format(topic=topic)
    # Fallback nếu index vượt số prompt mặc định
    return (f"Ảnh nền cảnh thiên nhiên/thành phố/mây trời số {index}, "
            f"phong cách {params.image_style}, chủ đề '{topic}', "
            f"tỉ lệ {params.aspect_ratio}, không chữ.")


def _download_image(page, loc, dest: Path) -> bool:
    """Lấy src của ảnh và tải về dest bằng request context của trình duyệt."""
    try:
        src = loc.get_attribute("src")
        if not src:
            return False
        resp = page.context.request.get(src)
        if resp.ok:
            dest.write_bytes(resp.body())
            return True
    except Exception:
        pass
    return False


def generate_images(
    project_id: int,
    topic: str,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    project_name: str | None = None,
) -> list[str]:
    """Trả về danh sách đường dẫn ảnh đã tạo (0..n)."""
    params = params or config.PARAMS
    sels = config.get_selectors("chatgpt")
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    saved: list[str] = []
    with BrowserSession() as sess:
        page = sess.new_page()
        _p("Mở ChatGPT...", 2.0)
        page.goto(config.CHATGPT_URL, wait_until="domcontentloaded")
        wait_for_manual_login(page, sels["prompt_box"], "ChatGPT",
                              timeout_sec=300)

        n = params.image_count
        for i in range(n):
            pct = 5.0 + (i / max(n, 1)) * 90.0
            _p(f"[{i+1}/{n}] Tạo ảnh {i}...", pct)
            prompt = _prompt_for(i, topic, params)

            # Gõ prompt + gửi (mỗi ảnh 1 tin nhắn mới cho gọn)
            if not fill_first(page, sels["prompt_box"], prompt):
                raise RuntimeError("Không tìm thấy ô nhập prompt ChatGPT "
                                   "(cập nhật selector 'prompt_box').")
            if not click_first(page, sels["send_button"], timeout_ms=5000):
                page.keyboard.press("Enter")

            # Chờ ảnh xuất hiện
            dest = out_dir / f"{i}.png"
            deadline = time.time() + config.BROWSER.image_wait_sec
            got = False
            while time.time() < deadline:
                loc = query_first(page, sels["generated_image"], timeout_ms=3000)
                if loc is not None and _download_image(page, loc, dest):
                    got = True
                    break
                page.wait_for_timeout(2000)
            if not got:
                raise RuntimeError(
                    f"Ảnh {i} không tạo được trong "
                    f"{config.BROWSER.image_wait_sec}s (kiểm tra selector "
                    f"'generated_image' hoặc ChatGPT bị giới hạn).")
            saved.append(str(dest))

    _p("Xong tạo ảnh", 100.0)
    return saved
