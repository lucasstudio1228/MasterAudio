"""
video/music_spec.py — Nguồn sự thật DUY NHẤT về NHẠC CỤ + THỂ LOẠI cho mọi prompt.

- INSTRUMENTS: danh mục nhạc cụ cho form New Project. Mỗi nhạc cụ có mô tả HÌNH
  (để Gemini vẽ đúng trong bảng phân tích nhân vật), TƯ THẾ CHƠI (piano = ngồi
  trước đàn, không thể "cầm" khi đứng — lý do Gemini từng tự đổi sang guitar),
  chuyển động khi chơi (cho Veo) và mô tả âm sắc cho Suno.
- GENRES: hồ sơ thể loại (dải BPM, có trống/bass không, màu đệm, mix, từ khoá loại
  trừ) để AI viết Styles Suno theo FORM CHUẨN (xem SUNO_STYLE_FORM bên dưới).

Mọi chuỗi ở đây là TIẾNG ANH vì được dán thẳng vào prompt gửi model.
"""
from __future__ import annotations

import re
from typing import Optional


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ══════════════════════════════════════════════════════════════════
# NHẠC CỤ
# ══════════════════════════════════════════════════════════════════
# portable=False ⇒ nhạc cụ đặt cố định (piano, đàn tranh, harp lớn, bát hát…):
# turnaround đứng KHÔNG cầm đàn; bảng nhân vật thêm ô "PLAYING POSE" ngồi chơi.
INSTRUMENTS: list[dict] = [
    {"key": "piano", "label": "Piano", "vi": "Piano (dương cầm)",
     "aliases": ["piano", "grand piano", "upright piano", "felt piano", "dương cầm"],
     "portable": False,
     "visual": "a full-size acoustic upright piano (about 1.2 m tall and 1.5 m wide) standing on the floor, full 88-key black-and-white keyboard, with a separate wooden piano bench",
     "placement": "it is never on a desk, a table or a lap and is never miniature",
     "pose": "seated on the piano bench facing the keyboard, back upright, feet on the floor near the pedals, both hands resting on the keys",
     "motion": "fingers pressing the piano keys slowly and softly, wrists relaxed, shoulders breathing gently",
     "suno": "solo acoustic piano (warm felt / soft-hammer tone)",
     "short": "piano", "chords": True},
    {"key": "bamboo_flute", "label": "Bamboo Flute", "vi": "Sáo trúc",
     "aliases": ["bamboo flute", "dizi", "xiao", "sáo trúc", "sao truc", "flute"],
     "portable": True,
     "visual": "a slender natural bamboo flute with finger holes and thin silk bindings",
     "pose": "holding the bamboo flute horizontally at the lips with both hands, fingers over the holes",
     "motion": "fingertips lifting and covering the flute holes slowly, shoulders and chest rising very gently, clear air around the flute",
     "suno": "solo Chinese bamboo flute (dizi / xiao, breathy airy tone)",
     "short": "bamboo flute", "chords": False},
    {"key": "guzheng", "label": "Guzheng", "vi": "Đàn tranh (Guzheng)",
     "aliases": ["guzheng", "zither", "đàn tranh", "dan tranh", "koto"],
     "portable": False,
     "visual": "a long wooden guzheng zither lying flat on a low stand, many silk strings over movable bridges",
     "pose": "seated in front of the guzheng, both hands over the strings, right fingers plucking, left hand pressing beyond the bridges",
     "motion": "fingers plucking the zither strings delicately, left hand gently bending a string, strings vibrating softly",
     "suno": "solo guzheng (Chinese zither, plucked glissandi and gentle string bends)",
     "short": "guzheng", "chords": True},
    {"key": "erhu", "label": "Erhu", "vi": "Đàn nhị (Erhu)",
     "aliases": ["erhu", "đàn nhị", "dan nhi", "two string fiddle"],
     "portable": True,
     "visual": "an erhu two-string fiddle with a small hexagonal snakeskin-covered sound box, long slim neck and horsehair bow",
     "pose": "seated, the erhu resting upright on the left thigh, right hand drawing the bow horizontally",
     "motion": "the bow drawing slowly side to side, left fingers pressing the strings with gentle vibrato",
     "suno": "solo erhu (Chinese two-string fiddle, expressive slides and soft vibrato)",
     "short": "erhu", "chords": False, "own": ('strings',)},
    {"key": "pipa", "label": "Pipa", "vi": "Đàn tỳ bà (Pipa)",
     "aliases": ["pipa", "tỳ bà", "ty ba", "lute"],
     "portable": True,
     "visual": "a pear-shaped wooden pipa lute with a short bent neck and four strings",
     "pose": "seated, holding the pipa upright on the lap, right fingers plucking",
     "motion": "right fingers plucking in soft tremolo, left fingers pressing frets gently",
     "suno": "solo pipa (Chinese lute, soft plucked tremolo)",
     "short": "pipa", "chords": True},
    {"key": "shakuhachi", "label": "Shakuhachi", "vi": "Sáo Shakuhachi",
     "aliases": ["shakuhachi"],
     "portable": True,
     "visual": "a thick end-blown bamboo shakuhachi flute with a root end",
     "pose": "holding the shakuhachi vertically at the lips with both hands",
     "motion": "fingers lifting from the shakuhachi holes slowly, head tilting very slightly with the breath",
     "suno": "solo shakuhachi (Japanese end-blown bamboo flute, breathy meditative tone)",
     "short": "shakuhachi", "chords": False},
    {"key": "guitar", "label": "Acoustic Guitar", "vi": "Guitar mộc",
     "aliases": ["acoustic guitar", "guitar", "nylon guitar", "classical guitar"],
     "portable": True,
     "visual": "a wooden acoustic guitar with a round sound hole and six strings",
     "pose": "seated, the guitar resting on the lap, right hand fingerpicking over the sound hole",
     "motion": "right fingers fingerpicking slowly, left fingers holding a chord shape, strings vibrating softly",
     "suno": "solo fingerpicked acoustic guitar (warm nylon-string tone)",
     "short": "guitar", "chords": True},
    {"key": "harp", "label": "Harp", "vi": "Đàn hạc (Harp)",
     "aliases": ["harp", "đàn hạc", "dan hac", "celtic harp"],
     "portable": False,
     "visual": "a tall wooden concert / Celtic harp with a curved neck and many vertical strings",
     "placement": "it stands on the floor and is never on a desk or a table",
     "pose": "seated beside the harp, the harp leaning on the right shoulder, both hands reaching into the strings",
     "motion": "both hands plucking the harp strings in slow gentle arpeggios, strings shimmering softly",
     "suno": "solo concert harp (gentle arpeggios, shimmering glissandi)",
     "short": "harp", "chords": True},
    {"key": "kalimba", "label": "Kalimba", "vi": "Kalimba",
     "aliases": ["kalimba", "thumb piano", "mbira"],
     "portable": True,
     "visual": "a small wooden kalimba thumb piano with a row of metal tines",
     "pose": "holding the kalimba in both hands at chest height, thumbs on the tines",
     "motion": "thumbs flicking the kalimba tines lightly one by one",
     "suno": "solo kalimba (bright soft metal tines, music-box-like)",
     "short": "kalimba", "chords": True, "own": ('bells', 'mallet')},
    {"key": "handpan", "label": "Handpan", "vi": "Handpan / Hang drum",
     "aliases": ["handpan", "hang drum", "hang", "steel tongue drum", "tongue drum"],
     "portable": False,
     "visual": "a round UFO-shaped steel handpan with hammered tone fields",
     "pose": "seated cross-legged, the handpan resting on the lap, fingertips tapping the tone fields",
     "motion": "fingertips tapping the handpan tone fields softly, hands moving in slow arcs",
     "suno": "solo handpan (hang drum, resonant steel tones, soft finger strokes)",
     "short": "handpan", "chords": True, "own": ('bells',)},
    {"key": "singing_bowls", "label": "Singing Bowls", "vi": "Chuông xoay (Singing bowls)",
     "aliases": ["singing bowl", "singing bowls", "tibetan bowl", "chuông xoay", "crystal bowl"],
     "portable": False,
     "visual": "a set of bronze Tibetan singing bowls of different sizes on small cushions, with a wooden mallet",
     "pose": "seated cross-legged before the singing bowls, circling the wooden mallet around a bowl rim",
     "motion": "the mallet circling the bowl rim slowly and steadily, a soft shimmer in the air",
     "suno": "Tibetan singing bowls (long resonant overtones, soft strikes and rim drones)",
     "short": "singing bowls", "chords": False},
    {"key": "cello", "label": "Cello", "vi": "Cello",
     "aliases": ["cello", "violoncello"],
     "portable": False,
     "visual": "a wooden cello standing on its endpin with a long bow",
     "placement": "it stands on its endpin on the floor and is never on a desk or a table",
     "pose": "seated on a chair, the cello between the knees, right arm drawing the bow",
     "motion": "the bow drawing long slow strokes, left fingers with gentle vibrato",
     "suno": "solo cello (warm legato bowing, deep resonant tone)",
     "short": "cello", "chords": False, "own": ('strings',)},
    {"key": "violin", "label": "Violin", "vi": "Violin",
     "aliases": ["violin", "fiddle"],
     "portable": True,
     "visual": "a wooden violin with a bow",
     "pose": "standing or seated, the violin tucked under the chin, right arm drawing the bow",
     "motion": "the bow gliding slowly across the strings, left fingers with soft vibrato",
     "suno": "solo violin (soft legato, gentle vibrato)",
     "short": "violin", "chords": False, "own": ('strings',)},
    {"key": "music_box", "label": "Music Box", "vi": "Hộp nhạc (Music box)",
     "aliases": ["music box"],
     "portable": True,
     "visual": "a small open wooden music box with a visible metal comb and turning cylinder",
     "pose": "holding the open music box in both palms, turning its small crank",
     "motion": "one hand turning the small crank slowly, the cylinder rotating",
     "suno": "music box (delicate metal comb melody, lullaby tone)",
     "short": "music box", "chords": True, "own": ('bells', 'mallet')},
]

