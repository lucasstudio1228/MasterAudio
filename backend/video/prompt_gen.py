"""
video/prompt_gen.py — Dùng OpenAI GPT để biến IDEA của người dùng thành bộ
prompt cho quy trình 3 ẢNH → N CLIP (mặc định 20).

Đầu ra: (prompts, motions), cùng hồ sơ continuity cho caller lưu.
- prompts: ĐÚNG 3 prompt ảnh (Gemini)
    "1" = BẢN PHÂN TÍCH NHÂN VẬT CHÍNH (main character model sheet): turnaround
          trước/nghiêng/sau + hàng biểu cảm + các ô cận cảnh (trang phục, nhạc
          cụ, đạo cụ) + dải bảng màu, có nhãn chữ IN HOA tiếng Anh.
    "2" = BẢN PHÂN TÍCH LINH THÚ / THÚ CƯNG (pet character sheet) cùng kiểu, kèm
          SIZE MAP so sánh tỉ lệ với người.
    "0" = ẢNH BÌA (thumbnail) — tạo 1 lần duy nhất, SẠCH chữ, chừa nửa trái.
- motions: ĐÚNG N prompt CẢNH + CHUYỂN ĐỘNG cho Flow/Veo. Cả N clip dùng CHUNG
  hai character sheet làm nguyên liệu (ingredients) nên nhân vật & linh thú giữ
  nguyên; mỗi prompt tả một BỐI CẢNH KHÁC NHAU.

Khung bố cục của hai model sheet do CODE dựng (hàm `_model_sheet_prompt`), AI
chỉ điền nội dung riêng của nhân vật — nhờ vậy mọi project ra đúng một kiểu
trang thay vì phụ thuộc may rủi của model.

An toàn: thiếu API key / lỗi mạng / JSON hỏng → None; workflow dừng trước
khi mở công cụ tạo media, không thay ý tưởng bằng cảnh mặc định.
"""
from __future__ import annotations

import json
import math
import re
from typing import Optional

from . import music_spec
from .footwear_safety import mentions_bare_feet, sanitize_data
from .translate import TranslationError, non_english_fields, to_english

# Số clip (= số bối cảnh) mặc định nếu caller không truyền. Giữ khớp
# config.VideoParams.scene_clips.
DEFAULT_SCENE_COUNT = 20

# HOẠT ĐỘNG của nhân vật qua N cảnh. Trước 2026-10-25 luật là ≥60% cảnh phải
# CHƠI nhạc cụ → 20 clip gần như cùng một tư thế; người dùng: "tạo nhiều hoạt
# động khác chứ không phải chỉ mỗi chơi nhạc cụ". Giờ chỉ 20–30% cảnh chơi nhạc
# cụ (cảnh 0 = clip intro luôn chơi, khớp ảnh bìa), còn lại mỗi cảnh MỘT hoạt
# động đời thường êm KHÁC nhau. Nhạc cụ vẫn khoá đúng loại (piano là piano).
PLAYING_SCENE_RATIO = (0.2, 0.3)
# Trong các cảnh KHÔNG chơi nhạc cụ, tối thiểu chừng này phần có hoạt động khác nhau.
MIN_DISTINCT_ACTIVITY_RATIO = 0.75
# Gợi ý cho AI (chậm, êm, an toàn với bộ lọc Veo: không lửa/khói/hơi nước).
CALM_ACTIVITIES = (
    "reading a book", "writing in a journal", "sketching in a sketchbook",
    "painting a small watercolour", "watering potted plants", "tending a herb garden",
    "arranging wildflowers in a vase", "pouring tea into a small cup",
    "walking slowly along a path", "strolling along the shore",
    "gazing at the view from a quiet spot", "gentle stretching or slow tai chi",
    "meditating with eyes closed", "petting or brushing the companion",
    "feeding the companion", "fishing with a bamboo rod", "rowing a small wooden boat",
    "picking fruit or berries into a basket", "gathering herbs or flowers",
    "sweeping fallen leaves", "hanging laundry in the breeze", "folding paper cranes",
    "knitting a scarf", "brush calligraphy", "flying a kite", "skipping stones on water",
    "feeding koi or birds", "stacking a small stone cairn",
    "washing vegetables in a stream", "writing a letter", "resting in a hammock",
    "watching rain from a window", "stargazing on a blanket",
)


def playing_scene_range(n: int) -> tuple[int, int]:
    """(tối thiểu, tối đa) số cảnh nhân vật CHƠI nhạc cụ trong n cảnh."""
    lo = max(1, math.ceil(n * PLAYING_SCENE_RATIO[0] - 1e-9))
    hi = max(lo, math.floor(n * PLAYING_SCENE_RATIO[1] + 1e-9))
    return lo, hi


def _instrument_coverage(scenes: dict[str, str] | None, spec) -> float:
    if not spec or not scenes:
        return 1.0
    hits = sum(1 for v in scenes.values() if music_spec.mentions_instrument(v, spec))
    return hits / max(1, len(scenes))


def _scene_items(raw) -> list[dict]:
    """'scenes' (list/dict như AI trả) → [{prompt, activity, flag}] theo thứ tự."""
    if isinstance(raw, dict):
        raw = [raw[k] for k in sorted(raw, key=lambda k: int(k) if str(k).isdigit() else 10**6)]
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            prompt, act, flag = it.get("prompt"), it.get("activity"), it.get("plays_instrument")
        else:
            prompt, act, flag = it, "", None
        out.append({"prompt": prompt if isinstance(prompt, str) else "",
                    "activity": act.strip() if isinstance(act, str) else "", "flag": flag})
    return out


def _play_re(spec) -> re.Pattern:
    names = {spec["label"].lower(), *(a.lower() for a in spec.get("aliases", []))}
    names -= _AMBIGUOUS_ALIASES
    alt = "|".join(sorted((re.escape(x) for x in names if x), key=len, reverse=True))
    return re.compile(rf"\bplay(?:s|ing|ed)?\s+(?:on\s+)?(?:(?:the|a|an|her|his|their)\s+)?"
                      rf"(?:[\w-]+\s+){{0,2}}(?:{alt})s?\b", re.I)


def _plays_in(item: dict, spec) -> bool:
    """Cảnh này nhân vật CHƠI nhạc cụ? Theo cờ plays_instrument của AI; thiếu cờ
    → prompt có nhắc nhạc cụ; cờ false mà prompt vẫn ghi "playing the piano" → chơi."""
    flag = item.get("flag")
    if isinstance(flag, str):
        flag = flag.strip().lower() in ("true", "yes", "y", "1")
    if flag is True:
        return True
    text = f"{item.get('activity', '')} {item.get('prompt', '')}"
    if flag is None:
        return bool(spec) and music_spec.mentions_instrument(text, spec)
    return bool(spec) and bool(_play_re(spec).search(text))


_ACT_STOP = {"the", "a", "an", "her", "his", "their", "she", "he", "they", "character",
             "main", "while", "and", "with", "on", "in", "at", "by", "to", "of", "is",
             "are", "very", "quiet", "calm", "slow", "gentle", "soft", "lazy", "peaceful"}


def _activity_key(text: str) -> str:
    """Động từ chính của hoạt động ("reading a book" → "read") để so trùng."""
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _ACT_STOP or w.endswith("ly"):
            continue
        return w[:-3] if w.endswith("ing") and len(w) > 5 else w
    return ""


def _activity_report(raw, spec, n: int) -> dict:
    """Kiểm bộ cảnh: số cảnh chơi nhạc cụ nằm trong khoảng cho phép + các cảnh
    còn lại có hoạt động KHÁC nhau. `off` = độ lệch (0 = đạt) để chọn bộ tốt nhất."""
    items = _scene_items(raw)[:n]
    plays = [_plays_in(it, spec) for it in items]
    n_play = sum(plays)
    # AI bỏ trường "activity" → đoán từ câu đầu prompt (hồ sơ cũ).
    keys = [_activity_key(it["activity"] or it["prompt"])
            for it, p in zip(items, plays) if not p]
    distinct = len({k for k in keys if k})
    lo, hi = playing_scene_range(n) if spec else (0, n)
    need = math.ceil(len(keys) * MIN_DISTINCT_ACTIVITY_RATIO) if keys else 0
    probs = []
    if spec and not lo <= n_play <= hi:
        probs.append(f"{n_play}/{n} cảnh chơi {spec['label']} (cần {lo}–{hi})")
    if distinct < need:
        probs.append(f"chỉ {distinct} hoạt động khác nhau ở {len(keys)} cảnh không chơi "
                     f"nhạc (cần ≥{need})")
    off = max(0, lo - n_play) + max(0, n_play - hi) + max(0, need - distinct)
    return {"playing": n_play, "distinct": distinct, "problem": "; ".join(probs), "off": off}


def _activity_summary(rep: dict, spec) -> str:
    return ((f", {rep['playing']} cảnh chơi {spec['label']}" if spec else "")
            + f", {rep['distinct']} hoạt động khác")


