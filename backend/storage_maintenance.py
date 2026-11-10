"""Quét + dọn dữ liệu thừa trên đĩa (nút «🧹 Dọn dữ liệu thừa»).

Chỉ dọn những thứ CHẮC CHẮN thừa, mỗi mục có khoá ổn định; `clean(keys)` luôn
quét lại rồi mới làm, nên client không thể gửi đường dẫn tuỳ ý:
  • orphans   — thư mục gắn ID của project không còn tồn tại
  • trash     — phần sót của các lần xoá trước (file bị khoá lúc xoá, vỏ rỗng)
  • patched   — _final_patched.mp4 cũ (final.mp4 đã mới hơn)
  • old_mixes — bản mix cũ (giữ bản video đang dùng + bản vá mới nhất)
  • dup_wavs  — WAV Suno trùng giữa staging và media → hardlink (không mất file)
Project đang chạy tác vụ luôn được bỏ qua. Thư mục `Projects/` (output tool cũ)
chỉ báo dung lượng, không bao giờ đụng tới.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from sqlmodel import Session, select

from backend.models import Mix, Project
from backend.project_cleanup import (DEFAULT_ROOTS, DELETE_LOCK, TRASH,
                                     CleanupRoots, ProjectBusyError,
                                     ProjectCleanupError, _guard_idle,
                                     _move_with_retry, _purge_with_retries,
                                     _validate, owned_paths, project_busy,
                                     sweep_pending, tree_size)

_MEDIA_ID = re.compile(r"\(#(\d+)\)$")
_DONE_PHASES = {"committed", "cleanup_pending", "completed"}
_MIX_TOKEN = re.compile(r"^mix_(\d+)_[0-9a-f]{32}$")
_ACTIVE_MIX = {"pending", "running", "rendering", "queued"}
_PROBE = 256 * 1024

CATEGORIES = {
    "orphans": ("Thư mục của project đã xoá",
                "Dữ liệu còn sót của project không còn trong danh sách."),
    "trash": ("Phần sót của lần xoá trước",
              "File bị khoá lúc xoá project / vỏ thư mục rỗng."),
    "patched": ("Video thay nhạc cũ (_final_patched.mp4)",
                "Bản tạm cũ hơn final.mp4 hiện tại — không còn dùng."),
    "old_mixes": ("Bản mix cũ",
                  "Giữ bản mix mới nhất mà video đang dùng; xoá các bản mix trước."),
    "dup_wavs": ("WAV Suno bị trùng (staging ↔ media)",
                 "Gộp 2 bản giống hệt thành hardlink — vẫn giữ đủ file ở cả 2 chỗ."),
}


@dataclass
class Item:
    key: str
    category: str
    label: str
    bytes: int
    project_id: int | None = None
    detail: str = ""
    payload: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "bytes": self.bytes,
                "project_id": self.project_id, "detail": self.detail}


# ── Dung lượng ─────────────────────────────────────────────────────────────
def usage_bytes(paths, seen: set | None = None) -> int:
    """Dung lượng THẬT (hardlink chỉ tính 1 lần theo (dev, inode))."""
    seen = set() if seen is None else seen
    total = 0
    for base in paths:
        for current, _dirs, files in os.walk(base, followlinks=False):
            for name in files:
                try:
                    info = os.stat(os.path.join(current, name), follow_symlinks=False)
                except OSError:
                    continue
                ident = (info.st_dev, info.st_ino)
                if info.st_ino and ident in seen:
                    continue
                seen.add(ident)
                total += info.st_size
    return total


def project_usage(session: Session, roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    sizes = {}
    for project in session.exec(select(Project)).all():
        if project.deleted_at is not None:      # ở Thùng rác → tính riêng
            continue
        paths = [path for _root, path in owned_paths(project.id, project.name, roots)]
        sizes[project.id] = usage_bytes(paths)
    trash = roots.workspace / "Trash"
    return {"projects": sizes, "total_bytes": sum(sizes.values()),
            "trash_bytes": tree_size(trash) if trash.is_dir() else 0}


# ── Quét ───────────────────────────────────────────────────────────────────
def _id_of(root: Path, name: str, roots: CleanupRoots) -> int | None:
    if root == roots.media:
        match = _MEDIA_ID.search(name)
        if match:
            return int(match.group(1))
        if name.isdigit():
            return int(name)
        if name.startswith("project_") and name[8:].isdigit():
            return int(name[8:])
        return None
    if root == roots.suno:
        return int(name[8:]) if name.startswith("project_") and name[8:].isdigit() else None
    return int(name) if name.isdigit() else None


def _scan_orphans(live: set[int], roots: CleanupRoots) -> list[Item]:
    from backend.video.config import MEDIA_GROUPS
    items = []
    for root in roots.owned_roots():
        if not root.is_dir():
            continue
        entries = list(root.iterdir())
        if root == roots.media:     # cả media/Mix Set/* và media/Track lẻ/*
            for group in MEDIA_GROUPS:
                if (root / group).is_dir():
                    entries.extend((root / group).iterdir())
        for entry in sorted(entries):
            if not entry.is_dir() or entry.name == TRASH:
                continue
            pid = _id_of(root, entry.name, roots)
            if pid is None or pid in live:
                continue
            rel = entry.relative_to(roots.workspace).as_posix()
            items.append(Item(f"orphans:{rel}", "orphans", rel, tree_size(entry),
                              pid, f"project #{pid} không còn tồn tại",
                              {"root": str(root), "path": str(entry)}))
    return items


def _journal_phases(roots: CleanupRoots) -> dict[str, str]:
    phases = {}
    if roots.journals.is_dir():
        for journal in roots.journals.iterdir():
            try:
                document = json.loads((journal / "manifest.json").read_text(
                    encoding="utf-8"))
            except (OSError, ValueError):
                continue
            phases[journal.name] = document.get("phase", "")
    return phases


def _scan_trash(session: Session, roots: CleanupRoots) -> list[Item]:
    items, phases = [], _journal_phases(roots)
    live = set(session.exec(select(Project.id)).all())
    empty = 0
    for root in roots.owned_roots():
        trash = root / TRASH
        if not trash.is_dir():
            continue
        for token in sorted(trash.iterdir()):
            if not token.is_dir():
                continue
            if not any(token.iterdir()):
                empty += 1
                continue
            # Chỉ dọn quarantine của lần xoá ĐÃ commit (DB không còn project /
            # bản mix); restore_pending/không nhật ký = cần khôi phục → không đụng.
            mix_token = _MIX_TOKEN.match(token.name)
            if mix_token:
                if session.get(Mix, int(mix_token.group(1))) is not None:
                    continue
            elif phases.get(token.name) not in _DONE_PHASES:
                continue
            rel = token.relative_to(roots.workspace).as_posix()
            items.append(Item(f"trash:{rel}", "trash", rel, tree_size(token), None,
                              "quarantine của project đã xoá",
                              {"root": str(root), "path": str(token)}))
    # File bị khoá lúc xoá (xoá tại chỗ) — ghi trong nhật ký cleanup_pending.
    if roots.journals.is_dir():
        for journal in sorted(roots.journals.iterdir()):
            try:
                document = json.loads((journal / "manifest.json").read_text(
                    encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if document.get("phase") != "cleanup_pending":
                continue
            for raw in document.get("cleanup_pending") or []:
                path = Path(raw)
                if (not path.exists() or TRASH in path.parts
                        or document.get("project_id") in live):
                    continue
                rel = path.relative_to(roots.workspace).as_posix() \
                    if path.is_relative_to(roots.workspace) else raw
                items.append(Item(f"trash:{rel}", "trash", rel, tree_size(path),
                                  document.get("project_id"),
                                  "file bị khoá lúc xoá project", {"sweep": True}))
    if empty:
        items.append(Item("trash:empty", "trash", f"{empty} thư mục rỗng", 0, None,
                          "vỏ quarantine rỗng do lần xoá lỗi trước đây",
                          {"sweep": True}))
    return items


def _newest_wav(project_id: int, roots: CleanupRoots) -> Path | None:
    folder = roots.outputs / str(project_id)
    wavs = list(folder.glob("*/mix.wav")) if folder.is_dir() else []
    return max(wavs, key=lambda p: p.stat().st_mtime) if wavs else None


def _scan_patched(projects, busy: set[int], roots: CleanupRoots) -> list[Item]:
    from backend.video.config import project_dir
    items = []
    for project in projects:
        if project.id in busy:
            continue
        final_dir = project_dir(project.id, project.name, root=roots.media,
                                kind=getattr(project, "kind", None)) / "final"
        temp = final_dir / "_final_patched.mp4"
        if not temp.is_file():
            continue
        final = final_dir / "final.mp4"
        newest = _newest_wav(project.id, roots)
        mtime = temp.stat().st_mtime
        # Còn giá trị CHỈ khi mới hơn cả final.mp4 lẫn bản mix mới nhất (lần thay
        # nhạc trước bị khoá file, chờ người dùng bấm lại) → giữ.
        stale = (not final.exists() or final.stat().st_mtime >= mtime
                 or (newest is not None and newest.stat().st_mtime >= mtime))
        if stale:
            rel = temp.relative_to(roots.workspace).as_posix()
            items.append(Item(f"patched:{project.id}", "patched", project.name,
                              temp.stat().st_size, project.id, rel,
                              {"path": str(temp)}))
    return items


def _base_of(title: str) -> int | None:
    match = re.match(r"^Vá mix #(\d+)", title or "")
    return int(match.group(1)) if match else None


def _scan_old_mixes(session: Session, projects, busy: set[int],
                    roots: CleanupRoots) -> list[Item]:
    from backend.mix_patch import latest_mix
    from backend.video.config import project_dir
    items = []
    for project in projects:
        if project.id in busy:
            continue
        folder = roots.outputs / str(project.id)
        mixes = session.exec(select(Mix).where(Mix.project_id == project.id)
                             .order_by(Mix.created_at.desc())).all()
        if not mixes:
            continue
        newest = _newest_wav(project.id, roots)
        keep = {m.id for m in mixes if (m.status or "") in _ACTIVE_MIX}
        if newest is not None and newest.parent.name.isdigit():
            keep.add(int(newest.parent.name))
        latest = latest_mix(session, project.id)
        if latest is not None:
            keep.add(latest.id)
        # Bản mix gốc của bản vá mới nhất: còn cần nếu final.mp4 CHƯA được thay
        # nhạc (nút «Thay nhạc trong final.mp4» so độ dài với bản gốc).
        patched = next((m for m in mixes if _base_of(m.title) and m.status == "completed"),
                       None)
        if patched is not None:
            final = (project_dir(project.id, project.name, root=roots.media,
                                 kind=getattr(project, "kind", None))
                     / "final" / "final.mp4")
            wav = folder / str(patched.id) / "mix.wav"
            if final.exists() and wav.exists() and final.stat().st_mtime < wav.stat().st_mtime:
                keep.add(_base_of(patched.title))
        if newest is None:
            continue    # không xác định được bản đang dùng → không đoán
        for mix in mixes:
            if mix.id in keep or (mix.status or "") in _ACTIVE_MIX:
                continue
            path = folder / str(mix.id)
            items.append(Item(f"old_mixes:{mix.id}", "old_mixes",
                              f"{project.name} — {mix.title}",
                              tree_size(path) if path.is_dir() else 0, project.id,
                              f"outputs/{project.id}/{mix.id}",
                              {"mix_id": mix.id, "path": str(path)}))
    return items


def _same_head_tail(a: Path, b: Path, size: int) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        if fa.read(_PROBE) != fb.read(_PROBE):
            return False
        if size > _PROBE:
            offset = max(0, size - _PROBE)
            fa.seek(offset)
            fb.seek(offset)
            return fa.read(_PROBE) == fb.read(_PROBE)
    return True


def _dup_pairs(project: Project, roots: CleanupRoots) -> list[tuple[Path, Path, int]]:
    from backend.video.config import project_dir
    staging = roots.suno / f"project_{project.id}"
    tracks = project_dir(project.id, project.name, root=roots.media,
                         kind=getattr(project, "kind", None)) / "suno_tracks"
    if not staging.is_dir() or not tracks.is_dir():
        return []
    by_name: dict[str, Path] = {}
    for folder in (tracks, tracks / "_rejected"):
        if folder.is_dir():
            for item in folder.iterdir():
                if item.is_file() and item.suffix.lower() == ".wav":
                    by_name.setdefault(item.name, item)
    pairs = []
    for wav in staging.rglob("*.wav"):
        twin = by_name.get(wav.name)
        if twin is None:
            continue
        try:
            a, b = os.stat(wav), os.stat(twin)
            if (a.st_size != b.st_size or a.st_dev != b.st_dev
                    or (a.st_ino and a.st_ino == b.st_ino)):
                continue
            if _same_head_tail(wav, twin, a.st_size):
                pairs.append((wav, twin, a.st_size))
        except OSError:
            continue
    return pairs


def _scan_dups(projects, busy: set[int], roots: CleanupRoots) -> list[Item]:
    items = []
    for project in projects:
        if project.id in busy:
            continue
        pairs = _dup_pairs(project, roots)
        if pairs:
            items.append(Item(f"dup_wavs:{project.id}", "dup_wavs", project.name,
                              sum(size for *_p, size in pairs), project.id,
                              f"{len(pairs)} file WAV"))
    return items


def _busy_ids(session: Session, projects) -> set[int]:
    busy = set()
    for project in projects:
        try:
            if project_busy(session, project.id):
                busy.add(project.id)
        except Exception:       # noqa: BLE001 — không xác định được → coi như bận
            busy.add(project.id)
    return busy


def _scan_items(session: Session, roots: CleanupRoots) -> tuple[list[Item], set[int]]:
    every = session.exec(select(Project)).all()
    live = {p.id for p in every}          # project ở Thùng rác vẫn "sống" (khôi phục được)
    # Thùng rác tự quản (7 ngày) — không gợi ý dọn phần dữ liệu của project đã vào đó.
    projects = [p for p in every if p.deleted_at is None]
    busy = _busy_ids(session, projects)
    orphans = _scan_orphans(live, roots)
    # File bị khoá lúc xoá nằm đúng chỗ thư mục mồ côi → chỉ liệt kê 1 lần.
    seen = {item.label for item in orphans}
    trash = [item for item in _scan_trash(session, roots) if item.label not in seen]
    items = (orphans + trash
             + _scan_patched(projects, busy, roots)
             + _scan_old_mixes(session, projects, busy, roots)
             + _scan_dups(projects, busy, roots))
    return items, busy


def scan(session: Session, roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    with DELETE_LOCK:
        items, busy = _scan_items(session, roots)
    categories = []
    for key, (title, desc) in CATEGORIES.items():
        group = [item for item in items if item.category == key]
        categories.append({"key": key, "title": title, "description": desc,
                           "bytes": sum(i.bytes for i in group),
                           "items": [i.public() for i in group]})
    legacy = roots.workspace / "Projects"
    return {
        "categories": categories,
        "total_bytes": sum(i.bytes for i in items),
        "busy_project_ids": sorted(busy),
        "untouched": [{"label": "Projects/ (output tool cũ — không tự xoá)",
                       "bytes": tree_size(legacy)}] if legacy.is_dir() else [],
    }


# ── Dọn ────────────────────────────────────────────────────────────────────
def link_or_copy(source, destination) -> None:
    """Hardlink (không tốn thêm đĩa) nếu được, ngược lại copy2 như cũ."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _relink(staging: Path, twin: Path) -> None:
    temporary = staging.with_name(staging.name + f".{uuid.uuid4().hex[:8]}.linktmp")
    os.link(twin, temporary)
    try:
        os.replace(temporary, staging)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _clean_item(session: Session, item: Item, roots: CleanupRoots) -> int:
    if item.category in ("orphans", "trash") and "path" in item.payload:
        root, path = Path(item.payload["root"]), Path(item.payload["path"])
        before = tree_size(path)
        _purge_with_retries(path, root)
        if path.exists():
            raise ProjectCleanupError(f"Còn file bị khoá trong {path}")
        return before
    if item.category == "patched":
        path = Path(item.payload["path"])
        with _guard_idle(session, item.project_id):
            size = path.stat().st_size
            path.unlink()
        return size
    if item.category == "old_mixes":
        mix = session.get(Mix, item.payload["mix_id"])
        path, root = Path(item.payload["path"]), roots.outputs
        moved = None
        with _guard_idle(session, item.project_id):
            if mix is None or mix.project_id != item.project_id:
                raise ProjectCleanupError("Bản mix đã thay đổi — hãy quét lại.")
            if path.exists():
                _validate(path, root, inspect_tree=True)
                moved = root / TRASH / f"mix_{mix.id}_{uuid.uuid4().hex}"
                moved.parent.mkdir(parents=True, exist_ok=True)
                if not _move_with_retry(path, moved):
                    moved = None
            try:
                session.delete(mix)
                session.commit()
            except Exception:
                session.rollback()
                if moved is not None:
                    moved.rename(path)      # trả thư mục về chỗ cũ
                raise
        size = item.bytes
        target = moved or path
        if target.exists():
            _purge_with_retries(target, root)
        if target.exists():
            raise ProjectCleanupError(
                f"Đã xoá bản mix khỏi danh sách nhưng còn file bị khoá: {target}")
        return size
    if item.category == "dup_wavs":
        project = session.get(Project, item.project_id)
        if project is None:
            return 0
        freed = 0
        with _guard_idle(session, project.id):
            pairs = _dup_pairs(project, roots)
        for staging, twin, size in pairs:
            try:
                _relink(staging, twin)
                freed += size
            except OSError:
                continue
        return freed
    return 0


def clean(session: Session, keys: list[str],
          roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    wanted = set(keys)
    done, errors, freed = [], [], 0
    with DELETE_LOCK:
        items, _busy = _scan_items(session, roots)
        selected = [item for item in items if item.key in wanted]
        if any(item.payload.get("sweep") for item in selected):
            result = sweep_pending(session, roots)
            freed += result["freed_bytes"]
            done.extend(item.key for item in selected if item.payload.get("sweep"))
        for item in selected:
            if item.payload.get("sweep"):
                continue
            try:
                freed += _clean_item(session, item, roots)
                done.append(item.key)
            except ProjectBusyError:
                session.rollback()
                errors.append(f"{item.label}: project đang chạy tác vụ — bỏ qua.")
            except (ProjectCleanupError, OSError) as exc:
                session.rollback()
                errors.append(f"{item.label}: {exc}")
        # Dọn vỏ trash của các bản mix vừa chuyển đi.
        sweep_pending(session, roots)
    missing = sorted(wanted - {item.key for item in selected})
    if missing:
        errors.append(f"{len(missing)} mục không còn trong lần quét mới — đã bỏ qua.")
    return {"freed_bytes": freed, "done": done, "errors": errors}
