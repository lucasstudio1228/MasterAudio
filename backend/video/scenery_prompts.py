"""
video/scenery_prompts.py — Chế độ CHỈ CẢNH VẬT (project.cast_mode = "scenery").

Cùng quy trình 3 ẢNH → N CLIP như chế độ nhân vật, nhưng KHÔNG có nhân vật chính
và KHÔNG có linh thú/thú nuôi — chỉ có nơi chốn trong "Ý tưởng của bạn":
    "1" = ENVIRONMENT DESIGN SHEET (world sheet): 1 khung lớn + 3 khung khu vực
          khác của CÙNG nơi chốn + ô cận chi tiết + dải màu, nhãn tiếng Anh.
    "2" = ATMOSPHERE & LIGHT SHEET (mood sheet): cùng 1 góc nhìn dưới 4 trạng
          thái ánh sáng/thời tiết + ô nghiên cứu yếu tố chuyển động + dải màu.
    "0" = ảnh bìa — chính nơi chốn đó, sạch chữ, theo công thức bố cục.
  motions = N góc nhìn KHÁC NHAU của cùng thế giới — cảnh ĐỘNG như phim thường
          (từ 2026-10-27): nước chảy, mây trôi, gió lùa cây cỏ, mưa/tuyết/cánh
          hoa rơi… + máy quay chuyển động mượt (dolly, tracking, pan, crane…),
          KHÔNG khoá máy / siêu chậm như ảnh tĩnh.

Khoá continuity lưu như chế độ thường để mọi bước sau (validate, dịch, viết bù
cảnh) chạy nguyên: "character_sheet" = khối nơi chốn (world), "pet_sheet" = khối
không khí (mood), kèm cast_mode="scenery". Mọi prompt đều chứa
config.SCENERY_MARK để driver nhận ra lúc gửi (bọc bảng bối cảnh, phủ định
người/thú, câu intro riêng).
"""
from __future__ import annotations

import re
from typing import Optional

from . import config
from .translate import TranslationError, non_english_fields, to_english

# Từ chỉ NGƯỜI hoặc CON VẬT — không được xuất hiện trong hồ sơ cảnh vật. Đom
# đóm (fireflies) là hạt sáng môi trường nên được phép.
_LIVING_WORDS = (
    r"people|persons?|humans?|man|men|woman|women|girls?|boys?|child(?:ren)?|kids?|"
    r"babies|baby|monks?|nuns?|travell?ers?|wanderers?|hikers?|pilgrims?|figures?|"
    r"silhouettes?|characters?|players?|musicians?|someone|somebody|anyone|villagers?|"
    r"farmers?|fisherm[ae]n|lad(?:y|ies)|gentlem[ae]n|couples?|crowds?|visitors?|"
    r"warriors?|samurai|geishas?|maidens?|elders?|grand(?:mother|father|ma|pa)s?|"
    r"he|she|his|her|hers|him|himself|herself|"
    r"animals?|cats?|kittens?|dogs?|pupp(?:y|ies)|fox(?:es)?|deer|fawns?|rabbits?|"
    r"bunn(?:y|ies)|birds?|owls?|cranes?|herons?|swans?|ducks?|sparrows?|eagles?|"
    r"pandas?|bears?|horses?|cows?|sheep|goats?|fish(?:es)?|koi|carps?|turtles?|"
    r"frogs?|squirrels?|monkeys?|tigers?|lions?|wol(?:f|ves)|dragons?|phoenix(?:es)?|"
    r"creatures?|pets?|companions?|butterfl(?:y|ies)|dragonfl(?:y|ies)|bees?|"
    r"wildlife|livestock")
_LIVING_RE = re.compile(rf"\b(?:{_LIVING_WORDS})\b", re.I)
# "no people, no animals" / "without any figures" — phủ định thì không tính.
_NEGATED_RE = re.compile(
    rf"\b(?:no|without(?:\s+any)?|free of|empty of|devoid of|never any|nor|not a single|"
    rf"absence of)\s+(?:other\s+|visible\s+|living\s+)?(?:{_LIVING_WORDS})\b"
    rf"(?:\s*(?:,|or|and|nor|/)\s*(?:no\s+|any\s+)?(?:{_LIVING_WORDS})\b)*", re.I)


