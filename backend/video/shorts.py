"""
video/shorts.py — Cắt 1 đoạn DỌC (9:16) ở GIỮA final.mp4 làm YouTube Short.

Quy trình chuẩn (2026-10-07):
  1. Chọn đoạn: lấy track đang phát ở GIỮA video (tracklist của bản mix) → đoạn
     nằm GỌN trong 1 bài (không dính crossfade chuyển bài). Không có tracklist →
     lấy đúng giữa video.
  2. Trích 1 khung hình dọc giữa đoạn → AI (vision) viết nội dung Short NGẮN,
     SÚC TÍCH: title (+#Shorts), hook chữ trên màn hình, mô tả 2–3 dòng, hashtag.
  3. ffmpeg: seek nhanh vào final.mp4 → crop giữa 9:16 → 1080×1920 → chồng hook
     (Pillow vẽ, không để model vẽ chữ) → fade hình/tiếng đầu-cuối → short.mp4.
  4. Đăng NHÁP như video dài (youtube_driver.upload_draft) — dọc & ≤ 3 phút nên
     YouTube tự nhận là Short. KHÔNG publish.

Cấu hình (data/video_overrides.json, đều tuỳ chọn):
  "auto_short": true      — tự đăng Short sau khi đăng nháp video dài xong
  "short_seconds": 45     — độ dài Short (15–60 giây)
  "short_crop_x": 0.5     — vị trí khung crop theo chiều ngang (0=trái, 1=phải)
  "short_hook": true      — chồng câu hook lên video

Hook (chuẩn 2026-10-12): lời HỎI THĂM ân cần bằng tiếng Anh nói thẳng với người xem
("How are you feeling today?", "Rough day? Stay and listen a while.") — mỗi Short
1 góc hỏi thăm khác nhau, KHÔNG trùng/na ná các hook đã dùng (sổ
data/short_hooks_used.json, mọi kênh). AI viết trùng → hỏi lại → vẫn trùng thì lấy
câu chưa dùng trong kho dự phòng.
"""
from __future__ import annotations

import base64
import difflib
import json
import random
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .assembler import probe_duration

SHORT_W, SHORT_H = 1080, 1920
DEFAULT_SECONDS = 45.0
MIN_SECONDS, MAX_SECONDS = 15.0, 60.0
_TRACK_PAD = 6.0          # tránh mép bài (crossfade 5s của bản mix)
_FALLBACK_MODEL = "gpt-4o-mini"

_HOOK_FONTS = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
]


def _log_fn(log):
    def _l(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _l


# ── Cấu hình ────────────────────────────────────────────────────────────
def settings() -> dict:
    ov = config.load_overrides() or {}

    def _f(key, default, lo, hi):
        try:
            return min(max(float(ov.get(key, default)), lo), hi)
        except (TypeError, ValueError):
            return default
    return {
        "auto_short": bool(ov.get("auto_short", True)),
        "seconds": _f("short_seconds", DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS),
        "crop_x": _f("short_crop_x", 0.5, 0.0, 1.0),
        "hook": bool(ov.get("short_hook", True)),
    }


# ── 1) Chọn đoạn ────────────────────────────────────────────────────────
def pick_segment(duration: float, tracklist: Optional[list[dict]],
                 length: float) -> tuple[float, float, str]:
    """(start, length, tên bài) — đoạn quanh GIỮA video, gọn trong 1 bài nếu được."""
    duration = max(float(duration or 0.0), 0.0)
    length = min(length, max(duration - 1.0, 1.0))
    mid = duration / 2.0
    entries = sorted(tracklist or [], key=lambda e: e["start_seconds"])
    for i, e in enumerate(entries):
        t0 = float(e["start_seconds"])
        t1 = float(entries[i + 1]["start_seconds"]) if i + 1 < len(entries) else duration
        if not (t0 <= mid < t1):
            continue
        lo, hi = t0 + _TRACK_PAD, t1 - _TRACK_PAD - length
        if hi >= lo:
            # Bắt đầu sau ~1/3 bài (đã vào giai điệu chính), không vượt khỏi bài.
            start = min(max(t0 + (t1 - t0) / 3.0, lo), hi)
            return round(start, 2), length, str(e.get("name") or "")
        break
    start = min(max(mid - length / 2.0, 0.0), max(duration - length, 0.0))
    return round(start, 2), length, ""


def _crop_filter(crop_x: float) -> str:
    """Crop 9:16 theo chiều cao gốc → scale 1080×1920."""
    return (f"crop=trunc(ih*9/16/2)*2:ih:(iw-trunc(ih*9/16/2)*2)*{crop_x:.3f}:0,"
            f"scale={SHORT_W}:{SHORT_H}:flags=lanczos,setsar=1")


def _run(cmd: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", creationflags=flags)


# ── 2) Khung hình cho AI ────────────────────────────────────────────────
def extract_frame(final_path: str, at: float, out_png: str | Path,
                  crop_x: float = 0.5) -> bool:
    r = _run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(final_path),
              "-frames:v", "1", "-vf", _crop_filter(crop_x), str(out_png)],
             timeout=120)
    return r.returncode == 0 and Path(out_png).exists()


