"""Thư mục «nhac_mix» trong mỗi project: chứa ĐÚNG 1 file nhạc đã mix (2026-11-10).

media/<nhóm>/<project>/nhac_mix/<tên project>.wav — bản mix mới nhất, không cần
video. Dùng hardlink tới outputs/<pid>/<mix>/mix.wav (cùng ổ đĩa → không tốn thêm
dung lượng); khác ổ thì sao chép. Mix mới/vá mix sẽ thay file cũ.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

MIX_AUDIO_DIR = "nhac_mix"


def _safe(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in (name or "")).strip(" .")
    return out or "mix"


def export_mix_audio(project_id: int, project_name: Optional[str],
                     mix_wav: str | Path) -> Optional[Path]:
    """Đặt bản mix vào media/.../<project>/nhac_mix/. Lỗi thì bỏ qua (trả None)."""
    src = Path(mix_wav)
    if not src.is_file():
        return None
    try:
        from backend.video import config as vconfig
        folder = vconfig.project_dir(project_id, project_name) / MIX_AUDIO_DIR
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{_safe(project_name or f'project {project_id}')}{src.suffix.lower()}"
        tmp = folder / f".{dest.name}.tmp"
        tmp.unlink(missing_ok=True)
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        tmp.replace(dest)
        # Chỉ giữ 1 file nhạc trong thư mục.
        for old in folder.iterdir():
            if old.is_file() and old != dest:
                try:
                    old.unlink()
                except OSError:
                    pass
        print(f"[mix] Đã lưu nhạc mix: {dest}", flush=True)
        return dest
    except Exception as exc:     # noqa: BLE001 — tính năng phụ, không làm hỏng mix
        print(f"[mix] Không lưu được thư mục nhac_mix: {exc}", flush=True)
        return None