def _playing_first(raw, spec):
    """Cảnh 0 = clip INTRO (phát 1 lần đầu video, khớp ảnh bìa đang chơi nhạc cụ)
    → đưa cảnh chơi nhạc cụ đầu tiên lên vị trí 0."""
    if not spec or not isinstance(raw, list):
        return raw
    for i, it in enumerate(_scene_items(raw)):
        if _plays_in(it, spec):
            return raw if i == 0 else [raw[i], *raw[:i], *raw[i + 1:]]
    return raw


def _scene_bodies(raw) -> dict[str, str]:
    """Phần SCENE do AI viết (chưa gắn khối khoá) — để đo độ phủ nhạc cụ."""
    if isinstance(raw, list):
        return {str(i): (it.get("prompt") if isinstance(it, dict) else it) or ""
                for i, it in enumerate(raw)}
    if isinstance(raw, dict):
        return {str(k): (v.get("prompt") if isinstance(v, dict) else v) or ""
                for k, v in raw.items()}
    return {}


def _misuse_count(bodies: dict, spec) -> int:
    return sum(_instrument_misuse(b, spec) for b in bodies.values() if isinstance(b, str))


def _drop_misuse_sentences(text: str, spec) -> str:
    """Bỏ riêng các câu tả nhạc cụ sai thực tế (dùng khi đã hết lượt viết lại)."""
    sents = re.split(r"(?<=[.!?])\s+", text or "")
    kept = [x for x in sents if not _instrument_misuse(x, spec)]
    return " ".join(kept).strip() or (text or "")


def _without_misuse(raw, spec):
    """Bản sao 'scenes' (list/dict như AI trả) đã bỏ câu tả nhạc cụ sai."""
    def fix(item):
        if isinstance(item, dict):
            p = item.get("prompt")
            return {**item, "prompt": _drop_misuse_sentences(p, spec)} if isinstance(p, str) else item
        return _drop_misuse_sentences(item, spec) if isinstance(item, str) else item
    if isinstance(raw, list):
        return [fix(x) for x in raw]
    if isinstance(raw, dict):
        return {k: fix(v) for k, v in raw.items()}
    return raw


def _resolve_instrument(instrument: str, idea: str, api_config, log):
    """Nhạc cụ project: tham số > khoá 'instrument' trong JSON ngữ cảnh."""
    name = (instrument or "").strip() or music_spec.instrument_from_context(idea)
    return music_spec.resolve_instrument(name, api_config, log) if name else None


def _is_photoreal(style: str | None) -> bool:
    """Phong cách tả thực (photoreal) hay hoạt hình? Nhận diện từ chuỗi brief."""
    s = (style or "").lower()
    return "photoreal" in s or "tả thực" in s


def _system_for(style_brief: str) -> str:
    if _is_photoreal(style_brief):
        medium = "PHOTOREALISTIC CINEMATIC FILM"
        rule1 = (
            f"'{style_brief}' — it MUST be cinematic photorealism that looks like "
            "a REAL PHOTOGRAPH / LIVE-ACTION FILM (true-to-life skin, fabric, hair, "
            "fur and material detail, cinematic lighting, depth of field). "
            "ABSOLUTELY NO animation, NO cartoon/anime, NO flat 2D illustration, "
            "NO stylised Pixar/Disney-like 3D. Always describe characters and "
            "materials as they look in real life. "
        )
    else:
        medium = "ANIMATED FILM"
        rule1 = (
            f"'{style_brief}' — ABSOLUTELY NOT photorealistic, NOT a real "
            "photograph, NO lifelike human skin/fabric/hair, NO hyperrealism. "
            "Always describe characters and materials in the VISUAL LANGUAGE of "
            "the chosen animation style, never as real people or objects. "
        )
    return (
        "You are a senior visual director specialising in "
        f"{medium} ({style_brief}) for music-playlist videos of cover songs "
        "(emotional ballads, chill acoustic, upbeat remixes). You think cinematically: every shot is a FILM FRAME with a "
        "storytelling PURPOSE, not a dry description. You are also a MOTION "
        "director: for every image, imagine it animated into an 8-second clip and "
        "write motion that is physically plausible, smooth and loopable. "
        f"SUPREME RULE 1: every shot MUST follow the style {rule1}"
        "SUPREME RULE 2: stay FAITHFUL to the user's idea (the exact characters, "
        "props, setting and actions); NEVER replace them with any default "
        "character or setting. WRITE EVERYTHING IN ENGLISH ONLY — every value in "
        "the JSON must be English (no other language, no diacritics). Return "
        "ONLY valid JSON, no explanation, no markdown."
    )

# Model dự phòng khi cấu hình lỡ để tên model của Claude cũ.
_FALLBACK_MODEL = "gpt-4o-mini"

# Model đôi khi trả JSON hỏng/thiếu key MỘT CÁCH NGẪU NHIÊN (1 lần hỏng KHÔNG
# nghĩa là API/model sai). Thử lại vài lần trước khi bỏ cuộc để pipeline không bị
# dừng oan với "AI chưa viết được prompt".
_MAX_ATTEMPTS = 3


def _bullets(items, prefix: str = "   – ") -> str:
    return "\n".join(f"{prefix}{str(x).strip()}" for x in items if str(x).strip())


def _text(v) -> str:
    """Chuỗi đã trim; mọi kiểu khác (None/số/dict) → ''."""
    return v.strip() if isinstance(v, str) else ""


def _str_list(v, min_len: int = 1, max_len: int = 12) -> list[str]:
    """Danh sách chuỗi đã làm sạch; trả [] nếu không đủ min_len phần tử.

    Model hay trả list of dict ({"label": ..., "hex": ...}) → gộp value lại
    thành một dòng thay vì vứt cả mục.
    """
    if isinstance(v, str):
        v = [line for line in v.splitlines() if line.strip()]
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, dict):
            item = " — ".join(str(x).strip() for x in item.values() if str(x).strip())
        s = str(item).strip().lstrip("-•– ").strip()
        if s:
            out.append(s)
    if len(out) < min_len:
        return []
    return out[:max_len]


def _settings_of(scenes) -> list[str]:
    """Rút danh sách bối cảnh (để lưu vào manifest & tránh lặp khi sinh bù)."""
    if not isinstance(scenes, (list, tuple)):
        return []
    out: list[str] = []
    for s in scenes:
        if isinstance(s, dict):
            val = _text(s.get("setting")) or _text(s.get("prompt"))[:80]
            if val:
                out.append(val)
    return out


# ── Bảng nhân vật CHỈ vẽ NGƯỜI (yêu cầu 2026-10-02) ──────────────────────
# Gemini từng vẽ laptop cạnh FRONT VIEW và một "cây đàn piano" méo sau BACK
# VIEW: AI viết "Instrument: …" + "Props: silver laptop…" vào khối danh tính →
# khối này được dán vào MỌI khung của bảng. Nay: khối danh tính chỉ còn cơ thể
# + trang phục; nhạc cụ có ô INSTRUMENT REFERENCE riêng, quần áo có ô OUTFIT
# REFERENCE riêng, đồ vật chỉ xuất hiện trong prompt CẢNH.
_PROP_WORDS = re.compile(
    r"\b(?:laptops?|computers?|monitors?|screens?|(?:smart)?phones?|tablets?|"
    r"books?|notebooks?|journals?|papers?|sheet music|pens?|pencils?|desks?|"
    r"tables?|chairs?|stools?|benches|bench|sofas?|couch(?:es)?|cushions?|cups?|"
    r"mugs?|teacups?|teapots?|bottles?|lanterns?|candles?|gourds?|bags?|"
    r"backpacks?|satchels?|baskets?|umbrellas?|keyboards?|lamps?|props?|"
    r"prayer beads|staffs?|walking sticks?|headphones?)\b", re.I)
_OBJECT_LABEL = re.compile(
    r"^\s*(?:instruments?|props?|items?|objects?|held items?|equipment|devices?)\s*:", re.I)
_INSET_DROP_HEADS = ("INSTRUMENT", "PROP", "ITEM", "OBJECT", "EQUIPMENT", "DEVICE")
# Alias quá ngắn/đa nghĩa ("hang" = handpan) không dùng để lọc câu.
_AMBIGUOUS_ALIASES = {"hang", "fiddle", "lute"}


def _instrument_words_re(spec) -> re.Pattern:
    words = {a.lower() for it in music_spec.INSTRUMENTS for a in it["aliases"]}
    words |= {it["label"].lower() for it in music_spec.INSTRUMENTS}
    if spec:
        words |= {spec["label"].lower(), *(a.lower() for a in spec.get("aliases", []))}
    words |= {"instrument", "instruments", "guitar", "ukulele", "saxophone"}
    words -= _AMBIGUOUS_ALIASES
    alt = "|".join(sorted((re.escape(w) for w in words if w), key=len, reverse=True))
    return re.compile(rf"\b(?:{alt})s?\b", re.I)


def _strip_objects(text: str, spec=None, *, instruments: bool = True) -> str:
    """Bỏ mọi câu/vế nhắc ĐỒ VẬT (và nhạc cụ nếu `instruments`) — giữ cơ thể,
    khuôn mặt, tóc, trang phục và phụ kiện ĐEO trên người."""
    inst_re = _instrument_words_re(spec) if instruments else None
    out: list[str] = []
    for line in (text or "").splitlines():
        kept: list[str] = []
        for sent in re.split(r"(?<=[.;!?])\s+", line):
            if not sent.strip() or _OBJECT_LABEL.match(sent):
                continue
            parts = [part for part in re.split(r",\s*", sent)
                     if part.strip() and not _PROP_WORDS.search(part)
                     and not (inst_re and inst_re.search(part))]
            if not parts:
                continue
            clean = ", ".join(parts).strip()
            if clean[-1] not in ".;!?":
                clean += "."
            kept.append(clean)
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out).strip()


