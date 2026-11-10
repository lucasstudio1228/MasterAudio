"""
video/instrument_qc.py — Chấm điểm KHÁCH QUAN một bài Suno có đúng "chỉ một nhạc cụ
chính" hay không, bằng mô hình phân loại âm thanh PANNs Cnn14 (AudioSet, 527 nhãn —
có Flute / Zither / Piano / Guitar / Bowed string / Bell / Singing bowl / Singing…).

Cách đo: cắt bài thành cửa sổ 10 s (bước 5 s), lấy xác suất từng nhãn, gộp nhãn
thành HỌ nhạc cụ (flute, zither, piano…). Với mỗi cửa sổ biết họ nào đang nổi; từ
đó tính:
- lead_share  : tỉ lệ cửa sổ mà nhạc cụ chính nghe rõ (≥ LEAD_MIN);
- leaks       : họ nhạc cụ KHÁC xuất hiện rõ (≥ LEAK_MIN) ở bao nhiêu % cửa sổ;
- vocal_share : tỉ lệ cửa sổ có giọng người (hát/ngân/chant).
Trọng số model: ~/panns_data/Cnn14_mAP=0.431.pth (bản chính thức trên Zenodo).

Chỉ đọc file âm thanh — không mở trình duyệt, không tốn credit.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import music_spec

SAMPLE_RATE = 32000
WINDOW_SEC = 10.0
HOP_SEC = 5.0
LEAD_MIN = 0.10      # nhạc cụ chính "nghe rõ" trong cửa sổ
LEAK_MIN = 0.10      # họ nhạc cụ khác "nghe rõ" trong cửa sổ
VOCAL_MIN = 0.20

PANNS_DIR = Path.home() / "panns_data"
CHECKPOINT = PANNS_DIR / "Cnn14_mAP=0.431.pth"
LABELS_CSV = PANNS_DIR / "class_labels_indices.csv"

# Họ nhạc cụ → nhãn AudioSet.
FAMILIES: dict[str, tuple[str, ...]] = {
    "flute": ("Flute", "Wind instrument, woodwind instrument"),
    "zither": ("Zither",),
    "plucked": ("Plucked string instrument", "Mandolin", "Sitar", "Banjo"),
    "guitar": ("Guitar", "Acoustic guitar", "Electric guitar", "Steel guitar, slide guitar",
               "Tapping (guitar technique)", "Strum", "Ukulele"),
    "piano": ("Piano", "Electric piano", "Keyboard (musical)", "Harpsichord", "Organ"),
    "harp": ("Harp",),
    "bowed": ("Bowed string instrument", "String section", "Violin, fiddle", "Cello",
              "Double bass", "Orchestra"),
    "bells": ("Bell", "Church bell", "Chime", "Wind chime", "Tubular bells", "Gong",
              "Singing bowl"),
    "mallet": ("Glockenspiel", "Vibraphone", "Marimba, xylophone", "Steelpan"),
    "drums": ("Drum", "Drum kit", "Drum machine", "Snare drum", "Bass drum", "Cymbal",
              "Hi-hat", "Percussion", "Tabla", "Drum roll"),
    "synth": ("Synthesizer",),
    "reeds_brass": ("Saxophone", "Clarinet", "Brass instrument", "Trumpet", "French horn",
                    "Harmonica", "Accordion"),
    "vocals": ("Singing", "Choir", "Humming", "Chant", "Mantra", "Male singing",
               "Female singing", "Child singing", "Synthetic singing", "Speech"),
}

# Nhạc cụ (music_spec key) → họ được coi là CHÍNH (có thể nhiều họ vì AudioSet
# gọi guzheng là "Zither" hoặc "Plucked string", handpan là "Steelpan"…).
LEAD_FAMILIES: dict[str, tuple[str, ...]] = {
    "bamboo_flute": ("flute",), "shakuhachi": ("flute",),
    "guzheng": ("zither", "plucked"), "pipa": ("plucked", "zither"),
    "erhu": ("bowed",), "violin": ("bowed",), "cello": ("bowed",),
    "guitar": ("guitar", "plucked"), "harp": ("harp", "plucked"),
    "piano": ("piano",), "kalimba": ("mallet", "plucked", "bells"),
    "handpan": ("mallet", "bells"), "singing_bowls": ("bells",),
    "music_box": ("mallet", "bells"),
}
# Họ được phép theo thể loại (không tính là lẫn): lofi có trống nhẹ.
GENRE_ALLOWED: dict[str, tuple[str, ...]] = {"lofi": ("drums",)}


def lead_families(instrument: str) -> tuple[str, ...]:
    spec = music_spec.find_instrument(instrument) if instrument else None
    return LEAD_FAMILIES.get(spec["key"], ()) if spec else ()


@lru_cache(maxsize=1)
def _label_index() -> dict[str, int]:
    with open(LABELS_CSV, encoding="utf-8") as f:
        rows = list(csv.reader(f))[1:]
    return {r[2]: int(r[0]) for r in rows}


@lru_cache(maxsize=1)
def _model():
    if not CHECKPOINT.exists() or CHECKPOINT.stat().st_size < 3e8:
        raise FileNotFoundError(
            f"Thiếu trọng số PANNs ({CHECKPOINT}). Tải Cnn14_mAP=0.431.pth từ "
            "https://zenodo.org/record/3987831 vào thư mục đó.")
    import torch
    from panns_inference import AudioTagging
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return AudioTagging(checkpoint_path=str(CHECKPOINT), device=device)


def available() -> bool:
    """Đủ điều kiện chấm (trọng số + nhãn + thư viện) — kiểm nhẹ, chưa nạp model."""
    import importlib.util
    try:
        ok_files = (CHECKPOINT.exists() and CHECKPOINT.stat().st_size >= 3e8
                    and LABELS_CSV.exists())
    except OSError:
        return False
    return ok_files and all(importlib.util.find_spec(m) is not None
                            for m in ("torch", "panns_inference", "librosa"))


def release() -> None:
    """Giải phóng model khỏi RAM/VRAM sau khi chấm xong một lô."""
    loaded = _model.cache_info().currsize > 0
    _model.cache_clear()
    if loaded and "torch" in sys.modules:
        import gc
        gc.collect()
        torch = sys.modules["torch"]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def describe(r: dict) -> str:
    """Tóm tắt 1 dòng cho log: «chính 82% · lẫn piano 20%, bells 10% · giọng 0% → SẠCH»."""
    leaks = ", ".join(f"{f} {v:.0%}" for f, v in list(r.get("leaks", {}).items())[:3])
    return (f"chính {r.get('lead_share', 0):.0%} · lẫn {leaks or 'không'} · "
            f"giọng {r.get('vocal_share', 0):.0%} → {'SẠCH' if r.get('clean') else 'LẪN'}")


def badness(r: dict) -> float:
    """Mức lẫn tổng (càng thấp càng tốt): % cửa sổ có họ nhạc cụ lạ + có giọng."""
    return float(r.get("leak_score", 0.0)) + float(r.get("vocal_share", 0.0))


def _load(path: Path):
    import librosa
    y, _ = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    return y


def window_probs(path: Path):
    """Ma trận (số cửa sổ × 527) xác suất nhãn."""
    import numpy as np
    y = _load(path)
    win, hop = int(WINDOW_SEC * SAMPLE_RATE), int(HOP_SEC * SAMPLE_RATE)
    if len(y) < win:
        y = np.pad(y, (0, win - len(y)))
    starts = range(0, len(y) - win + 1, hop)
    chunks = np.stack([y[s:s + win] for s in starts]).astype("float32")
    model = _model()
    out = []
    for i in range(0, len(chunks), 16):
        clip, _ = model.inference(chunks[i:i + 16])
        out.append(clip)
    return np.concatenate(out, axis=0)


def family_matrix(probs) -> dict:
    import numpy as np
    idx = _label_index()
    return {fam: np.max(probs[:, [idx[n] for n in names if n in idx]], axis=1)
            for fam, names in FAMILIES.items()}


def analyze(path: str | Path, instrument: str = "", music_style: str = "") -> dict:
    """Điểm 1 bài. leak_score = tổng % cửa sổ có họ nhạc cụ khác (càng thấp càng
    tốt); clean = nhạc cụ chính rõ ≥60% thời lượng, không họ nào lẫn >15%, không
    giọng người."""
    import numpy as np
    path = Path(path)
    probs = window_probs(path)
    fam = family_matrix(probs)
    leads = lead_families(instrument)
    genre = music_spec.find_genre(music_style)["key"] if music_style else ""
    allowed = set(leads) | set(GENRE_ALLOWED.get(genre, ())) | {"vocals"}
    n = probs.shape[0]
    lead_curve = (np.max(np.stack([fam[f] for f in leads]), axis=0) if leads
                  else np.zeros(n))
    leaks = {f: round(float(np.mean(v >= LEAK_MIN)), 3) for f, v in fam.items()
             if f not in allowed}
    leaks = {f: s for f, s in sorted(leaks.items(), key=lambda kv: -kv[1]) if s > 0}
    lead_share = float(np.mean(lead_curve >= LEAD_MIN)) if leads else math.nan
    vocal_share = float(np.mean(fam["vocals"] >= VOCAL_MIN))
    # Mức trội: trung vị (lead − họ khác mạnh nhất) theo cửa sổ.
    others = [fam[f] for f in fam if f not in allowed]
    dominance = (float(np.median(lead_curve - np.max(np.stack(others), axis=0)))
                 if leads and others else math.nan)
    worst = max(leaks.values(), default=0.0)
    return {
        "file": path.name, "seconds": round(n * HOP_SEC + WINDOW_SEC - HOP_SEC),
        "windows": n, "lead": list(leads), "lead_share": round(lead_share, 3),
        "lead_mean": round(float(np.mean(lead_curve)), 3),
        "dominance": round(dominance, 3), "leaks": leaks,
        "leak_score": round(sum(leaks.values()), 3), "worst_leak": worst,
        "vocal_share": round(vocal_share, 3),
        "clean": bool(leads) and lead_share >= 0.6 and worst <= 0.15 and vocal_share <= 0.05,
    }


def summarize(results: list[dict]) -> dict:
    import statistics as st
    ok = [r for r in results if "error" not in r]
    if not ok:
        return {"tracks": 0}
    fams: dict[str, list[float]] = {}
    for r in ok:
        for f, s in r["leaks"].items():
            fams.setdefault(f, []).append(s)
    return {
        "tracks": len(ok),
        "clean_rate": round(sum(r["clean"] for r in ok) / len(ok), 3),
        "lead_share": round(st.mean(r["lead_share"] for r in ok), 3),
        "dominance": round(st.mean(r["dominance"] for r in ok), 3),
        "leak_score": round(st.mean(r["leak_score"] for r in ok), 3),
        "vocal_share": round(st.mean(r["vocal_share"] for r in ok), 3),
        "leak_by_family": {f: round(sum(v) / len(ok), 3)
                           for f, v in sorted(fams.items(), key=lambda kv: -sum(kv[1]))},
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Chấm điểm nhạc cụ chính cho file âm thanh.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--instrument", default="")
    ap.add_argument("--style", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    results = []
    for f in a.files:
        try:
            r = analyze(f, a.instrument, a.style)
        except Exception as e:      # noqa: BLE001
            r = {"file": Path(f).name, "error": f"{type(e).__name__}: {e}"}
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))
    summary = summarize(results)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "tracks": results},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
