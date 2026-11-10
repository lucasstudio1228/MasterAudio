"""Catalog thể loại chuẩn dùng để viết Styles cho Suno Cover.

``music_line`` (thị trường/khu vực) không phải thể loại.  Module này là nguồn
duy nhất cho BPM âm nhạc, groove, hòa âm, nhạc cụ và cấu trúc của genre; tempo
32/35 trong Suno Editor là một tham số workflow hoàn toàn khác.
"""
from __future__ import annotations

import re
import unicodedata


GENRE_PROFILES: dict[str, dict] = {
    "ballad": {
        "label": "Ballad", "aliases": ("ballad",), "bpm": "68-82 BPM",
        "groove": "slow 4/4, restrained kick and snare, spacious pulse",
        "music": "warm piano, acoustic guitar, soft strings, simple diatonic harmony",
        "arrangement": "intimate verse, gradual pre-chorus lift, emotionally wider chorus",
        "mix": "natural dynamics, warm and spacious vocal-forward mix",
        "avoid": "no dance drop, no club bass, no instrumental solo",
    },
    "pop": {
        "label": "Pop", "aliases": ("pop", "modern pop"), "bpm": "95-120 BPM",
        "groove": "steady polished pop drums with a clear backbeat",
        "music": "clean bass, bright keys, clean rhythm guitar, concise melodic hooks",
        "arrangement": "verse-pre-chorus-chorus form with strong section contrast and repeated vocal hook",
        "mix": "bright balanced radio-ready vocal-forward mix",
        "avoid": "no EDM drop unless explicitly selected, no instrumental solo",
    },
    "rnb": {
        "label": "Contemporary R&B", "aliases": ("r&b", "rnb", "rhythm and blues"),
        "bpm": "68-96 BPM", "groove": "laid-back syncopated pocket, subtle swing and ghost notes",
        "music": "round sub-bass, Rhodes, restrained guitar, rich 7th and 9th chords",
        "arrangement": "intimate verses, vocal ad-libs between lines, layered but lyric-led chorus",
        "mix": "smooth modern low end with clear close lead vocal",
        "avoid": "no house beat, no rock guitars, no instrumental solo",
    },
    "rock": {
        "label": "Rock", "aliases": ("rock", "pop rock", "pop-rock"), "bpm": "90-140 BPM",
        "groove": "driving live drum kit with a firm snare backbeat",
        "music": "electric rhythm guitars, solid electric bass, supporting power-chord layers",
        "arrangement": "dynamic verse-chorus band arrangement with a strong vocal anthem chorus",
        "mix": "punchy organic band mix with centered intelligible lead vocal",
        "avoid": "no electronic dance drop, no guitar solo, no long instrumental break",
    },
    "acoustic": {
        "label": "Acoustic", "aliases": ("acoustic", "unplugged"), "bpm": "72-105 BPM",
        "groove": "gentle organic pulse with light hand percussion or brushed kit",
        "music": "fingerpicked acoustic guitar, soft piano, warm natural bass",
        "arrangement": "small intimate arrangement that grows subtly around the chorus",
        "mix": "natural unplugged room sound with close clear lead vocal",
        "avoid": "no synth drop, no heavy drums, no instrumental solo",
    },
    "jazz": {
        "label": "Vocal Jazz", "aliases": ("jazz", "vocal jazz"), "bpm": "70-120 BPM",
        "groove": "relaxed swing or laid-back straight-eighth groove with brushed drums",
        "music": "upright bass, piano comping, extended 7th/9th harmony, subtle clean guitar or muted horn color",
        "arrangement": "vocal-led small-combo form with short answers only between sung phrases",
        "mix": "natural live-room balance with warm centered vocal",
        "avoid": "no bebop solo, no extended improvisation, no instrumental break",
    },
    "blues": {
        "label": "Vocal Blues", "aliases": ("blues", "blue", "slow blues"), "bpm": "65-100 BPM",
        "groove": "slow 12/8 or restrained shuffle groove",
        "music": "I-IV-V blues harmony, warm organ, steady bass, restrained electric guitar responses",
        "arrangement": "vocal call-and-response with brief fills only after lyric phrases",
        "mix": "warm organic vintage-toned mix with raw clear lead vocal",
        "avoid": "no guitar solo, no long jam, no dance drop",
    },
    "lofi": {
        "label": "Lo-fi", "aliases": ("lofi", "lo-fi", "lo fi"), "bpm": "70-90 BPM",
        "groove": "lazy swung boom-bap drums, soft kick and snare",
        "music": "mellow Rhodes, jazz-colored chords, rounded bass, subtle tape and vinyl texture",
        "arrangement": "minimal repeating bed with small changes under continuous lead singing",
        "mix": "soft textured backing but clean intelligible lead vocal above it",
        "avoid": "no muffled vocal, no beat-only passage, no instrumental solo",
    },
    "house": {
        "label": "House", "aliases": ("house",), "bpm": "120-128 BPM",
        "groove": "four-on-the-floor kick, clap on 2 and 4, offbeat hi-hats",
        "music": "syncopated synth bass, chord stabs and restrained pads",
        "arrangement": "vocal-led club arrangement with short transitions and a hook-focused chorus",
        "mix": "clean sidechained club mix with the lead vocal above the groove",
        "avoid": "no festival drop, no instrumental solo, no long DJ intro",
    },
    "deep house": {
        "label": "Deep House", "aliases": ("deep house", "deephouse"), "bpm": "118-124 BPM",
        "groove": "restrained four-on-the-floor kick, swung hats and spacious percussion",
        "music": "round deep bassline, Rhodes or organ 7th/9th chords, warm pads and subtle stabs",
        "arrangement": "gradual hypnotic groove with intimate verses and a vocal-hook chorus",
        "mix": "warm spacious club mix, controlled sidechain, vocal clearly above the groove",
        "avoid": "no aggressive EDM build, no festival drop, no instrumental solo",
    },
    "future house": {
        "label": "Future House", "aliases": ("future house", "futurehouse"), "bpm": "124-128 BPM",
        "groove": "punchy four-on-the-floor drums with crisp claps and syncopated bounce",
        "music": "bouncy modulated bass, metallic or pluck chord stabs, tight sidechained synth layers",
        "arrangement": "compact build into a vocal-hook chorus with short controlled dance transitions",
        "mix": "bright punchy modern club mix with dominant lead vocal",
        "avoid": "no dubstep section, no extended drop, no instrumental solo",
    },
    "tropical house": {
        "label": "Tropical House", "aliases": ("tropical house", "tropical"), "bpm": "100-115 BPM",
        "groove": "relaxed four-on-the-floor groove with light syncopated percussion",
        "music": "warm bass, marimba or mallet plucks, muted guitar and airy flute-like texture",
        "arrangement": "breezy vocal-led verses and a gentle melodic chorus without a hard drop",
        "mix": "soft sunny spacious dance mix with clear lead vocal",
        "avoid": "no aggressive festival drop, no steel-drum cliché overload, no instrumental solo",
    },
    "vietmix": {
        "label": "Vietmix", "aliases": ("vietmix", "viet mix", "mix viet", "mix việt"),
        "bpm": "124-128 BPM", "groove": "steady four-on-the-floor dance groove with firm kick and clap",
        "music": "rolling bass, modern synth chords, concise risers and restrained transition effects",
        "arrangement": "Vietnamese vocal dance-remix form that preserves the original melody and keeps each chorus vocal-led",
        "mix": "energetic clean club mix with vocals clearly louder than the backing",
        "avoid": "no new genre fusion, no festival instrumental drop, no instrumental solo",
    },
    "dance remix": {
        "label": "Dance Remix", "aliases": ("dance remix", "remix", "edm", "dance"),
        "bpm": "120-130 BPM", "groove": "punchy four-on-the-floor dance drums",
        "music": "firm bass, modern synth chords, concise builds and transition effects",
        "arrangement": "vocal-led remix structure preserving the original melody with short transitions into each chorus",
        "mix": "energetic polished dance mix with dominant intelligible lead vocal",
        "avoid": "no genre fusion, no long instrumental drop, no instrumental solo",
    },
}


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold()).replace("đ", "d")
    return re.sub(r"[^a-z0-9&]+", " ", "".join(c for c in value if not unicodedata.combining(c))).strip()