def _split_head(item: str) -> tuple[str, str]:
    for sep in ("—", " - ", ":"):
        if sep in item:
            head, body = item.split(sep, 1)
            return head.strip(), body.strip()
    return "", item.strip()


def _split_main_insets(insets: list[str], spec) -> tuple[list[str], str]:
    """(ô cận cảnh CHỈ cơ thể/trang phục, mô tả nhạc cụ cho ô INSTRUMENT REFERENCE).
    Ô nhạc cụ sai (vd guitar ở project piano) bị bỏ; ô đồ vật bị bỏ."""
    callouts: list[str] = []
    inst_desc = ""
    for item in insets:
        head, body = _split_head(item)
        up = head.upper()
        if "INSTRUMENT" in up:
            if not inst_desc and (not spec or music_spec.mentions_instrument(item, spec)):
                inst_desc = body
            continue
        if any(h in up for h in _INSET_DROP_HEADS):
            continue
        body = _strip_objects(body, spec)
        if body:
            callouts.append(f"{head} — {body}" if head else body)
    return callouts, inst_desc


def _instrument_identity(spec) -> str:
    """Dòng NHẠC CỤ (code tự viết, đúng thực tế) nối sau khối danh tính trong ảnh
    bìa + prompt cảnh — KHÔNG có trong bảng nhân vật (turnaround tay không)."""
    if not spec:
        return ""
    name = spec["label"].lower()
    line = (f"INSTRUMENT ({spec['label']}): {spec['visual']}. Whenever playing, the "
            f"character is {spec['pose']}.")
    if not spec["portable"]:
        line += (f" The {name} is a large fixed instrument: the character sits at it "
                 f"and never holds, lifts or carries it"
                 f"{'; ' + spec['placement'] if spec.get('placement') else ''}.")
    # Từ 2026-10-25 chỉ 20–30% cảnh chơi nhạc cụ; khối này dán vào MỌI cảnh.
    line += (" Show the character playing it only when the SCENE says so; otherwise "
             "follow the SCENE's activity.")
    return line


def _identity_with_instrument(sheet: str, spec) -> str:
    sheet = (sheet or "").strip()
    line = _instrument_identity(spec)
    if not line or "INSTRUMENT (" in sheet:
        return sheet
    return f"{sheet}\n{line}"


_HOLD_VERBS = (r"(?:hold|holds|holding|held|carry|carries|carrying|carried|cradl\w*|"
               r"hug|hugs|hugging|clutch\w*|lift|lifts|lifting|lifted)")
_GAP_WORD = (r"(?:(?!(?:hands?|fingers?|wrists?|palms?|over|above|near|beside|by|at|to|"
             r"toward|towards|from|on|onto|in|next)\b)[\w'-]+\s+)")
_FIXED_SPOT = r"(?:desk|table|lap|shelf|windowsill|counter|nightstand|bed)"


def _instrument_misuse(text: str, spec) -> bool:
    """Prompt tả SAI thực tế với nhạc cụ cố định: cầm/bê/ôm piano, piano trên
    bàn hay trong lòng… (ảnh bìa project 11: "piano and laptop are on the desk")."""
    if not spec or spec["portable"] or not text:
        return False
    names = {spec["label"].lower(), *(a.lower() for a in spec.get("aliases", []))}
    names -= _AMBIGUOUS_ALIASES
    alt = "|".join(sorted((re.escape(n) for n in names if n), key=len, reverse=True))
    t = text.lower()
    if re.search(rf"\b{_HOLD_VERBS}\s+{_GAP_WORD}{{0,3}}(?:{alt})s?\b", t):
        return True
    if re.search(rf"\b(?:{alt})s?\s+(?:[\w'-]+\s+){{0,3}}(?:in|into)\s+"
                 rf"(?:her|his|their|both)\s+(?:hands|arms)\b", t):
        return True
    if spec.get("placement") and re.search(
            rf"\b(?:{alt})s?\b(?:\s+and\s+(?:a\s+|an\s+|the\s+|her\s+|his\s+)?[\w-]+)?"
            rf"\s+(?:(?:is|are|sits|sit|rests|rest|placed|sitting|resting|lies|lying|"
            rf"stands|standing)\s+)?(?:on|atop|upon)\s+(?:the|a|her|his|their)\s+"
            rf"(?:[\w-]+\s+)?{_FIXED_SPOT}\b", t):
        return True
    return False


def _instrument_sheet_parts(spec, detail: str = "") -> tuple[list[str], str]:
    """(ô bổ sung, luật vẽ) cho NHẠC CỤ trên bảng nhân vật chính.

    Nhạc cụ KHÔNG nằm trong turnaround 360° (luôn tay không) mà có hai ô riêng:
    INSTRUMENT REFERENCE (nhạc cụ đứng một mình, đúng tỉ lệ thật) + PLAYING POSE
    (cùng nhân vật ở tư thế chơi đúng — piano: ngồi ghế đàn, đàn đứng trên sàn)."""
    if not spec:
        if not detail:
            return [], ""
        return ([f'INSTRUMENT REFERENCE — a separate box labelled "INSTRUMENT REFERENCE": '
                 f"the instrument ALONE (no person), realistic real-world proportions: "
                 f"{detail}"],
                "• The instrument appears ONLY in the INSTRUMENT REFERENCE box — never "
                "in the turnaround, expressions or detail callouts.")
    name = spec["label"].lower()
    extra = f" {detail.rstrip('.')}." if detail else ""
    ref = (f'INSTRUMENT REFERENCE — a separate box labelled "INSTRUMENT REFERENCE": the '
           f"{name} ALONE (no person), drawn like the real instrument with correct "
           f"real-world size, proportions and construction: {spec['visual']}.{extra}")
    if spec["portable"]:
        place = f"The {name} is held only in its correct playing position"
    else:
        place = (f"The {name} is a large fixed instrument: the character SITS at it to "
                 f"play and never holds, lifts or carries it"
                 f"{'; ' + spec['placement'] if spec.get('placement') else ''}")
    pose = (f'PLAYING POSE — a separate larger box labelled "PLAYING POSE": the same '
            f"character {spec['pose']} — {spec['visual']}. {place}. Correct hand "
            f"placement, natural fingers, realistic scale between the person and the {name}.")
    others = ", ".join(music_spec.other_instruments(spec))
    rule = (f"• INSTRUMENT LOCK: the ONLY musical instrument on this sheet is the "
            f"{spec['label'].upper()}. It appears ONLY in the INSTRUMENT REFERENCE box "
            f"and the PLAYING POSE box — never in the turnaround, the expressions or the "
            f"detail callouts. {place}. Do NOT draw any other instrument ({others}).")
    return [ref, pose], rule


