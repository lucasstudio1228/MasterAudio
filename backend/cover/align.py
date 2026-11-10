"""
cover/align.py — AI căn thời gian lời bài hát theo từng câu.

Quy trình cho 1 bài:
  1. Tách giọng hát khỏi nhạc nền bằng Demucs (htdemucs, GPU) → vocals 16 kHz
     mono, lưu cache (tách 1 lần, căn lại bao nhiêu lần cũng được).
  2a. CÓ lời: stable-ts "align" — ép Whisper khớp ĐÚNG lời người dùng đưa vào
      giọng hát → mốc bắt đầu/kết thúc từng câu (giữ nguyên chữ của người dùng).
  2b. KHÔNG có lời: Whisper nghe và chép lời (transcribe) → câu + mốc thời gian
      (người dùng nên rà lại chính tả).

Model Whisper + Demucs được giữ trong RAM/GPU giữa các bài của cùng 1 lượt
chạy (nạp model mất ~10–30 s), giải phóng bằng release_models().
"""
from __future__ import annotations

import threading
import difflib
import re
import unicodedata
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .lyrics import normalize_lines

WHISPER_SR = 16000
CACHE_ROOT = Path(__file__).parent.parent.parent / "data" / "cover_cache"

_lock = threading.Lock()
_whisper = {"name": None, "model": None}
_demucs = {"sep": None}

Log = Optional[Callable[[str], None]]


def _say(log: Log, msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def release_models() -> None:
    """Giải phóng Whisper + Demucs khỏi GPU (gọi khi xong 1 lượt căn lời)."""
    with _lock:
        _whisper["name"] = None
        _whisper["model"] = None
        _demucs["sep"] = None
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def vocals_cache_path(track_id: int, audio_path: str) -> Path:
    p = Path(audio_path)
    try:
        stamp = int(p.stat().st_mtime)
    except OSError:
        stamp = 0
    return CACHE_ROOT / f"track{track_id}_{stamp}_vocals16k.wav"


def _load_mono16k(path: str) -> np.ndarray:
    import soundfile as sf
    try:
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception:
        # MP3/M4A… → giải mã bằng ffmpeg
        import subprocess
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                            "-ar", str(WHISPER_SR), "-f", "f32le", "-"],
                           capture_output=True, check=True)
        return np.frombuffer(r.stdout, dtype=np.float32).copy()
    mono = audio.mean(axis=1)
    if sr != WHISPER_SR:
        import librosa
        mono = librosa.resample(mono, orig_sr=sr, target_sr=WHISPER_SR,
                                res_type="soxr_hq")
    return np.ascontiguousarray(mono, dtype=np.float32)


def separate_vocals(track_id: int, audio_path: str, log: Log = None) -> str:
    """Tách giọng hát → WAV 16 kHz mono trong cache. Có rồi thì dùng lại."""
    import soundfile as sf
    out = vocals_cache_path(track_id, audio_path)
    if out.exists() and out.stat().st_size > 1000:
        _say(log, "Dùng lại giọng hát đã tách trước đó.")
        return str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _demucs["sep"] is None:
            _say(log, "Nạp AI tách giọng (Demucs)…")
            import demucs.api
            _demucs["sep"] = demucs.api.Separator(
                model="htdemucs", device=_device(), shifts=1, overlap=0.25)
        sep = _demucs["sep"]
    _say(log, "Tách giọng hát khỏi nhạc nền…")
    _, stems = sep.separate_audio_file(audio_path)
    voc = stems["vocals"].cpu().numpy()          # (channels, samples)
    mono = voc.mean(axis=0).astype(np.float32)
    if sep.samplerate != WHISPER_SR:
        import librosa
        mono = librosa.resample(mono, orig_sr=sep.samplerate, target_sr=WHISPER_SR,
                                res_type="soxr_hq")
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0:
        mono = mono * (0.95 / peak)
    sf.write(str(out), mono, WHISPER_SR, subtype="FLOAT")
    return str(out)


def _get_whisper(name: str, log: Log):
    with _lock:
        if _whisper["model"] is None or _whisper["name"] != name:
            _say(log, f"Nạp AI nghe lời (Whisper {name}) — lần đầu có thể tải model "
                      f"vài GB, vui lòng đợi…")
            import stable_whisper
            _whisper["model"] = None
            _whisper["model"] = stable_whisper.load_model(name, device=_device())
            _whisper["name"] = name
        return _whisper["model"]