# ── 3) Hook chữ trên màn hình ───────────────────────────────────────────
def _balance_lines(words: list[str], n: int, measure, max_w: float) -> list[str]:
    """Chia `words` thành n dòng sao cho dòng DÀI NHẤT ngắn nhất (tránh 1 chữ
    lẻ loi ở dòng cuối). [] nếu không chia vừa max_w."""
    from itertools import combinations
    if n <= 1 or len(words) <= n:
        return []
    best, best_w = [], None
    for cuts in combinations(range(1, len(words)), n - 1):
        b = (0,) + cuts + (len(words),)
        lines = [" ".join(words[b[i]:b[i + 1]]) for i in range(n)]
        w = max(measure(ln) for ln in lines)
        if w <= max_w and (best_w is None or w < best_w):
            best, best_w = lines, w
    return best


def _sentence_layout(text: str, draw, font, max_w: float, max_lines: int = 4,
                     min_size: int = 40, start_size: int = 0,
                     fonts: Optional[list[str]] = None):
    """Hook 2 vế («Rough day? Stay a while.»): mỗi câu bắt đầu DÒNG MỚI để câu mời
    không dính đuôi câu hỏi. (font, lines) hoặc None nếu chỉ 1 câu / không vừa.

    `fonts` = bộ font đang dùng (phải truyền đúng bộ của render_hook_png, nếu
    không chữ sẽ lặng lẽ đổi về font mặc định khi phải giảm cỡ)."""
    from .thumbnail import _load_font, _wrap
    cands = fonts or _HOOK_FONTS
    parts = [s for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    if len(parts) != 2:
        return None
    size = max(start_size, font.size)
    while size >= min_size:
        f = font if size == font.size else _load_font(cands, size)
        if f is None:
            return None

        def measure(t, f=f):
            return draw.textlength(t, font=f)
        lines: list[str] = []
        for s in parts:
            n = len(_wrap(s, f, draw, max_w))
            lines += _balance_lines(s.split(), n, measure, max_w) or _wrap(s, f, draw, max_w)
        if len(lines) <= max_lines and all(measure(ln) <= max_w for ln in lines):
            return f, lines
        size -= 2
    return None


def render_hook_png(text: str, out_png: str | Path,
                    width: int = SHORT_W, height: int = SHORT_H,
                    style: Optional[dict] = None) -> bool:
    """PNG trong suốt: dải tối mờ phía trên + hook chữ viền tối, căn giữa, nằm ở
    vùng 8–20% chiều cao (né thanh tìm kiếm trên & chữ/nút Shorts dưới).

    `style` (thumbnail_recipe.hook_style) đổi FONT + MÀU chữ theo công thức của
    project → mỗi Short một kiểu chữ khác. Không truyền → chữ trắng font mặc
    định như trước."""
    text = " ".join((text or "").split())
    if not text:
        return False
    try:
        from PIL import Image, ImageDraw
        from .thumbnail import _fit_wrapped, _usable
    except Exception:
        return False
    fonts = list((style or {}).get("fonts") or []) + _HOOK_FONTS
    # Font trang trí thiếu dấu tiếng Việt → _usable đẩy font đủ glyph lên đầu.
    fonts = _usable(fonts, text)
    fill = tuple((style or {}).get("fill") or (255, 255, 255))
    stroke_rgb = tuple((style or {}).get("stroke") or (10, 18, 15))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    # Dải tối gradient ở 1/3 trên để chữ luôn đọc được trên nền sáng.
    band_h = int(height * 0.30)
    band = Image.new("L", (1, band_h))
    for y in range(band_h):
        band.putpixel((0, y), int(120 * (1 - y / band_h)))
    shade = Image.new("RGBA", (width, band_h), (8, 14, 12, 0))
    shade.putalpha(band.resize((width, band_h)))
    img.alpha_composite(shade, (0, 0))

    draw = ImageDraw.Draw(img)
    max_w = int(width * 0.84)
    font, lines = _fit_wrapped(fonts, text, draw, max_w,
                               int(width * 0.078), max_lines=3, min_size=40)
    if font is None or not lines:
        return False
    lines = _balance_lines(text.split(), len(lines),
                           lambda t: draw.textlength(t, font=font), max_w) or lines
    font, lines = (_sentence_layout(text, draw, font, max_w,
                                    start_size=int(width * 0.078),
                                    fonts=fonts) or (font, lines))
    stroke = max(4, int(font.size * 0.08))
    top, bottom = draw.textbbox((0, 0), "Ag", font=font, stroke_width=stroke)[1::2]
    lh = bottom - top
    gap = int(lh * 0.10)
    block = len(lines) * lh + (len(lines) - 1) * gap
    y = int(height * 0.08) + max(0, (int(height * 0.12) - block) // 2) - top
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((width - w) / 2, y), ln, font=font, fill=fill + (255,),
                  stroke_width=stroke, stroke_fill=stroke_rgb + (235,))
        y += lh + gap
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, format="PNG")
    return True