def living_hits(text: str) -> list[str]:
    """Từ chỉ người/con vật còn sót trong text (bỏ qua câu phủ định «no people…»)."""
    t = _NEGATED_RE.sub(" ", text or "")
    return sorted({m.group(0).lower() for m in _LIVING_RE.finditer(t)})


def drop_living_sentences(text: str) -> str:
    """Bỏ riêng các câu nhắc người/con vật (dùng khi đã hết lượt viết lại)."""
    sents = re.split(r"(?<=[.!?;])\s+", text or "")
    kept = [s for s in sents if not living_hits(s)]
    return " ".join(kept).strip()


def _str_items(v, lo: int, hi: int) -> list[str]:
    from .prompt_gen import _str_list
    return _str_list(v, lo, hi)


def _bullets(items) -> str:
    from .prompt_gen import _bullets as b
    return b(items)


# ── Khung bảng thiết kế (code dựng, AI chỉ điền nội dung) ─────────────────
def world_sheet_prompt(*, title_en: str, world: str, views, insets, palette,
                       aspect_ratio: str = "16:9") -> str:
    key, *areas = list(views) or ["KEY VIEW — the signature view of the place"]
    return f"""{config.SCENERY_MARK} — ENVIRONMENT DESIGN SHEET / LOCATION CONCEPT SHEET — one single {aspect_ratio} sheet, laid out neatly like an animation background design document: flat neutral light-grey background (#ECECEC), clearly separated framed panels, thin callout lines connecting each label to its detail.

TOP TITLE (large uppercase English text, short): "{title_en}".

THE PLACE (draw it IDENTICALLY in every panel):
{world}

REQUIRED LAYOUT:
• KEY VIEW — one large framed panel (about half of the sheet), labelled with its short name: {key}
• AREA VIEWS — three smaller framed panels showing other spots of THE SAME place, each with a short uppercase label:
{_bullets(areas[:3])}
• DETAIL CALLOUTS — small boxes of signature elements, each with a short uppercase English label:
{_bullets(insets)}
• COLOUR STRIP at the bottom — label "COLOR PALETTE": square colour swatches in a horizontal row, each with its small hex code underneath, in this exact order:
{_bullets(palette)}

DRAWING RULES:
• This is a DESIGN DOCUMENT of a PLACE, NOT a film frame: every panel shows THE SAME location — same landmarks, architecture, materials, vegetation and palette.
• EMPTY OF LIFE: no people, no human figures or silhouettes, no characters, no animals, no creatures in any panel.
• Even, neutral daylight in every panel (light and weather studies belong to the atmosphere sheet).
• Text is only for LABELS: uppercase, English, very short, never over the artwork, no paragraphs, no watermark, no logo."""


def mood_sheet_prompt(*, world: str, mood: str, frames, insets, palette,
                      aspect_ratio: str = "16:9") -> str:
    return f"""{config.SCENERY_MARK} — ATMOSPHERE & LIGHT SHEET (lighting / colour-script study of one place) — one single {aspect_ratio} sheet, laid out neatly like an animation colour-script document: flat neutral light-grey background (#ECECEC), clearly separated framed panels, short labels.

TOP TITLE (large uppercase English text, short): "ATMOSPHERE & LIGHT".

THE PLACE (identical in every panel):
{world}

ATMOSPHERE TO KEEP:
{mood}

REQUIRED LAYOUT:
• LIGHT STUDIES ROW — four equal framed panels of THE SAME signature view with the same composition; only light, weather and colour change. Each panel has a short uppercase label:
{_bullets(frames)}
• AMBIENT ELEMENT STUDIES — small boxes, each with a short uppercase English label, showing the gentle moving elements of this place:
{_bullets(insets)}
• COLOUR STRIP at the bottom — label "COLOR PALETTE": square swatches with hex codes, in this exact order:
{_bullets(palette)}

DRAWING RULES:
• This is a DESIGN DOCUMENT, NOT a film frame: the four light studies share the exact same viewpoint and landmarks.
• EMPTY OF LIFE: no people, no human figures or silhouettes, no characters, no animals, no creatures anywhere.
• Text is only for LABELS: uppercase, English, very short, no paragraphs, no watermark, no logo."""