def _model_sheet_prompt(*, title_en: str, subject: str, insets,
                        palette, scale_note: str, expressions,
                        extra_sections=None, aspect_ratio: str = "16:9",
                        instrument_spec=None, kind: str = "main",
                        outfit_items=None, instrument_detail: str = "") -> str:
    """Dựng prompt cho MỘT tấm "bảng phân tích nhân vật" (character model sheet).

    Khung bố cục cố định (turnaround 3 góc → hàng biểu cảm → các ô cận cảnh có
    nhãn → dải bảng màu → chú thích tỉ lệ) do code quy định, AI chỉ điền nội
    dung. Nhãn viết IN HOA TIẾNG ANH, NGẮN — model sinh ảnh đánh vần kém nên
    chữ càng ngắn càng ít lỗi; đây là ảnh tài liệu nên CÓ chữ là đúng ý đồ
    (khác ảnh 0 thumbnail: tuyệt đối không chữ).
    """
    main = kind == "main"
    boxes, inst_rule = (_instrument_sheet_parts(instrument_spec, instrument_detail)
                        if main else ([], ""))
    if main:
        outfit = (_bullets(outfit_items) if outfit_items else
                  "   – every clothing piece and worn accessory described in SUBJECT")
        boxes = [('OUTFIT REFERENCE — a separate box labelled "OUTFIT REFERENCE": each '
                  "clothing piece and worn accessory drawn ON ITS OWN, laid out flat (flat-lay, "
                  "no body, no mannequin), each with a tiny label, exactly matching the "
                  f"turnaround:\n{outfit}"), *boxes]
        turn = ("relaxed upright standing pose, EMPTY-HANDED with the arms relaxed at the "
                "sides — ONLY the character's body, hair and worn outfit; nothing in or "
                "near the hands, no instrument, no furniture, no objects of any kind")
        callout_note = "an enlarged view of a BODY, FACE, HAIR or OUTFIT detail (no objects)"
        only_rule = ("• CHARACTER ONLY: the turnaround, the expressions and the detail "
                     "callouts show the person alone — no laptop, phone, book, cup, bag, "
                     "lantern, desk, chair, bench, plant or any other prop. The musical "
                     "instrument appears only in its own reference boxes; the clothing is "
                     "worn on the body and repeated piece by piece in OUTFIT REFERENCE.")
        same = "identical face, hair, outfit and colours"
    else:
        turn = ("natural relaxed standing pose — ONLY the animal itself; no objects, no "
                "toys, no furniture, no instrument, no person beside it")
        callout_note = "an enlarged view of one body detail"
        only_rule = ("• ANIMAL ONLY: no objects, toys, furniture or instruments anywhere on "
                     "the sheet (the SCALE note may use a plain human silhouette).")
        same = "identical face, body, fur/feathers, markings and colours"
    extra = "\n".join(f"• {s.strip()}" for s in [*boxes, *(extra_sections or [])]
                      if s.strip())
    return f"""CHARACTER MODEL SHEET / TURNAROUND — one single {aspect_ratio} sheet, laid out neatly like an animation production design document: flat neutral light-grey background (#ECECEC) with a very faint thin grid, clearly separated blocks, thin callout lines connecting each label to its detail.

TOP TITLE (large uppercase English text, short): "{title_en}".

SUBJECT (draw it IDENTICALLY in every frame on this sheet):
{subject}

REQUIRED LAYOUT, top to bottom:
• FULL-BODY 360° TURNAROUND ROW — same base line, SAME HEIGHT, same proportions, same neutral lighting, {turn}: three figures side by side, labelled underneath "FRONT VIEW", "SIDE VIEW", "BACK VIEW". All three MUST be the same character, matching every detail and colour.
• EXPRESSION ROW — label "FACE EXPRESSIONS": four close-up face frames of the same face, each with a small label:
{_bullets(expressions)}
• DETAIL CALLOUTS — each box is {callout_note} with a short uppercase English label and a callout line to the matching spot on the turnaround:
{_bullets(insets)}
• COLOUR STRIP at the bottom — label "COLOR PALETTE": square colour swatches in a horizontal row, each with its small hex code underneath, in this exact order:
{_bullets(palette)}
• SCALE NOTE — label "SCALE": {scale_note}
{extra}

DRAWING RULES:
• This is a DESIGN DOCUMENT, NOT a film scene: no environment, no landscape, no atmospheric effects, no dramatic shadows — flat background, even lighting, clear readable shapes.
• Every frame on the sheet must be THE SAME character: {same}. Any mismatch between views is a serious error.
{only_rule}
• Text is only for LABELS: uppercase, English, very short, placed neatly, never over the figures, no long paragraphs, no watermark, no logo.
• Never crop the character's feet or head in any frame; leave clean margins around the sheet.{chr(10) + inst_rule if inst_rule else ""}"""


def _style_notes(style: str) -> tuple[str, str]:
    """(mô tả medium, câu nhắc phong cách) cho prompt người dùng."""
    if _is_photoreal(style):
        return (
            "CINEMATIC PHOTOREALISTIC SHORT FILM",
            f'Stay true to "{style}": materials, skin, fabric and fur must look '
            "REAL like live-action film, cinematic lighting, depth of field. "
            "ABSOLUTELY NO cartoon/anime/flat 2D/stylised Pixar-like 3D.",
        )
    return (
        "ANIMATED SHORT FILM",
        f'Stay true to "{style}": animated visual language, NOT photorealistic, '
        "NOT a real photograph, NO hyperrealism.",
    )


def _instrument_prompt_parts(spec, n: int) -> tuple[str, str, str]:
    """(khối luật, vế chuyển động cảnh, vế thumbnail) theo nhạc cụ project."""
    if not spec:
        return ("", "the character doing this scene's own calm ACTIVITY with slow, "
                    "natural, anatomically correct hand motion, shoulders breathing very "
                    "gently, eyelids softly blinking", "")
    name = spec["label"].lower()
    lo, hi = playing_scene_range(n)
    block = (f"\n{music_spec.visual_instrument_rules(spec)}\n"
             f"The project NAME and DESCRIPTION define the mood, setting and story; "
             f"the {spec['label']} is the music of the video: the character plays it in "
             f"SOME scenes and spends the other scenes on calm everyday activities.\n")
    away = (f"the {name} is absent or simply stands nearby, never carried"
            if not spec["portable"] else f"the {name} is set down beside them so both "
            "hands are free")
    scene = (f"the character doing this scene's ACTIVITY with slow natural motion — in "
             f"{lo} to {hi} of the {n} scenes (scene 1 always among them) the activity is "
             f"PLAYING the {name} ({spec['pose']}; {spec['motion']}) and the word "
             f"\"{name}\" appears in the prompt; in EVERY other scene the character does a "
             f"DIFFERENT calm everyday activity (see ACTIVITIES) with relaxed, anatomically "
             f"correct hands and {away}; eyelids softly blinking")
    thumb = f" The character is playing the {name} ({spec['pose']})."
    return block, scene, thumb


def _activities_rule(spec, n: int) -> str:
    """Luật ĐA DẠNG HOẠT ĐỘNG cho N cảnh (gửi kèm lời nhắc viết cảnh)."""
    lo, hi = playing_scene_range(n)
    playing = (f"{lo} to {hi} of the {n} scenes (scene 1 among them) show the character "
               f"PLAYING the {spec['label'].lower()}; " if spec else "")
    return (
        f"ACTIVITIES — VARIETY IS REQUIRED: {playing}every other scene gives the character "
        "a DIFFERENT calm everyday activity that suits its setting — never the same "
        "activity twice and never just sitting and posing. Ideas to pick from and adapt "
        f"(or invent others that fit the idea): {', '.join(CALM_ACTIVITIES)}. The companion "
        "takes part naturally (watching, walking alongside, resting nearby, being petted). "
        "Objects an activity needs (book, brush, watering can, cup, basket, rod, oar…) are "
        "simple, held naturally with correct hands, and appear only in that scene. Every "
        "activity is slow, gentle and needs no flame or heat (no cooking, no candle lighting).")


# ── Bạn đồng hành (ảnh 2): thú cưng thật hay linh thú — theo BỐI CẢNH ─────
# Lofi (phòng học, quán cà phê, thành phố đêm…) chỉ đi cùng THÚ CƯNG thật; trước
# đây prompt luôn gợi ý linh thú (qilin…) → project lofi ra "qilin phát sáng".
_SPIRIT_WORDS = re.compile(
    r"\b(?:spirits?|spiritual|qilin|kirin|dragons?|phoenix|unicorns?|griffins?|"
    r"kitsune|nine[- ]tailed|myth\w*|legendary|magic\w*|fairy|fae|ethereal|"
    r"celestial|divine|deity|glow\w*|luminous|luminescent|bioluminescent|aura|"
    r"enchanted|supernatural|horns?|antlers?)\b", re.I)


def _companion_mode(idea: str) -> str:
    """'pet' (bắt buộc thú cưng thật) khi thể loại nhạc project là lofi; còn lại
    'auto' — AI chọn thú cưng hay linh thú theo bối cảnh."""
    try:
        ctx = json.loads(idea or "")
    except (ValueError, TypeError):
        ctx = None
    if not isinstance(ctx, dict):
        return "auto"
    styles = ctx.get("music_styles")
    if isinstance(styles, (list, tuple)):
        styles = " ".join(str(x) for x in styles)
    probe = " ".join(str(x) for x in (ctx.get("purpose"), styles) if x)
    return "pet" if music_spec.find_genre(probe)["key"] == "lofi" else "auto"


def _companion_rule(mode: str) -> str:
    if mode == "pet":
        return (
            "   This is LOFI content: the companion MUST be an ordinary real-world PET that\n"
            "   plausibly lives with the character in this setting (e.g. a cat, a small dog,\n"
            "   a rabbit, a hamster, a parakeet, a turtle). Natural real anatomy, natural\n"
            "   fur/feather colours, natural animal behaviour. ABSOLUTELY NO spirit, mythical,\n"
            "   magical or fantasy creature: no glow, no aura, no horns, no wings on mammals,\n"
            "   no supernatural traits. If the idea already names a pet → use exactly it.\n"
            '   "pet_kind" MUST be "pet". It ACCOMPANIES the main character in the scenes.')
    return (
        "   Choose from the CONTEXT (setting, era, culture, realism of the idea):\n"
        "   • If the idea already names a companion → use exactly that one.\n"
        "   • A modern / urban / everyday / realistic world → an ordinary real PET (cat,\n"
        "     dog, rabbit, bird…) with natural anatomy and NO magical traits.\n"
        "   • Only an explicitly mythical, legendary, ancient-fantasy world → a spirit\n"
        "     creature that fits the culture (e.g. East Asian legend → qilin / crane /\n"
        "     nine-tailed fox; temperate forest → owl / deer / snow fox).\n"
        "   • When unsure, prefer a real animal (pet or local wildlife).\n"
        '   Set "pet_kind" to "pet" or "spirit creature". It must be plausible, cute or\n'
        "   calm, and ACCOMPANY the main character in the scenes.")


def _is_spirit(pet_kind: str, pet_text: str) -> bool:
    kind = (pet_kind or "").lower()
    if kind:
        return any(w in kind for w in ("spirit", "myth", "creature", "legend", "magic"))
    return bool(_SPIRIT_WORDS.search(pet_text or ""))


