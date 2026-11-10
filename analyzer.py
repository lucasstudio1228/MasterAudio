"""Phân tích RMS energy để tìm điểm chuyển tiếp giữa 2 track."""
import librosa
import numpy as np

# Ngưỡng "gần như im lặng" = 15% mức năng lượng đại diện của track.
SILENCE_RATIO = 0.15
REFERENCE_PERCENTILE = 90


def to_mono(audio: np.ndarray) -> np.ndarray:
    """Mix xuống mono CHỈ để đo năng lượng — không đụng vào audio gốc."""
    if audio.ndim == 1:
        return np.ascontiguousarray(audio, dtype=np.float32)
    return np.ascontiguousarray(audio.mean(axis=1), dtype=np.float32)


def find_transition_points(
    audio: np.ndarray,    # float32, shape (n_samples,) hoặc (n_samples, channels)
    sr: int,
    search_window_sec: float = 15.0,
    frame_length: int = 2048,
    hop_length: int = 512,
) -> tuple[int, int]:
    """
    Tìm biên vùng fade dựa trên RMS energy.

    Returns:
        tail_point: sample index — nơi phần đuôi của track đã tắt hẳn xuống
                    nền im lặng (biên cuối của fade-out), tìm trong
                    search_window_sec giây CUỐI. Vùng crossfade của track đi ra
                    kết thúc tại đây.
        head_point: sample index — nơi track bắt đầu có tiếng trở lại
                    (biên đầu của fade-in), tìm trong search_window_sec giây ĐẦU.
                    Vùng crossfade của track đi vào bắt đầu tại đây.

    Cả hai luôn nằm strictly trong (0, n_samples) và tail_point > head_point,
    nên vùng crossfade không bao giờ rỗng.
    """
    n_samples = len(audio)
    if n_samples < frame_length * 2:
        return max(n_samples - 1, 1), min(1, max(n_samples - 2, 0))

    mono = to_mono(audio)

    # Clip window nếu track ngắn hơn 2x search window (tránh 2 vùng chồng nhau)
    window_sec = min(search_window_sec, n_samples / sr / 2.0)
    window_samples = max(int(window_sec * sr), hop_length * 2)

    rms = librosa.feature.rms(y=mono, frame_length=frame_length,
                              hop_length=hop_length)[0]
    centers = np.arange(len(rms)) * hop_length     # librosa center=True
    threshold = float(np.percentile(rms, REFERENCE_PERCENTILE)) * SILENCE_RATIO

    # --- head: frame ĐẦU TIÊN vượt ngưỡng trong window đầu ---
    head_mask = centers < window_samples
    loud_head = np.flatnonzero(head_mask & (rms >= threshold))
    if loud_head.size:
        head_point = int(centers[loud_head[0]])
    else:
        head_point = int(window_samples)           # cả window đầu là im lặng

    # --- tail: frame CUỐI CÙNG vượt ngưỡng trong window cuối ---
    tail_mask = centers >= (n_samples - window_samples)
    loud_tail = np.flatnonzero(tail_mask & (rms >= threshold))
    if loud_tail.size:
        tail_point = int(centers[loud_tail[-1]]) + hop_length
    else:
        tail_point = int(n_samples - window_samples)

    head_point = int(np.clip(head_point, 1, n_samples - 2))
    tail_point = int(np.clip(tail_point, head_point + 1, n_samples - 1))
    return tail_point, head_point