def with_world_lock(scene: str, world: str, mood: str) -> str:
    """Khoá nguyên liệu ở ĐẦU mỗi prompt cảnh (giống _with_ingredient_lock)."""
    head = config.INGREDIENT_LOCK_SCENERY
    keep = " ".join(x.strip() for x in (world, mood) if x and x.strip())
    if keep:
        head += f"\nWORLD TO KEEP: {keep}"
    return f"{head}\n\nSCENE: {(scene or '').strip()}"


def thumbnail_prompt(world: str, mood: str, thumb: str) -> str:
    return (f"{config.SCENERY_MARK} — THE PLACE (KEEP ABSOLUTELY FIXED — same landmarks, "
            f"architecture, vegetation, materials and colours): {world.strip()}\n"
            f"ATMOSPHERE: {mood.strip()}\n\n"
            f"THIS SHOT (scenery only — the place is empty and peaceful, NO people, "
            f"NO characters, NO animals): {thumb.strip()}")


def scene_dict(raw, n: int, world: str, mood: str) -> Optional[dict[str, str]]:
    items: list[str] = []
    seq = raw if isinstance(raw, list) else (
        [raw.get(str(i)) or raw.get(i) for i in range(n)] if isinstance(raw, dict) else None)
    if seq is None:
        return None
    for it in seq:
        v = it.get("prompt") if isinstance(it, dict) else it
        items.append(v.strip() if isinstance(v, str) else "")
    if len(items) < n or any(len(v) < 30 for v in items[:n]):
        return None
    return {str(i): with_world_lock(items[i], world, mood) for i in range(n)}


def _scene_texts(raw) -> list[str]:
    seq = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []
    out = []
    for it in seq:
        v = it.get("prompt") if isinstance(it, dict) else it
        if isinstance(v, str):
            out.append(v)
    return out


def _clean_scenes(raw):
    def fix(it):
        if isinstance(it, dict) and isinstance(it.get("prompt"), str):
            return {**it, "prompt": drop_living_sentences(it["prompt"])}
        return drop_living_sentences(it) if isinstance(it, str) else it
    if isinstance(raw, list):
        return [fix(x) for x in raw]
    if isinstance(raw, dict):
        return {k: fix(v) for k, v in raw.items()}
    return raw


# ── Lời nhắc ─────────────────────────────────────────────────────────────
def _style_rule(style_brief: str) -> tuple[str, str]:
    from .prompt_gen import _is_photoreal
    if _is_photoreal(style_brief):
        return ("PHOTOREALISTIC CINEMATIC FILM",
                f"'{style_brief}' — it MUST be cinematic photorealism that looks like a "
                "REAL PHOTOGRAPH / LIVE-ACTION FILM (true-to-life materials, foliage, "
                "water and light, depth of field). ABSOLUTELY NO animation, NO "
                "cartoon/anime, NO flat 2D illustration. ")
    return ("ANIMATED FILM",
            f"'{style_brief}' — ABSOLUTELY NOT photorealistic, NOT a real photograph, NO "
            "hyperrealism. Always describe the place in the VISUAL LANGUAGE of the "
            "chosen animation style. ")


def _scenes_system(style_brief: str) -> str:
    from .prompt_gen import _is_photoreal
    medium = ("photorealistic cinematic film" if _is_photoreal(style_brief)
              else "animated film")
    return (
        f"You are a senior MOTION DIRECTOR and cinematographer for a chill / "
        f"relaxing {medium} ({style_brief}) that shows ONLY SCENERY: no people and "
        "no animals. Task: write scene + motion prompts (image-to-video, 8-second "
        "clips) for the video model (Veo). Every clip is a LIVING, MOVING scene, "
        "never a still image: the place itself is clearly in motion (flowing "
        "rivers and waterfalls, waves, clouds moving across the sky, wind through "
        "trees, grass and flowers, falling rain, snow, petals or leaves, drifting "
        "mist, swaying lanterns, turning waterwheels or windmills, moving light "
        "and shadows) and the camera makes one smooth, real cinematic move (dolly, "
        "tracking, pan, tilt, crane, orbit, aerial drift, push-in) at a normal, "
        "unhurried speed. "
        "Describe the motion POSITIVELY (the model's safety filter rejects "
        "prompts that mention fire, smoke or destruction, even negated). Write in "
        "ENGLISH ONLY. Return ONLY valid JSON."
    )