def _build_user_prompt(idea: str, title: str, keywords: str,
                       scene_count: int, aspect_ratio: str,
                       style: str, instrument_spec=None,
                       companion_mode: str = "auto",
                       thumbnail_directive: str = "") -> str:
    """Lời nhắc chính: yêu cầu AI trả hồ sơ nhân vật + bạn đồng hành + N bối cảnh.
    `thumbnail_directive` (thumbnail_recipe.composition_directive) đổi bố cục ảnh
    bìa theo công thức riêng của video; rỗng → bố cục cũ (nhân vật phải, chữ trái)."""
    n = scene_count
    if thumbnail_directive:
        thumb_layout = (f"Follow this composition exactly — {thumbnail_directive}\n"
                        f"   Aspect ratio {aspect_ratio}.")
        thumb_json = ("70-120 word thumbnail prompt, NO text, following the THUMBNAIL "
                      "COMPOSITION above (shot, placement, empty title area, lighting)")
    else:
        thumb_layout = ("Place the characters off-centre to the RIGHT, leaving clean negative space on\n"
                        f"   the LEFT HALF. Aspect ratio {aspect_ratio}.")
        thumb_json = "70-120 word thumbnail prompt, NO text, negative space on the left half"
    medium, style_note = _style_notes(style)
    inst = instrument_spec
    inst_block, inst_scene, inst_thumb = _instrument_prompt_parts(inst, n)
    inst_line = (f"EXACTLY the {inst['label']} above (never another instrument)"
                 if inst else "the specific instrument (flute, piano…)")
    companion_rule = _companion_rule(companion_mode)
    activities = _activities_rule(inst, n)
    lo, hi = playing_scene_range(n)
    plays_req = (f" {lo}–{hi} items have \"plays_instrument\": true (item 1 among them);"
                 if inst else "")
    return f"""USER IDEA: "{idea}"
{inst_block}
Project title (for the thumbnail): "{title}"
Suggested keywords for the thumbnail: {keywords}

═══ TASK ═══
Design an atmospheric music-playlist "{medium}" (backdrop for cover songs) consisting of:
  (A) TWO CHARACTER MODEL SHEETS — the main character and the companion
      (a real pet or a spirit creature, chosen by context). Both sheets will be fed to the video model as
      INGREDIENTS for all {n} clips, so they are the single "source of truth"
      for how the characters look. You do NOT write the drawing prompt directly —
      you supply the CONTENT (descriptions, detail lists, palette); the system
      already has the layout template.
  (B) ONE thumbnail image.
  (C) EXACTLY {n} SCENE PROMPTS for {n} 8-second clips — the SAME character +
      the SAME companion, but {n} COMPLETELY DIFFERENT SETTINGS.

═══ MANDATORY RULES ═══

1. STAY ABSOLUTELY FAITHFUL TO THE IDEA
   The exact character (gender, age, ethnicity, outfit, props, instrument,
   appearance) and the exact world the idea describes. NEVER invent characters
   or settings that are not in the idea (in particular do NOT default to "young
   person in a sage hoodie + wooden cabin + calico cat" — that is an old template).

2. MAIN CHARACTER — you MUST cover ALL of these groups (explicit user request):
   • APPEARANCE: hands, legs, eyes, nose, mouth, hair, ears, height, eyebrows, skin tone
   • OUTFIT: trousers, top, shoes/sandals, headband/scarf/hat… (material + colour + hex)
     — every piece is ALSO drawn on its own in an OUTFIT REFERENCE box ("main_outfit")
   • INSTRUMENT: {inst_line} — described SEPARATELY in "instrument_detail"
     (realistic material, colour, real-world size and construction). It is drawn
     ONLY in its own INSTRUMENT REFERENCE box and a PLAYING POSE box.
   • WORN ACCESSORIES only (glasses, hat, scarf, hair pin, jewellery…; a weapon
     — sword, bow, spear, staff… — ONLY when the idea/description includes one,
     worn sheathed on the back or at the belt on the sheet)
   • THE CHARACTER SHEET SHOWS THE PERSON ONLY: the 360° turnaround is EMPTY-HANDED —
     NO instrument, NO hand-held object or device (laptop, phone, book, cup, bag,
     lantern…), NO furniture. Objects of the story belong ONLY in the scene prompts.
     So "character_sheet", "main_subject", "main_insets" and "main_outfit" must never
     mention the instrument or any object.
   • REAL-WORLD LOGIC: large instruments stay where they really stand (a piano,
     harp or cello on the floor, a guzheng on its own low stand) — the character
     SITS at them to play and never holds, lifts or carries them; a piano is never
     on a desk, a table or a lap and is never miniature.

3. COMPANION (image 2)
{companion_rule}

4. {n} DIFFERENT SETTINGS — SHUFFLE-SAFE
   • The clips will be RANDOMLY SHUFFLED when assembled ⇒ NO continuing story,
     NO morning→noon→night progression, NO scene that depends on another.
     Every scene must stand alone and end in a peaceful state.
   • {n} CLEARLY DISTINCT locations/landscapes (different terrain, weather,
     time of day, altitude/camera distance). Never repeat a location.
   • KEEP the character, outfit, instrument, companion and main palette unchanged.
   • {activities}

5. SCENE PROMPTS (80–140 words each) — prompts for a VIDEO model (image-to-
   video, 8 seconds, ingredients = the 2 character sheets). Each prompt MUST have:
   a) An opening CHARACTER LOCK clause: use the character from ingredient image 1
      and the companion from ingredient image 2, KEEP face/outfit/colours unchanged.
   b) A concrete SETTING: place, time of day, weather, 3 layers foreground /
      midground / background.
   c) LIGHTING: light source, direction, colour temperature, shadows.
   d) ATMOSPHERE & PARTICLES: mist, pollen, sun rays, rain, fireflies, snow…
   e) ACTION & MOTION that is physically plausible and anatomically correct:
      {inst_scene}; companion reactions (tail swaying, ears
      folding, soft sniffing), environmental motion. The head only tilts VERY
      SLIGHTLY, never turns backwards; correct number of hands/fingers.
   f) CAMERA: state it and keep it SIMPLE (locked camera, or an EXTREMELY slow
      pan/push-in).
   g) PACING: slow, meditative, smooth, loopable (seamless loop). No cuts, no
      fade-in/fade-out, no text.
   {style_note}

6. THUMBNAIL — 70–120 words: the same character + companion, eye-catching,
   emotionally rich composition.{inst_thumb} ABSOLUTELY NO text/title/typography/logo/
   watermark in the image (the title "{title}" will be added later by software).
   {thumb_layout}

7. CONTENT SAFETY (the video model's safety filter rejects the whole clip
   otherwise): the main character is a peaceful ADULT (25+), never a child or
   teenager, never a real or famous person; NO blood, injury, fire or smoke;
   no brand, studio or artist names.
   FOOTWEAR: the main character ALWAYS wears simple footwear (sandals,
   slippers, shoes or boots) — even if the idea says "barefoot", give them
   simple woven straw sandals instead. Never write barefoot, bare feet, soles
   or toes (the image model silently refuses bare-feet close-ups).
   WEAPONS follow the idea/description: if it describes a warrior, guardian,
   archer, samurai… with a weapon, KEEP that weapon exactly as described
   (sword, bow, spear, staff…), consistent in every scene; if it describes
   none, do not invent one. Keep the overall mood calm and meditative.

8. LANGUAGE: every value you return must be written in ENGLISH only — no other
   language, no Vietnamese, no diacritics. Transliterate proper names into plain
   unaccented Latin letters.

═══ OUTPUT FORMAT ═══
Return ONLY valid JSON (no explanation, no markdown):
{{
  "character_bible": "full profile of the main character + companion + world",
  "character_sheet": "CONDENSED 60-100 word identity block of the MAIN CHARACTER ONLY: gender/age/ethnicity + face/hair/eyes/skin/height + EVERY outfit item with hex + worn accessories. NO instrument, NO hand-held object/prop/device, NO furniture, NO setting/action/lighting (the system adds the instrument separately)",
  "main_subject": "2-4 sentences so an artist can draw the main character turnaround (build, head-to-body ratio, EMPTY-HANDED relaxed standing pose with arms at the sides, demeanour) — no objects",
  "main_expressions": ["NEUTRAL — short description", "CALM — ...", "DETERMINED — ...", "JOYFUL — ..."],
  "main_insets": ["UPPERCASE ENGLISH LABEL — detailed English description (5-8 items: hair & head accessories, face/eyes, hands (empty), upper-body outfit, lower-body outfit, footwear (shoes/sandals/boots — never bare feet), worn accessories) — NO instrument, NO props"],
  "main_outfit": ["UPPERCASE PIECE NAME — material, colour + hex (3-7 items: every clothing piece and worn accessory, for the OUTFIT REFERENCE flat-lay)"],
  "instrument_detail": "1-2 sentences describing ONLY the instrument realistically (material, colour, real-world size, construction, ornament) — no person",
  "main_scale": "height & proportion note for the main character, e.g. 'HEIGHT 1.62 m — 6.5 heads tall'",
  "pet_kind": "pet | spirit creature",
  "pet_name": "short English name of the companion",
  "pet_sheet": "CONDENSED 60-100 word identity block of the COMPANION: species/breed, size, fur/feathers (scales/horns only for a spirit creature), colours + hex, identifying marks, collar if any. NO setting/action",
  "pet_subject": "2-4 sentences to draw the companion turnaround",
  "pet_expressions": ["NEUTRAL — ...", "CALM — ...", "ALERT — ...", "PLAYFUL — ..."],
  "pet_insets": ["UPPERCASE ENGLISH LABEL — description (5-8 items: head & senses, eyes, ears, legs & paws/claws, fur/feathers, tail, markings, collar if any)"],
  "pet_scale": "SIZE MAP comparing scale: human {{height}} versus companion {{height/length}}, described in one sentence",
  "pet_traits": ["3-5 personality traits (symbolic meanings only for a spirit creature), each 'UPPERCASE LABEL — short description'"],
  "palette": ["#RRGGBB — English colour name — role (character colour / companion colour / background colour / accent colour)"],
  "thumbnail": "{thumb_json}",
  "scenes": [
    {{"setting": "short setting name", "activity": "short activity, e.g. reading a book", "plays_instrument": false, "prompt": "80-140 word scene prompt covering all 7 layers a–g"}}
  ]
}}
Required: "scenes" has EXACTLY {n} items, each with non-empty "setting",
"activity" and "prompt", {n} DIFFERENT "setting" values;{plays_req} every other
item has a DIFFERENT "activity". "palette" has 5–7 items. "main_insets"
and "pet_insets" have 5–8 items each. Do not leave any key above empty. All
values in ENGLISH."""