# Nhạc cụ "khác" người dùng tự gõ → vẫn khoá đúng tên đó, dùng mô tả chung.
_GENERIC = {
    "portable": True,
    "visual": "the {name} drawn accurately as the real instrument",
    "pose": "playing the {name} in its correct natural playing position",
    "motion": "hands playing the {name} slowly and gently with correct technique",
    "suno": "solo {name}",
    "short": "{name}",
    "chords": False,
}


def instrument_options() -> list[dict]:
    """Danh sách cho dropdown New Project (key/label/vi)."""
    return [{"key": i["key"], "label": i["label"], "vi": i["vi"],
             "portable": i["portable"]} for i in INSTRUMENTS]


def find_instrument(name: str) -> Optional[dict]:
    """Tra nhạc cụ theo key/label/alias (không phân biệt hoa thường). None nếu
    rỗng. Tên lạ → spec chung giữ NGUYÊN tên người dùng chọn."""
    n = _norm(name)
    if not n:
        return None
    for spec in INSTRUMENTS:
        names = [spec["key"].replace("_", " "), spec["label"], *spec["aliases"]]
        if any(_norm(x) == n for x in names):
            return spec
    # Khớp chứa (vd "Lofi Piano", "Chinese Bamboo Flute") — ưu tiên alias dài nhất.
    best, best_len = None, 0
    for spec in INSTRUMENTS:
        for alias in [spec["label"], *spec["aliases"]]:
            a = _norm(alias)
            if a and re.search(rf"\b{re.escape(a)}\b", n) and len(a) > best_len:
                best, best_len = spec, len(a)
    if best:
        return best
    label = re.sub(r"\s+", " ", name.strip())
    return {"key": "custom", "label": label, "vi": label, "aliases": [label.lower()],
            **{k: (v.format(name=label if k != "short" else label.lower())
                   if isinstance(v, str) else v)
               for k, v in _GENERIC.items()}}


