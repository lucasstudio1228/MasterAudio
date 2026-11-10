"""Thùng rác project: xoá = chuyển vào Trash/, 7 ngày sau mới xoá vĩnh viễn.

- Chuyển vào thùng rác: project phải RẢNH (như xoá cũ). Các thư mục THUỘC
  project (media/<tên (#id)>, outputs/<id>, stems/<id>, data/suno_batches/
  project_<id>) được ĐỔI TÊN (rename, cùng ổ → tức thì, không chép) vào
  ``Trash/<tên (#id)>__<thời điểm>/<gốc>/...`` kèm ``_trash.json``. Bản ghi DB
  GIỮ NGUYÊN, chỉ đặt ``project.deleted_at`` → project biến khỏi danh sách và
  mọi API thao tác (404), lô sản xuất bỏ qua nó.
- Thư mục có file đang mở (WinError 5) không đổi tên được → để TẠI CHỖ, ghi vào
  ``in_place``; khôi phục thì giữ nguyên, xoá vĩnh viễn thì xoá tại chỗ.
- Khôi phục: đổi tên ngược về đúng đường dẫn cũ (đường dẫn tuyệt đối trong DB
  vẫn đúng) rồi xoá ``deleted_at``.
- Xoá vĩnh viễn (tay, hoặc tự động khi quá 7 ngày — chạy TRONG backend, xem
  ``purge_expired``): ``project_cleanup.delete_project_data`` (DB + phần tại
  chỗ + mốc ID không tái dùng) rồi xoá mục trong Trash/.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

from sqlmodel import Session, select

from backend.models import Project
from backend.project_cleanup import (DEFAULT_ROOTS, DELETE_LOCK, CleanupRoots,
                                     ProjectCleanupError, _guard_idle,
                                     _move_with_retry, _owned_paths,
                                     _purge_with_retries, _validate,
                                     _write_journal, delete_project_data,
                                     tree_size)

RETENTION_DAYS = 7
TRASH_DIRNAME = "Trash"
MANIFEST = "_trash.json"


def trash_root(roots: CleanupRoots = DEFAULT_ROOTS) -> Path:
    return roots.workspace / TRASH_DIRNAME


def _root_keys(roots: CleanupRoots) -> dict[str, Path]:
    return {"media": roots.media, "outputs": roots.outputs, "stems": roots.stems,
            "suno_batches": roots.suno}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def purge_at(project: Project) -> datetime | None:
    deleted = _utc(project.deleted_at)
    return deleted + timedelta(days=RETENTION_DAYS) if deleted else None


def _read_manifest(entry: Path) -> dict:
    try:
        return json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _entry_dir(project: Project, roots: CleanupRoots) -> Path | None:
    if not project.trash_dir:
        return None
    entry = Path(project.trash_dir)
    _validate(entry, trash_root(roots))
    return entry


# ── Chuyển vào thùng rác ───────────────────────────────────────────────────
def move_to_trash(session: Session, project: Project,
                  roots: CleanupRoots = DEFAULT_ROOTS,
                  now: datetime | None = None) -> dict:
    with DELETE_LOCK:
        return _move_to_trash(session, project, roots,
                              now or datetime.now(timezone.utc))


def _move_to_trash(session: Session, project: Project, roots: CleanupRoots,
                   now: datetime) -> dict:
    from backend.video.config import project_folder_name

    if project.deleted_at is not None:
        raise ProjectCleanupError(f"«{project.name}» đã ở trong Thùng rác.")
    keys = {str(path): key for key, path in _root_keys(roots).items()}
    root = trash_root(roots)
    _validate(root, roots.workspace)
    moved: list[tuple[Path, Path]] = []
    in_place: list[str] = []
    with _guard_idle(session, project.id):
        targets = _owned_paths(project, roots)
        entry = root / (f"{project_folder_name(project.name, project.id)}__"
                        f"{now.strftime('%Y%m%d-%H%M%S')}")
        _validate(entry, root)
        entry.mkdir(parents=True, exist_ok=False)
        document = {
            "project_id": project.id, "project_name": project.name,
            "deleted_at": now.isoformat(),
            "purge_after": (now + timedelta(days=RETENTION_DAYS)).isoformat(),
            "phase": "staging", "paths": [], "in_place": [],
        }
        _write_journal(entry / MANIFEST, document)
        try:
            for owner, source in targets:
                destination = entry / keys[str(owner)] / source.name
                _validate(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    ok = _move_with_retry(source, destination)
                except OSError:
                    ok = False          # khác ổ đĩa / lỗi khác → để tại chỗ
                if ok:
                    moved.append((source, destination))
                    document["paths"].append({"original": str(source),
                                              "trashed": str(destination)})
                else:
                    in_place.append(str(source))
            document["in_place"] = in_place
            _write_journal(entry / MANIFEST, document)
            project.deleted_at = now
            project.trash_dir = str(entry)
            session.add(project)
            session.commit()
        except Exception as exc:
            session.rollback()
            errors = []
            for source, destination in reversed(moved):
                try:
                    if source.exists():
                        raise ProjectCleanupError(f"Đích khôi phục đã tồn tại: {source}")
                    destination.rename(source)
                except Exception as restore_exc:     # noqa: BLE001
                    errors.append(str(restore_exc))
            if not errors:
                shutil.rmtree(entry, ignore_errors=True)
            else:
                document.update(phase="restore_pending", error=str(exc),
                                restore_errors=errors)
                try:
                    _write_journal(entry / MANIFEST, document)
                except OSError:
                    pass
            raise ProjectCleanupError(
                f"Không chuyển được «{project.name}» vào Thùng rác: {exc}"
                + (f" — cần trả tay các thư mục trong {entry}" if errors else "")) from exc
        document["phase"] = "trashed"
        _write_journal(entry / MANIFEST, document)
    warnings = []
    if in_place:
        warnings.append("Một số thư mục đang bị mở (trình phát/Explorer) nên giữ "
                        "tại chỗ; sẽ xoá cùng project khi hết hạn: " + "; ".join(in_place))
    return {"ok": True, "trashed": True, "trash_dir": str(entry),
            "moved": [str(s) for s, _d in moved], "in_place": in_place,
            "purge_after": document["purge_after"], "warnings": warnings}


# ── Khôi phục ──────────────────────────────────────────────────────────────
def restore_from_trash(session: Session, project: Project,
                       roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    with DELETE_LOCK:
        if project.deleted_at is None:
            raise ProjectCleanupError(f"«{project.name}» không ở trong Thùng rác.")
        entry = _entry_dir(project, roots)
        document = _read_manifest(entry) if entry else {}
        items = document.get("paths") or []
        owners = list(_root_keys(roots).values())
        # Kiểm hết TRƯỚC khi đổi tên cái nào (không khôi phục nửa vời).
        for item in items:
            original, trashed = Path(item["original"]), Path(item["trashed"])
            owner = next((o for o in owners if _inside(original, o)), None)
            if owner is None:
                raise ProjectCleanupError(f"Đường dẫn gốc lạ trong nhật ký: {original}")
            _validate(original, owner)
            _validate(trashed, trash_root(roots))
            if original.exists() and trashed.exists():
                raise ProjectCleanupError(
                    f"Không khôi phục được: {original} đã tồn tại (dữ liệu mới?). "
                    "Hãy đổi tên/di chuyển thư mục đó rồi thử lại.")
        restored = []
        for item in items:
            original, trashed = Path(item["original"]), Path(item["trashed"])
            if not trashed.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            trashed.rename(original)
            restored.append(str(original))
        project.deleted_at = None
        project.trash_dir = None
        session.add(project)
        session.commit()
        if entry is not None and entry.exists():
            leftover = [p for p in entry.rglob("*") if p.is_file() and p.name != MANIFEST]
            if not leftover:
                shutil.rmtree(entry, ignore_errors=True)
        return {"ok": True, "restored": restored}


def _inside(path: Path, root: Path) -> bool:
    try:
        return bool(Path(path).resolve().relative_to(Path(root).resolve()).parts)
    except ValueError:
        return False


# ── Xoá vĩnh viễn ──────────────────────────────────────────────────────────
def purge_trashed(session: Session, project: Project,
                  roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    """Xoá hẳn 1 project đang ở thùng rác (DB + mục Trash/ + phần tại chỗ)."""
    with DELETE_LOCK:
        if project.deleted_at is None:
            raise ProjectCleanupError(f"«{project.name}» không ở trong Thùng rác.")
        entry = _entry_dir(project, roots)
        size = tree_size(entry) if entry and entry.exists() else 0
        info = delete_project_data(session, project, roots)
        warnings = list(info.get("warnings") or [])
        freed = int(info.get("freed_bytes") or 0)
        if entry is not None and entry.exists():
            _purge_with_retries(entry, trash_root(roots))
            if entry.exists():
                left = tree_size(entry)
                freed += max(0, size - left)
                warnings.append(f"Còn file bị khoá trong {entry} — sẽ tự dọn lại sau.")
            else:
                freed += size
        return {"ok": True, "freed_bytes": freed, "warnings": warnings}


def purge_expired(session: Session, roots: CleanupRoots = DEFAULT_ROOTS,
                  now: datetime | None = None) -> dict:
    """Xoá vĩnh viễn project đã ở thùng rác quá 7 ngày + mục Trash/ mồ côi
    (project không còn trong DB). Chỉ gọi TRONG backend (biết job nào đang bận)."""
    now = now or datetime.now(timezone.utc)
    purged, freed, errors = [], 0, []
    with DELETE_LOCK:
        for project in session.exec(select(Project)).all():
            due = purge_at(project)
            if due is None or due > now:
                continue
            name = project.name
            try:
                info = purge_trashed(session, project, roots)
                purged.append(name)
                freed += info["freed_bytes"]
            except Exception as exc:     # noqa: BLE001 — thử lại lần sau
                session.rollback()
                errors.append(f"{name}: {exc}")
        freed += _sweep_orphans(session, roots)
    return {"purged": purged, "freed_bytes": freed, "errors": errors}


def _sweep_orphans(session: Session, roots: CleanupRoots) -> int:
    root = trash_root(roots)
    if not root.is_dir():
        return 0
    projects = {p.id: p for p in session.exec(select(Project)).all()}
    freed = 0
    for entry in list(root.iterdir()):
        if not entry.is_dir():
            continue
        document = _read_manifest(entry)
        pid = document.get("project_id")
        owner = projects.get(pid)
        if owner is not None and owner.trash_dir and \
                Path(owner.trash_dir).resolve() == entry.resolve():
            continue                        # đang dùng
        if owner is not None or document.get("phase") in ("staging", "restore_pending"):
            # Project còn sống (đã khôi phục) / mục chưa rõ trạng thái: chỉ gỡ
            # vỏ RỖNG, không bao giờ xoá dữ liệu có thể còn cần.
            if not [p for p in entry.rglob("*") if p.is_file() and p.name != MANIFEST]:
                shutil.rmtree(entry, ignore_errors=True)
            continue
        if not document:                     # không có nhật ký → không đoán
            continue
        before = tree_size(entry)
        _purge_with_retries(entry, root)
        freed += before - (tree_size(entry) if entry.exists() else 0)
    try:
        if not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass
    return freed


# ── Liệt kê ────────────────────────────────────────────────────────────────
def list_trash(session: Session, roots: CleanupRoots = DEFAULT_ROOTS,
               now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for project in session.exec(select(Project)).all():
        if project.deleted_at is None:
            continue
        due = purge_at(project)
        entry = None
        try:
            entry = _entry_dir(project, roots)
        except ProjectCleanupError:
            pass
        document = _read_manifest(entry) if entry else {}
        in_place = [p for p in document.get("in_place") or [] if Path(p).exists()]
        size = (tree_size(entry) if entry and entry.exists() else 0) + \
            sum(tree_size(Path(p)) for p in in_place)
        left = (due - now).total_seconds() if due else 0
        out.append({
            "id": project.id, "name": project.name,
            "description": project.description,
            "deleted_at": _utc(project.deleted_at).isoformat(),
            "purge_at": due.isoformat() if due else None,
            "days_left": max(0.0, round(left / 86400, 1)),
            "size_bytes": size, "trash_dir": str(entry) if entry else None,
            "in_place": in_place,
        })
    out.sort(key=lambda r: r["deleted_at"], reverse=True)
    return out