def _motion_system(style_brief: str) -> str:
    medium = ("photorealistic cinematic film" if _is_photoreal(style_brief)
              else "animated film")
    return (
        f"You are a senior MOTION DIRECTOR for a music-playlist (cover songs) {medium} "
        f"({style_brief}). Task: write scene + motion prompts (image-to-video, "
        "8-second clips) for the video model (Veo). This is the MOST ERROR-PRONE "
        "step: video models often 'hallucinate' (smoke/vapour from the flute or "
        "mouth, heads turning backwards, distorted hands/fingers, extra limbs, "
        "morphing). So your prompts must be EXTREMELY PRECISE and PHYSICALLY "
        "CONSTRAINED — describe the correct calm motion POSITIVELY instead of "
        "listing forbidden things (the model's safety filter rejects prompts that "
        "mention fire, smoke, distortion or injuries, even negated). "
        "Write in ENGLISH ONLY. "
        "Return ONLY valid JSON."
    )


def _build_scenes_user_prompt(idea: str, style: str, character_sheet: str,
                              pet_sheet: str, scene_count: int,
                              avoid: list[str] | None = None,
                              instrument_spec=None) -> str:
    n = scene_count
    _, style_note = _style_notes(style)
    inst_block, inst_scene, _ = _instrument_prompt_parts(instrument_spec, n)
    inst_scene = "(a) " + inst_scene
    activities = _activities_rule(instrument_spec, n)
    lo, hi = playing_scene_range(n)
    plays_req = (f" {lo}–{hi} items have \"plays_instrument\": true (item 1 among them);"
                 if instrument_spec else "")
    avoid_block = ""
    if avoid:
        joined = "; ".join(str(a).strip() for a in avoid if str(a).strip())
        if joined:
            avoid_block = ("\nSETTINGS ALREADY USED (do NOT repeat them, invent "
                           f"completely different ones): {joined}\n")
    return f"""OVERALL IDEA: "{idea}"
Style: {style}

MAIN CHARACTER (ingredient image 1 — KEEP ABSOLUTELY UNCHANGED):
{character_sheet}

COMPANION (ingredient image 2 — KEEP ABSOLUTELY UNCHANGED):
{pet_sheet}
{avoid_block}{inst_block}
The two sheets above are character model sheets that will be loaded as
INGREDIENTS for the video model. Write EXACTLY {n} scene prompts, each creating
one 8-second clip: the SAME character + the SAME companion, {n} COMPLETELY
DIFFERENT SETTINGS.

MANDATORY RULES for every prompt (80–140 words):
1. START with a lock clause: use the character from ingredient image 1 and the
   companion from ingredient image 2, keeping face, hair, outfit, colours,
   instrument and props UNCHANGED. This is NOT a design sheet — place the
   characters in a real FILM SCENE; never redraw the turnaround, labels or palette.
2. Its own SETTING: place, time of day, weather, and 3 clear layers
   foreground / midground / background.
3. Concrete LIGHTING (source, direction, colour temperature, shadows) +
   ATMOSPHERE & PARTICLES (mist, pollen, sun rays, rain, fireflies, snow…).
4. LOGICAL & ANATOMICALLY CORRECT: natural, feasible motion. The head only tilts
   VERY SLIGHTLY (NEVER turns backwards/180°); correct number and shape of hands
   and fingers. NO morphing, NO duplicated characters, NO added/removed people
   or objects, NO scene cuts.
5. 3 LAYERS OF MOTION: {inst_scene}; (b) companion reactions (tail swaying, ears folding, soft
   breathing, nuzzling); (c) environment + SIMPLE camera (locked camera, or an
   EXTREMELY slow pan/push-in). No fade-in/fade-out.
6. SHUFFLE-SAFE: the clips will be randomly shuffled ⇒ every scene must stand
   alone, never continue another scene, no order-dependent events.
   {activities}
7. PHRASE EVERYTHING POSITIVELY and keep it SAFE: describe what should happen
   (calm steady pose, natural hands on the instrument or on the activity's
   object, one continuous shot). NEVER write negative lists and NEVER mention smoke, steam, vapour,
   fire, distortion, extra limbs, head spinning, blood or injuries —
   the safety filter rejects the clip on those words even when negated.
   Weapons are allowed when the character sheet / idea includes them — keep
   them exactly as described.
8. Write in ENGLISH ONLY (no other language, no diacritics); include Veo-friendly
   keywords (slow motion, subtle, cinematic, seamless loop, anatomically
   correct). {style_note}

═══ OUTPUT ═══ Return ONLY JSON (no explanation, no markdown):
{{
  "scenes": [
    {{"setting": "short setting name", "activity": "short activity, e.g. watering plants", "plays_instrument": false, "prompt": "80-140 word scene prompt"}}
  ]
}}
Required: "scenes" has EXACTLY {n} items, {n} DIFFERENT "setting" values;{plays_req}
every other item has a DIFFERENT "activity"; no empty item."""


def generate_scene_prompts(
    character_sheet: str,
    pet_sheet: str,
    idea: str,
    style: str,
    api_config,
    scene_count: int = DEFAULT_SCENE_COUNT,
    log=None,
    avoid: list[str] | None = None,
    instrument: str = "",
    cast_mode: str = "character_pet",
) -> Optional[dict[str, str]]:
    """Sinh (hoặc sinh LẠI) bộ prompt cảnh cho các clip từ HAI character sheet.

    Dùng khi muốn đổi bối cảnh mà KHÔNG tạo lại ảnh. Trả dict đủ `scene_count`
    khoá "0".."N-1", hoặc None nếu lỗi/không đủ.
    cast_mode="scenery": hai khoá là khối nơi chốn + không khí (không nhân vật)."""
    if cast_mode == "scenery":
        from . import scenery_prompts
        return scenery_prompts.generate_scene_prompts(
            character_sheet, pet_sheet, idea, style, api_config, scene_count,
            log=log, avoid=avoid, instrument=instrument)

    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    if not (character_sheet or "").strip() or not (pet_sheet or "").strip():
        _log("Thiếu character_sheet/pet_sheet — không sinh được prompt cảnh.")
        return None
    if not api_config or not getattr(api_config, "api_key", None):
        _log("Chưa cấu hình API key — không sinh prompt cảnh được.")
        return None
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        _log(f"Không import được openai ({e}).")
        return None

    model = api_config.model
    if not model or model.lower().startswith("claude"):
        model = _FALLBACK_MODEL
    n = int(scene_count)
    spec = _resolve_instrument(instrument, idea, api_config, _log)
    # Prompt gửi Veo phải 100% tiếng Anh: ý tưởng người dùng (thường tiếng
    # Việt) được dịch trước khi đưa vào lời nhắc.
    try:
        idea = to_english(idea, api_config, _log)
        avoid = to_english(list(avoid), api_config, _log) if avoid else avoid
    except TranslationError as e:
        _log(f"{e} — dừng sinh prompt cảnh.")
        return None
    try:
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        _log(f"Không khởi tạo được client OpenAI ({type(e).__name__}: {e}).")
        return None
    # Khối danh tính (khoá continuity) chỉ có cơ thể + trang phục; dòng NHẠC CỤ
    # đúng thực tế (piano: ngồi ghế đàn, đàn đứng trên sàn) do code nối thêm.
    ident = _identity_with_instrument(character_sheet, spec)
    user_msg = _build_scenes_user_prompt(idea, style, ident, pet_sheet, n, avoid,
                                         instrument_spec=spec)
    last_reason = "không rõ"
    best = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=32000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _motion_system(style)},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = resp.choices[0].message.content or ""
        except Exception as e:
            last_reason = f"gọi OpenAI (prompt cảnh) thất bại ({type(e).__name__}: {e})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        data = _extract_json(text)
        if not isinstance(data, dict):
            last_reason = "OpenAI (prompt cảnh) trả về không phải JSON"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        if not mentions_bare_feet(character_sheet):
            # Nhân vật mang dép (Gemini chặn ngầm ảnh chân trần) — cảnh không được
            # tả lại chân trần. Hồ sơ cũ đã vẽ chân trần thì giữ nguyên cho khớp ảnh.
            data = sanitize_data(data)
        bad = non_english_fields(data.get("scenes"))
        if bad:
            last_reason = f"prompt cảnh còn chữ không phải tiếng Anh ({', '.join(bad[:4])})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        raw = _playing_first(data.get("scenes"), spec)
        scenes = _scene_dict(raw, n, ident, pet_sheet)
        if scenes is None:
            last_reason = f"JSON prompt cảnh thiếu/không đủ {n} mục hợp lệ"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        misuse = _misuse_count(_scene_bodies(raw), spec)
        report = _activity_report(raw, spec, n)
        if report["off"] or misuse:
            if best is None or (misuse, report["off"]) < (best[2], best[1]["off"]):
                best = (raw, report, misuse)
            last_reason = (f"{misuse} cảnh tả {spec['label']} sai thực tế (cầm/bê/đặt "
                           "trên bàn)" if misuse else report["problem"])
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — viết lại…")
            continue
        _log(f"Đã sinh {n} prompt cảnh (tiếng Anh) từ hai character sheet ({model}"
             f"{_activity_summary(report, spec)}).")
        return scenes
    if best is not None:
        scenes = _scene_dict(_without_misuse(best[0], spec), n, ident, pet_sheet)
        if scenes:
            _log(f"Dùng bộ cảnh tốt nhất ({_activity_summary(best[1], spec)[2:]}"
                 f"{f', đã bỏ câu tả sai ở {best[2]} cảnh' if best[2] else ''}); khối "
                 "danh tính đầu mỗi prompt vẫn khoá đúng nhạc cụ.")
            return scenes
    _log(f"Không sinh được prompt cảnh sau {_MAX_ATTEMPTS} lần (lý do cuối: {last_reason}).")
    return None