def same_instrument(a: str, b: str) -> bool:
    """Hai tên (vd 'Piano' / 'piano solo' / 'Dương cầm') có cùng một nhạc cụ?"""
    fa, fb = find_instrument(a), find_instrument(b)
    if not fa or not fb:
        return False
    if fa["key"] != "custom" or fb["key"] != "custom":
        return fa["key"] == fb["key"]
    return _norm(a) == _norm(b)


def resolve_instrument(name: str, api_config=None, log=None) -> Optional[dict]:
    """find_instrument + dịch tên nhạc cụ tự gõ (tiếng Việt) sang tiếng Anh để
    prompt 100% tiếng Anh. Dịch lỗi → bỏ khoá nhạc cụ (không gửi chữ Việt)."""
    spec = find_instrument(name)
    if spec and spec["key"] == "custom" and not spec["label"].isascii():
        try:
            from .translate import to_english
            spec = find_instrument(to_english(spec["label"], api_config, log))
        except Exception:      # noqa: BLE001 — thiếu API/lỗi dịch
            return None
        if spec and not spec["label"].isascii():
            return None
    return spec


def instrument_terms(spec: Optional[dict]) -> list[str]:
    """Các từ để KIỂM TRA prompt có nhắc đúng nhạc cụ (chuẩn hoá)."""
    if not spec:
        return []
    terms = {_norm(a) for a in [spec["label"], *spec.get("aliases", [])]
             if a.isascii()}
    # "acoustic guitar" → chấp nhận cả "guitar"; "bamboo flute" → "flute".
    last = _norm(spec["label"]).split(" ")[-1]
    if len(last) >= 4:
        terms.add(last)
    return sorted(t for t in terms if t)


