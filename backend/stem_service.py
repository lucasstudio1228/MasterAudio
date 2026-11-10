"""
stem_service.py
Wrapper cho Demucs htdemucs_ft.
Chạy trong ThreadPoolExecutor — không có asyncio context.

Triết lý chất lượng ("cân bằng thông minh"):
  - Tách stem ở CHẤT LƯỢNG TỐI ĐA (shifts cao + overlap 0.5) để bleed thấp nhất
    ngay từ đầu.
  - LƯU STEM GẦN NHƯ NGUYÊN BẢN (chỉ LUFS-normalize, KHÔNG nướng denoise cứng)
    → để nguyên volume thì chất âm giữ tối đa.
  - Khử noise/bleed được làm LÚC MIX, phụ thuộc mức volume: kéo stem càng thấp
    thì gate + spectral-denoise + downward-expander càng mạnh → bleed biến mất
    triệt để đúng lúc muốn bỏ stem đó (xem load_mixed_stems / _smart_clean).
"""
import json, sqlite3, numpy as np, soundfile as sf
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

STEMS_ROOT = Path(__file__).parent.parent / "stems"
DB_PATH    = Path(__file__).parent.parent / "data" / "app.db"
STEM_NAMES = ["other", "bass", "drums", "vocals"]

# ─── Cấu hình chất lượng tách (max quality) ─────────────────────
# shifts = test-time augmentation: chạy model nhiều lần với dịch pha rồi trung
# bình → ít artifact & bleed hơn, nhưng chậm tuyến tính theo số shifts.
# overlap cao → giảm seam artifact giữa các segment.
# GPU chịu được shifts cao; CPU để thấp hơn cho đỡ quá lâu.
SEP_SHIFTS_GPU = 5
SEP_SHIFTS_CPU = 2
SEP_OVERLAP    = 0.5

# ─── DB helpers (sqlite3 thuần — không dùng SQLModel) ───────────

def _db_conn():
    return sqlite3.connect(str(DB_PATH))

def _db_set_status(stem_id: int, status: str,
                   error: str | None = None,
                   gain: float | None = None):
    now  = datetime.now(timezone.utc).isoformat()
    conn = _db_conn()
    try:
        if status == "running":
            conn.execute(
                "UPDATE trackstem SET status=?, started_at=? WHERE id=?",
                (status, now, stem_id))
        elif status in ("completed", "failed"):
            conn.execute(
                """UPDATE trackstem
                   SET status=?, completed_at=?, error_message=?,
                       lufs_gain_applied=?
                   WHERE id=?""",
                (status, now, error, gain, stem_id))
        else:
            conn.execute("UPDATE trackstem SET status=? WHERE id=?",
                         (status, stem_id))
        conn.commit()
    finally:
        conn.close()

def _db_save_waveform(stem_id: int, waveform_json: str):
    conn = _db_conn()
    try:
        conn.execute("UPDATE trackstem SET waveform_data=? WHERE id=?",
                     (waveform_json, stem_id))
        conn.commit()
    finally:
        conn.close()