def _segments(result) -> list[dict]:
    segs = []
    for s in getattr(result, "segments", None) or []:
        text = str(getattr(s, "text", "") or "").strip()
        if text:
            segs.append({"text": text, "start": float(s.start), "end": float(s.end)})
    return segs


def _fold_text(text: str) -> str:
    value = unicodedata.normalize("NFKD", (text or "").casefold()).replace("đ", "d")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _line_similarity(left: str, right: str) -> float:
    a, b = _fold_text(left), _fold_text(right)
    if not a or not b:
        return 0.0
    sequence = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    aset, bset = set(a.split()), set(b.split())
    overlap = len(aset & bset) / max(1, min(len(aset), len(bset)))
    containment = 1.0 if a in b or b in a else 0.0
    return max(sequence, overlap * 0.92, containment)


_VIETNAMESE_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]")


def _lyrics_language(configured: str, lines: list[str]) -> str:
    """Ưu tiên ngôn ngữ thể hiện ngay trong lyrics.

    ``music_line=US-UK`` mô tả dòng nhạc, không đồng nghĩa ca sĩ hát tiếng Anh.
    Nếu lời có ký tự tiếng Việt đặc trưng thì Whisper phải nghe/căn ở ``vi``;
    nếu không nhận diện chắc chắn được thì giữ lựa chọn đã cấu hình.
    """
    text = " ".join(lines)
    return "vi" if _VIETNAMESE_RE.search(text) else configured


def _vocal_regions(audio: np.ndarray, sample_rate: int = WHISPER_SR) -> list[tuple[float, float]]:
    """Tìm các vùng có năng lượng giọng rõ trong stem vocal đã tách.

    Đây không phải transcript: nó chỉ giúp phát hiện một lượt hát sau mà ASR
    bỏ sót hoàn toàn. Các khoảng ngắn dưới 3 giây bị loại để tránh tiếng nhạc
    rò từ Demucs kích hoạt lời giả.
    """
    if audio is None or len(audio) < sample_rate:
        return []
    frame = max(1, int(sample_rate * 0.25))
    count = int(np.ceil(len(audio) / frame))
    padded = np.pad(np.asarray(audio, dtype=np.float32),
                    (0, count * frame - len(audio)))
    rms = np.sqrt(np.mean(padded.reshape(count, frame) ** 2, axis=1) + 1e-12)
    threshold = max(0.004, float(rms.max(initial=0.0)) * 0.04)
    active = rms >= threshold

    # Lấp các nhịp nghỉ ngắn giữa hai câu hát (tối đa 1.5 giây).
    max_gap = max(1, round(1.5 / 0.25))
    i = 0
    while i < count:
        if active[i]:
            i += 1
            continue
        j = i
        while j < count and not active[j]:
            j += 1
        if i > 0 and j < count and j - i <= max_gap:
            active[i:j] = True
        i = j

    regions: list[tuple[float, float]] = []
    i = 0
    while i < count:
        if not active[i]:
            i += 1
            continue
        j = i + 1
        while j < count and active[j]:
            j += 1
        start, end = i * 0.25, min(j * 0.25, len(audio) / sample_rate)
        if end - start >= 3.0:
            regions.append((start, end))
        i = j
    return regions


