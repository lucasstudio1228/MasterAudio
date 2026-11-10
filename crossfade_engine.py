"""Render mix: streaming write, equal-power crossfade, không buffer cả output vào RAM."""
import numpy as np
import librosa
import soundfile as sf

from analyzer import find_transition_points
from audio_loader import Track, load_audio

CHUNK_SAMPLES = 65536
PEAK_CEILING = 0.99
TRIM_FADE_SEC = 2.0
BIT_DEPTH_SUBTYPES = {24: "PCM_24", 32: "FLOAT"}


def make_crossfade_curves(n_samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Equal-power cosine crossfade. KHÔNG dùng linear – gây loudness dip."""
    t = np.linspace(0, np.pi / 2, n_samples)
    fade_out = np.cos(t)   # 1.0 → 0.0
    fade_in = np.sin(t)    # 0.0 → 1.0
    return fade_out, fade_in


def _to_stereo(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return np.column_stack([audio, audio])
    if audio.shape[1] == 1:
        return np.repeat(audio, 2, axis=1)
    if audio.shape[1] > 2:
        return np.ascontiguousarray(audio[:, :2])
    return audio


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    try:
        return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr,
                                res_type="kaiser_best", axis=0)
    except Exception:                       # resampy chưa cài → soxr chất lượng cao
        return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr,
                                res_type="soxr_hq", axis=0)


def prepare_track(track: Track, target_sr: int) -> np.ndarray:
    """Load → resample → stereo → float32, shape (n_samples, 2)."""
    audio, sr = load_audio(track)
    audio = np.asarray(audio, dtype=np.float32)
    audio = _resample(audio, sr, target_sr)
    audio = _to_stereo(audio)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _limit(buffer: np.ndarray) -> np.ndarray:
    """Chống clipping: chỉ scale khi thực sự vượt trần."""
    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > PEAK_CEILING:
        return buffer * (PEAK_CEILING / peak)
    return buffer


class _StreamWriter:
    """Ghi tuần tự ra file theo chunk, cắt đúng target và fade-out khi cắt."""

    def __init__(self, handle: sf.SoundFile, sample_rate: int,
                 target_samples: int | None):
        self.handle = handle
        self.sample_rate = sample_rate
        self.target_samples = target_samples
        self.position = 0
        self.finished = False

    def write(self, buffer: np.ndarray) -> None:
        if self.finished or buffer.size == 0:
            return
        if self.target_samples is not None and \
                self.position + len(buffer) > self.target_samples:
            keep = max(self.target_samples - self.position, 0)
            buffer = np.array(buffer[:keep], dtype=np.float32, copy=True)
            self._fade_out(buffer)
            self.finished = True
            if buffer.size == 0:
                return
        for start in range(0, len(buffer), CHUNK_SAMPLES):
            self.handle.write(_limit(buffer[start:start + CHUNK_SAMPLES]))
        self.position += len(buffer)

    def _fade_out(self, buffer: np.ndarray) -> None:
        """Cắt tại target sẽ tạo click — fade-out equal-power ở đuôi."""
        n = min(int(TRIM_FADE_SEC * self.sample_rate), len(buffer))
        if n <= 1:
            return
        fade_out, _ = make_crossfade_curves(n)
        buffer[-n:] *= fade_out[:, None]


def render_mix(
    playlist: list[Track],
    output_path: str,
    crossfade_sec: float = 15.0,
    target_sr: int = 48000,
    bit_depth: int = 24,
    target_seconds: float | None = None,
    progress_callback=None,
    preloaded_audio: dict | None = None,
    hard_cut: bool = False,
) -> list[dict]:
    """
    Ghép playlist thành 1 file WAV lossless.

    Với mỗi cặp track: bỏ phần dead-air ở đầu/đuôi (analyzer), rồi overlap đúng
    crossfade_sec bằng equal-power curve. Track đầu giữ nguyên fade-in, track
    cuối giữ nguyên fade-out.

    Duration là SOFT target: playlist_generator dừng thêm bài ngay khi vượt
    target, nên bài cuối luôn được phát TRỌN VẸN (không cắt giữa chừng). Chỉ khi
    hard_cut=True mới cắt cứng đúng target_seconds (kèm fade-out ở điểm cắt).

    Returns: list timestamps {index, name, source_file, start_seconds, end_seconds}
    """
    if not playlist:
        raise ValueError("Playlist rỗng — không có gì để render.")
    subtype = BIT_DEPTH_SUBTYPES.get(int(bit_depth))
    if subtype is None:
        raise ValueError(f"bit_depth phải là 24 hoặc 32, nhận được {bit_depth}")

    crossfade_samples = max(int(crossfade_sec * target_sr), 1)
    target_samples = (int(target_seconds * target_sr)
                      if (target_seconds and hard_cut) else None)
    estimated_total = sum(
        max(t.duration_seconds - crossfade_sec, 1.0) for t in playlist
    ) * target_sr

    timestamps: list[dict] = []

    def _get_audio(track: Track) -> np.ndarray:
        """Dùng preloaded audio (đã resample + LUFS normalize) nếu có,
        ngược lại load từ file như cũ. Giữ backward-compatible."""
        if preloaded_audio is not None and track.path in preloaded_audio:
            arr = np.asarray(preloaded_audio[track.path], dtype=np.float32)
            return np.ascontiguousarray(_to_stereo(arr), dtype=np.float32)
        return prepare_track(track, target_sr)

    with sf.SoundFile(output_path, mode="w", samplerate=target_sr,
                      channels=2, subtype=subtype) as handle:
        writer = _StreamWriter(handle, target_sr, target_samples)

        current = _get_audio(playlist[0])
        tail_cur, _ = find_transition_points(current, target_sr, crossfade_sec)
        body_start = 0                      # track đầu giữ nguyên fade-in
        track_start = 0

        for i in range(len(playlist) - 1):
            nxt = _get_audio(playlist[i + 1])
            tail_next, head_next = find_transition_points(nxt, target_sr, crossfade_sec)

            overlap = max(min(crossfade_samples,
                              tail_cur - body_start,
                              tail_next - head_next), 1)

            writer.write(current[body_start:max(tail_cur - overlap, body_start)])
            fade_start = writer.position

            fade_out, fade_in = make_crossfade_curves(overlap)
            blended = (current[tail_cur - overlap:tail_cur] * fade_out[:, None] +
                       nxt[head_next:head_next + overlap] * fade_in[:, None])
            writer.write(blended)

            timestamps.append(_stamp(len(timestamps), playlist[i], track_start,
                                     fade_start + overlap, target_sr))
            track_start = fade_start
            current, tail_cur, body_start = nxt, tail_next, head_next + overlap

            if progress_callback:
                progress_callback(writer.position, estimated_total)
            if writer.finished:
                break

        writer.write(current[body_start:])   # track cuối giữ nguyên fade-out
        if writer.position > track_start:
            timestamps.append(_stamp(len(timestamps), playlist[len(timestamps)],
                                     track_start, writer.position, target_sr))
        if progress_callback:
            progress_callback(writer.position, writer.position)

    return timestamps


def _stamp(index: int, track: Track, start: int, end: int, sr: int) -> dict:
    return {
        "index": index + 1,
        "name": track.name,            # main.py thay bằng tên do AI đặt
        "source_file": track.filename,
        "start_seconds": start / sr,
        "end_seconds": end / sr,
    }
