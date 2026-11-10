"""Shuffle theo VÒNG + luật T+k (k = nửa số bài, tối thiểu 5).

Mỗi vòng là 1 hoán vị ngẫu nhiên của TOÀN BỘ library (mọi bài được phát đều
nhau, không bài nào bị bỏ/lặp lệch). Lặp vòng cho tới khi đủ thời lượng yêu cầu.
T+k: 1 bài đã phát thì k bài kế tiếp không được là nó — áp dụng cả ở chỗ nối
giữa 2 vòng (đầu vòng mới không được trùng k bài cuối vòng trước).

Vì sao k = n//2 (15 bài → T+7): mô phỏng 400 lần, gap TRUNG BÌNH luôn ≈ n bất
kể k; k chỉ nâng gap TỐI THIỂU (= k+1) nhưng k càng lớn thứ tự các vòng càng
giống nhau (k = n-1 ⇒ lặp y hệt, mất shuffle). n//2: gap tối thiểu 8 bài mà
vòng sau chỉ giống vòng trước ~13% (Kendall τ).
"""
import random
from typing import Optional

from audio_loader import Track

MIN_WINDOW_SIZE = 5
MIN_EFFECTIVE_SECONDS = 1.0


def effective_duration(track: Track, crossfade_sec: float) -> float:
    """Thời gian track thực sự đóng góp vào mix (2 track overlap crossfade_sec)."""
    return max(track.duration_seconds - crossfade_sec, MIN_EFFECTIVE_SECONDS)


def generate_playlist(
    tracks: list[Track],
    target_seconds: float,
    crossfade_sec: float,
    effective: Optional[dict[str, float]] = None,
    window: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> list[Track]:
    """
    Xếp playlist đủ target_seconds theo shuffle-vòng + T+`window`
    (None → max(5, n//2)).

    effective: {track.path: giây đóng góp THỰC} (đã trừ dead-air + crossfade,
    đo từ audio thật) — để tổng mix không hụt so với yêu cầu. Thiếu thì ước
    lượng bằng duration - crossfade.

    Duration là SOFT target: dừng ngay khi đủ, bài cuối phát trọn vẹn ⇒ mix
    luôn >= target (dư tối đa 1 bài).
    """
    if not tracks:
        return []
    rng = rng or random

    def eff(t: Track) -> float:
        if effective and t.path in effective:
            return max(float(effective[t.path]), MIN_EFFECTIVE_SECONDS)
        return effective_duration(t, crossfade_sec)

    # Library quá nhỏ thì không thể giữ đủ T+k — giữ khoảng cách lớn nhất có thể.
    if window is None:
        window = max(MIN_WINDOW_SIZE, len(tracks) // 2)
    win = max(0, min(window, len(tracks) - 1))

    playlist: list[Track] = []
    last_pos: dict[str, int] = {}
    accumulated = 0.0

    while accumulated < target_seconds:
        pool = list(tracks)
        rng.shuffle(pool)
        while pool and accumulated < target_seconds:
            pos = len(playlist)
            # Bài hợp lệ = chưa phát trong `win` vị trí gần nhất.
            idx = next((i for i, t in enumerate(pool)
                        if pos - last_pos.get(t.path, -10**9) > win), None)
            if idx is None:
                # Chỉ xảy ra khi library ≤ 2×win: lấy bài đã phát LÂU nhất.
                idx = min(range(len(pool)),
                          key=lambda i: last_pos.get(pool[i].path, -10**9))
            chosen = pool.pop(idx)
            playlist.append(chosen)
            last_pos[chosen.path] = pos
            accumulated += eff(chosen)

    return playlist
