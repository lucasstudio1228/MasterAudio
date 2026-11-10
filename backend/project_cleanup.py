"""Delete only a project's owned files, with rollback before DB commit.

Imported Track.filepath/Mix.output_dir/SunoBatch.staging_dir values are never
used as deletion targets. A failed purge leaves a clearly reported quarantine;
the small recovery journal under data/project_deletions records its location.

Windows: a folder cannot be renamed while ANY file inside is open (VLC, Explorer
preview, Defender scan, the tool's own video player) → WinError 5. Such folders
are retried briefly, then deleted IN PLACE after the DB commit; files that stay
locked are recorded as `cleanup_pending` and swept later (`sweep_pending`).
Project IDs are never reused (`install_id_guard`), so a leftover `outputs/<id>`
can never be mistaken for a newer project's data.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time
import uuid

from sqlmodel import Session, select

from backend.models import (Mix, Project, SunoBatch, SunoCandidate, Track,
                            TrackStem, YoutubeUpload)


class ProjectCleanupError(RuntimeError):
    """Deletion stopped without silently discarding failed filesystem work."""


class ProjectBusyError(ProjectCleanupError):
    """A project is still being used by a worker."""


@dataclass(frozen=True)
class CleanupRoots:
    workspace: Path

    @property
    def media(self) -> Path:
        return self.workspace / "media"

    @property
    def outputs(self) -> Path:
        return self.workspace / "outputs"

    @property
    def stems(self) -> Path:
        return self.workspace / "stems"

    @property
    def suno(self) -> Path:
        return self.workspace / "data" / "suno_batches"

    @property
    def journals(self) -> Path:
        return self.workspace / "data" / "project_deletions"

    def owned_roots(self) -> tuple[Path, ...]:
        return (self.media, self.outputs, self.stems, self.suno)


DEFAULT_ROOTS = CleanupRoots(Path(__file__).resolve().parent.parent)
# "paused" = job tạm dừng vẫn giữ file/khoá → coi là đang bận.
_ACTIVE = {"pending", "running", "paused"}
TRASH = ".project-trash"
# Rename runs while the worker locks are held → keep it short (~1s); anything
# still locked is deleted in place after the commit instead.
_RENAME_RETRY_DELAYS = (0.3, 0.7)
# In-place purge runs outside the locks: a few passes for transient scanners.
_PURGE_RETRY_DELAYS = (1.0, 2.0)
_sleep = time.sleep
# Serializes deletions and sweeps (bulk delete + startup sweep + junk cleaner).
DELETE_LOCK = threading.RLock()
_FLOOR_FILE = "id_floor.json"
_JOURNAL_NAME = re.compile(r"^project_(\d+)_[0-9a-f]{32}$")


def _managers():
    # Lazy imports keep path/DB tests independent of the audio runtime.
    from backend.job_manager import job_manager
    from backend.stem_job_manager import stem_job_manager
    from backend.video.job_manager import video_job_manager
    return job_manager, stem_job_manager, video_job_manager


def _busy_unlocked(session: Session, project_id: int, audio, stem, video) -> bool:
    mixes = session.exec(select(Mix).where(Mix.project_id == project_id)).all()
    stems = session.exec(
        select(TrackStem).where(TrackStem.project_id == project_id)).all()
    video_job = video._jobs.get(project_id)
    busy = bool(video_job and video_job.status in _ACTIVE)
    busy = busy or any(
        (job := audio._jobs.get(mix.id)) and job.status in _ACTIVE
        for mix in mixes)
    busy = busy or any(
        stem._statuses.get(item.id) in _ACTIVE
        or ((future := stem._futures.get(item.id)) is not None
            and not future.done())
        for item in stems)
    now = datetime.now(timezone.utc)
    for batch in session.exec(
            select(SunoBatch).where(SunoBatch.project_id == project_id)).all():
        expiry = batch.lock_expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        busy = busy or bool(batch.lock_token and expiry and expiry > now)
    return busy


def project_busy(session: Session, project_id: int) -> bool:
    """True nếu project đang có tác vụ chạy/chờ (audio, stem, video, Suno)."""
    audio, stem, video = _managers()
    with ExitStack() as stack:
        for manager in (audio, stem, video):
            stack.enter_context(manager._lock)
        return _busy_unlocked(session, project_id, audio, stem, video)


@contextmanager
def _guard_idle(session: Session, project_id: int):
    """Serialize this check/delete against worker state updates and submits.