def _stem_record_still_exists(stem_id: int) -> bool:
    """Re-check DB trước khi write — tránh orphan files khi project bị xóa."""
    conn = _db_conn()
    try:
        row = conn.execute(
            "SELECT id FROM trackstem WHERE id=?", (stem_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()

# ─── Waveform thumbnail ──────────────────────────────────────────

def _rms_thumbnail(audio: np.ndarray, n_points: int = 200) -> list[float]:
    """
    Tính 200 điểm RMS để vẽ waveform mini.
    Input: (samples, channels) hoặc (samples,)
    """
    mono  = audio.mean(axis=1) if audio.ndim == 2 else audio
    chunk = max(1, len(mono) // n_points)
    pts   = []
    for i in range(n_points):
        seg = mono[i*chunk : (i+1)*chunk]
        pts.append(float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0)
    mx = max(pts) or 1.0
    return [v / mx for v in pts]

# ─── Khử noise/bleed phụ thuộc volume (chạy lúc MIX) ─────────────
#
# strength ∈ [0, 1]: 0 = không đụng gì (stem để nguyên) → chất âm tối đa.
#                    1 = stem bị kéo về ~0 → khử triệt để.
# Được tính từ mức volume trong load_mixed_stems: strength = clip(1 - vol, 0, 1).

# Dưới ngưỡng này coi như "để nguyên" → bỏ qua xử lý cho khỏi tốn CPU + giữ
# nguyên chất âm.
_CLEAN_EPS = 0.05

# prop_decrease cho spectral gate, nội suy theo strength (nhẹ → rất mạnh).
_SPECTRAL_MIN = 0.45
_SPECTRAL_MAX = 0.95


def _env_follow(x: np.ndarray, sr: int, tau: float = 0.030) -> np.ndarray:
    """Bao biên (envelope) mượt bằng one-pole IIR — O(n), rất nhanh."""
    try:
        from scipy.signal import lfilter
        a = float(np.exp(-1.0 / max(tau * sr, 1.0)))
        return lfilter([1.0 - a], [1.0, -a], x).astype(np.float32)
    except Exception:
        # Fallback: không smooth (vẫn hoạt động, chỉ kém mượt hơn).
        return x.astype(np.float32)


def _downward_expander(audio: np.ndarray, sr: int, strength: float) -> np.ndarray:
    """
    Downward expander: đè các đoạn năng lượng THẤP (chủ yếu là bleed/noise ở
    khoảng lặng) xuống sâu, giữ nguyên các đoạn năng lượng cao (tín hiệu thật).
    Ngưỡng và độ sâu tăng theo strength.
    """
    if strength <= 0:
        return audio
    x = np.ascontiguousarray(audio, dtype=np.float32)
    mono = x.mean(axis=1) if x.ndim == 2 else x

    env  = _env_follow(np.abs(mono), sr, tau=0.030)
    ref  = float(np.percentile(env, 99)) or 1.0

    # Ngưỡng: -45 dB (nhẹ) → -25 dB (mạnh) so với đỉnh.
    thr_db = -45.0 + strength * 20.0
    thr    = ref * (10.0 ** (thr_db / 20.0))
    # Sàn gain khi dưới ngưỡng: -20 dB (nhẹ) → -80 dB (gần như câm).
    floor  = 10.0 ** ((-20.0 - strength * 60.0) / 20.0)

    ratio = np.clip(env / (thr + 1e-9), 0.0, 1.0)     # gần ngưỡng → gần 1
    gain  = np.where(env < thr, floor + (1.0 - floor) * ratio, 1.0).astype(np.float32)
    gain  = _env_follow(gain, sr, tau=0.050)          # làm mượt để tránh zipper

    return (x * gain[:, None]).astype(np.float32) if x.ndim == 2 \
        else (x * gain).astype(np.float32)


def _spectral_clean(audio: np.ndarray, sr: int, strength: float,
                    stem_name: str) -> np.ndarray:
    """Spectral noise gate (noisereduce) — khử bleed/hiss băng rộng."""
    if strength <= 0:
        return audio
    try:
        import noisereduce as nr
    except Exception:
        return audio

    prop = _SPECTRAL_MIN + (_SPECTRAL_MAX - _SPECTRAL_MIN) * float(strength)
    try:
        x = np.ascontiguousarray(audio, dtype=np.float32)
        if x.ndim == 1:
            return nr.reduce_noise(y=x, sr=sr, stationary=True,
                                   prop_decrease=prop, n_fft=2048,
                                   n_jobs=1).astype(np.float32)
        out = np.zeros_like(x, dtype=np.float32)
        for ch in range(x.shape[1]):
            out[:, ch] = nr.reduce_noise(
                y=x[:, ch], sr=sr, stationary=True,
                prop_decrease=prop, n_fft=2048, n_jobs=1).astype(np.float32)
        return out
    except Exception as e:
        print(f"  ⚠️  spectral_clean {stem_name} lỗi ({type(e).__name__}: {e}) "
              f"— dùng stem gốc")
        return audio


def _smart_clean(audio: np.ndarray, sr: int, strength: float,
                 stem_name: str) -> np.ndarray:
    """
    Kết hợp "mọi cách" để khử triệt để khi stem bị giảm:
      1. Spectral gate  → bỏ hiss/bleed băng rộng.
      2. Downward expander → bỏ bleed còn sót ở khoảng lặng.
    strength càng cao xử lý càng mạnh; strength≈0 thì trả nguyên audio.
    """
    if strength < _CLEAN_EPS:
        return audio
    cleaned = _spectral_clean(audio, sr, strength, stem_name)
    cleaned = _downward_expander(cleaned, sr, strength)
    return cleaned

# ─── CUDA check ─────────────────────────────────────────────────

def _get_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"  🎮 GPU: {name} ({vram:.1f} GB VRAM) — CUDA enabled")
            return "cuda"
    except ImportError:
        pass
    print("  ⚠️  CUDA không khả dụng — dùng CPU")
    return "cpu"

# ─── Main separation function ────────────────────────────────────

def separate_track(
    stem_id:    int,
    track_id:   int,
    project_id: int,
    audio_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Chạy Demucs htdemucs_ft cho 1 track ở CHẤT LƯỢNG TỐI ĐA.
    Gọi từ StemJobManager trong ThreadPoolExecutor.

    Pipeline:
      1. Load track gốc → đo LUFS
      2. Demucs separation (shifts cao, overlap 0.5) → 4 stem tensors
      3. Convert tensors → numpy arrays
      4. Apply LUFS normalization (cùng gain cho tất cả stems)
      5. Lưu mỗi stem thành WAV float32 (KHÔNG nén, gần như nguyên bản)
      6. Tính waveform thumbnails
      7. Lưu kết quả vào DB

    Lưu ý: KHÔNG denoise ở đây — giữ stem nguyên chất; việc khử noise/bleed
    được làm lúc mix theo mức volume (load_mixed_stems).
    """
    import demucs.api
    from backend.loudness_service import (
        normalize_stems_to_target, TARGET_LUFS
    )

    _db_set_status(stem_id, "running")

    try:
        # out_dir tạo lazy SAU guard check (Bước 6) — tránh orphan directory
        # nếu project bị xóa trong khi job đang chạy.
        out_dir = STEMS_ROOT / str(project_id) / str(track_id)

        # ── Bước 1: Load track gốc để đo LUFS ──
        if progress_cb: progress_cb("Đang load audio...")
        original, orig_sr = sf.read(audio_path, dtype="float32")
        if original.ndim == 1:
            original = np.column_stack([original, original])

        # ── Bước 2: Khởi tạo Demucs (max quality) ──
        if progress_cb: progress_cb("Đang load model htdemucs_ft...")
        device = _get_device()
        shifts = SEP_SHIFTS_GPU if device == "cuda" else SEP_SHIFTS_CPU
        separator = demucs.api.Separator(
            model="htdemucs_ft",
            device=device,
            shifts=shifts,        # test-time augmentation → ít artifact/bleed
            overlap=SEP_OVERLAP,  # 0.5 → giảm seam artifact
        )

        # ── Bước 3: Separation ──
        if progress_cb: progress_cb(
            f"Đang tách stems chất lượng cao "
            f"{'(GPU)' if device=='cuda' else '(CPU)'} shifts={shifts}...")
        origin_tensor, separated = separator.separate_audio_file(audio_path)

        # ── Bước 4: Convert tensor → numpy float32 ──
        # Demucs output: tensor shape (channels, samples)
        stem_arrays: dict[str, np.ndarray] = {}
        for name, tensor in separated.items():
            if name not in STEM_NAMES:
                continue
            arr = tensor.cpu().numpy().T.astype(np.float32)   # → (samples, ch)
            if arr.ndim == 1:
                arr = np.column_stack([arr, arr])
            stem_arrays[name] = arr

        # ── Bước 5: LUFS Normalization ──
        if progress_cb: progress_cb("Đang normalize LUFS...")
        target_sr = separator.samplerate
        if orig_sr != target_sr:
            import librosa
            original = librosa.resample(
                original.T, orig_sr=orig_sr, target_sr=target_sr,
                res_type="kaiser_best"
            ).T

        # Apply CÙNG gain cho tất cả stems (đo từ track gốc)
        normalized_stems, gain_applied = normalize_stems_to_target(
            stems=stem_arrays,
            original_audio=original,
            sr=target_sr,
            target_lufs=TARGET_LUFS,
        )

        # ── Existence guard: tránh orphan files nếu project/track bị xóa
        #    trong khi job đang chạy ──
        if not _stem_record_still_exists(stem_id):
            import shutil
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            return

        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Bước 6: Lưu WAV float32 (KHÔNG nén, giữ nguyên chất) ──
        if progress_cb: progress_cb("Đang lưu stem files...")
        for name in STEM_NAMES:
            arr = normalized_stems.get(name)
            if arr is None:
                continue
            wav_path = out_dir / f"{name}.wav"
            sf.write(str(wav_path), arr, target_sr, subtype="FLOAT")

        # ── Bước 7: Waveform thumbnails ──
        if progress_cb: progress_cb("Đang tính waveform...")
        waveform = {
            name: _rms_thumbnail(normalized_stems[name])
            for name in STEM_NAMES
            if name in normalized_stems
        }
        _db_save_waveform(stem_id, json.dumps(waveform))
        _db_set_status(stem_id, "completed", gain=float(gain_applied))

        if progress_cb: progress_cb("Hoàn thành!")

    except Exception as e:
        import traceback
        err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
        _db_set_status(stem_id, "failed", error=err_msg)
        raise

# ─── Mix stems back → 1 audio array ─────────────────────────────

def load_mixed_stems(
    project_id: int,
    track_id:   int,
    vol_other:  float,
    vol_bass:   float,
    vol_drums:  float,
    vol_vocals: float,
    target_sr:  int,
    den_other:  float = 0.0,
    den_bass:   float = 0.0,
    den_drums:  float = 0.0,
    den_vocals: float = 0.0,
) -> Optional[np.ndarray]:
    """
    Load 4 stem WAV, khử noise/bleed THÔNG MINH theo volume, apply volume,
    mix thành 1 stereo array.  Return None nếu bất kỳ stem nào thiếu.

    Volume range 0.0–2.0:
      0.0 = mute hoàn toàn
      1.0 = giữ nguyên (sau LUFS normalize) — KHÔNG khử gì, chất âm tối đa
      2.0 = boost gấp đôi

    Cơ chế "cân bằng thông minh":
      strength = clip(1 - volume, 0, 1)
      - volume ≥ ~0.95 → strength ≈ 0 → stem giữ nguyên bản.
      - volume càng thấp → strength càng cao → spectral gate + downward
        expander càng mạnh → bleed/noise của stem đó bị khử triệt để trước
        khi bị kéo nhỏ, nên không còn "bóng ma" bleed trong bản mix.

    Sau khi mix: soft limiter để tránh méo tiếng.
    """
    import librosa

    stem_dir = STEMS_ROOT / str(project_id) / str(track_id)
    volumes  = {
        "other":  vol_other,
        "bass":   vol_bass,
        "drums":  vol_drums,
        "vocals": vol_vocals,
    }
    # Độ mạnh khử noise thủ công mỗi stem (0..1) — làm sàn cho strength.
    manual_denoise = {
        "other":  den_other,
        "bass":   den_bass,
        "drums":  den_drums,
        "vocals": den_vocals,
    }

    for name in STEM_NAMES:
        if not (stem_dir / f"{name}.wav").exists():
            return None

    mixed: Optional[np.ndarray] = None

    for name in STEM_NAMES:
        arr, sr = sf.read(str(stem_dir / f"{name}.wav"), dtype="float32")

        # Đảm bảo stereo TRƯỚC khi xử lý.
        if arr.ndim == 1:
            arr = np.column_stack([arr, arr])
        elif arr.shape[1] == 1:
            arr = np.column_stack([arr[:, 0], arr[:, 0]])

        vol = volumes[name]
        if vol is None:
            vol = 1.0

        # Khử noise/bleed — làm ở sr GỐC của stem (chính xác hơn), trước resample.
        # strength = max(tự động theo volume, thủ công do người dùng đặt).
        auto_strength   = float(np.clip(1.0 - vol, 0.0, 1.0))
        manual_strength = float(np.clip(manual_denoise.get(name, 0.0) or 0.0,
                                        0.0, 1.0))
        strength = max(auto_strength, manual_strength)
        if strength >= _CLEAN_EPS:
            arr = _smart_clean(arr, sr, strength, name)

        # Resample về target_sr nếu khác.
        if sr != target_sr:
            arr = librosa.resample(
                arr.T, orig_sr=sr, target_sr=target_sr,
                res_type="kaiser_best"
            ).T
            if arr.ndim == 1:
                arr = np.column_stack([arr, arr])

        # Apply volume.
        arr = arr * vol

        # Accumulate.
        if mixed is None:
            mixed = arr
        else:
            n     = min(len(mixed), len(arr))
            mixed = mixed[:n] + arr[:n]

    if mixed is None:
        return None

    # Soft limiter sau khi mix (KHÔNG hard-clip).
    peak = np.max(np.abs(mixed))
    if peak > 0.99:
        mixed = mixed * (0.99 / peak)

    return mixed