def _scene_dict(raw, n: int, character_sheet: str,
                pet_sheet: str) -> Optional[dict[str, str]]:
    """Chuẩn hoá 'scenes' (list hoặc dict) thành {"0":prompt, ...} đủ n mục.

    Mỗi prompt được gắn thêm MỆNH ĐỀ KHOÁ nguyên liệu ở đầu để model video
    không vẽ lại bảng thiết kế mà đặt đúng nhân vật vào cảnh."""
    items: list[str] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                v = it.get("prompt")
            else:
                v = it
            items.append(v.strip() if isinstance(v, str) else "")
    elif isinstance(raw, dict):
        for i in range(n):
            v = raw.get(str(i)) or raw.get(i)
            if isinstance(v, dict):
                v = v.get("prompt")
            items.append(v.strip() if isinstance(v, str) else "")
    else:
        return None
    if len(items) < n or any(not v for v in items[:n]):
        return None
    return {str(i): _with_ingredient_lock(items[i], character_sheet, pet_sheet)
            for i in range(n)}


def _with_ingredient_lock(scene: str, character_sheet: str,
                          pet_sheet: str) -> str:
    """Dán mệnh đề khoá nguyên liệu vào ĐẦU prompt cảnh gửi cho Flow/Veo.

    Model video nhận 2 ảnh nguyên liệu là BẢNG THIẾT KẾ (nhiều góc nhìn, có nhãn
    chữ). Không nói rõ, nó dễ dựng lại chính bảng thiết kế đó thành clip. Câu
    khoá này buộc nó chỉ LẤY DANH TÍNH từ nguyên liệu rồi đặt vào cảnh thật."""
    scene = (scene or "").strip()
    from .config import INGREDIENT_LOCK_FULL
    head = INGREDIENT_LOCK_FULL
    ident = " ".join(x.strip() for x in (character_sheet, pet_sheet) if x and x.strip())
    if ident:
        head += f"\nIDENTITY TO KEEP: {ident}"
    return f"{head}\n\nSCENE: {scene}"

def _with_character_lock(sheet: str, scene: str) -> str:
    """Ghép KHỐI danh tính cố định (sheet) vào ĐẦU prompt cảnh (scene) — y nguyên
    cho mọi shot để nhân vật đồng bộ. `sheet` rỗng → trả nguyên `scene`."""
    sheet = (sheet or "").strip()
    scene = (scene or "").strip()
    if not sheet:
        return scene
    return (f"CHARACTERS (KEEP ABSOLUTELY FIXED IN EVERY SHOT — never change face, "
            f"hair, outfit, colours or props): {sheet}\n\n"
            f"SETTING & ACTION OF THIS SHOT: {scene}")