# ── 4) Render short.mp4 ─────────────────────────────────────────────────
def render_short(final_path: str, out_path: str | Path, start: float,
                 length: float, hook_png: Optional[str | Path] = None,
                 crop_x: float = 0.5, log=None) -> Path:
    _log = _log_fn(log)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    fo = max(length - 1.2, 0.0)
    vf = f"[0:v]{_crop_filter(crop_x)},fps=30,fade=t=in:st=0:d=0.6,fade=t=out:st={fo:.2f}:d=1.2"
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
           "-i", str(final_path)]
    if hook_png and Path(hook_png).exists():
        cmd += ["-loop", "1", "-t", f"{length:.2f}", "-i", str(hook_png)]
        fc = (f"{vf}[bg];[1:v]format=rgba,fade=t=in:st=0.3:d=0.8:alpha=1,"
              f"fade=t=out:st={fo:.2f}:d=1.2:alpha=1[hk];"
              f"[bg][hk]overlay=0:0:shortest=1:format=auto[v]")
    else:
        fc = f"{vf}[v]"
    afo = max(length - 2.0, 0.0)
    fc += f";[0:a]afade=t=in:st=0:d=1,afade=t=out:st={afo:.2f}:d=2[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-t", f"{length:.2f}", str(tmp)]
    _log(f"Short: ffmpeg cắt {length:.0f}s từ giây {start:.0f} → 1080×1920")
    r = _run(cmd)
    if r.returncode != 0 or not tmp.exists():
        raise RuntimeError(f"ffmpeg tạo Short lỗi: {r.stderr.strip()[-400:]}")
    tmp.replace(out)
    return out