def mentions_instrument(text: str, spec: Optional[dict]) -> bool:
    terms = instrument_terms(spec)
    if not terms:
        return True             # không có gì để đối chiếu
    hay = f" {_norm(text)} "
    return any(f" {t} " in hay or f" {t}s " in hay for t in terms)


# Nhạc cụ có CAO ĐỘ hay "lọt" vào Styles / biến thể từng bài dù không phải lead.
# Lỗi 2026-10-04: project Guitar + Lofi vẫn ra piano vì màu đệm lofi mặc định có
# "soft Rhodes chord pads" (Rhodes = piano điện). Đo PANNs 2026-10-08: project sáo
# trúc lẫn harp/guitar/piano + chuông/bát (màu đệm "temple bell", "singing-bowl").
MELODIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "piano": ("piano", "pianos", "keyboard", "keyboards"),
    # Piano ĐIỆN tách riêng: project Piano (dương cầm) không được thêm lớp Rhodes.
    "e_piano": ("rhodes", "wurlitzer", "electric piano", "e piano"),
    "guitar": ("guitar", "guitars"),
    "harp": ("harp", "harps"),
    "zither": ("guzheng", "guqin", "zither", "zithers", "koto"),
    "pipa": ("pipa", "lute", "shamisen"),
    "erhu": ("erhu",),
    "cello": ("cello", "cellos"),
    "violin": ("violin", "violins", "fiddle"),
    "strings": ("string section", "string ensemble", "string pad", "string pads",
                "orchestra", "orchestral"),
    "flute": ("flute", "flutes", "dizi", "xiao", "shakuhachi"),
    "saxophone": ("saxophone", "sax"),
    "ukulele": ("ukulele",),
    "synth": ("synth lead", "lead synth", "synth melody", "synth arpeggio", "arpeggiator"),
    "mallet": ("marimba", "vibraphone", "glockenspiel", "celesta", "xylophone"),
    "bells": ("bell", "bells", "singing bowl", "singing bowls", "chime", "chimes",
              "gong", "gongs"),
    "kalimba": ("kalimba", "mbira"),
    "handpan": ("handpan", "hang drum"),
    "music_box": ("music box",),
}

# Từ cho Exclude styles theo họ (chỉ các họ hay lọt nhất — Exclude ngắn, trúng đích).
_FAMILY_EXCLUDES: dict[str, tuple[str, ...]] = {
    "piano": ("piano",),
    "e_piano": ("electric piano",),
    "guitar": ("guitar",),
    "harp": ("harp",),
    "zither": ("guzheng",),
    "strings": ("string section",),
    "synth": ("synth lead",),
    "bells": ("bells",),
    "flute": ("flute",),
    "erhu": ("erhu",),
    "saxophone": ("saxophone",),
}


def _families_in(text: str) -> set[str]:
    # "bell-like tone", "harp-like shimmer" mô tả ÂM SẮC, không phải nhạc cụ thứ hai.
    hay = re.sub(r"\b[a-z]+ like\b", " ", f" {_norm(text)} ")
    return {fam for fam, words in MELODIC_FAMILIES.items()
            if any(f" {w} " in hay for w in words)}


def own_families(spec: Optional[dict]) -> set[str]:
    """Họ nhạc cụ của chính lead (vd erhu = erhu + violin/fiddle + strings)."""
    if not spec:
        return set()
    own = _families_in(f"{spec['label']} {spec.get('suno', '')}") | set(spec.get("own", ()))
    return own | {"piano"} if "e_piano" in own else own


