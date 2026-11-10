"""
video/chatgpt_image.py — ChatGPT web vẽ bộ ảnh của tool Cover (yêu cầu người
dùng 2026-10-29: «ảnh bìa cho chatgpt làm»):
  ảnh 0 = ảnh bìa gốc (không chữ, không đính kèm) →
  ảnh 1 = bảng phân tích nhân vật, ảnh 2 = bảng tham chiếu bối cảnh (đính kèm 0.png).

Cùng hợp đồng với muse_image.generate_images_muse / gemini_driver.generate_images
để service._generate_images gọi thay nhau (ChatGPT chính → Muse.ai dự phòng).
Driver ChatGPT chỉ đính kèm được 1 ảnh → ảnh cần ≥2 ảnh tham chiếu (bìa của quy
trình cũ dựa trên 2 bảng nhân vật) báo lỗi để máy dự phòng vẽ.

An toàn: dùng chung driver chatgpt_thumbnail (KHÔNG tự đăng nhập, captcha → chờ
người dùng tự xác minh).
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config

SPEC = ("Wide landscape 3:2 image. Absolutely no text, letters, numbers, logo, "
        "watermark or border anywhere in the picture.")
# Bảng tham chiếu CẦN nhãn chữ ngắn (bảng phân tích) → không cấm chữ.
SHEET_SPEC = "Wide landscape 3:2 image, one single reference sheet."


def image_prompt(body: str, *, sheet: bool = False) -> str:
    spec = SHEET_SPEC if sheet else SPEC
    b = " ".join((body or "").split())
    return b if b.startswith(spec) else f"{spec} {b}"


def _log_line(out_dir: Path, msg: str) -> None:
    try:
        with (out_dir / "_chatgpt_image.log").open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def generate_images_chatgpt(
    project_id: int,
    topic: str,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
    prompts_override: Optional[dict] = None,
    style: Optional[str] = None,
    only: Optional[list[int]] = None,
    _draw: Optional[Callable[..., bytes]] = None,
) -> list[str]:
    """restart (xoá *.png rồi vẽ lại), resume (chỉ ảnh thiếu), only=[i] (ghi đè)."""
    from .chatgpt_thumbnail import draw_scene
    from .flow_driver import assert_english_prompts
    from .gemini_driver import _prompt_for
    from .muse_image import _has_image, to_png_16x9
    from .thumbnail_ai import _reference_jpeg

    draw = _draw or draw_scene
    params = params or config.PARAMS
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = params.image_count

    def _p(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    def _existing() -> list[str]:
        return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]

    if only is not None:
        todo = sorted({int(i) for i in only if 0 <= int(i) < n})
        if not todo:
            _p("Không có ảnh hợp lệ để tạo lại.", 100.0)
            return _existing()
    else:
        if not resume:
            for old in out_dir.glob("*.png"):
                try:
                    old.unlink()
                except OSError:
                    pass
        todo = [i for i in range(n) if not (resume and _has_image(out_dir, i))]
        if not todo:
            _p("Đã đủ ảnh — bỏ qua (resume)", 100.0)
            return _existing()

    order = config.image_order()
    todo.sort(key=lambda i: (order.index(i), 0) if i in order else (len(order), i))
    if any(len(config.image_refs(i)) > 1 for i in todo) or not config.single_image(params):
        raise RuntimeError("ChatGPT chỉ đính kèm được 1 ảnh tham chiếu (quy trình cũ: "
                           "ảnh bìa dựa trên 2 bảng nhân vật) — chuyển máy dự phòng.")
    prompts = {i: image_prompt(_prompt_for(i, topic, params, prompts_override, style),
                               sheet=i in config.SHEET_IMAGES)
               for i in todo}
    assert_english_prompts(list(prompts.items()))

    cancel = getattr(progress_cb, "is_cancelled", None)
    _log_line(out_dir, f"=== ChatGPT vẽ ảnh {todo} (project {project_id}) ===")
    for k, i in enumerate(todo):
        pct = 3.0 + 94.0 * k / len(todo)
        refs = config.image_refs(i)
        missing = [r for r in refs if not _has_image(out_dir, r)]
        if missing:
            raise RuntimeError(f"Ảnh {i} phải vẽ dựa trên ảnh bìa nhưng chưa có ảnh "
                               f"{missing} — tạo ảnh bìa trước rồi tạo lại ảnh {i}.")
        role = config.image_roles(config.images_cast_mode(out_dir)).get(i, f"ảnh {i}")
        _p(f"[ChatGPT {k + 1}/{len(todo)}] Vẽ ảnh {i} — {role}…", pct)
        ref = _reference_jpeg(out_dir / f"{refs[0]}.png") if refs else None
        raw = draw(prompts[i], ref=ref, log=lambda m, pct=pct: _p(m, pct),
                   cancel_check=cancel)
        with tempfile.TemporaryDirectory(prefix="mm_chatgpt_img_") as d:
            src = Path(d) / "raw.img"
            src.write_bytes(raw)
            w, h = to_png_16x9(src, out_dir / f"{i}.png",
                               mode="crop" if i == config.IMG_THUMBNAIL else "pad")
        _log_line(out_dir, f"ảnh {i}: OK {w}x{h}")
    _p(f"ChatGPT đã vẽ xong {len(todo)} ảnh.", 100.0)
    return _existing()


__all__ = ["generate_images_chatgpt", "image_prompt", "SPEC"]