Managers expose no shared project lifecycle API yet, so keep their existing
locks for the short rename + DB transaction, never for the expensive purge.
"""
    audio, stem, video = _managers()
    with ExitStack() as stack:
        for manager in (audio, stem, video):
            stack.enter_context(manager._lock)
        if _busy_unlocked(session, project_id, audio, stem, video):
            raise ProjectBusyError(
                "Project đang có tác vụ chạy/chờ. Hãy dừng tác vụ và chờ dừng "
                "hoàn toàn trước khi xoá project.")
        yield


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _reject_links(path: Path) -> None:
    """Reject Windows junctions/reparse points as well as POSIX symlinks."""
    path = _absolute(path)
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProjectCleanupError(f"Không xoá qua symlink/junction: {part}")


def _validate(path: Path, root: Path, *, inspect_tree: bool = False) -> None:
    path, root = _absolute(path), _absolute(root)
    _reject_links(root)
    _reject_links(path)
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectCleanupError(f"Đường dẫn nằm ngoài vùng project: {path}") from exc
    if not relative.parts:
        raise ProjectCleanupError(f"Không được xoá thư mục gốc: {root}")
    if path.exists() and not path.is_dir():
        raise ProjectCleanupError(f"Đường dẫn project không phải thư mục: {path}")
    if inspect_tree and path.exists():
        # os.walk(followlinks=False) plus explicit checks also catches junctions
        # before rmtree can traverse them on an older Windows/Python runtime.
        for current, directories, files in os.walk(path, followlinks=False):
            for name in (*directories, *files):
                _reject_links(Path(current) / name)


def owned_paths(project_id: int, project_name: str | None,
                roots: CleanupRoots = DEFAULT_ROOTS) -> list[tuple[Path, Path]]:
    """(root, path) các thư mục THUỘC project (chỉ những cái đang tồn tại)."""
    if not isinstance(project_id, int) or project_id <= 0:
        raise ProjectCleanupError("Project ID không hợp lệ để xoá dữ liệu.")
    from backend.video.config import MEDIA_GROUPS, project_folder_name

    media_names = {
        str(project_id), f"project_{project_id}",
        project_folder_name(project_name, project_id),
    }
    # media/<tên> (project cũ) và media/Mix Set|Track lẻ/<tên> (project mới).
    media_dirs = [roots.media, *(roots.media / g for g in MEDIA_GROUPS)]
    found: set[Path] = set()
    for folder in media_dirs:
        if folder.is_dir():
            # ID suffix is an ownership marker, so old names after a rename remain
            # owned. Bare legacy names are ambiguous and deliberately not guessed.
            found.update(item for item in folder.iterdir()
                         if item.name.endswith(f" (#{project_id})"))
        found.update(folder / name for name in media_names)
    candidates = [(roots.outputs, roots.outputs / str(project_id)),
                  (roots.stems, roots.stems / str(project_id)),
                  (roots.suno, roots.suno / f"project_{project_id}")]
    candidates.extend((roots.media, path) for path in sorted(found))
    return [(root, path) for root, path in candidates if path.exists()]


def _owned_paths(project: Project, roots: CleanupRoots) -> list[tuple[Path, Path]]:
    for root in (*roots.owned_roots(), roots.journals):
        _validate(root, roots.workspace)
    result = owned_paths(project.id, project.name, roots)
    for root, path in result:
        _validate(path, root, inspect_tree=True)
    return result


def tree_size(path: Path) -> int:
    """Tổng byte của file trong cây (không theo symlink/junction)."""
    total, stack = 0, [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.is_symlink():
                                stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _write_journal(path: Path, document: dict) -> None:
    # Atomic replacement avoids leaving an unreadable recovery record.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _delete_records(session: Session, project: Project) -> None:
    # Explicit flushes preserve FK ordering even when SQLite FK checks are on.
    from backend.models import CoverSettings, CoverSong, SourceSong
    for model in (SunoCandidate, TrackStem, YoutubeUpload, SunoBatch, Mix,
                  CoverSong, CoverSettings, SourceSong, Track):
        for record in session.exec(
                select(model).where(model.project_id == project.id)).all():
            session.delete(record)
        session.flush()
    session.delete(project)
    session.flush()


def _move_with_retry(source: Path, destination: Path) -> bool:
    """True = đã chuyển vào quarantine; False = vẫn bị khoá (WinError 5/32)."""
    for delay in (*_RENAME_RETRY_DELAYS, None):
        try:
            source.rename(destination)
            return True
        except PermissionError:
            if delay is None:
                return False
            _sleep(delay)
    return False


def _purge_tree(path: Path) -> None:
    """rmtree nhưng KHÔNG dừng ở file bị khoá: xoá hết phần xoá được, gỡ
    read-only rồi thử lại từng mục; mục còn khoá được giữ lại (path còn tồn tại)."""
    def retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _purge_with_retries(path: Path, root: Path) -> None:
    for delay in (0.0, *_PURGE_RETRY_DELAYS):
        if delay:
            _sleep(delay)
        try:
            _validate(path, root, inspect_tree=True)
            _purge_tree(path)
        except OSError:
            pass
        if not path.exists():
            return


def _remove_empty_trash(roots: CleanupRoots) -> None:
    """Gỡ vỏ quarantine RỖNG (lần xoá rollback để lại). Không đụng mục có file."""
    for root in roots.owned_roots():
        trash = root / TRASH
        if not trash.is_dir():
            continue
        for token in list(trash.iterdir()):
            try:
                if token.is_dir() and not any(token.iterdir()):
                    token.rmdir()
            except OSError:
                pass
        try:
            if not any(trash.iterdir()):
                trash.rmdir()
        except OSError:
            pass


def _lock_hint(path: Path) -> str:
    from backend.win_locks import lockers
    apps = lockers([path])
    if apps:
        return " Đang mở bởi: " + ", ".join(apps) + "."
    return (" Có thể đang mở trong File Explorer/trình phát video hoặc Windows "
            "Defender đang quét.")


# ── ID không bao giờ dùng lại ──────────────────────────────────────────────
def deleted_id_floor(roots: CleanupRoots = DEFAULT_ROOTS) -> int:
    """ID lớn nhất từng bị xoá (file floor + tên nhật ký project_<id>_<hex>)."""
    best = 0
    try:
        best = int(json.loads((roots.journals / _FLOOR_FILE).read_text(
            encoding="utf-8")).get("max_deleted_id", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        best = 0
    if roots.journals.is_dir():
        for item in roots.journals.iterdir():
            match = _JOURNAL_NAME.match(item.name)
            if match:
                best = max(best, int(match.group(1)))
    return best


def _record_deleted_id(project_id: int, roots: CleanupRoots) -> None:
    try:
        _write_journal(roots.journals / _FLOOR_FILE,
                       {"max_deleted_id": max(deleted_id_floor(roots), project_id)})
    except OSError:
        pass        # tên thư mục nhật ký vẫn giữ mốc này


_GUARDED: set[int] = set()


def install_id_guard(engine, roots: CleanupRoots = DEFAULT_ROOTS) -> None:
    """Project mới luôn lấy ID > mọi ID từng có (SQLite mặc định dùng lại
    max(id)+1 sau khi xoá project cuối → outputs/<id> sót lại bị nhận nhầm)."""
    from sqlalchemy import event, text
    if id(engine) in _GUARDED:
        return
    _GUARDED.add(id(engine))

    @event.listens_for(Project, "before_insert")
    def _assign_fresh_id(_mapper, connection, target):
        if target.id is not None or connection.engine is not engine:
            return
        top = connection.execute(text("SELECT MAX(id) FROM project")).scalar() or 0
        # Nhiều project trong 1 flush (tạo lô) → nối tiếp nhau, không trùng.
        last = connection.info.get("project_id_last", 0)
        target.id = max(int(top), deleted_id_floor(roots), int(last)) + 1
        connection.info["project_id_last"] = target.id


# ── Xoá ────────────────────────────────────────────────────────────────────
def delete_project_data(session: Session, project: Project,
                        roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    """Delete DB + owned files; restore staged files if the DB commit fails."""
    with DELETE_LOCK:
        return _delete_project_data(session, project, roots)


def _delete_project_data(session: Session, project: Project,
                         roots: CleanupRoots) -> dict:
    project_id = project.id
    token = f"project_{project_id}_{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path, Path]] = []
    in_place: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    pending: list[str] = []
    freed = 0
    with _guard_idle(session, project_id):
        targets = _owned_paths(project, roots)
        sizes = {str(path): tree_size(path) for _root, path in targets}
        journal_dir = roots.journals / token
        _validate(journal_dir, roots.journals)
        journal_dir.mkdir(parents=True)
        journal_path = journal_dir / "manifest.json"
        document = {
            "project_id": project_id, "project_name": project.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "staging", "paths": [
                {"original": str(path),
                 "quarantine": str(root / TRASH / token / path.name)}
                for root, path in targets],
        }
        _write_journal(journal_path, document)
        try:
            for root, source in targets:
                destination = root / TRASH / token / source.name
                _validate(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Both endpoints are verified and within the same owned root;
                # rename is recoverable and does not copy large media files.
                if _move_with_retry(source, destination):
                    moved.append((root, source, destination))
                else:
                    in_place.append((root, source))
                    try:
                        destination.parent.rmdir()
                    except OSError:
                        pass
            document["phase"] = "staged"
            document["in_place"] = [str(source) for _root, source in in_place]
            _write_journal(journal_path, document)
            _delete_records(session, project)
            session.commit()
        except Exception as exc:
            session.rollback()
            restore_errors = []
            for root, source, destination in reversed(moved):
                try:
                    _validate(source, root)
                    _validate(destination, root)
                    if source.exists():
                        raise ProjectCleanupError(f"Đích khôi phục đã tồn tại: {source}")
                    destination.rename(source)
                except Exception as restore_exc:
                    restore_errors.append(str(restore_exc))
            if not restore_errors:
                _remove_empty_trash(roots)
            document["phase"] = "restore_pending" if restore_errors else "restored"
            document["error"] = str(exc)
            document["restore_errors"] = restore_errors
            try:
                _write_journal(journal_path, document)
            except OSError:
                pass  # Initial staging journal still contains recovery paths.
            detail = f"Không xoá project; giao dịch đã rollback. Nhật ký: {journal_path}."
            if restore_errors:
                detail += " Cần khôi phục thư mục quarantine: " + "; ".join(restore_errors)
            raise ProjectCleanupError(detail) from exc
        document["phase"] = "committed"
        try:
            _write_journal(journal_path, document)
        except OSError as exc:
            warnings.append(f"Không cập nhật được nhật ký sau commit: {exc}")
    _record_deleted_id(project_id, roots)

    # All DB records are now gone. Purge errors must never masquerade as a
    # rollback: retain the quarantined files and report their exact locations.
    deleted = []
    for root, source, destination in moved:
        try:
            _validate(destination, root, inspect_tree=True)
            shutil.rmtree(destination)
        except OSError as exc:
            warnings.append(f"Chưa dọn xong quarantine {destination}: {exc}")
        except ProjectCleanupError as exc:
            warnings.append(str(exc))
        if destination.exists():
            pending.append(str(destination))
            freed += max(0, sizes.get(str(source), 0) - tree_size(destination))
            continue
        deleted.append(str(source))
        freed += sizes.get(str(source), 0)
        try:
            if destination.parent.exists() and not any(destination.parent.iterdir()):
                destination.parent.rmdir()
        except OSError:
            pass
    # Thư mục không đổi tên được (file bị khoá) → xoá tại chỗ phần xoá được.
    for root, source in in_place:
        try:
            _purge_with_retries(source, root)
        except ProjectCleanupError as exc:
            warnings.append(str(exc))
        if source.exists():
            pending.append(str(source))
            freed += max(0, sizes.get(str(source), 0) - tree_size(source))
            warnings.append(f"Còn file bị khoá trong {source}.{_lock_hint(source)} "
                            "Tool sẽ tự dọn lại sau (khi khởi động / lần xoá sau / "
                            "nút «Dọn dữ liệu thừa»).")
        else:
            deleted.append(str(source))
            freed += sizes.get(str(source), 0)
    document.update(phase="cleanup_pending" if pending else "completed",
                    cleanup_pending=pending, warnings=warnings)
    try:
        _write_journal(journal_path, document)
    except OSError as exc:
        warnings.append(f"Không cập nhật được nhật ký dọn file: {exc}")
    return {"ok": True, "deleted_paths": deleted, "cleanup_pending": pending,
            "warnings": warnings, "cleanup_journal": str(journal_path),
            "freed_bytes": freed}


def _root_for(path: Path, roots: CleanupRoots) -> Path | None:
    absolute = _absolute(path)
    for root in roots.owned_roots():
        try:
            if absolute.relative_to(_absolute(root)).parts:
                return root
        except ValueError:
            continue
    return None


def sweep_pending(session: Session, roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    """Dọn lại file còn sót của các lần xoá trước (bị khoá lúc xoá) + vỏ
    quarantine rỗng. Chỉ xoá đúng đường dẫn ghi trong nhật ký."""
    freed, cleared, remaining_all = 0, [], []
    with DELETE_LOCK:
        live_ids = set(session.exec(select(Project.id)).all())
        journals = sorted(roots.journals.iterdir()) if roots.journals.is_dir() else []
        for journal_dir in journals:
            manifest = journal_dir / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                document = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pending = document.get("cleanup_pending") or []
            if document.get("phase") != "cleanup_pending" or not pending:
                continue
            project_id = document.get("project_id")
            remaining = []
            for raw in pending:
                path = Path(raw)
                root = _root_for(path, roots)
                # In-place leftovers sit at the project's original path: never
                # touch them while a project with that ID exists.
                if root is None or (TRASH not in path.parts and project_id in live_ids):
                    remaining.append(raw)
                    continue
                if not path.exists():
                    cleared.append(raw)
                    continue
                before = tree_size(path)
                try:
                    _purge_with_retries(path, root)
                except ProjectCleanupError:
                    pass
                if path.exists():
                    freed += max(0, before - tree_size(path))
                    remaining.append(raw)
                else:
                    freed += before
                    cleared.append(raw)
            document["cleanup_pending"] = remaining
            document["phase"] = "cleanup_pending" if remaining else "completed"
            remaining_all.extend(remaining)
            try:
                _write_journal(manifest, document)
            except OSError:
                pass
        _remove_empty_trash(roots)
    return {"freed_bytes": freed, "cleared": cleared, "remaining": remaining_all}