def competing_instruments(text: str, spec: Optional[dict]) -> list[str]:
    """Các từ chỉ nhạc cụ có cao độ KHÁC lead xuất hiện trong text (không có
    spec → không kiểm)."""
    if not spec:
        return []
    own = own_families(spec)
    hay = re.sub(r"\b[a-z]+ like\b", " ", f" {_norm(text)} ")
    return [w for fam, words in MELODIC_FAMILIES.items() if fam not in own
            for w in words if f" {w} " in hay]


def plays_chords(spec: Optional[dict]) -> bool:
    """Lead tự chơi được hợp âm? Không có spec (AI tự chọn lead) → coi như có.
    Nhạc cụ ĐƠN ÂM (sáo, nhị, cello…) mà nhận "Chord progression" thì Suno buộc
    phải thêm đàn đệm (piano/guitar/harp) — nguyên nhân lẫn nhạc cụ số 1."""
    return True if not spec else bool(spec.get("chords", True))


def lead_short(spec: Optional[dict]) -> str:
    """Tên ngắn để nhắc lại nhạc cụ chính trong Styles ("the bamboo flute …")."""
    if not spec:
        return "lead"
    return spec.get("short") or _norm(spec["label"]) or "lead"


def accents_for(accents, spec: Optional[dict]) -> list[str]:
    """Màu đệm hợp lệ cho lead: bỏ mục có nhạc cụ giai điệu khác (Rhodes cho
    project guitar, harp/cello cho project sáo…)."""
    kept = [a for a in accents if not competing_instruments(a, spec)]
    return kept or ["a soft warm ambient pad"]


def leak_exclusions(spec: Optional[dict]) -> list[str]:
    """Exclude styles chặn các họ nhạc cụ hay lọt (piano, guitar, harp, đàn tranh,
    dàn dây, synth lead, chuông/bát, sáo, nhị, sax) — TRỪ họ của chính lead.
    Không có spec → chỉ chặn piano (Suno, nhất là lofi, rất hay tự chèn)."""
    if not spec:
        return []
    own = own_families(spec)
    return [t for fam, terms in _FAMILY_EXCLUDES.items() if fam not in own for t in terms]


def other_instruments(spec: Optional[dict], limit: int = 6) -> list[str]:
    """Nhạc cụ KHÁC hay bị model vẽ nhầm — đưa vào câu cấm cho ảnh Gemini."""
    common = ["guitar", "violin", "flute", "ukulele", "piano", "harp", "cello", "saxophone"]
    mine = set(instrument_terms(spec))
    return [c for c in common if c not in mine
            and not any(c in t.split(" ") for t in mine)][:limit]


def instrument_from_context(idea: str) -> str:
    """Rút 'instrument' từ chuỗi JSON ngữ cảnh project (prompt_workflow)."""
    import json
    try:
        data = json.loads(idea or "")
    except (ValueError, TypeError):
        return ""
    return (data.get("instrument") or "").strip() if isinstance(data, dict) else ""


def visual_instrument_rules(spec: Optional[dict]) -> str:
    """Khối luật nhạc cụ cho lời nhắc AI viết prompt ảnh/cảnh (tiếng Anh)."""
    if not spec:
        return ""
    label = spec["label"]
    carry = ("" if spec["portable"] else
             f" REAL-WORLD LOGIC: a {label.lower()} is a large fixed instrument — the "
             f"character NEVER holds, lifts, hugs or carries it in their hands or arms; "
             f"{spec.get('placement') or 'it stays in its natural playing position'}. "
             f"In standing poses the character is empty-handed and the {label.lower()} "
             f"stands nearby; whenever they play, they are {spec['pose']}.")
    return (
        f"FIXED LEAD INSTRUMENT (chosen by the user — NON-NEGOTIABLE): {label.upper()} — "
        f"{spec['visual']}. The main character is a {label.lower()} player. "
        f"Correct playing position: {spec['pose']}. Playing motion: {spec['motion']}.{carry} "
        f"Whenever an instrument appears (character sheet, thumbnail, the scenes where the "
        f"character plays) it is ONLY this one; never swap it for another instrument "
        f"(no {', '.join(other_instruments(spec))}).")