def _system(style_brief: str) -> str:
    medium, rule1 = _style_rule(style_brief)
    return (
        "You are a senior environment director and background art director for "
        f"{medium} ({style_brief}) chill / relaxing / meditation / lofi content. "
        "This video contains NO characters at all: no people and no animals — only "
        "the scenery itself, its light and its living natural motion. You think "
        "cinematically and imagine every view animated into an 8-second clip that "
        "is clearly ALIVE and MOVING, never a still image: physically plausible "
        "natural motion of the place plus one smooth, real camera move. "
        f"SUPREME RULE 1: every shot MUST follow the style {rule1}"
        "SUPREME RULE 2: stay FAITHFUL to the world in the user's idea (the exact "
        "place, landscape, architecture, season, weather, mood and objects); if the "
        "idea mentions people or animals, leave them out and keep only their world. "
        "WRITE EVERYTHING IN ENGLISH ONLY. Return ONLY valid JSON, no explanation, "
        "no markdown."
    )


def _instrument_note(spec, n: int) -> str:
    if not spec:
        return ("• No musical instrument is shown being played; nobody is there to "
                "play it.")
    k = max(1, round(n * 0.15))
    return (f"• The music of this video is played on the {spec['label']}, but NOBODY "
            f"appears. The {spec['label']} MAY appear resting UNATTENDED as a quiet "
            f"detail of the place (in its real-world place and size) in at most {k} "
            "scenes and/or the thumbnail, only when it fits the idea — never being "
            "played, never moving.")


