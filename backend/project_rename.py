"""Đổi tên project AN TOÀN.

Thư mục media = "<tên> (#id)" (video/config.project_folder_name) và tên còn nằm
trong chữ ký bộ prompt (images/prompts.json → context.title/input_signature).
Chỉ sửa `Project.name` sẽ làm tool mất liên kết ảnh/clip/final và `prepare()`
báo "Hồ sơ đã đổi nhưng project có ảnh". Vì vậy đổi tên = (1) đổi tên thư mục,
(2) thay đường dẫn cũ → mới trong DB, (3) cập nhật tiêu đề + chữ ký prompt, tất
cả khi project KHÔNG có tác vụ nào chạy; lỗi giữa chừng → trả thư mục về cũ.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from backend.models import Project

MAX_NAME_LEN = 150


class RenameError(RuntimeError):
    """Không đổi được tên (tên sai, trùng thư mục, file đang bị mở…)."""


def clean_name(name: str | None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise RenameError("Tên project không được để trống.")
    if len(name) > MAX_NAME_LEN:
        raise RenameError(f"Tên project dài quá {MAX_NAME_LEN} ký tự.")
    return name


def _path_variants(old: Path, new: Path) -> list[tuple[str, str]]:
    """Các dạng chuỗi đường dẫn có thể nằm trong DB: thường, '/', JSON-escape."""
    o, n = str(old), str(new)
    pairs = [(o, n), (o.replace("\\", "/"), n.replace("\\", "/")),
             (o.replace("\\", "\\\\"), n.replace("\\", "\\\\"))]
    seen, out = set(), []
    for a, b in pairs:
        if a not in seen:
            seen.add(a)
            out.append((a, b))
    return out


def _replace_paths_in_db(session: Session, old: Path, new: Path) -> int:
    """Thay tiền tố thư mục cũ → mới ở MỌI cột chữ của MỌI bảng. Tiền tố
    '...\\media\\<tên> (#id)' đủ đặc thù nên không đụng nhầm project khác
    (dấu phân cách ngay sau đảm bảo '(#9)' không khớp '(#90)')."""
    conn = session.connection()
    tables = [r[0] for r in conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"))]
    total = 0
    for table in tables:
        cols = [r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))
                if not r[2] or any(k in r[2].upper() for k in ("CHAR", "TEXT", "CLOB", "JSON"))]
        for col in cols:
            for a, b in _path_variants(old, new):
                # Đúng bằng thư mục, hoặc thư mục + dấu phân cách ở bất kỳ đâu
                # trong chuỗi (đầu đường dẫn hay giữa JSON).
                res = conn.execute(text(
                    f'UPDATE "{table}" SET "{col}" = :new WHERE "{col}" = :old'),
                    {"new": b, "old": a})
                total += res.rowcount or 0
                for sep in ("\\", "/"):
                    res = conn.execute(text(
                        f'UPDATE "{table}" SET "{col}" = replace("{col}", :old, :new) '
                        f'WHERE instr("{col}", :old) > 0'),
                        {"old": a + sep, "new": b + sep})
                    total += res.rowcount or 0
    return total


def _prompt_signature(saved: dict) -> str | None:
    from backend.video import prompt_catalog
    keys = ("context", "style", "count", "scene_count", "style_version",
            "workflow_version")
    if not all(k in saved for k in keys):
        return None
    inputs = {k: saved[k] for k in keys}
    return prompt_catalog.prompt_hash(json.dumps(inputs, ensure_ascii=False, sort_keys=True))


def retitle_manifest(images_dir: Path, new_title: str) -> bool:
    """Đổi tiêu đề trong prompts.json + tính lại chữ ký, CHỈ khi chữ ký cũ khớp
    đúng nội dung (tránh "hợp thức hoá" một bộ đã lệch). Prompt ảnh/cảnh giữ
    nguyên — tên chỉ là chữ chồng lên thumbnail/intro bằng font thật."""
    path = images_dir / "prompts.json"
    if not path.exists():
        return False
    saved = json.loads(path.read_text(encoding="utf-8"))
    ctx = saved.get("context")
    if not isinstance(ctx, dict):
        return False
    consistent = _prompt_signature(saved) == saved.get("input_signature")
    ctx["title"] = new_title
    saved["title"] = new_title
    if consistent:
        saved["input_signature"] = _prompt_signature(saved)
    from backend.video.prompt_workflow import save_manifest
    save_manifest(path, saved)
    return consistent


def _relocate(session: Session, project: Project, old_dir: Path, new_dir: Path,
              apply, action: str) -> tuple[bool, int]:
    """Chuyển thư mục media old_dir → new_dir + thay đường dẫn trong DB + apply()
    (sửa field project) trong 1 lần, khi project rảnh. Lỗi → trả thư mục về cũ."""
    from backend.project_cleanup import ProjectBusyError, _guard_idle
    same_dir = os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir))
    try:
        guard = _guard_idle(session, project.id)
        guard.__enter__()
    except ProjectBusyError:
        raise ProjectBusyError(
            "Project đang có tác vụ chạy/chờ (Suno/Mix/Video/Upload). Hãy chờ "
            f"xong hoặc huỷ tác vụ rồi {action}.") from None
    moved, rows = False, 0
    try:
        if old_dir.exists() and not same_dir and new_dir.exists():
            raise RenameError(f"Đã có thư mục «{new_dir.name}» ở nơi mới — chọn tên khác.")
        if old_dir.exists() and str(old_dir) != str(new_dir):
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(old_dir, new_dir)
            except OSError as e:
                raise RenameError(
                    "Không chuyển được thư mục media — có file của project đang "
                    "được mở (VLC, trình xem ảnh, Explorer, OneDrive…). Đóng các "
                    f"cửa sổ đó rồi thử lại. ({e})") from e
            moved = True
        try:
            rows = _replace_paths_in_db(session, old_dir, new_dir) if moved else 0
            apply()
            session.add(project)
            session.commit()
        except Exception:
            session.rollback()
            if moved:
                os.rename(new_dir, old_dir)
            raise
    finally:
        guard.__exit__(None, None, None)
    return moved, rows


def change_kind(session: Session, project: Project, kind: str,
                media_root: Path | None = None) -> dict:
    """Đổi loại project Mix Set ↔ Track lẻ và chuyển thư mục media vào nhóm
    tương ứng (media/Mix Set | media/Track lẻ). Project cũ nằm thẳng trong
    media/ cũng được xếp vào nhóm khi đổi/xác nhận loại."""
    from backend.video import config as vconfig
    if kind not in ("mixset", "single"):
        raise RenameError("Loại project phải là Mix Set hoặc Track lẻ.")
    root = Path(media_root or vconfig.MEDIA_ROOT)
    old_dir = vconfig.project_dir(project.id, project.name, root=root,
                                  kind=getattr(project, "kind", None))
    new_dir = root / vconfig.media_group(kind) / vconfig.project_folder_name(
        project.name, project.id)
    if kind == (getattr(project, "kind", None) or "mixset") and \
            os.path.normcase(str(old_dir)) == os.path.normcase(str(new_dir)):
        return {"kind": kind, "changed": False, "moved": False, "db_rows": 0}
    moved, rows = _relocate(session, project, old_dir, new_dir,
                            lambda: setattr(project, "kind", kind), "chuyển nhóm")
    session.refresh(project)
    return {"kind": kind, "changed": True, "moved": moved, "db_rows": rows,
            "folder": str(new_dir.relative_to(root)) if moved else None}


def rename_project(session: Session, project: Project, new_name: str,
                   media_root: Path | None = None) -> dict:
    """Đổi tên project + thư mục media + đường dẫn trong DB + prompts.json.
    Raise RenameError / project_cleanup.ProjectBusyError."""
    from backend.video import config as vconfig

    new_name = clean_name(new_name)
    old_name = project.name or ""
    if new_name == old_name:
        return {"old_name": old_name, "name": new_name, "changed": False,
                "moved": False, "db_rows": 0, "manifest": False}
    root = Path(media_root or vconfig.MEDIA_ROOT)
    old_dir = vconfig.project_dir(project.id, old_name, root=root,
                                  kind=getattr(project, "kind", None))
    new_dir = old_dir.parent / vconfig.project_folder_name(new_name, project.id)
    moved, rows = _relocate(session, project, old_dir, new_dir,
                            lambda: setattr(project, "name", new_name), "đổi tên")
    manifest = False
    try:
        manifest = retitle_manifest(new_dir / vconfig.STEM_IMAGE, new_name)
    except Exception:       # noqa: BLE001 — tên đã đổi xong; prompt chỉ là phụ
        pass
    session.refresh(project)
    return {"old_name": old_name, "name": new_name, "changed": True,
            "moved": moved, "db_rows": rows, "manifest": manifest,
            "folder": new_dir.name}