def _extract_json(text: str) -> Optional[dict]:
    """Lấy object JSON đầu tiên trong text (chịu được rào ```json)."""
    if not text:
        return None
    # bỏ hàng rào code nếu có
    # Greedy \{.*\}: JSON của ta có object lồng nhau → non-greedy (.*?) sẽ dừng ở
    # dấu } ĐẦU TIÊN và cắt cụt object → JSON hỏng.
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        raw = text[start:end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None


def generate_prompts(
    idea: str,
    title: str,
    keywords: str,
    scene_count: int,
    aspect_ratio: str,
    style: str,
    api_config,
    log=None,
    creative_brief: str = "",
    metadata_out: Optional[dict] = None,
    instrument: str = "",
    thumbnail_directive: str = "",
    cast_mode: str = "character_pet",
) -> Optional[tuple[dict[str, str], dict[str, str]]]:
    """
    Trả về (prompts, motions) hoặc None nếu không dùng được AI.
      prompts: ĐÚNG 3 prompt ảnh — "1" main character sheet, "2" pet character
               sheet, "0" thumbnail.
      motions: `scene_count` prompt cảnh cho Flow (best-effort; thiếu thì caller
               gọi generate_scene_prompts bổ sung).
    `metadata_out` (nếu truyền) nhận character_sheet / pet_sheet / palette /
    settings để caller lưu vào prompts.json làm khoá continuity.
    `api_config` là instance APIConfig (có api_key/base_url/model).
    `log` (tùy chọn): callable(msg) để ghi log tiến trình.
    `cast_mode`="scenery" → CHỈ CẢNH VẬT (video/scenery_prompts.py).
    """
    if cast_mode == "scenery":
        from . import scenery_prompts
        return scenery_prompts.generate_prompts(
            idea, title, keywords, scene_count, aspect_ratio, style, api_config,
            log=log, creative_brief=creative_brief, metadata_out=metadata_out,
            instrument=instrument, thumbnail_directive=thumbnail_directive)

    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    idea = (idea or "").strip()
    if not idea:
        _log("Không có ý tưởng/hồ sơ — chưa sinh prompt.")
        return None
    if not api_config or not getattr(api_config, "api_key", None):
        _log("Chưa cấu hình OPENAI_API_KEY — dừng chuẩn bị prompt.")
        return None

    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        _log(f"Không import được openai ({e}) — dừng chuẩn bị prompt.")
        return None

    # Nếu model còn để tên Claude cũ thì đổi sang GPT mặc định.
    model = api_config.model
    if not model or model.lower().startswith("claude"):
        model = _FALLBACK_MODEL

    try:
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        _log(f"Không khởi tạo được client OpenAI ({type(e).__name__}: {e}) — "
             f"dừng chuẩn bị prompt.")
        return None
    # Mọi prompt gửi Gemini/Flow phải 100% tiếng Anh ⇒ dịch ý tưởng / tiêu đề /
    # từ khoá / brief của người dùng TRƯỚC khi đưa vào lời nhắc (JSON hồ sơ được
    # dịch từng trường, giữ nguyên khoá).
    try:
        idea = to_english(idea, api_config, _log)
        title = to_english(title or "", api_config, _log)
        keywords = to_english(keywords or "", api_config, _log)
        creative_brief = to_english(creative_brief or "", api_config, _log)
    except TranslationError as e:
        _log(f"{e} — dừng chuẩn bị prompt (không gửi prompt lẫn tiếng Việt).")
        return None
    spec = _resolve_instrument(instrument, idea, api_config, _log)
    if spec:
        _log(f"Khoá nhạc cụ project: {spec['label']} — bảng nhân vật, ảnh bìa và các "
             "cảnh đều phải chơi đúng nhạc cụ này.")
    companion_mode = _companion_mode(idea)
    if companion_mode == "pet":
        _log("Thể loại lofi → bạn đồng hành (ảnh 2) bắt buộc là thú cưng thật, "
             "không linh thú.")
    user_msg = _build_user_prompt(idea, title, keywords, scene_count,
                                  aspect_ratio, style, instrument_spec=spec,
                                  companion_mode=companion_mode,
                                  thumbnail_directive=thumbnail_directive)
    user_msg += ("\nSHARED CREATIVE BRIEF (reference data; the user's specific "
                 "requirements take priority over these palette/lighting "
                 "suggestions):\n" + creative_brief)
    sys_msg = _system_for(style)

    # Thử lại _MAX_ATTEMPTS lần: model thỉnh thoảng trả JSON hỏng/cắt cụt/thiếu
    # key một cách ngẫu nhiên — 1 lần hỏng không phải lỗi cấu hình. Chỉ trả None
    # (→ RuntimeError ở service) sau khi đã thử hết.
    last_reason = "không rõ"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                # Đủ rộng cho hồ sơ 2 nhân vật + 40 prompt cảnh (90-140 từ tiếng
                # Việt mỗi cảnh); tránh JSON bị cắt cụt (finish_reason=length)
                # → thiếu key. Cần model có giới hạn output lớn (vd gpt-4.1:
                # 32768) khi sinh đủ 40 cảnh trong một lượt.
                max_tokens=32000,
                # BẮT model trả JSON hợp lệ (không prose/markdown). Không có cờ
                # này, gpt-4.1 qua proxy THỈNH THOẢNG trả kèm văn xuôi → parse
                # hỏng "không phải JSON hợp lệ" ngẫu nhiên (gặp cả khi count nhỏ).
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = resp.choices[0].message.content or ""
            finish = getattr(resp.choices[0], "finish_reason", None)
        except Exception as e:
            last_reason = f"gọi OpenAI thất bại ({type(e).__name__}: {e})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        if finish == "length":
            last_reason = "JSON bị cắt cụt (finish_reason=length)"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        data = _extract_json(text)
        if not isinstance(data, dict):
            last_reason = "trả về không phải JSON hợp lệ"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        # Chân trần → Gemini chặn ngầm bảng nhân vật («I encountered an error…»).
        data = sanitize_data(data)
        bad = non_english_fields(data)
        if bad:
            last_reason = f"còn chữ không phải tiếng Anh ở {', '.join(bad[:4])}"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        # HAI KHỐI DANH TÍNH là "nguồn sự thật" cho cả bộ: character_sheet dán
        # vào ảnh 0/1, pet_sheet dán vào ảnh 0/2, và cả hai dán vào MỌI prompt
        # cảnh. Không để model tự paraphrase ngoại hình ở từng cảnh (paraphrase
        # khác nhau → nhân vật lệch giữa các clip).
        raw_sheet = _text(data.get("character_sheet")) or _text(data.get("character_bible"))
        pet_sheet = _text(data.get("pet_sheet"))
        # Bảng nhân vật CHỈ vẽ người: bỏ mọi câu nhắc nhạc cụ / đồ vật khỏi khối
        # danh tính (đây cũng là khoá continuity dán vào ảnh 0/1 + mọi cảnh).
        sheet = _strip_objects(raw_sheet, spec)
        if len(sheet) < 20 or len(pet_sheet) < 20:
            last_reason = "thiếu character_sheet hoặc pet_sheet"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        palette = _str_list(data.get("palette"), 3, 8)
        main_insets, inst_from_insets = _split_main_insets(
            _str_list(data.get("main_insets"), 4, 10), spec)
        pet_insets = _str_list(data.get("pet_insets"), 4, 10)
        if not palette or len(main_insets) < 3 or not pet_insets:
            last_reason = "thiếu palette / main_insets / pet_insets"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        main_outfit = [x for x in (_strip_objects(o, spec) for o in
                                   _str_list(data.get("main_outfit"), 2, 8)) if x]
        inst_detail = _strip_objects(_text(data.get("instrument_detail")) or inst_from_insets,
                                     spec, instruments=False)
        if spec and inst_detail and not music_spec.mentions_instrument(inst_detail, spec):
            inst_detail = ""        # AI tả nhầm nhạc cụ khác → chỉ dùng mô tả chuẩn
        main_expr = (_str_list(data.get("main_expressions"), 2, 6)
                     or ["NEUTRAL — serene, composed look",
                         "CALM — half-closed eyes, faint smile",
                         "DETERMINED — steady, resolute gaze",
                         "JOYFUL — gentle smile, crescent eyes"])
        pet_expr = (_str_list(data.get("pet_expressions"), 2, 6)
                    or ["NEUTRAL — relaxed", "CALM — eyes half closed",
                        "ALERT — ears up, eyes wide", "PLAYFUL — cheerful"])

        thumb = _text(data.get("thumbnail"))
        if len(thumb) < 30:
            last_reason = "thiếu prompt ảnh bìa (thumbnail)"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        if _instrument_misuse(thumb, spec):
            last_reason = (f"ảnh bìa tả {spec['label']} sai thực tế (cầm/bê/đặt trên "
                           "bàn) — phải ngồi chơi")
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — viết lại…")
            continue

        pet_name = _text(data.get("pet_name"))
        pet_traits = _str_list(data.get("pet_traits"), 1, 6)
        pet_subject = _text(data.get("pet_subject"))
        pet_text = " ".join([pet_name, pet_sheet, pet_subject, *pet_insets, *pet_traits])
        spirit = _is_spirit(_text(data.get("pet_kind")), pet_text)
        if companion_mode == "pet" and (spirit or _SPIRIT_WORDS.search(pet_text)):
            hit = _SPIRIT_WORDS.search(pet_text)
            last_reason = ("lofi: bạn đồng hành phải là thú cưng thật, AI lại tả "
                           f"linh thú{f' ({hit.group(0)})' if hit else ''}")
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — viết lại…")
            continue
        companion = "CREATURE" if spirit else "PET"
        pet_name = pet_name or f"companion {companion.lower()}"
        # Khối ĐỐI TƯỢNG = mô tả tạo hình chi tiết + khối danh tính cô đọng
        # (character_sheet) — khối này cũng đứng đầu MỌI prompt cảnh, nên ảnh và
        # clip tả cùng một nhân vật bằng cùng một câu chữ.
        main_subject = _strip_objects(_text(data.get("main_subject")), spec)
        # Ảnh bìa + mọi cảnh: khối danh tính + dòng NHẠC CỤ đúng thực tế (code viết).
        ident = (_identity_with_instrument(sheet, spec) if spec else
                 f"{sheet}\nINSTRUMENT: {inst_detail}" if inst_detail else sheet)
        out = {
            "1": _model_sheet_prompt(
                title_en="MAIN CHARACTER SHEET",
                subject=f"{main_subject}\n{sheet}" if main_subject else sheet,
                insets=main_insets, palette=palette,
                scale_note=(_text(data.get("main_scale"))
                            or "state the character's height and head-count proportion"),
                expressions=main_expr, aspect_ratio=aspect_ratio,
                instrument_spec=spec, kind="main", outfit_items=main_outfit,
                instrument_detail=inst_detail),
            "2": _model_sheet_prompt(
                title_en=f"COMPANION {companion} SHEET",
                subject=f"{pet_subject}\n{pet_sheet}" if pet_subject else pet_sheet,
                insets=pet_insets, palette=palette,
                scale_note=(_text(data.get("pet_scale"))
                            or "SIZE MAP: compare the human's height with the companion"),
                expressions=pet_expr, aspect_ratio=aspect_ratio, kind="companion",
                extra_sections=([
                    "CHARACTER TRAITS — a separate small box, uppercase labels, short list: "
                    + "; ".join(pet_traits)
                ] if pet_traits else None)),
            "0": _with_character_lock(f"{ident}\nCOMPANION {companion} ({pet_name}): {pet_sheet}",
                                      thumb),
        }

        # Caller bổ sung prompt cảnh riêng nếu thiếu; chưa đủ thì không chạy media.
        raw_scenes = _playing_first(data.get("scenes"), spec)
        motions = _scene_dict(raw_scenes, scene_count, ident, pet_sheet) or {}
        misuse = _misuse_count(_scene_bodies(raw_scenes), spec)
        report = _activity_report(raw_scenes, spec, scene_count)
        if motions and (report["off"] or misuse):
            # Hồ sơ nhân vật dùng được; chỉ bộ cảnh chưa đạt → để workflow viết
            # lại bộ cảnh (generate_scene_prompts có cùng kiểm tra).
            _log(f"{misuse} cảnh tả {spec['label']} sai thực tế — sẽ viết lại bộ cảnh."
                 if misuse else f"Bộ cảnh chưa đủ đa dạng ({report['problem']}) — sẽ "
                 "viết lại bộ cảnh.")
            motions = {}
        if len(motions) == scene_count:
            _log(f"Đã sinh 3 prompt ảnh (2 bảng phân tích nhân vật + ảnh bìa) "
                 f"và {scene_count} prompt cảnh ({model}, lần {attempt}).")
        else:
            _log(f"Đã sinh 3 prompt ảnh ({model}, lần {attempt}); prompt cảnh "
                 f"{len(motions)}/{scene_count} — workflow sẽ sinh bổ sung.")
        if metadata_out is not None:
            metadata_out.update(
                character_sheet=sheet, pet_sheet=pet_sheet, pet_name=pet_name,
                pet_kind="spirit creature" if spirit else "pet", palette=palette,
                character_bible=_text(data.get("character_bible")),
                settings=_settings_of(raw_scenes))
        return out, motions

    _log(f"AI không sinh được prompt sau {_MAX_ATTEMPTS} lần thử "
         f"(lý do cuối: {last_reason}) — dừng trước bước tạo media.")
    return None