# ══════════════════════════════════════════════════════════════════
# THỂ LOẠI (music_style) → thông số Suno
# ══════════════════════════════════════════════════════════════════
GENRES: list[dict] = [
    {"key": "lofi", "match": ["lofi", "lo fi", "lo-fi", "chillhop", "study"],
     "label": "Lofi hip hop / chillhop",
     "bpm": (68, 88), "drums": "soft dusty boom-bap drums, brushed snare, light swing",
     "bass": "warm rounded sub bass", "beatless": False,
     "scales": "major 7th / minor 7th jazz harmony (e.g. Fmaj7, Dm9, Bbmaj7)",
     "accents": ["soft vinyl crackle", "gentle rain on a window", "a faint distant city hum",
                 "warm tape hiss", "a muted round bass line", "light brushed hi-hat swing",
                 "soft room tone", "a quiet night breeze"],
     "mix": "tape saturation, gentle low-pass warmth, side-chained softness, wide but cozy stereo",
     "exclude": ["heavy 808", "trap hi-hats", "EDM build-ups"]},
    {"key": "zen", "match": ["zen", "chinese", "oriental", "asian", "tao", "temple", "guqin"],
     "label": "Zen meditation",
     "bpm": (50, 66), "drums": "", "bass": "", "beatless": True,
     "scales": "major / minor pentatonic",
     "accents": ["wind through bamboo leaves", "a quiet mountain stream", "soft rain on temple eaves",
                 "a low warm drone pad", "distant birdsong in the mist", "soft wind over a still lake",
                 "faint rustling leaves", "a slow breathing mountain wind"],
     "mix": "long hall reverb, airy high end, wide stereo, natural room ambience",
     "exclude": ["drums", "percussion", "beat"]},
    {"key": "sleep", "match": ["sleep", "deep sleep", "lullaby", "insomnia"],
     "label": "Deep sleep ambient",
     "bpm": (44, 60), "drums": "", "bass": "", "beatless": True,
     "scales": "major or Lydian, very consonant, slow harmonic rhythm",
     "accents": ["a slow breathing ocean tide", "a soft warm drone pad", "faint night crickets",
                 "gentle rain on leaves", "a faint distant night breeze", "soft wind through pines"],
     "mix": "dark soft high end, long lush reverb, no sharp transients, very low dynamics",
     "exclude": ["drums", "percussion", "beat", "sudden loud notes"]},
    {"key": "ambient", "match": ["ambient", "space", "drone", "atmospheric"],
     "label": "Ambient soundscape",
     "bpm": (50, 70), "drums": "", "bass": "", "beatless": True,
     "scales": "major / Lydian / Dorian, slow evolving harmony",
     "accents": ["a slow evolving warm pad", "a distant shimmer reverb", "soft airy noise textures",
                 "a low warm drone", "faint field-recording wind", "gentle tape-delay echoes"],
     "mix": "deep reverb, wide stereo field, smooth low-mids, soft high shelf",
     "exclude": ["drums", "percussion", "beat"]},
    {"key": "spa", "match": ["spa", "healing", "relax", "yoga", "massage", "wellness", "reiki"],
     "label": "Healing spa relaxation",
     "bpm": (55, 72), "drums": "", "bass": "", "beatless": True,
     "scales": "major / major pentatonic, warm consonant harmony",
     "accents": ["a flowing water stream", "gentle birdsong", "a soft warm drone pad",
                 "soft wind through palms", "a slow ocean tide", "light rain on leaves"],
     "mix": "warm, soft and airy, gentle reverb, no harsh frequencies",
     "exclude": ["drums", "percussion", "beat"]},
    {"key": "meditation", "match": ["meditation", "mindful", "calm", "peace"],
     "label": "Meditation",
     "bpm": (50, 68), "drums": "", "bass": "", "beatless": True,
     "scales": "pentatonic, Dorian or Lydian, slow harmonic rhythm",
     "accents": ["a slow warm drone pad", "a quiet flowing stream", "soft wind through pines",
                 "faint night crickets", "gentle rain on leaves", "distant birdsong at dawn"],
     "mix": "long reverb tail, wide stereo, warm low-mids, soft high end",
     "exclude": ["drums", "percussion", "beat"]},
]
_DEFAULT_GENRE = "meditation"


def find_genre(music_style: str) -> dict:
    n = f" {_norm(music_style)} "
    for g in GENRES:
        if any(f" {_norm(m)} " in n for m in g["match"]):
            return g
    return next(g for g in GENRES if g["key"] == _DEFAULT_GENRE)