def genre_key(*values: str) -> str:
    """Nhận dạng theo thứ tự ưu tiên; giá trị đầu tiên hợp lệ luôn thắng."""
    aliases = sorted(
        ((alias, key) for key, item in GENRE_PROFILES.items() for alias in item["aliases"]),
        key=lambda pair: len(pair[0]), reverse=True)
    for value in values:
        text = _fold(value)
        if not text:
            continue
        for alias, key in aliases:
            folded_alias = _fold(alias)
            if re.search(rf"(?<![a-z0-9]){re.escape(folded_alias)}(?![a-z0-9])", text):
                return key
    return ""


def genre_profile(*values: str) -> dict | None:
    key = genre_key(*values)
    if not key:
        return None
    return {"key": key, **GENRE_PROFILES[key]}


def canonical_genre_context(*values: str) -> dict:
    profile = genre_profile(*values)
    if not profile:
        return {}
    return {name: profile[name] for name in
            ("key", "label", "bpm", "groove", "music", "arrangement", "mix", "avoid")}


def genre_prompt_core(*values: str) -> str:
    profile = genre_profile(*values)
    if not profile:
        raw = next((" ".join(v.split()) for v in values if (v or "").strip()), "Contemporary Pop")
        return (f"{raw} vocal cover, genre-authentic rhythm and harmony, coherent vocal-led "
                "arrangement, polished modern mix, no genre fusion, no instrumental solo")
    return ", ".join((
        f"{profile['label']} vocal cover", profile["bpm"], profile["groove"],
        profile["music"], profile["arrangement"], profile["mix"], profile["avoid"],
    ))