def build_user_prompt(idea: str, title: str, keywords: str, scene_count: int,
                      aspect_ratio: str, style: str, instrument_spec=None,
                      thumbnail_directive: str = "") -> str:
    from .prompt_gen import _style_notes
    n = scene_count
    medium, style_note = _style_notes(style)
    if thumbnail_directive:
        thumb_layout = (f"Follow this composition exactly — {thumbnail_directive}\n"
                        f"   Aspect ratio {aspect_ratio}.")
        thumb_json = ("70-120 word thumbnail prompt of the place, NO text, NO people or "
                      "animals, following the THUMBNAIL COMPOSITION above")
    else:
        thumb_layout = ("Place the main landmark off-centre to the RIGHT, leaving clean "
                        "negative space on\n   the LEFT HALF. Aspect ratio "
                        f"{aspect_ratio}.")
        thumb_json = ("70-120 word thumbnail prompt of the place, NO text, NO people or "
                      "animals, negative space on the left half")
    return f"""USER IDEA: "{idea}"
Project title (for the thumbnail): "{title}"
Suggested healing keywords for the thumbnail: {keywords}

═══ TASK ═══
Design a chill / relaxing / meditation "{medium}" that shows ONLY SCENERY — NO
main character, NO companion, NO people, NO animals. It consists of:
  (A) TWO ENVIRONMENT SHEETS of the ONE world the idea describes — a WORLD
      SHEET (the place: landmarks, architecture, vegetation, water, materials)
      and an ATMOSPHERE SHEET (its light, weather and moving elements).
      Both sheets are fed to the video model as INGREDIENTS for all {n} clips, so
      they are the single "source of truth" for how the place looks. You supply
      the CONTENT; the system already has the layout template.
  (B) ONE thumbnail image of the place.
  (C) EXACTLY {n} SCENE PROMPTS for {n} 8-second clips — {n} DIFFERENT VIEWS of
      that same world.

═══ MANDATORY RULES ═══

1. STAY ABSOLUTELY FAITHFUL TO THE IDEA'S WORLD
   The exact place, landscape, architecture, season, weather, mood and objects
   the idea describes. If the idea mentions a person, a musician or an animal,
   LEAVE THEM OUT and keep only the place they would be in. NEVER invent a
   different world.

2. EMPTY OF LIFE — NEVER mention people, characters, figures, silhouettes or
   animals of any kind (not even to say they are absent; the system adds that
   rule). Life is suggested only by the place itself: light, water, plants,
   wind, an unattended object, a lantern, steam-free tea on a table. Fireflies
   may appear as tiny light particles.
{_instrument_note(instrument_spec, n)}

3. {n} DIFFERENT VIEWS — SHUFFLE-SAFE
   • The clips are RANDOMLY SHUFFLED when assembled ⇒ no continuing story, no
     order-dependent events; every view stands alone in a peaceful state.
   • {n} CLEARLY DISTINCT spots/views of the SAME world (different area, camera
     distance — wide vista / medium / close detail —, time of day and weather).
     Never repeat a view. Keep the world's landmarks, materials and palette.

4. SCENE PROMPTS (70–130 words each) for a VIDEO model (8 seconds, ingredients =
   the 2 environment sheets). Each prompt MUST have:
   a) An opening lock clause: the place from the two environment sheets, landmarks,
      materials and colours unchanged, shown as one real film shot.
   b) A concrete SPOT of the world, time of day, weather, 3 layers foreground /
      midground / background.
   c) LIGHTING: light source, direction, colour temperature, shadows.
   d) ATMOSPHERE & PARTICLES: mist, pollen, sun rays, rain, fireflies, snow…
   e) LIVING MOTION — clearly visible, NEVER a still image: name 2-3 elements of
      THIS spot that move during the 8 seconds — flowing or cascading water,
      waves, ripples, clouds moving across the sky, wind sweeping through trees,
      grass, flowers or curtains, falling rain, snow, petals or leaves, drifting
      mist, swaying lanterns or wind chimes, a turning waterwheel or windmill, a
      moored boat rocking, flickering lantern light, moving light and shadows.
   f) CAMERA MOVE — one smooth, natural cinematic move, different from clip to
      clip: dolly forward or back, lateral tracking, pan, tilt-up reveal, crane
      rise, gentle orbit, aerial drift / flyover, push-in toward a detail.
      Normal, unhurried speed — never a locked or frozen camera.
   g) PACING: calm, smooth and continuous at natural real-time speed. No cuts,
      no fades, no text.
   Phrase everything POSITIVELY; never mention smoke, fire or destruction.
   {style_note}

5. THUMBNAIL — 70–120 words: the most beautiful, emotionally rich view of the
   world. ABSOLUTELY NO text/title/typography/logo/watermark in the image (the
   title "{title}" will be added later by software).
   {thumb_layout}

6. LANGUAGE: every value must be ENGLISH only — no other language, no
   diacritics. Transliterate proper names into plain unaccented Latin letters.

═══ OUTPUT FORMAT ═══
Return ONLY valid JSON (no explanation, no markdown):
{{
  "world_bible": "full description of the world: geography, landmarks, architecture, vegetation, water, materials, mood",
  "world_title": "SHORT UPPERCASE ENGLISH NAME OF THE PLACE (2-5 words)",
  "world_sheet": "CONDENSED 60-100 word identity block of THE PLACE: terrain, signature landmarks and architecture (materials + hex colours), vegetation species, water features, ground surfaces, recurring small details. NO lighting/time of day, NO people, NO animals",
  "world_views": ["UPPERCASE LABEL — one area/view of the place (exactly 4 items: the signature key view first, then 3 other spots)"],
  "world_insets": ["UPPERCASE LABEL — close-up of a signature element (5-8 items: materials, plants, stones, water, small objects)"],
  "mood_sheet": "CONDENSED 40-80 word ATMOSPHERE block: light quality, weather range, ambient moving elements, colour mood with hex. NO people, NO animals",
  "mood_frames": ["UPPERCASE LABEL — the key view under one light/weather state (exactly 4 items, e.g. MISTY DAWN — …)"],
  "mood_insets": ["UPPERCASE LABEL — study of one moving element (3-6 items, e.g. WATER RIPPLES — …)"],
  "palette": ["#RRGGBB — English colour name — role"],
  "thumbnail": "{thumb_json}",
  "scenes": [
    {{"setting": "short name of the spot/view", "focus": "short focal element", "prompt": "70-130 word scene prompt covering all layers a–g"}}
  ]
}}
Required: "scenes" has EXACTLY {n} items with non-empty "setting" and "prompt",
{n} DIFFERENT "setting" values. "palette" has 5–7 items. Do not leave any key
above empty. All values in ENGLISH."""