# ══════════════════════════════════════════════════════════════════
# FORM CHUẨN STYLES SUNO
# ══════════════════════════════════════════════════════════════════
# Thứ tự cố định — code ghép các trường AI trả về theo đúng form này (AI không
# viết đoạn văn tự do nữa). Form v2 (2026-10-08): nhạc cụ chính ĐỨNG ĐẦU và được
# nhắc lại bằng tên ngắn; câu khẳng định (Styles) — phủ định để ở Exclude. "NN BPM"
# + "Key:" + nhãn hợp âm được suno_prompt dùng để biến tấu tempo/giọng từng Create.
SUNO_STYLE_FORM = (
    "Lead: {lead}. {genre}, {mood}. The {short} alone carries the melody: {melody}. "
    "Key: {key}. {harmony}. Tempo: {bpm} BPM, {groove}. Background, very quiet: "
    "{support}. Structure: {structure}. Mix: {mix}. Purely instrumental, no vocals."
)
# Lead chơi được hợp âm (piano, guitar, harp, đàn tranh…) → chính nó giữ hoà âm.
HARMONY_CHORDS = "Harmony on the {short}: {progression}"
# Lead ĐƠN ÂM (sáo, nhị, cello…) → KHÔNG gửi vòng hợp âm (Suno sẽ tự thêm đàn đệm).
HARMONY_DRONE = "Harmony: a single faint low drone beneath the {short}"

# Thông số UI Suno do TOOL đặt cố định (AI không cần/không được đổi) — ghi vào
# lời nhắc để AI hiểu Styles là kênh DUY NHẤT điều khiển âm nhạc.
SUNO_FIXED_PARAMS = (
    "Suno settings fixed by the tool: model v6, Advanced mode, Lyrics without sung words "
    "(instrumental), Duration Auto (target 3-5 min songs), Max Mode Off, Variety Off, "
    "Style Influence high and Weirdness low (stick closely to the Styles text), Styles "
    "box max 1000 characters, Exclude styles box filled by the tool. Your Styles text is "
    "the ONLY control over the music, so every field must be concrete and audible and "
    "the lead instrument must dominate every moment.")


def _competitor_names(spec: dict) -> list[str]:
    own = own_families(spec)
    names = {"piano": "piano / keys", "e_piano": "Rhodes / electric piano",
             "zither": "guzheng / guqin",
             "strings": "string section / orchestra", "synth": "synth lead",
             "bells": "bells / singing bowls / chimes / gongs", "music_box": "music box"}
    return [names.get(f, f) for f in MELODIC_FAMILIES if f not in own]


def suno_rules(spec: Optional[dict], genre: dict) -> str:
    """Khối thông số nhạc cụ + thể loại gửi kèm cho AI viết Styles."""
    lo, hi = genre["bpm"]
    rhythm = (f"BEATLESS — no drums, no percussion, no beat; groove = free-flowing rubato"
              if genre["beatless"] else
              f"light groove allowed: {genre['drums']}; bass: {genre['bass']}")
    lead = (f"LEAD INSTRUMENT (fixed by the user — must be the solo voice and named in "
            f"'lead'): {spec['suno']}. It is the ONLY melodic instrument: do NOT add "
            f"any other melodic instrument in any field "
            f"(no {', '.join(_competitor_names(spec))})." if spec else
            "LEAD INSTRUMENT: infer ONE fitting lead from the title/idea.")
    if spec and not plays_chords(spec):
        lead += (f" The {lead_short(spec)} is MONOPHONIC (one note at a time): it cannot "
                 f"play chords, so return 'progression' as an EMPTY array — the tool adds "
                 f"only a faint low drone, never a chord instrument.")
    elif spec:
        lead += f" The {lead_short(spec)} plays BOTH the melody and its own chords."
    lead += (" 'support' = nature sounds / ambience / at most ONE soft drone pad"
             + ("" if genre["beatless"] else " / the genre's soft drums and bass")
             + " — never a pitched instrument (no bells, bowls, chimes, plucked or bowed "
             "strings, keys).")
    textures = accents_for(genre["accents"], spec)[:5]
    return (
        f"{lead}\n"
        f"GENRE PROFILE: {genre['label']}. Tempo range {lo}-{hi} BPM. Rhythm: {rhythm}. "
        f"Harmony: {genre['scales']}. Typical textures: {', '.join(textures)}. "
        f"Mix: {genre['mix']}.\n"
        f"{SUNO_FIXED_PARAMS}")
