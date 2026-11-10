"""
cover/loudness_balance.py — ĐO và CÂN BẰNG độ to giữa các bài trong Mix.

Vì sao không chỉ chuẩn LUFS tích hợp như cũ: các bản master đều -14 LUFS
nhưng tai vẫn nghe bài to bài nhỏ — bài ít dải động (nén nhiều) nghe to đều,
bài nhiều dải động có đoạn điệp khúc to hơn hẳn đoạn verse. Thứ người nghe so
sánh khi chuyển bài là ĐOẠN TO (điệp khúc), nên ở đây:

  1. Đo mỗi bài theo EBU R128 / ITU-R BS.1770 (lọc K-weighting của pyloudnorm):
     • integrated  — độ to trung bình cả bài (LUFS)
     • loud        — độ to «đoạn to»: phân vị 90 của short-term loudness (cửa sổ 3 s)
     • lra         — dải động (Loudness Range, LU)
     • peak_db     — đỉnh mẫu (dBFS)
  2. Đưa «đoạn to» của mọi bài về cùng một mức; dời chung cả danh sách sao cho
     trung vị độ to tích hợp = độ to mục tiêu của Mix (preset master / -16).
     Cộng thêm chỉnh tay từng bài (CoverSong.mix_gain_db) nếu người dùng muốn.
  3. Bài cần tăng mà đỉnh vượt trần → limiter nhìn trước (lookahead) chặn ở
     -1 dBFS thay vì hạ cả bài xuống (cách cũ làm bài đó lại nhỏ đi).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SILENCE_LUFS = -70.0
SHORT_TERM_SEC = 3.0          # cửa sổ short-term (EBU R128)
SHORT_TERM_HOP_SEC = 0.5
LOUD_PERCENTILE = 90          # «đoạn to» = phân vị 90 của short-term
CEILING_DB = -1.0             # trần đỉnh sau cân bằng (dBFS)
LIMITER_WINDOW_SEC = 0.010    # nửa cửa sổ nhìn trước/nhả của limiter
MAX_BOOST_DB = 12.0
MAX_CUT_DB = -24.0
MANUAL_RANGE_DB = 12.0        # chỉnh tay mỗi bài trong ±12 dB
FLAG_LU = 1.5                 # lệch hơn mức này so với trung vị → đánh dấu
HEAVY_LIMIT_DB = 3.0          # limiter phải nén hơn mức này → cảnh báo

_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()


def _to_2d(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio)
    return a[:, None] if a.ndim == 1 else a


def _power_cumsum(audio: np.ndarray, sr: int) -> np.ndarray:
    """Tổng tích luỹ công suất K-weighted (cộng các kênh, hệ số kênh L/R = 1)."""
    meter = pyln.Meter(sr)
    a = _to_2d(audio).astype(np.float64)
    power = np.zeros(a.shape[0], dtype=np.float64)
    for ch in range(min(a.shape[1], 2)):
        x = a[:, ch]
        for stage in meter._filters.values():
            x = stage.apply_filter(x)
        power += x * x
    return np.concatenate(([0.0], np.cumsum(power)))


def _blocks(cs: np.ndarray, sr: int, block_sec: float, hop_sec: float) -> np.ndarray:
    """Công suất trung bình của các khối dài block_sec, bước hop_sec."""
    n = len(cs) - 1
    size = int(round(block_sec * sr))
    hop = max(int(round(hop_sec * sr)), 1)
    if n < size or size <= 0:
        return np.zeros(0)
    starts = np.arange(0, n - size + 1, hop)
    return (cs[starts + size] - cs[starts]) / size


def _lufs(power) -> np.ndarray:
    return -0.691 + 10.0 * np.log10(np.maximum(power, 1e-20))


def _gated_integrated(power: np.ndarray) -> float | None:
    """Độ to tích hợp theo BS.1770-4 (cổng tuyệt đối -70, cổng tương đối -10)."""
    if not len(power):
        return None
    loud = _lufs(power)
    keep = power[loud > SILENCE_LUFS]
    if not len(keep):
        return None
    rel = _lufs(keep.mean()) - 10.0
    keep = keep[_lufs(keep) > rel]
    return float(_lufs(keep.mean())) if len(keep) else None


def analyze(audio: np.ndarray, sr: int) -> dict:
    """Số đo độ to của 1 bài. Giá trị None = bài quá ngắn / im lặng."""
    a = _to_2d(audio)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    cs = _power_cumsum(a, sr)
    integrated = _gated_integrated(_blocks(cs, sr, 0.4, 0.1))
    st = _blocks(cs, sr, SHORT_TERM_SEC, SHORT_TERM_HOP_SEC)
    st_lufs = _lufs(st) if len(st) else np.zeros(0)
    gated = st_lufs[st_lufs > SILENCE_LUFS]
    loud = lra = None
    if len(gated):
        rel = _lufs(st[st_lufs > SILENCE_LUFS].mean()) - 20.0
        rel_gated = gated[gated > rel]
        if len(rel_gated):
            loud = float(np.percentile(rel_gated, LOUD_PERCENTILE))
            lra = float(np.percentile(rel_gated, 95) - np.percentile(rel_gated, 10))
    if loud is None and integrated is not None:
        loud, lra = integrated, 0.0
    return {
        "integrated": None if integrated is None else round(integrated, 2),
        "loud": None if loud is None else round(loud, 2),
        "lra": None if lra is None else round(lra, 2),
        "peak_db": round(float(20.0 * np.log10(peak)), 2) if peak > 0 else None,
        "duration": round(a.shape[0] / sr, 2) if sr else 0.0,
    }


def analyze_cached(path: str, load) -> dict:
    """analyze() có nhớ theo (đường dẫn, mtime, size) — mở lại bảng tức thì.
    load: hàm không đối số trả (audio, sr)."""
    try:
        st = os.stat(path)
        key = (os.path.normcase(str(Path(path).resolve())), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None:
        with _cache_lock:
            if key in _cache:
                return dict(_cache[key])
    audio, sr = load()
    stats = analyze(audio, sr)
    if key is not None:
        with _cache_lock:
            _cache[key] = dict(stats)
    return stats


def _median(values: list[float]) -> float:
    return float(np.median(np.asarray(values, dtype=np.float64)))


def plan(stats: list[dict], target_lufs: float,
         manual_db: list[float] | None = None) -> list[dict]:
    """Gain cho từng bài để «đoạn to» bằng nhau, trung vị độ to = target_lufs.

    Bài không đo được (im lặng) → gain 0. Trả cùng thứ tự với stats."""
    manual_db = list(manual_db or [0.0] * len(stats))
    ok = [i for i, s in enumerate(stats)
          if s.get("integrated") is not None and s.get("loud") is not None]
    out = []
    if ok:
        diffs = [stats[i]["integrated"] - stats[i]["loud"] for i in ok]
        level = target_lufs - _median(diffs)      # mức «đoạn to» chung
        ref_median = _median([stats[i]["loud"] for i in ok])
    for i, s in enumerate(stats):
        manual = max(-MANUAL_RANGE_DB, min(MANUAL_RANGE_DB, float(manual_db[i] or 0.0)))
        if i not in ok:
            out.append({"auto_gain_db": 0.0, "manual_gain_db": manual, "gain_db": 0.0,
                        "deviation_lu": None, "flag": False, "after_integrated": None,
                        "after_loud": None, "limit_db": 0.0, "heavy_limit": False})
            continue
        auto = level - s["loud"]
        gain = max(MAX_CUT_DB, min(MAX_BOOST_DB, auto + manual))
        peak_after = (s["peak_db"] if s.get("peak_db") is not None else -120.0) + gain
        limit_db = max(0.0, peak_after - CEILING_DB)
        dev = s["loud"] - ref_median
        out.append({
            "auto_gain_db": round(auto, 2), "manual_gain_db": round(manual, 2),
            "gain_db": round(gain, 2), "deviation_lu": round(dev, 2),
            "flag": bool(abs(dev) > FLAG_LU),
            "after_integrated": round(s["integrated"] + gain, 2),
            "after_loud": round(s["loud"] + gain, 2),
            "limit_db": round(limit_db, 2), "heavy_limit": bool(limit_db > HEAVY_LIMIT_DB),
        })
    return out


def limit(audio: np.ndarray, sr: int, ceiling_db: float = CEILING_DB) -> tuple[np.ndarray, float]:
    """Limiter nhìn trước, liên kết 2 kênh: đỉnh mẫu không vượt ceiling_db.

    Gain cần = min(1, trần/|x|); lọc min (cửa sổ 2h+1) rồi lấy trung bình trượt
    cùng cỡ → mọi mẫu được giảm ÍT NHẤT bằng mức cần (không vượt trần) mà gain
    vẫn mượt (không méo bậc thang). Trả (audio, mức giảm lớn nhất dB)."""
    ceiling = 10.0 ** (ceiling_db / 20.0)
    a = _to_2d(audio)
    env = np.max(np.abs(a), axis=1)
    if not env.size or float(env.max()) <= ceiling:
        return audio, 0.0
    need = np.minimum(1.0, ceiling / np.maximum(env, 1e-12)).astype(np.float32)
    size = 2 * max(int(LIMITER_WINDOW_SEC * sr), 1) + 1
    gain = uniform_filter1d(minimum_filter1d(need, size=size, mode="nearest"),
                            size=size, mode="nearest")
    gain = np.minimum(gain, need)          # chặn sai số làm tròn float
    out = (a * gain[:, None]).astype(a.dtype, copy=False)
    out = np.clip(out, -ceiling, ceiling)
    reduction = float(-20.0 * np.log10(max(float(gain.min()), 1e-6)))
    out = out[:, 0] if np.asarray(audio).ndim == 1 else out
    return np.ascontiguousarray(out), round(reduction, 2)


def apply(audio: np.ndarray, sr: int, gain_db: float,
          ceiling_db: float = CEILING_DB) -> tuple[np.ndarray, float]:
    """Nhân gain rồi qua limiter. Trả (audio, dB limiter đã giảm)."""
    out = np.asarray(audio, dtype=np.float32) * np.float32(10.0 ** (gain_db / 20.0))
    return limit(out, sr, ceiling_db)
