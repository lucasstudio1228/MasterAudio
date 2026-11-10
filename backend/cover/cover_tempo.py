"""
cover/cover_tempo.py — Chỉnh tempo BẢN COVER đã tải về (tab ② Suno Cover, thanh
tempo + nút OK ở từng bản).

Khác cover/tempo.py (đổi tempo bài GỐC trước khi đưa lên Suno). Ở đây:
  • Lần chỉnh đầu, bản Suno gốc được chép vào ban_cover/_goc_truoc_tempo/ (không xoá).
  • Bản đổi tempo (giữ nguyên cao độ — ffmpeg atempo) ghi ĐÈ đúng đường dẫn của
    Track → Lời bài hát, Mix, Video, kiểm tra lỗi dùng luôn bản mới.
  • Luôn tính từ bản gốc đã cất → chỉnh nhiều lần không bị giảm chất lượng;
    chọn 1.00× là trả về đúng bản Suno gốc.
  • Lời đã căn: atempo kéo giãn đều cả bài nên mốc thời gian chỉ cần nhân tỉ lệ.
"""
from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

from . import mastering, tempo
from .lyrics import dump_lines, load_lines

MIN_FACTOR = 0.5                 # chậm nhất: bằng nửa tốc độ bản Suno
MAX_FACTOR = 1.5                 # nhanh nhất: gấp rưỡi
BACKUP_STEM = "_goc_truoc_tempo"


class CoverTempoError(RuntimeError):
    pass


def normalize_factor(value, strict: bool = True) -> float:
    """strict=False: giá trị cũ trong DB lỗi/ngoài khoảng → coi như 1.0."""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        factor = math.nan
    if not math.isfinite(factor) or not MIN_FACTOR - 1e-9 <= factor <= MAX_FACTOR + 1e-9:
        if not strict:
            return 1.0
        raise CoverTempoError(
            f"Tempo phải nằm trong khoảng {MIN_FACTOR:g}× đến {MAX_FACTOR:g}×.")
    return round(factor, 3)


def is_tool_file(path: Path, cover_dir: Path) -> bool:
    """Chỉ đụng file nằm thẳng trong ban_cover của project (không sửa file người dùng)."""
    try:
        return Path(path).resolve().parent == Path(cover_dir).resolve()
    except OSError:
        return False


def backup_path_for(path: Path) -> Path:
    path = Path(path)
    return path.parent / BACKUP_STEM / path.name


def _codec_args(path: Path, subtype: str | None) -> list[str]:
    ext = path.suffix.lower()
    if ext == ".wav":
        sub = (subtype or "").upper()
        codec = ("pcm_f32le" if "FLOAT" in sub else "pcm_s24le" if "24" in sub
                 else "pcm_s32le" if "32" in sub else "pcm_s16le")
        return ["-c:a", codec]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "320k"]
    raise CoverTempoError(f"Chưa hỗ trợ chỉnh tempo file {ext or 'không đuôi'}.")


def render(original: Path, dest: Path, factor: float, subtype: str | None,
           cfg: dict | None = None) -> None:
    """Xuất ``original`` ở tốc độ ``factor`` rồi thay nguyên tử vào ``dest``."""
    factor = normalize_factor(factor)
    original, dest = Path(original), Path(dest)
    if not original.is_file():
        raise CoverTempoError("Không thấy bản Suno gốc đã cất để chỉnh tempo.")
    temporary = dest.with_name(f".{dest.stem}.tempo.tmp{dest.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        if abs(factor - 1.0) < 1e-9:
            shutil.copyfile(original, temporary)     # mtime mới → cache cũ không bị dùng nhầm
        else:
            try:
                exe = mastering.resolve_ffmpeg(cfg or mastering.load_config())
            except mastering.MasterError as e:
                raise CoverTempoError(str(e)) from e
            result = mastering._ffmpeg(exe, [
                "-y", "-loglevel", "error", "-i", str(original), "-map", "0:a:0", "-vn",
                "-filter:a", tempo.atempo_filter(factor),
                *_codec_args(dest, subtype), str(temporary)])
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise CoverTempoError(f"ffmpeg không đổi được tempo: {detail[-400:]}")
        if not temporary.is_file() or temporary.stat().st_size <= 1024:
            raise CoverTempoError("ffmpeg không tạo được file tempo mới.")
        try:
            os.replace(temporary, dest)
        except PermissionError as e:
            raise CoverTempoError(
                "File bản cover đang được chương trình khác mở (trình nghe nhạc, "
                "Explorer…) — đóng lại rồi bấm OK lần nữa.") from e
    finally:
        temporary.unlink(missing_ok=True)


def apply(path: Path, current_factor: float, new_factor: float, subtype: str | None,
          cfg: dict | None = None) -> Path:
    """Đổi tempo file bản cover ``path`` sang ``new_factor`` (tính từ bản gốc đã cất).
    Trả đường dẫn bản Suno gốc đã cất."""
    path = Path(path)
    backup = backup_path_for(path)
    if not backup.is_file():
        if abs(current_factor - 1.0) > 1e-9:
            raise CoverTempoError(
                f"Không thấy bản Suno gốc đã cất ({backup}) — không chỉnh tiếp được "
                "để tránh đổi tempo chồng lên bản đã đổi.")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    render(backup, path, new_factor, subtype, cfg)
    return backup


def drop_master(path: Path) -> None:
    """Bỏ bản master của tempo cũ (file sinh ra, master lại được bất cứ lúc nào)."""
    for p in [*mastering.master_paths(Path(path), "both"), mastering._info_path(Path(path))]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def duration_of(path: Path, fallback: float) -> float:
    try:
        import soundfile as sf
        return round(float(sf.info(str(path)).duration), 3)
    except Exception:
        return round(float(fallback), 3)


def scale_lyrics(lyrics_json: str | None, ratio: float) -> str | None:
    """Nhân mốc thời gian các câu đã căn với ``ratio`` (= tempo cũ / tempo mới).
    None = chưa có câu nào được căn (không cần đổi)."""
    lines = load_lines(lyrics_json)
    if not lines:
        return None
    for line in lines:
        line["start"] = line["start"] * ratio
        if line.get("end") is not None:
            line["end"] = line["end"] * ratio
    return dump_lines(lines)