def build_scenes_user_prompt(idea: str, style: str, world: str, mood: str,
                             scene_count: int, avoid: list[str] | None = None,
                             instrument_spec=None) -> str:
    from .prompt_gen import _style_notes
    n = scene_count
    _, style_note = _style_notes(style)
    avoid_block = ""
    if avoid:
        joined = "; ".join(str(a).strip() for a in avoid if str(a).strip())
        if joined:
            avoid_block = ("\nVIEWS ALREADY USED (do NOT repeat them, invent completely "
                           f"different ones): {joined}\n")
    return f"""OVERALL IDEA: "{idea}"
Style: {style}

THE PLACE (ingredient image 1 — KEEP ABSOLUTELY UNCHANGED):
{world}

ATMOSPHERE (ingredient image 2):
{mood}
{avoid_block}
This video shows ONLY SCENERY — no people and no animals. Write EXACTLY {n}
scene prompts, each creating one 8-second clip: {n} COMPLETELY DIFFERENT VIEWS
(spot, camera distance, time of day, weather) of the SAME world.

MANDATORY RULES for every prompt (70–130 words):
1. START with a lock clause: the place from the two environment sheets, landmarks,
   materials and colours unchanged, shown as one real film shot — never the sheet
   layout, labels or palette.
2. Its own SPOT: place, time of day, weather, and 3 clear layers foreground /
   midground / background.
3. Concrete LIGHTING + ATMOSPHERE & PARTICLES (mist, pollen, sun rays, rain,
   fireflies, snow…).
4. LIVING MOTION — the clip is clearly ALIVE, NEVER a still image: 2-3 elements
   of this spot visibly moving (flowing water, waves, clouds moving across the
   sky, wind through trees and grass, falling rain / snow / petals / leaves,
   drifting mist, swaying lanterns, a turning waterwheel or windmill, moving
   light and shadows) PLUS one smooth cinematic CAMERA MOVE (dolly, tracking,
   pan, tilt, crane, orbit, aerial drift, push-in), varied between clips, at a
   normal unhurried speed — never a locked or frozen camera. No cuts, no fades.
5. NEVER mention people, characters, figures, silhouettes or animals of any kind
   — not even to say they are absent.
{_instrument_note(instrument_spec, n)}
6. SHUFFLE-SAFE: every view stands alone, never continues another one.
7. PHRASE EVERYTHING POSITIVELY; never mention smoke, fire or destruction.
8. ENGLISH ONLY; Veo-friendly keywords (cinematic, smooth camera movement,
   natural motion, real-time). {style_note}

═══ OUTPUT ═══ Return ONLY JSON (no explanation, no markdown):
{{
  "scenes": [
    {{"setting": "short name of the spot/view", "focus": "short focal element", "prompt": "70-130 word scene prompt"}}
  ]
}}
Required: "scenes" has EXACTLY {n} items, {n} DIFFERENT "setting" values; no
empty item."""


# ── Gọi model ────────────────────────────────────────────────────────────
def _client_and_model(api_config, log):
    from .prompt_gen import _FALLBACK_MODEL
    if not api_config or not getattr(api_config, "api_key", None):
        log("Chưa cấu hình API key — dừng chuẩn bị prompt cảnh vật.")
        return None, None
    try:
        from openai import OpenAI
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        log(f"Không khởi tạo được client OpenAI ({type(e).__name__}: {e}).")
        return None, None
    model = api_config.model
    if not model or model.lower().startswith("claude"):
        model = _FALLBACK_MODEL
    return client, model


