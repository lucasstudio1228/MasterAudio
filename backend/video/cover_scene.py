"""
video/cover_scene.py — Hồ sơ prompt CHẾ ĐỘ 1 ẢNH của tool Cover (2026-10-29).

Video cover = ảnh bìa (0) → bảng phân tích nhân vật (1) + bảng tham chiếu bối
cảnh (2) vẽ TỪ ảnh bìa → clip 00 «ảnh bìa sống dậy» (intro), clip 01 cùng bối
cảnh lặp nhẹ, clip 02–05 = 4 góc quay nhân vật (nguyên liệu: 2 bảng).

Prompt tiếng Anh: ảnh bìa, intro, loop (AI hoặc mẫu) + 2 bảng + 4 góc quay (mẫu
cố định — bảng vẽ từ ảnh đính kèm nên không cần tả lại nhân vật).
AI viết nếu được (API key/model ở ⚙️ Settings); AI lỗi → mẫu dựng sẵn theo thể
loại + từ khoá mùa/giờ/nơi chốn trong tên/ý tưởng project, để chuỗi tự động
không bao giờ kẹt ở bước viết prompt.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Optional

# Phiên bản mẫu ảnh bìa (lưu trong prompts.json). 4 = nhân vật theo đúng giọng
# nam/nữ/song ca, đồng thời bối cảnh và đạo cụ theo genre (2026-11-07).
# Đổi số → «Làm lại từ đầu»
# viết lại hồ sơ; chạy tiếp (resume) vẫn giữ hồ sơ cũ.
COVER_REV = 4

# ── Mẫu dựng sẵn ────────────────────────────────────────────────
# Nơi chốn: (từ khoá, mô tả cảnh, chuyển động nền). Xét theo thứ tự.
_PLACES: list[tuple[tuple[str, ...], str, str]] = [
    (("biển", "bãi biển", "beach", "sea", "ocean", "đại dương"),
     "a quiet sandy beach at the water's edge, gentle waves rolling in, a wooden "
     "pier fading into the distance",
     "waves rolling softly onto the sand, sea breeze moving the grass on the dunes"),
    (("mưa", "rain", "rainy"),
     "a cozy cafe corner beside a large window streaked with raindrops, blurred "
     "city lights outside",
     "raindrops sliding down the glass, steam rising slowly from a cup, lights "
     "shimmering through the wet window"),
    (("phố", "city", "đô thị", "sài gòn", "hà nội", "hanoi", "saigon", "rooftop"),
     "a rooftop terrace above a glowing city skyline, string lights along the "
     "railing",
     "city lights twinkling, string lights swaying slightly in the breeze, clouds "
     "drifting across the sky"),
    (("rừng", "forest", "núi", "mountain", "đồi"),
     "a misty pine forest trail opening onto a mountain valley",
     "mist drifting between the trees, leaves trembling in the breeze, light rays "
     "shifting through the branches"),
    (("hồ", "lake", "sông", "river"),
     "a calm lakeside with a small wooden dock and reeds along the shore",
     "ripples spreading across the water, reeds swaying, reflections shimmering"),
    (("quê", "countryside", "đồng lúa", "rice field", "làng"),
     "a peaceful countryside rice field with a narrow dirt path and a lone tree",
     "rice stalks waving in the wind, clouds drifting slowly, birds far away"),
]

# Nơi chốn mặc định theo phong cách cover (genre_defaults).
_STYLE_PLACE = {
    "remix": _PLACES[2],
    "ballad": _PLACES[1],
    "acoustic": (
        (), "a warm wooden room with an acoustic guitar resting by a sunlit window, "
            "a small plant and a stack of vinyl records",
        "curtains moving gently, dust motes floating in the sunbeam, leaves of the "
        "plant swaying slightly"),
    "original": (
        (), "an empty street at the edge of town lined with warm shop lights",
        "lights flickering softly, a light breeze moving leaves along the pavement, "
        "clouds drifting overhead"),
}

# Fallback khi API không dùng được: vẫn phân biệt subgenre thay vì dồn mọi nhạc
# nhẹ vào quán cà phê và mọi remix lên rooftop.
_GENRE_PLACES = (
    (("jazz", "blue", "blues"),
     "an intimate vintage jazz lounge with a small velvet stage, brass details and warm table lamps",
     "soft stage haze drifting, warm practical lights breathing gently, curtains moving slightly"),
    (("r&b", "rnb", "soul"),
     "an elegant contemporary listening lounge with deep indigo light, sculptural seating and reflective glass",
     "slow neon reflections moving across the glass, sheer curtains swaying, subtle haze"),
    (("rock", "alternative", "pop rock"),
     "a cinematic rehearsal stage in a converted industrial warehouse with amplifiers and dramatic backlights",
     "backlights pulsing softly, light haze moving across the stage, cables and fabric shifting in the air"),
    (("lofi", "lo-fi", "lo fi"),
     "a cozy lived-in bedroom studio by a rain-speckled window, warm desk lamp, records and headphones",
     "rain sliding down the window, record turning slowly, warm lamp glow and curtains moving"),
    (("house", "vietmix", "remix", "edm"),
     "a sophisticated neon club terrace with a compact DJ booth overlooking the city",
     "city lights flickering rhythmically, subtle club haze, LED reflections moving across the controller"),
    (("pop",),
     "a colourful modern city terrace with graphic light panels and a clean cinematic skyline",
     "light panels shifting colour gently, clouds drifting, reflections moving across polished surfaces"),
)

_SEASONS = [
    (("mùa thu", "autumn", "fall"), "in autumn, golden and red leaves",
     "golden leaves falling slowly"),
    (("mùa xuân", "spring", "tết"), "in spring, soft blossoms on the branches",
     "petals drifting in the air"),
    (("mùa hè", "summer"), "on a warm summer day, lush green",
     "heat haze and bright sunlight flickering"),
    (("mùa đông", "winter", "snow", "giáng sinh", "christmas", "noel"),
     "in winter, a light layer of snow", "light snow falling"),
]

_TIMES = [
    (("đêm", "night", "midnight", "khuya"), "at night under a deep blue sky"),
    (("hoàng hôn", "sunset", "chiều", "dusk"), "at golden-hour sunset"),
    (("bình minh", "sunrise", "buổi sáng", "morning"), "in soft early-morning light"),
]

# Nhân vật chính bám theo giọng hát đã phân tích: nam, nữ hoặc song ca nam–nữ.
# Nhân vật là người HƯ CẤU, trưởng thành (~25 tuổi), nhìn thẳng camera, nửa
# người, là tâm điểm ảnh bìa; trang phục/bối cảnh thay đổi theo đúng thể loại.
# (mô tả người, chuyển động nhỏ của người trong clip)
# {people} = người theo Dòng nhạc (VN → "Vietnamese ", US-UK → "" …), điền lúc dùng.
_WOMAN = ("a glamorous fictional young {people}woman in her mid-twenties with long "
          "dark hair")
_COVERED = ("fully clothed, tasteful fashion-magazine look, her face covered below the "
            "eyes so only her eyes are visible")
_PERSONS = {
    "remix": (f"{_WOMAN}, a female DJ in a sleek black crop top and a high-waisted "
              "leather mini skirt, a glossy black face mask over her nose and mouth, "
              "headphones around her neck, standing behind a glowing DJ controller, "
              f"{_COVERED}, looking straight into the camera with a confident gaze",
              "she nods to the beat and moves her hands on the controller"),
    "ballad": (f"{_WOMAN}, in an elegant off-shoulder satin evening dress, a delicate "
               f"black lace veil over her nose and mouth, {_COVERED}, looking gently "
               "into the camera with a tender, slightly wistful gaze",
               "she blinks slowly, the veil and her hair moving lightly in the breeze"),
    "acoustic": (f"{_WOMAN}, in a fitted cropped knit top and high-waisted denim "
                 "shorts, holding an acoustic guitar, a soft black fabric face mask "
                 f"over her nose and mouth, {_COVERED}, looking into the camera with "
                 "warm, smiling eyes",
                 "she strums the guitar softly and sways slightly"),
    "original": (f"{_WOMAN}, in a stylish bodycon mini dress with a cropped jacket, "
                 "a black face mask over her nose and mouth, headphones around her "
                 f"neck, {_COVERED}, looking into the camera with calm, alluring eyes",
                 "she sways slightly to the music, hair moving softly"),
}

_MAN = ("a charismatic fictional young {people}man in his mid-twenties with "
        "well-groomed dark hair")
_MAN_COVERED = ("fully clothed, tasteful fashion-magazine look, his face covered "
                "below the eyes so only his eyes are visible")
_MEN = {
    "remix": (f"{_MAN}, a male DJ in a fitted black shirt and tailored dark "
              "trousers, a glossy black face mask, headphones around his neck, "
              f"behind a glowing DJ controller, {_MAN_COVERED}, looking confidently "
              "into the camera", "he nods to the beat and works the controller"),
    "ballad": (f"{_MAN}, in a dark tailored suit over an open-collar shirt, a "
               f"black face mask, {_MAN_COVERED}, looking into the camera with a "
               "tender, introspective gaze", "he breathes slowly and turns his gaze, "
               "his jacket and hair moving lightly in the breeze"),
    "acoustic": (f"{_MAN}, in a fitted casual linen shirt and dark jeans, holding "
                 f"an acoustic guitar, a soft black face mask, {_MAN_COVERED}, "
                 "looking warmly into the camera", "he strums softly and sways"),
    "original": (f"{_MAN}, in a stylish fitted jacket and dark trousers, a black "
                 f"face mask, headphones around his neck, {_MAN_COVERED}, looking "
                 "calmly into the camera", "he moves naturally to the music"),
}

# Mô tả / ý tưởng có «cặp đôi» → ảnh bìa là MỘT cặp đôi hư cấu (nam + nữ, ~25
# tuổi), vẫn che mặt như quy tắc cũ: nữ đeo mạng/khẩu trang, nam đeo khẩu trang.
_COUPLE = ("a shy romantic fictional young {people}couple in their mid-twenties, a "
           "woman with long dark hair and a man standing close side by side")
_COUPLE_COVERED = ("both fully clothed, tasteful fashion-magazine look, both faces "
                   "covered below the eyes so only their eyes are visible")
_COUPLE_GESTURE = "they sway gently together and glance shyly at each other"
_COUPLES = {
    "remix": (f"{_COUPLE}, she a DJ in a sleek black crop top and a high-waisted "
              "leather mini skirt, he in a black bomber jacket, both wearing glossy "
              "black face masks over their noses and mouths, standing together behind "
              f"a glowing DJ controller, {_COUPLE_COVERED}, looking into the camera",
              "they nod to the beat together, her hands moving on the controller"),
    "ballad": (f"{_COUPLE}, she in an elegant off-shoulder satin evening dress with a "
               "delicate black lace veil over her nose and mouth, he in a dark "
               "tailored suit with a black face mask over his nose and mouth, their "
               f"hands almost touching, {_COUPLE_COVERED}, a tender, shy closeness",
               _COUPLE_GESTURE + ", the veil and her hair moving lightly in the breeze"),
    "acoustic": (f"{_COUPLE}, she in a fitted cropped knit top and denim, he in a "
                 "casual linen shirt holding an acoustic guitar, both wearing soft "
                 f"black fabric face masks over their noses and mouths, {_COUPLE_COVERED}, "
                 "sharing a warm, shy glance",
                 "he strums the guitar softly while they sway slightly together"),
    "original": (f"{_COUPLE}, she in a stylish mini dress with a cropped jacket, he in "
                 "a smart casual jacket, both wearing black face masks over their noses "
                 f"and mouths, {_COUPLE_COVERED}, a tender, shy closeness",
                 _COUPLE_GESTURE),
}
_COUPLE_WORDS = ("cặp đôi", "đôi tình nhân", "đôi trai gái", "đôi uyên ương",
                 "đôi bạn trẻ", "chàng và nàng", "hai người yêu nhau",
                 "couple", "couples", "lovers", "two lovers")


def wants_couple(context: dict) -> bool:
    """Mô tả / ý tưởng / tên bài nói tới một CẶP ĐÔI → vẽ cặp đôi thay vì 1 cô gái."""
    ctx = context or {}
    blob = _fold(" ".join(str(ctx.get(k) or "") for k in
                          ("title", "visual_idea", "description", "music_idea")))
    return _has(_COUPLE_WORDS, blob)


_STYLE_TIME = {"remix": "at dusk, vivid neon-tinted light", "ballad": "in the evening",
               "acoustic": "in warm afternoon light", "original": "at golden-hour sunset"}


def _fold(text: str) -> str:
    """Chữ thường, GIỮ dấu (bỏ dấu thì «mùa» trùng «mưa»), chuẩn NFC."""
    return unicodedata.normalize("NFC", text or "").casefold()


def _has(words: tuple[str, ...], text: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


def _people(line: dict) -> str:
    return f"{line['people']} " if line.get("people") else ""


def _playlist(line: dict, genre: str) -> str:
    """vd «Vietnamese Ballad music playlist», «US-UK Deep House music playlist»."""
    region = line.get("people") or line.get("label") or ""
    return " ".join(x for x in (region, (genre or "").strip(), "music playlist") if x)


def template_scene(context: dict, cast_mode: str = "character_pet",
                   couple: bool = False, male: bool = False) -> dict:
    """Mẫu tiếng Anh (không cần AI) → {scene, ambient, thumbnail, intro, loop}."""
    from backend.cover.genre_defaults import genre_defaults
    from backend.cover.music_line import line_profile
    ctx = context or {}
    line = line_profile(ctx.get("music_line", ""))
    blob = _fold(" ".join(str(ctx.get(k) or "") for k in
                          ("title", "visual_idea", "description", "purpose", "music_idea")))
    style = genre_defaults(ctx.get("purpose", ""), "", ctx.get("description", ""),
                           ctx.get("title", ""))["cover_style"]
    genre_blob = _fold(str(ctx.get("purpose") or ""))
    genre_place = next((p for p in _GENRE_PLACES if _has(p[0], genre_blob)), None)
    place = next((p for p in _PLACES if _has(p[0], blob)),
                 genre_place or _STYLE_PLACE.get(style, _PLACES[1]))
    _, where, ambient = place
    season = next((s for s in _SEASONS if _has(s[0], blob)), None)
    when = next((t[1] for t in _TIMES if _has(t[0], blob)), _STYLE_TIME.get(style, ""))
    scene = where + (f", {season[1]}" if season else "") + (f", {when}" if when else "")
    if season:
        ambient = f"{season[2]}, {ambient}"
    scenery = cast_mode == "scenery"
    persons = _COUPLES if couple else _MEN if male else _PERSONS
    person, gesture = persons.get(style, persons["ballad"])
    person = person.format(people=_people(line))
    who = "the couple" if couple else "the man" if male else "the woman"
    playlist = _playlist(line, ctx.get("purpose", ""))
    if scenery:
        thumbnail = (
            f"Cinematic still for a {playlist} cover: {scene}. No people and no "
            "animals anywhere in the frame. Shot on a full-frame cinema camera, 35mm "
            "lens, shallow depth of field, natural light, soft film grain, rich but "
            "gentle colour grading, calm emotional mood. Main subject slightly "
            "off-centre with clean, uncluttered negative space for a title. "
            "Absolutely no text, letters, logo or watermark.")
    else:
        thumbnail = (
            f"Eye-catching cinematic cover photo for a {playlist}: "
            f"{person}, framed from the waist up as the clear main subject, placed "
            f"off-centre on one side of the frame. Setting: {scene}, softly blurred "
            f"behind {who} with glowing bokeh lights. Shot on a full-frame cinema "
            "camera, 50mm lens, shallow depth of field, flattering warm key light on "
            "the face, sharp eyes, vivid but natural colour grading, polished "
            "magazine-cover look. The other side of the frame stays calm and "
            "uncluttered, with space for a large title. Absolutely no text, letters, "
            "logo or watermark.")
    if scenery:
        keep = "no people appear"
    elif couple:
        keep = ("keep the couple exactly as shown (the same face masks or veil covering "
                "their faces, same outfits and pose, still exactly two people); only "
                f"small natural movement: {gesture}")
    else:
        keep = (f"keep the woman exactly as shown (same face mask or veil covering "
                f"her face, same outfit and pose); only small natural movement: {gesture}")
    intro = (
        "Bring the reference image to life as the opening shot of a cinematic music "
        "video: start exactly on its composition, framing, lighting and colour "
        f"palette; {keep}. Realistic natural motion: {ambient}; the camera makes "
        "one very slow, smooth push-in. One continuous shot, no scene cut, no new "
        "people or objects, no text.")
    loop = (
        "The SAME place as the reference image with the same framing, lighting and "
        "colour palette, held as a calm living background for a long music video: "
        f"the camera stays still; only gentle natural ambient motion — {ambient}; "
        f"{keep}. Even, steady motion so the last frame matches the first and the "
        "clip loops seamlessly. One continuous shot, no scene cut, no new people "
        "or objects, no text.")
    return {"scene": scene, "ambient": ambient, "thumbnail": thumbnail,
            "intro": intro, "loop": loop}


# ── AI viết (nếu dùng được) ─────────────────────────────────────
_SYSTEM = (
    "You are a music-video art director. Analyze the exact subgenre, rhythm energy, "
    "emotional arc, lyrical story and supplied vocal cast before choosing visuals. "
    "You write prompts for a music cover video made from ONE image. The music genre "
    "and music line (region) were SELECTED BY THE USER: the image, outfit, props, "
    "place and mood must clearly fit exactly that genre and that line — never "
    "another genre or region. Return ONLY a "
    "JSON object with English string fields: scene (one sentence describing the "
    "place, season, time of day and light), thumbnail (a detailed image prompt "
    "for the cover still, cinematic photoreal unless the style says otherwise, "
    "in the style of {channels} covers, NO text in "
    "the image, clean negative space on one side for a title), intro (a video "
    "prompt that brings that exact image to life as the opening shot with one "
    "very slow push-in), loop (a video prompt for the SAME place and framing "
    "with a still camera and gentle ambient motion that loops seamlessly). No "
    "scene cuts, no new people, no text in any prompt. Avoid formulaic repeated "
    "venues, outfits, props, palettes and compositions; vary them coherently while "
    "remaining genre-authentic. Everything in English.")


def _couple_people(line: dict) -> str:
    return (f"Main subject: ONE FICTIONAL young adult {_people(line).upper()}COUPLE — "
            "a woman and a man in their mid-twenties, exactly two people, never a single "
            "person, never a crowd — in stylish outfits that suit the genre (fully "
            "clothed, tasteful magazine-editorial look, no nudity); her face COVERED "
            "below the eyes by an elegant face mask or lace veil and his face covered "
            "by a matching black face mask, so only their eyes show; framed waist-up, "
            "standing close together, off-centre, with a tender romantic body language "
            "that follows the project's story exactly (e.g. shy, hands almost touching, "
            "if the description says they are too shy to hold hands); props suit the "
            "genre (remix: she is a DJ behind a glowing controller; ballad: evening "
            "dress with lace veil and a dark suit; acoustic: he holds a guitar). Never "
            "real or famous people. Use the words 'couple' and 'mask' or 'veil' in the "
            "thumbnail prompt. In intro and loop they stay the same two people, faces "
            "still covered, with only small natural movement.")


def _male_people(line: dict) -> str:
    return (f"Main subject: ONE FICTIONAL young adult {_people(line).upper()}MAN "
            "in his mid-twenties — never a woman, never a couple — in a stylish "
            "fully clothed fashion-editorial outfit appropriate to the exact genre; "
            "his face COVERED below the eyes by an elegant black mask so only his "
            "eyes show; framed waist-up, looking into the camera, off-centre. Props "
            "must be genre-authentic (remix/house: DJ controller and headphones; "
            "ballad: restrained elegant styling; acoustic: guitar; rock: electric "
            "guitar or microphone; jazz/blues: intimate stage microphone). Never a "
            "real or famous person. In intro and loop he remains the same man with "
            "the same covered face, outfit and proportions.")


def ai_scene(context: dict, style_brief: str, cast_mode: str, directive: str,
             api_config, log=None, *, couple: bool = False,
             male: bool = False) -> Optional[dict]:
    """Hỏi AI 1 lần → dict như template_scene, hoặc None nếu không dùng được."""
    from .translate import non_english_fields
    from backend.cover.music_line import line_profile
    if not api_config or not getattr(api_config, "api_key", None):
        return None
    line = line_profile((context or {}).get("music_line", ""))
    genre = ((context or {}).get("purpose") or "").strip()
    try:
        from openai import OpenAI
        client = OpenAI(**api_config.to_client_kwargs())
        model = api_config.model
        if not model or model.lower().startswith("claude"):
            from .prompt_gen import _FALLBACK_MODEL as model
        people = ("NO people and NO animals anywhere." if cast_mode == "scenery" else
                  _couple_people(line) if couple else _male_people(line) if male else
                  f"Main subject: ONE glamorous FICTIONAL young {_people(line).upper()}"
                  "WOMAN (mid-"
                  "twenties, adult) — never a man, never a couple — in a stylish, "
                  "alluring, figure-hugging fashion outfit (fully clothed, tasteful "
                  "magazine-editorial look, no nudity), her face COVERED below the eyes "
                  "by an elegant face mask or lace veil so only her eyes show; framed "
                  "waist-up, looking straight into the camera, off-centre; outfit and "
                  "props suit the genre (remix: a female DJ behind a glowing "
                  "controller; ballad: an evening dress and lace veil; acoustic: with "
                  "a guitar; lofi/chill: headphones). Never a real or famous person. "
                  "Use the words 'woman' and 'mask' or 'veil' in the thumbnail prompt. "
                  "In intro and loop she stays the same, face still covered, with only "
                  "small natural movement.")
        cast_label = "male-female duet" if couple else "male" if male else "female"
        selected = (f"SELECTED BY USER — music genre: {genre or '(not set)'}; music "
                    f"line: {line['label']} ({line['sound']}). Follow both strictly; the "
                    f"analyzed vocal cast is {cast_label}. Follow all three strictly; "
                    "the idea/description adds place, story and mood. Do not reuse a "
                    "generic stock setting: infer genre-authentic venue, wardrobe, "
                    "props, lighting, palette and camera energy for this exact song.\n")
        # Cảnh màn hình dành cho sheet + clip footage, không được làm biến dạng
        # ảnh bìa/thumbnail mở đầu.
        cover_context = {k: v for k, v in (context or {}).items()
                         if k not in ("footage_scene_mode", "footage_scene_directive")}
        user = (selected + f"Project data (may be Vietnamese — translate the meaning): "
                f"{json.dumps(cover_context, ensure_ascii=False)}\nVisual style: {style_brief}\n"
                f"{people}\n" + (f"Thumbnail composition: {directive}\n" if directive else ""))
        r = client.chat.completions.create(
            model=model, messages=[{"role": "system",
                                    "content": _SYSTEM.replace("{channels}", line["channels"])},
                                   {"role": "user", "content": user}],
            temperature=0.8, timeout=90)
        text = (r.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else None
    except Exception as e:
        if log:
            log(f"AI chưa viết được prompt ảnh bìa ({type(e).__name__}: "
                f"{' '.join(str(e).split())[:120]}) — dùng mẫu dựng sẵn theo thể loại.")
        return None
    keys = ("scene", "thumbnail", "intro", "loop")
    if (not isinstance(data, dict)
            or any(not isinstance(data.get(k), str) or len(data[k].strip()) < 20 for k in keys)
            or non_english_fields({k: data[k] for k in keys})
            or len({data["thumbnail"].strip(), data["intro"].strip(), data["loop"].strip()}) < 3
            or (cast_mode != "scenery"
                and not (_couple_masked if couple else _man_masked if male
                         else _woman_masked)(data["thumbnail"]))):
        if log:
            who = "cặp đôi che mặt" if couple else "nam che mặt" if male else "nữ che mặt"
            log(f"AI trả prompt ảnh bìa chưa hợp lệ (hoặc nhân vật chưa phải {who}) "
                "— dùng mẫu dựng sẵn theo thể loại.")
        return None
    out = {k: " ".join(data[k].split()) for k in keys}
    out["ambient"] = ""
    return out


def _woman_masked(text: str) -> bool:
    t = (text or "").lower()
    return (bool(re.search(r"\b(woman|female|girl|lady)\b", t))
            and bool(re.search(r"\b(mask|masked|veil|veiled)\b", t))
            and not re.search(r"\b(man|men|boy|guy|couple)\b", t))


def _man_masked(text: str) -> bool:
    t = (text or "").lower()
    return (bool(re.search(r"\b(man|male|guy|gentleman)\b", t))
            and bool(re.search(r"\b(mask|masked|veil|veiled)\b", t))
            and not re.search(r"\b(woman|women|girl|lady|couple)\b", t))


def _couple_masked(text: str) -> bool:
    t = (text or "").lower()
    pair = (re.search(r"\b(couple|lovers)\b", t)
            or (re.search(r"\b(woman|girl|lady)\b", t) and re.search(r"\b(man|guy|boy)\b", t)))
    return bool(pair) and bool(re.search(r"\b(mask|masks|masked|veil|veiled)\b", t))


# ── Hai bảng tham chiếu (vẽ TỪ ảnh bìa đính kèm) + 4 góc quay nhân vật ──
def sheet_prompts(scene: str, cast_mode: str, couple: bool = False,
                  male: bool = False, footage_mode: str = "") -> dict:
    """Prompt ảnh 1 + 2. Ảnh bìa 0.png được ĐÍNH KÈM khi vẽ (config.image_refs)."""
    from .config import SCENERY_MARK
    if cast_mode == "scenery":
        one = (f"{SCENERY_MARK} — ENVIRONMENT DESIGN SHEET of the place in the attached "
               f"reference image (the cover): {scene}. Keep EXACTLY the same location, "
               "landmarks, materials, colours and light. Panels: wide establishing "
               "view, reverse view, left and right side views, a high-angle overview, "
               "close-ups of key details, and a colour-palette strip. Short uppercase "
               "English labels. NO people, NO animals in any panel.")
        two = (f"{SCENERY_MARK} — ATMOSPHERE & LIGHT SHEET of the same place as the "
               f"attached reference image: {scene}. Several panels of that exact place "
               "showing how light, sky, air and water move through the day, with arrows "
               "for light direction and a colour-palette strip. Short uppercase English "
               "labels. NO people, NO animals in any panel."
               + (" Include one WIDE SCREEN STAGE panel: a large clean front-facing "
                  "16:9 television or projector screen centred in that same place, "
                  "unobstructed, with a dark neutral screen surface."
                  if footage_mode else ""))
        return {"1": one, "2": two}
    # Bối cảnh riêng của bài → bảng nhân vật mỗi project có mô tả khác nhau
    # (trước đây câu mẫu cố định nên trùng prompt giữa các project).
    setting = (f"Story setting of this cover, for outfit mood and light only (keep a "
               f"plain neutral backdrop on the sheet): {scene}")
    if couple:
        one = ("CHARACTER REFERENCE SHEET of the couple (the woman and the man) in the "
               "attached reference image (the cover). Keep EXACTLY their face coverings "
               "(the same masks or veil), hair, outfits, accessories, colours and body "
               "proportions; both stay fully clothed in those same outfits and both "
               "faces stay covered below the eyes exactly as in the reference. Layout: "
               "for EACH of the two: full-body front view, side profile view, back view "
               "and three-quarter view standing side by side on the same base line, "
               "plus close-up panels of their covered faces and eyes, the outfit "
               "details and the shoes, and a colour-palette strip. Short uppercase "
               "English labels (HER FRONT, HIS FRONT, SIDE, BACK, 3/4, DETAILS). Same "
               "rendering style as the reference. " + setting)
        two = (f"{SCENERY_MARK} — SCENE REFERENCE SHEET of the place behind the couple "
               f"in the attached reference image (the cover): {scene}. Keep EXACTLY the "
               "same location, props, materials, colour palette and lighting. Panels: "
               "wide establishing view, reverse view, left and right side views, a "
               "high-angle overview and close-ups of key props and textures, plus a "
               "colour-palette strip. Short uppercase English labels. The place only — "
               "NO people, NO person, NO animals in any panel."
               + (" Include one clean front-facing 16:9 TV/projector screen panel, "
                  "positioned on the RIGHT side with an unobstructed dark surface."
                  if footage_mode else ""))
        return {"1": one, "2": two}
    person = "man" if male else "woman"
    possessive = "his" if male else "her"
    subject = "he" if male else "she"
    one = (f"CHARACTER REFERENCE SHEET of the {person} in the attached reference image (the "
           f"cover). Keep EXACTLY {possessive} face covering (the same mask or veil), "
           f"hair, outfit, accessories, colours and body proportions; {subject} stays "
           f"fully clothed in that same outfit and {possessive} face stays covered below "
           "the eyes exactly as in the reference. Layout: full-body front view, side "
           "profile view, back view and three-quarter view standing side by side on the "
           f"same base line, plus close-up panels of {possessive} covered face and eyes, "
           "the outfit details and the shoes, and a colour-palette strip. Short uppercase "
           "English labels (FRONT, SIDE, BACK, 3/4, DETAILS). Same rendering style as "
           "the reference. " + setting)
    two = (f"{SCENERY_MARK} — SCENE REFERENCE SHEET of the place behind the {person} in the "
           f"attached reference image (the cover): {scene}. Keep EXACTLY the same "
           "location, props, materials, colour palette and lighting. Panels: wide "
           "establishing view, reverse view, left and right side views, a high-angle "
           "overview and close-ups of key props and textures, plus a colour-palette "
           "strip. Short uppercase English labels. The place only — NO people, NO "
           "person, NO animals in any panel."
           + (" Include one clean front-facing 16:9 TV/projector screen panel, "
              "positioned on the RIGHT side with an unobstructed dark surface."
              if footage_mode else ""))
    return {"1": one, "2": two}


_ANGLES = (
    "Camera: a slow dolly-in to a close-up of her covered face and eyes, shallow "
    "depth of field; she slowly raises her gaze to the camera",
    "Camera: a smooth side tracking shot in profile at medium distance; she turns "
    "her head gently and her hair moves in the breeze",
    "Camera: a low-angle shot slowly tilting up from her outfit to her covered face; "
    "she sways gently to the music",
    "Camera: a wide shot slowly orbiting around her, revealing the whole place "
    "behind her; she poses confidently and moves naturally to the music",
)
_ANGLES_MALE = tuple(x.replace("her ", "his ").replace("she ", "he ")
                     for x in _ANGLES)
_ANGLES_COUPLE = (
    "Camera: a slow dolly-in to a close-up of the two of them, shallow depth of "
    "field; they glance shyly at each other, then at the camera",
    "Camera: a smooth side tracking shot in profile at medium distance; they walk "
    "slowly side by side, hands almost touching, hair moving in the breeze",
    "Camera: a low-angle shot slowly tilting up from their outfits to their covered "
    "faces; they sway gently together to the music",
    "Camera: a wide shot slowly orbiting around the couple, revealing the whole "
    "place behind them; they stand close and move naturally to the music",
)
_ANGLES_SCENERY = (
    "Camera: a smooth dolly forward deep into the place",
    "Camera: a lateral tracking shot gliding slowly to the right",
    "Camera: a gentle crane rise revealing the wider view",
    "Camera: a slow orbit around the main landmark",
)


def angle_motions(scene: str, ambient: str, cast_mode: str, couple: bool = False,
                  male: bool = False, footage_mode: str = "") -> dict:
    """Prompt clip 02–05: 4 góc quay; nguyên liệu = bảng 1 + bảng 2."""
    from .config import COVER_ANGLE_CLIPS
    amb = f" Ambient motion: {ambient}." if ambient else ""
    if cast_mode == "scenery":
        head = ("The two reference images are design sheets of ONE place, NOT film "
                "frames: use them only for the exact look of the place. Build ONE "
                f"continuous cinematic film shot of that place ({scene}) with no people "
                "and no animals. ")
        angles = _ANGLES_SCENERY
    elif couple:
        head = ("The two reference images are design sheets, NOT film frames: the first "
                "shows the couple (the woman and the man: their exact covered faces, hair "
                "and outfits), the second shows the place. Build ONE continuous cinematic "
                f"film shot of that same couple in that same place ({scene}); both faces "
                "stay covered exactly as on the sheet, same outfits, fully clothed, "
                "exactly these two people. ")
        angles = _ANGLES_COUPLE
    elif male:
        head = ("The two reference images are design sheets, NOT film frames: the first "
                "shows the man (his exact covered face, hair and outfit), the second "
                "shows the place. Build ONE continuous cinematic film shot of that "
                f"same man in that same place ({scene}); his face stays covered exactly "
                "as on the sheet, same outfit, fully clothed. ")
        angles = _ANGLES_MALE
    else:
        head = ("The two reference images are design sheets, NOT film frames: the first "
                "shows the woman (her exact covered face, hair and outfit), the second "
                "shows the place. Build ONE continuous cinematic film shot of that same "
                f"woman in that same place ({scene}); her face stays covered exactly as on "
                "the sheet, same outfit, fully clothed. ")
        angles = _ANGLES
    if footage_mode:
        if cast_mode == "scenery" or footage_mode == "screen_only":
            screen_shot = (
                "Camera locked in a wide symmetrical shot of a large front-facing 16:9 "
                "television or projector screen in this same place; screen occupies the "
                "RIGHT-CENTRE 43 percent of the frame at eye level, dark neutral and "
                "fully unobstructed; no people or animals")
        else:
            who = "the same couple" if couple else "the same man" if male else "the same woman"
            screen_shot = (
                f"Camera locked in a wide shot: {who} stands or sits in the LEFT third, "
                "seen naturally watching a large front-facing 16:9 television or "
                "projector screen in the RIGHT-CENTRE 43 percent of the frame; the "
                "screen is dark neutral, fully visible and never occluded")
        angles = (*angles[:-1], screen_shot)
    tail = (" No side-by-side panels, no labels, no colour swatches, no other people, "
            "one continuous shot, no scene cut, no text.")
    return {str(k): f"{head}{a}.{amb}{tail}" for k, a in zip(COVER_ANGLE_CLIPS, angles)}


def build(context: dict, style_brief: str, cast_mode: str, directive: str = "",
          api_config=None, log=None, *, use_ai: bool = True,
          couple: bool | None = None) -> tuple[dict, dict, dict]:
    """→ (prompts {"0","1","2"}, motions {"0": intro, "1": loop, "2".."5": góc quay},
    continuity). couple=None → ưu tiên giọng hát đã phân tích; chỉ suy từ câu
    chuyện/mô tả khi chưa xác định được giọng."""
    vocal_cast = str((context or {}).get("vocal_cast") or "auto").strip().lower()
    if couple is None:
        couple = vocal_cast == "duet" or (
            vocal_cast not in ("male", "female") and wants_couple(context))
    couple = bool(couple) and cast_mode != "scenery"
    male = vocal_cast == "male" and not couple and cast_mode != "scenery"
    data = (ai_scene(context, style_brief, cast_mode, directive, api_config, log,
                     couple=couple, male=male) if use_ai else None)
    source = "ai"
    if data is None:
        data, source = template_scene(context, cast_mode, couple, male), "template"
    thumb = data["thumbnail"]
    if directive and directive not in thumb:
        thumb = thumb.rstrip() + "\n\n" + directive
    footage_mode = str((context or {}).get("footage_scene_mode") or "").strip()
    prompts = {"0": thumb, **sheet_prompts(data["scene"], cast_mode, couple, male,
                                             footage_mode)}
    motions = {"0": data["intro"], "1": data["loop"],
               **angle_motions(data["scene"], data.get("ambient", ""), cast_mode,
                               couple, male, footage_mode)}
    continuity = {"scene": data["scene"], "source": source,
                  "vocal_cast": "duet" if couple else "male" if male else "female"}
    if couple:
        continuity["cast"] = "couple"
    return prompts, motions, continuity
