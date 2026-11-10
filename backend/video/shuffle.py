"""
video/shuffle.py — Sắp xếp ngẫu nhiên clip theo luật T+N (mặc định T+5):
không clip nào lặp lại trong N clip kế tiếp. Số clip đủ phủ hết duration audio.
"""
from __future__ import annotations
import math
import random


def slots_for_duration(audio_seconds: float, clip_seconds: float) -> int:
    """Số clip cần để phủ >= audio_seconds (mỗi clip clip_seconds giây)."""
    if clip_seconds <= 0:
        raise ValueError("clip_seconds phải > 0")
    return max(int(math.ceil(audio_seconds / clip_seconds)), 1)


def shuffle_t_plus_n(
    clips: list,
    count: int,
    window: int = 5,
    seed: int | None = None,
    first=None,
) -> list:
    """
    Trả về list dài `count`, chọn từ `clips` sao cho 1 clip không xuất hiện lại
    khi chưa có đủ `window` clip khác chen giữa (luật T+window).

    - Nếu số clip <= window: không thể đảm bảo tuyệt đối → nới cửa sổ xuống
      (len(clips)-1) để vẫn tránh lặp liền kề tối đa có thể.
    """
    if not clips:
        raise ValueError("Không có clip để shuffle.")
    if first is not None and first not in clips:
        raise ValueError("Clip mở đầu không nằm trong danh sách clips.")
    rng = random.Random(seed)

    pool = list(clips)
    eff_window = min(window, max(len(pool) - 1, 0))

    # Khi truyền first=clip_00, video final luôn mở bằng thumbnail/ảnh 0;
    # những lượt sau vẫn tuân thủ đầy đủ cửa sổ T+N.
    result: list = [first] if first is not None and count > 0 else []
    recent: list = list(result)
    while len(result) < count:
        available = [c for c in pool if c not in recent]
        if not available:                    # an toàn (không xảy ra khi đủ clip)
            available = list(pool)
        chosen = rng.choice(available)
        result.append(chosen)
        recent.append(chosen)
        if len(recent) > eff_window:
            recent.pop(0)
    return result


def build_video_sequence(
    clip_paths: list[str],
    audio_seconds: float,
    clip_seconds: float = 8.0,
    window: int = 5,
    seed: int | None = None,
) -> list[str]:
    """
    Tạo trình tự clip (đường dẫn) đủ phủ duration audio theo luật T+window.
    Đây là thứ tự cuối cùng để ffmpeg nối lại.
    """
    count = slots_for_duration(audio_seconds, clip_seconds)
    return shuffle_t_plus_n(clip_paths, count, window=window, seed=seed)


def build_chained_sequence(
    clip_pairs: dict,           # {clip_path: (start_img, end_img)}
    audio_seconds: float,
    clip_seconds: float = 8.0,
    window: int = 5,
    seed: int | None = None,
) -> list[str]:
    """
    Trình tự clip LIỀN MẠCH: clip kế có ảnh-đầu == ảnh-cuối của clip trước
    (nên khi nối, khung nối khớp nhau). Kết hợp T+window (không lặp trong
    `window` clip khi còn lựa chọn) + ngẫu nhiên.
    """
    from collections import defaultdict
    if not clip_pairs:
        raise ValueError("Không có clip (clip_pairs rỗng).")
    rng = random.Random(seed)
    clips = list(clip_pairs.keys())

    adj: dict = defaultdict(list)          # start_img -> [clip,...]
    for c, (s, _e) in clip_pairs.items():
        adj[s].append(c)

    count = slots_for_duration(audio_seconds, clip_seconds)
    eff_window = min(window, max(len(clips) - 1, 0))

    seq: list[str] = []
    recent: list[str] = []
    # Bắt đầu chuỗi bằng clip có ảnh-đầu == 0 (thumbnail) để video mở đầu bằng
    # ảnh 0; nếu (hiếm) không có, chọn ngẫu nhiên.
    starters = adj.get(0) or [c for c in clips if clip_pairs[c][0] == 0]
    cur = rng.choice(starters) if starters else rng.choice(clips)
    seq.append(cur)
    recent.append(cur)
    cur_end = clip_pairs[cur][1]

    while len(seq) < count:
        cands = adj.get(cur_end)
        if not cands:                      # nút cụt → nhảy ngẫu nhiên (hiếm)
            nxt = rng.choice(clips)
        else:
            avail = [c for c in cands if c not in recent] or cands
            nxt = rng.choice(avail)
        seq.append(nxt)
        recent.append(nxt)
        if len(recent) > eff_window:
            recent.pop(0)
        cur_end = clip_pairs[nxt][1]
    return seq