def _ask(client, model, system: str, user: str):
    """(data, finish) — data None nếu không phải JSON."""
    from .prompt_gen import _extract_json
    resp = client.chat.completions.create(
        model=model, max_tokens=32000, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    text = resp.choices[0].message.content or ""
    return _extract_json(text), getattr(resp.choices[0], "finish_reason", None)


def _logger(log):
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _log


def generate_prompts(idea: str, title: str, keywords: str, scene_count: int,
                     aspect_ratio: str, style: str, api_config, log=None,
                     creative_brief: str = "", metadata_out: Optional[dict] = None,
                     instrument: str = "", thumbnail_directive: str = ""
                     ) -> Optional[tuple[dict[str, str], dict[str, str]]]:
    """Như prompt_gen.generate_prompts nhưng CHỈ CẢNH VẬT. Trả (prompts, motions)
    hoặc None; metadata_out nhận khoá continuity (character_sheet = nơi chốn,
    pet_sheet = không khí) + cast_mode="scenery"."""
    from .prompt_gen import _MAX_ATTEMPTS, _resolve_instrument, _settings_of, _text
    _log = _logger(log)
    idea = (idea or "").strip()
    if not idea:
        _log("Không có ý tưởng/hồ sơ — chưa sinh prompt.")
        return None
    client, model = _client_and_model(api_config, _log)
    if client is None:
        return None
    try:
        idea = to_english(idea, api_config, _log)
        title = to_english(title or "", api_config, _log)
        keywords = to_english(keywords or "", api_config, _log)
        creative_brief = to_english(creative_brief or "", api_config, _log)
    except TranslationError as e:
        _log(f"{e} — dừng chuẩn bị prompt (không gửi prompt lẫn tiếng Việt).")
        return None
    spec = _resolve_instrument(instrument, idea, api_config, _log)
    _log("Chế độ CHỈ CẢNH VẬT — không nhân vật, không linh thú/thú nuôi; ảnh 1/2 là "
         "bảng thiết kế bối cảnh + bảng ánh sáng.")
    user = build_user_prompt(idea, title, keywords, scene_count, aspect_ratio, style,
                             instrument_spec=spec, thumbnail_directive=thumbnail_directive)
    if creative_brief:
        user += ("\nSHARED CREATIVE BRIEF (reference data for palette/light only; this "
                 "video has NO characters — ignore any mention of a character or "
                 "outfit):\n" + creative_brief)
    system = _system(style)
    last_reason = "không rõ"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        final = attempt == _MAX_ATTEMPTS
        try:
            data, finish = _ask(client, model, system, user)
        except Exception as e:
            last_reason = f"gọi OpenAI thất bại ({type(e).__name__}: {e})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        if finish == "length" or not isinstance(data, dict):
            last_reason = ("JSON bị cắt cụt (finish_reason=length)" if finish == "length"
                           else "trả về không phải JSON hợp lệ")
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        bad = non_english_fields(data)
        if bad:
            last_reason = f"còn chữ không phải tiếng Anh ở {', '.join(bad[:4])}"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        world = _text(data.get("world_sheet")) or _text(data.get("world_bible"))
        mood = _text(data.get("mood_sheet"))
        thumb = _text(data.get("thumbnail"))
        views = _str_items(data.get("world_views"), 2, 6)
        insets = _str_items(data.get("world_insets"), 3, 10)
        frames = _str_items(data.get("mood_frames"), 2, 6)
        m_insets = _str_items(data.get("mood_insets"), 2, 8)
        palette = _str_items(data.get("palette"), 3, 8)
        raw_scenes = data.get("scenes")
        texts = [world, mood, thumb, *views, *insets, *frames, *m_insets,
                 *_scene_texts(raw_scenes)]
        hits = sorted({h for t in texts for h in living_hits(t)})
        if hits and not final:
            last_reason = f"hồ sơ cảnh vật còn nhắc người/con vật ({', '.join(hits[:6])})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — viết lại…")
            continue
        if hits:
            # Hết lượt: bỏ riêng câu nhắc người/con vật (khối nơi chốn vẫn khoá).
            world, mood, thumb = (drop_living_sentences(x) for x in (world, mood, thumb))
            views, insets, frames, m_insets = (
                [x for x in lst if not living_hits(x)]
                for lst in (views, insets, frames, m_insets))
            raw_scenes = _clean_scenes(raw_scenes)
            _log(f"Đã bỏ các câu nhắc người/con vật ({', '.join(hits[:6])}).")
        if len(world) < 20 or len(mood) < 20:
            last_reason = "thiếu world_sheet hoặc mood_sheet"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        if len(thumb) < 30 or not palette or len(views) < 2 or len(insets) < 3 \
                or len(frames) < 2 or len(m_insets) < 2:
            last_reason = "thiếu thumbnail / palette / world_views / insets / mood_frames"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        title_en = (_text(data.get("world_title")) or "ENVIRONMENT SHEET").upper()[:48]
        prompts = {
            "1": world_sheet_prompt(title_en=title_en, world=world, views=views,
                                    insets=insets, palette=palette,
                                    aspect_ratio=aspect_ratio),
            "2": mood_sheet_prompt(world=world, mood=mood, frames=frames,
                                   insets=m_insets, palette=palette,
                                   aspect_ratio=aspect_ratio),
            "0": thumbnail_prompt(world, mood, thumb),
        }
        motions = scene_dict(raw_scenes, scene_count, world, mood) or {}
        if len(motions) == scene_count:
            _log(f"Đã sinh 3 prompt ảnh cảnh vật (bảng bối cảnh + bảng ánh sáng + ảnh "
                 f"bìa) và {scene_count} prompt cảnh ({model}, lần {attempt}).")
        else:
            _log(f"Đã sinh 3 prompt ảnh cảnh vật ({model}, lần {attempt}); prompt cảnh "
                 f"{len(motions)}/{scene_count} — workflow sẽ sinh bổ sung.")
        if metadata_out is not None:
            metadata_out.update(
                character_sheet=world, pet_sheet=mood, pet_name="", pet_kind="none",
                palette=palette, cast_mode="scenery",
                character_bible=_text(data.get("world_bible")),
                settings=_settings_of(raw_scenes))
        return prompts, motions
    _log(f"AI không sinh được hồ sơ cảnh vật sau {_MAX_ATTEMPTS} lần thử "
         f"(lý do cuối: {last_reason}) — dừng trước bước tạo media.")
    return None