# ── 5) Nội dung Short bằng AI ───────────────────────────────────────────
def _clean_tags(tags: str) -> str:
    out: list[str] = []
    for t in (tags or "").replace(",", " ").split():
        t = "#" + "".join(ch for ch in t.lstrip("#") if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in [x.lower() for x in out]:
            out.append(t.lower())
    if "#shorts" not in out:
        out.insert(0, "#shorts")
    return " ".join(out[:6])


def _finish_title(title: str) -> str:
    title = " ".join((title or "").replace("#Shorts", "").replace("#shorts", "").split())
    title = title[:88].rstrip(" -|·,")
    return f"{title} #Shorts"


# ── Hook hỏi thăm: sổ hook đã dùng + góc hỏi thăm + kho dự phòng ──────────
_HOOK_LEDGER = config.OVERRIDES.parent / "short_hooks_used.json"
_LEDGER_CAP = 500
_PROMPT_RECENT = 60          # số hook gần nhất đưa vào prompt để AI né
_SIMILAR = 0.78              # SequenceMatcher ≥ ngưỡng này = coi như trùng
_HOOK_MIN_WORDS, _HOOK_MAX_WORDS = 3, 16
_ledger_lock = threading.Lock()

# Mỗi Short chọn 1 góc (ưu tiên góc lâu chưa dùng) → câu hỏi thăm đa dạng.
_CHECKIN_ANGLES = [
    "ask simply how they are feeling today",
    "ask if today has been a rough or heavy day for them",
    "ask if they feel tired after a long day of work",
    "ask if they have been studying for too long",
    "ask if they have been sleeping well lately",
    "ask if their mind is busy or racing right now",
    "ask if they have been too hard on themselves lately",
    "ask when they last took a slow, deep breath",
    "ask if their heart feels heavy right now",
    "ask if they feel a little lonely or are missing someone",
    "ask if they are worried about tomorrow",
    "ask if they are carrying too much on their shoulders",
    "ask if they feel overwhelmed lately",
    "ask if they are okay, really, and say it is fine not to be",
    "ask if anyone has asked how they are doing today",
    "ask if their body feels tense, tight shoulders or jaw",
    "ask if they feel anxious and need a calm moment",
    "ask if this week has felt long",
    "ask if they have taken any time for themselves today",
    "ask if they have been kind to themselves today",
    "ask if they are still pushing through without a break",
    "ask what has been on their mind lately",
    "ask if they need permission to rest",
    "ask if today asked too much of them",
]

# Kho dự phòng (AI lỗi / AI viết trùng): câu đủ ý + ghép hỏi + mời nghe.
_POOL_HOOKS = [
    "How are you feeling today?",
    "Rough day? Stay here and breathe a little.",
    "Is today a heavy one? Let's listen together.",
    "Tired from today? Rest here for a minute.",
    "Have you been sleeping okay lately?",
    "How is your heart doing right now?",
    "Been too hard on yourself lately?",
    "Mind still racing? Let the music slow it down.",
    "Long week? You made it this far.",
    "When did you last take a real breath?",
    "Are you okay, really? Stay a while.",
    "Missing someone today? Let the music hold you.",
    "Carrying too much today? Set it down for a moment.",
    "Shoulders tight? Let them drop with the music.",
    "Overwhelmed lately? Just listen for a moment.",
    "Worried about tomorrow? Stay in this moment.",
    "Has anyone asked how you are doing today?",
    "You don't have to be okay right now.",
    "Need a quiet minute? This one is for you.",
    "How much did you hold in today?",
    "Is your mind tired too? Rest it here.",
    "Did today ask too much of you?",
    "Be gentle with yourself today. Just listen.",
    "Can't fall asleep? Let this calm you down.",
    "Studying too long? Give your mind a rest.",
    "Feeling anxious? Breathe slowly with the music.",
    "Had a bad day? Let some music help.",
    "What has been on your mind lately?",
    "Is something weighing on your heart today?",
    "Hey, how are you holding up?",
    "Still pushing through? Pause here for a while.",
    "Need permission to rest? Here it is.",
    "Has today been kind to you?",
    "Exhausted? Close your eyes and just listen.",
    "Did you take any time for yourself today?",
    "Feeling a little lost today? Stay with me.",
]
_POOL_QUESTIONS = [
    "How are you, really?", "Is today a hard day?", "Feeling worn out?",
    "Is your heart heavy?", "Did today drain you?", "Can't switch off?",
    "Feeling stressed?", "Tired of being strong?", "Need a break?",
    "Is your head too loud?", "Feeling alone?", "Running on empty?",
    "Was work too much today?", "Restless mind?", "Feeling low today?",
    "Too much on your plate?", "Heart feeling tired?", "Hard week so far?",
    "Missing home a little?", "Thinking too much again?", "Body aching from stress?",
    "Nobody checked on you today?", "Feeling stretched thin?", "Need some quiet?",
    "Is everything a bit much?", "Mind won't settle down?", "Had to hold it together?",
    "Feeling unseen lately?", "Holding back tears?", "Tired of overthinking?",
]
_POOL_INVITES = [
    "Let's listen to some music.", "Stay for a minute.", "Breathe with me.",
    "This music is for you.", "Rest here a while.", "Let the music hold you.",
    "Just listen and let go.", "Take this quiet moment.", "Slow down with me.",
    "You deserve this pause.", "Close your eyes and listen.",
    "Let these notes calm you.", "Sit with this sound.", "Unclench your jaw and listen.",
    "Give yourself one calm minute.", "You are safe here.", "Let it all soften.",
    "Put your phone down and breathe.", "This one is a hug.",
]


def _norm_hook(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", t).split())


def used_hooks(limit: Optional[int] = None) -> list[dict]:
    """Các hook Short đã dùng (cũ → mới): [{hook, angle, project_id, at}]."""
    try:
        data = json.loads(_HOOK_LEDGER.read_text(encoding="utf-8"))
        rows = [r for r in data if isinstance(r, dict) and r.get("hook")]
    except Exception:
        rows = []
    return rows[-limit:] if limit else rows


def remember_hook(hook: str, angle: str = "", project_id: Optional[int] = None) -> None:
    """Ghi hook vào sổ (ghi nguyên tử, giữ _LEDGER_CAP mục mới nhất)."""
    hook = " ".join((hook or "").split())
    if not hook:
        return
    with _ledger_lock:
        rows = used_hooks()
        rows.append({"hook": hook, "angle": angle or "", "project_id": project_id,
                     "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        rows = rows[-_LEDGER_CAP:]
        _HOOK_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HOOK_LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_HOOK_LEDGER)


def is_fresh_hook(hook: str, used: list[str]) -> bool:
    """False nếu hook trùng hoặc NA NÁ (≥ _SIMILAR) một hook đã dùng."""
    a = _norm_hook(hook)
    if not a:
        return False
    for u in used:
        b = _norm_hook(u)
        if not b:
            continue
        if a == b:
            return False
        sm = difflib.SequenceMatcher(None, a, b)
        if (sm.real_quick_ratio() >= _SIMILAR and sm.quick_ratio() >= _SIMILAR
                and sm.ratio() >= _SIMILAR):
            return False
    return True


def valid_hook(hook: str) -> bool:
    """Câu hook dùng được: tiếng Anh ASCII (không emoji/hashtag), 3–16 từ, ≤ 2 câu."""
    h = " ".join((hook or "").replace("’", "'").split())
    if not h or "#" in h or any(ord(c) > 126 for c in h):
        return False
    if not (_HOOK_MIN_WORDS <= len(h.split()) <= _HOOK_MAX_WORDS):
        return False
    return len([s for s in re.split(r"[.?!]+", h) if s.strip()]) <= 2


def pick_angle(used_rows: Optional[list[dict]] = None, exclude: tuple = ()) -> str:
    """Góc hỏi thăm chưa dùng gần đây (né ~2/3 số góc dùng gần nhất)."""
    rows = used_hooks() if used_rows is None else used_rows
    recent = {r.get("angle") for r in rows[-(len(_CHECKIN_ANGLES) * 2 // 3):]}
    fresh = [a for a in _CHECKIN_ANGLES if a not in recent and a not in exclude]
    return random.choice(fresh or [a for a in _CHECKIN_ANGLES if a not in exclude]
                         or _CHECKIN_ANGLES)


def pool_hook(used: Optional[list[str]] = None) -> str:
    """Câu hỏi thăm dự phòng CHƯA dùng: câu soạn sẵn → ghép hỏi + mời nghe."""
    used = [r["hook"] for r in used_hooks()] if used is None else list(used)
    singles = _POOL_HOOKS[:]
    random.shuffle(singles)
    for h in singles:
        if is_fresh_hook(h, used):
            return h
    combos = [f"{q} {i}" for q in _POOL_QUESTIONS for i in _POOL_INVITES]
    random.shuffle(combos)
    for h in combos:
        if is_fresh_hook(h, used):
            return h
    # Kho cạn (≥ vài trăm Short): lấy câu dùng LÂU NHẤT.
    last_use = {_norm_hook(u): i for i, u in enumerate(used)}
    return min(singles + combos, key=lambda h: last_use.get(_norm_hook(h), -1))


def _ai_hook_retry(client, model: str, angle: str, avoid: list[str],
                   rejected: str) -> str:
    """Hỏi riêng 1 câu hook mới (chỉ text) khi câu trước trùng / sai luật."""
    recent = "\n".join(f"- {h}" for h in avoid[-_PROMPT_RECENT:]) or "(none yet)"
    prompt = (
        "Write ONE new on-screen hook for a YouTube Short of calm healing music.\n"
        "It must be a warm, caring check-in spoken directly to the viewer, like a kind "
        "friend asking how they are, optionally followed by a short invitation to stay "
        "and listen. 4-14 words, at most 2 short sentences, natural US English, "
        "no hashtags, no emoji, no quotes, no time-of-day greetings.\n"
        f"Angle: {angle}.\n"
        f"Rejected (too similar to an old hook): {rejected or '(empty)'}\n"
        "It must differ clearly in wording AND idea from every hook below:\n"
        f"{recent}\n\n"
        'Return JSON only: {"hook": str}')
    resp = client.chat.completions.create(
        model=model, max_tokens=80, temperature=1.0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content":
                   "You write short, caring on-screen hooks. Return STRICT JSON only."},
                  {"role": "user", "content": prompt}])
    data = json.loads(resp.choices[0].message.content or "{}")
    return " ".join(str(data.get("hook") or "").replace('"', "").split())


def fallback_meta(channel_name: str, instrument: str, music_style: str,
                  long_title: str = "", used: Optional[list[str]] = None) -> dict:
    instr = (instrument or "relaxing").strip()
    style = (music_style or "healing").strip()
    hook = pool_hook(used)
    return {
        "title": _finish_title(f"{instr.title()} {style.title()} to Calm Your Mind"),
        "hook": hook,
        "description": (f"A minute of {instr} {style} to slow your breath and quiet "
                        f"the mind. Full session on {channel_name or 'our channel'}."),
        "hashtags": _clean_tags(f"#shorts #{instr.replace(' ', '')} #relaxingmusic "
                                f"#meditation #healing"),
        "hook_angle": "pool",
    }


def generate_short_meta(frame_png: Optional[str], channel_name: str,
                        instrument: str, music_style: str, long_title: str,
                        track_name: str, past_titles: list[str], api_config,
                        log=None, used: Optional[list[str]] = None) -> dict:
    """{'title','hook','description','hashtags','hook_angle'} — US English, ngắn
    & súc tích; hook = lời hỏi thăm KHÔNG trùng `used` (mặc định: sổ hook đã dùng).
    Không raise: lỗi/thiếu key → fallback_meta."""
    _log = _log_fn(log)
    rows = used_hooks()
    used = [r["hook"] for r in rows] if used is None else list(used)
    fb = fallback_meta(channel_name, instrument, music_style, long_title, used=used)
    angle = pick_angle(rows)
    if not api_config or not getattr(api_config, "api_key", None):
        _log("Short-meta: chưa có API key — dùng nội dung mặc định.")
        return fb
    try:
        from openai import OpenAI
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        _log(f"Short-meta: không tạo được client ({e}) — dùng mặc định.")
        return fb
    model = getattr(api_config, "model", None) or _FALLBACK_MODEL
    if model.lower().startswith("claude"):
        model = _FALLBACK_MODEL
    recent = "\n".join(f"- {t}" for t in (past_titles or [])[:15]) or "(none yet)"
    recent_hooks = "\n".join(f"- {h}" for h in used[-_PROMPT_RECENT:]) or "(none yet)"
    prompt = (
        "Write copy for a YouTube Short (vertical, under 60 seconds) cut from the "
        "middle of a long relaxing music video. Look at the attached frame.\n"
        f"Channel: {channel_name or '(unknown)'}\n"
        f"Instrument: {instrument or '(unspecified)'}\n"
        f"Music style / mood: {music_style or '(unspecified)'}\n"
        f"Long video title: {long_title or '(unknown)'}\n"
        f"Song playing: {track_name or '(unknown)'}\n\n"
        "RULES — short, punchy, concise, natural US English, no clickbait lies:\n"
        "1. title: 35-70 characters, curiosity or feeling-driven, mention the "
        "instrument or mood. Do NOT add #Shorts (added automatically). Must differ "
        "from the recent titles below.\n"
        "2. hook: on-screen text. A warm, caring check-in spoken directly to the "
        "viewer, like a kind friend asking how they are: usually a gentle question "
        "about their day or feelings, optionally followed by a short invitation to "
        "stay and listen. 4-14 words, at most 2 short sentences, no hashtags, no "
        "emoji, no quotes, no time-of-day greetings (viewers watch at any hour). "
        f"Angle for THIS Short: {angle}. Tone examples only, do NOT copy: "
        "'How are you feeling today?', 'Rough day? Stay here and breathe a little.' "
        "It must differ clearly in wording AND idea from every recently used hook "
        "listed below.\n"
        "3. description: 1-2 short sentences (max 200 characters) describing the "
        "scene in the frame and inviting viewers to the full session on the channel.\n"
        "4. hashtags: 4-6 lowercase #tags, first one #shorts.\n\n"
        f"Recent Short titles on this channel (avoid repeating):\n{recent}\n\n"
        f"Recently used hooks (never repeat or paraphrase closely):\n{recent_hooks}\n\n"
        'Return JSON only: {"title": str, "hook": str, "description": str, '
        '"hashtags": str}'
    )
    content: list[dict] = [{"type": "text", "text": prompt}]
    if frame_png and Path(frame_png).exists():
        b64 = base64.b64encode(Path(frame_png).read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    try:
        resp = client.chat.completions.create(
            model=model, max_tokens=500, temperature=0.9,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content":
                       "You are a concise YouTube Shorts copywriter for a calm "
                       "healing-music channel. Return STRICT JSON only."},
                      {"role": "user", "content": content}])
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        _log(f"Short-meta: gọi AI lỗi ({type(e).__name__}: {e}) — dùng mặc định.")
        return fb
    title = str(data.get("title") or "").strip()
    hook = str(data.get("hook") or "").replace("’", "'")
    hook = " ".join(re.sub(r"[\"“”]", "", hook).split())
    desc = " ".join(str(data.get("description") or "").split())
    if not title or not desc:
        _log("Short-meta: AI trả thiếu title/description — dùng mặc định.")
        return fb
    hook, hook_angle = _fresh_ai_hook(client, model, hook, angle, used, _log)
    if not hook:
        hook, hook_angle = fb["hook"], "pool"
        _log(f"Short-meta: hook AI trùng/không hợp lệ — dùng câu dự phòng «{hook}».")
    return {"title": _finish_title(title), "hook": hook, "hook_angle": hook_angle,
            "description": desc[:300], "hashtags": _clean_tags(data.get("hashtags"))}


def _fresh_ai_hook(client, model: str, hook: str, angle: str, used: list[str],
                   log) -> tuple[str, str]:
    """(hook, góc) hợp lệ & chưa dùng; hỏi lại AI tối đa 2 lần với góc khác.
    ("", "") nếu vẫn không được."""
    tried = [angle]
    for attempt in range(3):
        if valid_hook(hook) and is_fresh_hook(hook, used):
            return hook, angle
        if attempt == 2:
            break
        log(f"Short-meta: hook «{hook}» trùng/na ná hook cũ hoặc sai luật — "
            f"hỏi AI câu khác ({attempt + 1}/2)…")
        rejected = hook
        angle = pick_angle(exclude=tuple(tried))
        tried.append(angle)
        try:
            hook = _ai_hook_retry(client, model, angle, used, rejected)
        except Exception as e:
            log(f"Short-meta: hỏi lại hook lỗi ({type(e).__name__}: {e}).")
            break
    return "", ""


def compose_description(meta: dict, channel_name: str,
                        long_url: str = "") -> str:
    parts = [meta.get("description", "").strip()]
    if long_url:
        parts.append(f"▶ Full session: {long_url}")
    parts.append(meta.get("hashtags", "").strip())
    return "\n\n".join(p for p in parts if p)[:1000]


# ── Ghép lại: chuẩn bị short.mp4 + nội dung ─────────────────────────────
def _hook_style(project_id: Optional[int]) -> Optional[dict]:
    """Font + màu chữ hook theo công thức thumbnail của project. Best-effort:
    thiếu project_id / lỗi sổ công thức → None → chữ trắng mặc định."""
    if not project_id:
        return None
    try:
        from . import thumbnail_recipe
        return thumbnail_recipe.hook_style(
            thumbnail_recipe.recipe_for_project(int(project_id)))
    except Exception:
        return None


def build_short(final_path: str, out_dir: Path, tracklist: list[dict], *,
                channel_name: str, instrument: str, music_style: str,
                long_title: str, past_titles: list[str], api_config,
                progress_cb: Callable[[str, float], None],
                project_id: Optional[int] = None) -> dict:
    st = settings()
    dur = probe_duration(str(final_path))
    start, length, track = pick_segment(dur, tracklist, st["seconds"])
    progress_cb(f"Short: lấy {length:.0f}s ở giữa video (từ "
                f"{int(start // 3600)}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}"
                + (f", bài «{track}»" if track else "") + ")…", 5.0)
    frame = out_dir / "_short_frame.png"
    if not extract_frame(final_path, start + length / 2, frame, st["crop_x"]):
        frame = None
    progress_cb("Short: AI viết tiêu đề / hook / mô tả ngắn…", 15.0)
    meta = generate_short_meta(str(frame) if frame else None, channel_name,
                               instrument, music_style, long_title, track,
                               past_titles, api_config,
                               log=lambda m: progress_cb(m, 18.0))
    hook_png = out_dir / "_short_hook.png"
    use_hook = st["hook"] and render_hook_png(meta["hook"], hook_png,
                                              style=_hook_style(project_id))
    progress_cb(f"Short: crop dọc 9:16 + hook «{meta['hook']}»…" if use_hook
                else "Short: crop dọc 9:16…", 25.0)
    out = render_short(final_path, out_dir / "short.mp4", start, length,
                       hook_png if use_hook else None, st["crop_x"],
                       log=lambda m: progress_cb(m, 30.0))
    if use_hook:
        try:
            remember_hook(meta["hook"], meta.get("hook_angle", ""), project_id)
        except Exception as e:
            progress_cb(f"Short: không ghi được sổ hook ({e}).", 30.0)
    for p in (hook_png,):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    return {"path": str(out), "start": start, "length": length, "track": track,
            "meta": meta, "frame": str(frame) if frame else ""}