def expand_repeated_lyrics(lines: list[str], heard_segments: list[dict],
                           min_similarity: float = 0.43,
                           vocal_regions: list[tuple[float, float]] | None = None) -> list[str]:
    """Dùng transcript toàn bài làm mỏ neo để mở rộng lyrics bị viết một lượt.

    Ví dụ lyrics chỉ có Verse→Chorus nhưng audio hát Chorus lần hai, chuỗi mỏ
    neo sẽ đi 0..N-1 rồi quay về chỉ số Chorus; kết quả chứa thêm đúng phần đó.
    Chữ hiển thị luôn lấy từ lyrics đã kiểm tra, không lấy chính tả của Whisper.
    """
    clean = [line.strip() for line in lines if line and line.strip()]
    if len(clean) < 2 or not heard_segments:
        return clean
    n = len(clean)
    anchors: list[tuple[int, float, float]] = []
    unmatched: list[tuple[float, float, str]] = []
    previous: int | None = None
    for segment in heard_segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        scores = [_line_similarity(text, line) for line in clean]
        best_score = max(scores, default=0.0)
        if best_score < min_similarity:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or start)
            unmatched.append((start, end, text))
            continue
        choices = [i for i, score in enumerate(scores) if score >= best_score - 0.06]
        if previous is None:
            idx = min(choices)
        else:
            # Khi hai câu giống nhau ở nhiều verse/chorus, ưu tiên lần xuất hiện
            # kế tiếp theo thứ tự bài; vẫn cho phép quay lại khi chorus lặp.
            def distance(i: int) -> int:
                if i > previous:
                    return i - previous
                return (n - previous) + i
            idx = min(choices, key=lambda i: (distance(i), -scores[i]))
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        # Whisper đôi khi chia một câu thành hai segment kề nhau.
        if anchors and anchors[-1][0] == idx and start - anchors[-1][2] < 2.5:
            anchors[-1] = (idx, anchors[-1][1], max(end, anchors[-1][2]))
        else:
            anchors.append((idx, start, end))
        previous = idx

    if not anchors:
        return clean
    # Lyrics đã xác minh phải luôn xuất hiện ít nhất một lượt đầy đủ. Transcript
    # chỉ được phép THÊM lần hát lặp, tuyệt đối không làm rơi câu gốc.
    indices: list[int] = list(range(n))
    repeat_at: int | None = None
    for pos in range(1, len(anchors)):
        prev_idx, prev_start, _ = anchors[pos - 1]
        idx, start, _ = anchors[pos]
        large_backward = prev_idx - idx >= max(3, n // 3)
        reached_end = prev_idx >= n - 3
        if idx < prev_idx and (reached_end or large_backward) and start - prev_start >= 1.0:
            repeat_at = pos
            break
    if repeat_at is not None:
        repeated: list[int] = [anchors[repeat_at][0]]
        for idx, _start, _end in anchors[repeat_at + 1:]:
            prev = repeated[-1]
            if idx == prev:
                continue
            if idx > prev:
                repeated.extend(range(prev + 1, idx + 1))
            else:
                # Có thể bài hát lặp chorus thêm một lần nữa.
                repeated.append(idx)
        # Một lần lặp thường hát tới hết chorus/outro; ASR có thể bỏ sót câu cuối.
        if repeated:
            repeated.extend(range(repeated[-1] + 1, n))
        indices.extend(repeated)

    expanded = [clean[i] for i in indices]
    # Transcript dài gần gấp nhiều lần nhưng mỏ neo quá thưa: fallback an toàn
    # cho trường hợp cả bài được hát lại nguyên chu kỳ.
    heard_words = sum(len(_fold_text(str(s.get("text") or "")).split())
                      for s in heard_segments)
    lyric_words = sum(len(_fold_text(line).split()) for line in clean)
    ratio = heard_words / max(1, lyric_words)
    repeats = min(4, max(1, round(ratio)))
    if len(expanded) <= len(clean) and ratio >= 1.55 and repeats > 1:
        expanded = clean * repeats

    # Một số bản mix khiến Whisper nghe đúng lượt đầu nhưng nhận sai hoàn toàn
    # điệp khúc cuối (thường thành một câu quảng cáo/hallucination). Nếu đã đi
    # tới cuối lyrics và sau đó còn hẳn một vùng vocal dài, dùng thời lượng để
    # suy ra số câu cuối được hát lại. stable-ts ở bước kế tiếp vẫn phải ép căn
    # từng câu vào audio thật, nên đây chỉ là mở rộng ứng viên chứ không tự đặt
    # timestamp giả.
    if len(expanded) <= len(clean) and anchors and anchors[-1][0] >= n - 3:
        first_start = anchors[0][1]
        last_end = anchors[-1][2]
        late = [(start, end, text) for start, end, text in unmatched
                if start >= last_end + 5.0]
        energy_late = [(start, end) for start, end in (vocal_regions or [])
                       if start >= last_end + 5.0]
        if late or energy_late:
            late_start = min([start for start, _end, _text in late]
                             + [start for start, _end in energy_late])
            late_end = max([end for _start, end, _text in late]
                           + [end for _start, end in energy_late])
            late_span = late_end - late_start
            base_span = max(last_end - first_start, 1.0)
            late_words = sum(len(_fold_text(text).split()) for _, _, text in late)
            if late_span >= 12.0 and (late_words >= 6 or energy_late):
                tail_count = round(n * min(late_span / base_span, 1.0))
                tail_count = max(2, min(n, tail_count))
                expanded = clean + clean[n - tail_count:]
    return expanded or clean


def align_lines(vocals_path: str, lines: list[str], language: str,
                model_name: str = "large-v3", log: Log = None) -> list[dict]:
    """Khớp lời có sẵn vào giọng hát → [{text,start,end}] (giây, từ đầu bài)."""
    if not lines:
        raise ValueError("Chưa có lời bài hát để căn.")
    audio = _load_mono16k(vocals_path)
    model = _get_whisper(model_name, log)
    detected_language = _lyrics_language(language, lines)
    vocal_regions = _vocal_regions(audio)
    if detected_language != language:
        _say(log, "Lyrics là tiếng Việt — dùng nhận diện tiếng Việt dù dòng nhạc "
                  "của project là US-UK.")
    expanded = list(lines)
    try:
        _say(log, "Nghe toàn bộ phần vocal để phát hiện verse/điệp khúc hát lặp…")
        heard = model.transcribe(audio, language=detected_language, vad=True,
                                 regroup=True, verbose=None)
        expanded = expand_repeated_lyrics(lines, _segments(heard),
                                          vocal_regions=vocal_regions)
        if len(expanded) > len(lines):
            _say(log, f"Phát hiện đoạn hát lặp: mở rộng {len(lines)} thành "
                      f"{len(expanded)} câu hiển thị.")
    except Exception as exc:  # Whisper detect lỗi vẫn căn được lời gốc như trước.
        _say(log, f"Không dò được đoạn lặp ({type(exc).__name__}) — căn theo lyrics gốc.")
        expanded = list(lines)
    _say(log, f"Căn {len(expanded)} câu hát theo giọng ca sĩ…")
    result = model.align(audio, "\n".join(expanded), language=detected_language,
                         original_split=True)
    if result is None:
        raise RuntimeError("AI không khớp được lời với giọng hát (lời có thể "
                           "không đúng bài, hoặc bài không có giọng rõ).")
    segs = _segments(result)
    if len(segs) == len(expanded):
        # Giữ NGUYÊN chữ người dùng (đúng chính tả, đúng dấu) — chỉ lấy thời gian.
        out = []
        original_count = len(lines)
        for index, (text, segment) in enumerate(zip(expanded, segs)):
            start, end = segment["start"], segment["end"]
            # Ứng viên điệp khúc đoán dư mà forced-align không tìm thấy thường
            # có duration bằng 0; bỏ nó thay vì cho renderer hiện sai câu.
            if index >= original_count and end - start < 0.3:
                continue
            # stable-ts có thể kéo end của câu trước qua cả nhạc dạo tới câu
            # lặp kế tiếp. Cắt ở biên vùng vocal để lời biến mất khi hết hát.
            for region_start, region_end in vocal_regions:
                if region_start - 0.5 <= start <= region_end + 0.5:
                    end = min(end, region_end)
                    break
            out.append({"text": text, "start": start, "end": end})
    else:
        _say(log, f"Lưu ý: AI chia {len(segs)} đoạn khác {len(expanded)} câu cần căn — "
                  "hãy rà lại ở màn hình sửa lời.")
        out = segs
    return normalize_lines(out)


def transcribe(vocals_path: str, language: str, model_name: str = "large-v3",
               log: Log = None) -> list[dict]:
    """Không có lời → AI tự nghe và chép lời theo câu."""
    audio = _load_mono16k(vocals_path)
    model = _get_whisper(model_name, log)
    _say(log, "AI đang nghe và chép lời…")
    result = model.transcribe(audio, language=language, vad=True,
                              regroup=True, verbose=None)
    return normalize_lines(_segments(result))


def quality_report(lines: list[dict]) -> dict:
    """Đánh giá nhanh kết quả căn: câu quá ngắn (<0.4 s) thường là căn hỏng."""
    if not lines:
        return {"lines": 0, "suspicious": 0, "ok": False}
    bad = sum(1 for l in lines if (l["end"] - l["start"]) < 0.4)
    return {"lines": len(lines), "suspicious": bad,
            "ok": bad <= max(1, len(lines) // 10)}


def process_song(track_id: int, audio_path: str, lyrics: list[str], language: str,
                 model_name: str = "large-v3", log: Log = None) -> list[dict]:
    """Trọn quy trình 1 bài: tách giọng → căn (có lời) hoặc chép lời (không lời)."""
    vocals = separate_vocals(track_id, audio_path, log)
    if lyrics:
        return align_lines(vocals, lyrics, language, model_name, log)
    return transcribe(vocals, language, model_name, log)