def generate_scene_prompts(world: str, mood: str, idea: str, style: str, api_config,
                           scene_count: int, log=None, avoid: list[str] | None = None,
                           instrument: str = "") -> Optional[dict[str, str]]:
    """Sinh (lại) N prompt cảnh từ khoá nơi chốn + không khí đã chốt."""
    from .prompt_gen import _MAX_ATTEMPTS, _resolve_instrument
    _log = _logger(log)
    if len((world or "").strip()) < 20 or len((mood or "").strip()) < 20:
        _log("Thiếu khối nơi chốn/không khí — không sinh được prompt cảnh vật.")
        return None
    client, model = _client_and_model(api_config, _log)
    if client is None:
        return None
    n = int(scene_count)
    spec = _resolve_instrument(instrument, idea, api_config, _log)
    try:
        idea = to_english(idea, api_config, _log)
        avoid = to_english(list(avoid), api_config, _log) if avoid else avoid
    except TranslationError as e:
        _log(f"{e} — dừng sinh prompt cảnh.")
        return None
    user = build_scenes_user_prompt(idea, style, world, mood, n, avoid, instrument_spec=spec)
    system = _scenes_system(style)
    last_reason = "không rõ"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        final = attempt == _MAX_ATTEMPTS
        try:
            data, _ = _ask(client, model, system, user)
        except Exception as e:
            last_reason = f"gọi OpenAI (prompt cảnh) thất bại ({type(e).__name__}: {e})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        if not isinstance(data, dict):
            last_reason = "OpenAI (prompt cảnh) trả về không phải JSON"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        bad = non_english_fields(data.get("scenes"))
        if bad:
            last_reason = f"prompt cảnh còn chữ không phải tiếng Anh ({', '.join(bad[:4])})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        raw = data.get("scenes")
        hits = sorted({h for t in _scene_texts(raw) for h in living_hits(t)})
        if hits and not final:
            last_reason = f"cảnh còn nhắc người/con vật ({', '.join(hits[:6])})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — viết lại…")
            continue
        if hits:
            raw = _clean_scenes(raw)
        scenes = scene_dict(raw, n, world, mood)
        if scenes is None:
            last_reason = f"JSON prompt cảnh thiếu/không đủ {n} mục hợp lệ"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue
        _log(f"Đã sinh {n} prompt cảnh vật (tiếng Anh, không người/thú) ({model}).")
        return scenes
    _log(f"Không sinh được prompt cảnh vật sau {_MAX_ATTEMPTS} lần (lý do cuối: "
         f"{last_reason}).")
    return None


__all__ = ["generate_prompts", "generate_scene_prompts", "living_hits",
           "drop_living_sentences", "with_world_lock", "world_sheet_prompt",
           "mood_sheet_prompt", "thumbnail_prompt", "build_user_prompt",
           "build_scenes_user_prompt"]
