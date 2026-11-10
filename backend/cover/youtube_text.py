"""
cover/youtube_text.py — Tiêu đề / mô tả / hashtag YouTube cho video cover.

- Viết THUẦN 1 ngôn ngữ theo project (vi = tiếng Việt có dấu, en = US English).
- Mô tả có danh sách bài + mốc thời gian (dòng đầu 0:00 → YouTube tự chia
  chương), tên bài ghi đúng như người dùng nhập (kèm ca sĩ gốc nếu có).
- Không có API key / AI lỗi → mẫu soạn sẵn, không làm hỏng bước đăng nháp.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from backend.video import youtube_meta as ym

from .branding import STYLE_WORDS, project_cover

HEADER = {"vi": "🎵 Danh sách bài hát:", "en": "🎵 Tracklist:"}
VIDEO_LANGUAGE = {"vi": "vi", "en": "en-US"}
DEFAULT_TAGS = {
    "vi": {"ballad": "#nhacballad #cover #nhactre #ballad #nhachaynhat",
           "remix": "#nhacremix #remix #cover #nhactre #edm",
           "acoustic": "#acoustic #cover #nhactre #nhacchill",
           "original": "#cover #nhactre #nhachaynhat"},
    "en": {"ballad": "#ballad #cover #coversongs #lovesongs #playlist",
           "remix": "#remix #cover #coversongs #dancemusic #playlist",
           "acoustic": "#acoustic #cover #coversongs #chill #playlist",
           "original": "#cover #coversongs #playlist"},
}


def chapters(audio_path: Optional[str], language: str) -> list[dict]:
    """[{start_seconds, name}] từ mix_info.json; bài lặp lại (lặp cho đủ giờ)
    vẫn có mốc riêng. Tên = 'Bài – Ca sĩ gốc' nếu có."""
    if not audio_path:
        return []
    p = Path(audio_path)
    info = p.with_name(f"{p.stem}_info.json")
    if not info.exists():
        return []
    data = json.loads(info.read_text(encoding="utf-8"))
    out = []
    for t in data.get("tracks") or []:
        name = str(t.get("name") or "").strip()
        artist = str(t.get("artist") or "").strip()
        if artist:
            name = f"{name} – {artist}"
        out.append({"start_seconds": float(t.get("start_seconds") or 0), "name": name})
    return out


def _block(entries: list[dict], language: str) -> str:
    block = ym.format_tracklist(entries)
    return block.replace(ym.TRACKLIST_HEADER, HEADER[language], 1) if block else ""


def _fallback(project_name: str, channel: str, language: str, style: str,
              songs: list[str], tags: str) -> dict:
    first = ", ".join(songs[:3])
    if language == "vi":
        title = f"{project_name} | {first}"[:100] if first else project_name[:100]
        desc = (f"{project_name} — tuyển tập những bài hát cover được {channel or 'kênh'} "
                f"chọn lọc, phiên bản {STYLE_WORDS[style].split()[-1]} mới mẻ cho bạn "
                f"thư giãn, làm việc hay nghe trên đường.\n\n"
                f"Đừng quên bấm Đăng ký và bật chuông để nghe thêm nhiều bản cover mới.\n\n{tags}")
    else:
        title = f"{project_name} | {first}"[:100] if first else project_name[:100]
        desc = (f"{project_name} — a hand-picked playlist of fresh cover versions "
                f"({STYLE_WORDS[style]}) from {channel or 'our channel'}. Perfect for "
                f"relaxing, working or the daily commute.\n\n"
                f"Subscribe and turn on notifications for new covers every week.\n\n{tags}")
    return {"title": title, "description": desc, "hashtags": tags}


def generate(project_id: int, project_name: str, thumbnail_path: str,
             channel_name: str, default_hashtags: str, past_titles: list[str],
             api_config, audio_path: Optional[str], log=None) -> dict:
    """→ {title, description, hashtags, language} (language = mã ngôn ngữ video YouTube)."""
    say = ym._log_fn(log)
    c = project_cover(project_id)
    lang, style = c["language"], c["cover_style"]
    entries = chapters(audio_path, lang)
    uniq: list[str] = []
    for e in entries:
        if e["name"] not in uniq:
            uniq.append(e["name"])
    tags = " ".join(dict.fromkeys(
        (default_hashtags or "").split() + DEFAULT_TAGS[lang][style].split()))
    block = _block(entries, lang)

    def finish(meta: dict) -> dict:
        meta["description"] = ym.insert_tracklist(meta["description"], block)
        meta["language"] = VIDEO_LANGUAGE[lang]
        return meta

    fb = _fallback(project_name, channel_name, lang, style, uniq, tags)
    if not api_config or not getattr(api_config, "api_key", None):
        say("YT-meta: chưa có API key — dùng mẫu soạn sẵn.")
        return finish(fb)
    try:
        from openai import OpenAI
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:  # noqa: BLE001
        say(f"YT-meta: không tạo được client AI ({e}) — dùng mẫu soạn sẵn.")
        return finish(fb)
    model = getattr(api_config, "model", None) or "gpt-4o-mini"
    if model.lower().startswith("claude"):
        model = "gpt-4o-mini"

    language_rule = ("Vietnamese (natural, with full diacritics). Every word of "
                     "title, description and hashtags must be Vietnamese — no English "
                     "sentences." if lang == "vi" else
                     "natural US English only — no Vietnamese words.")
    recent = "\n".join(f"- {t}" for t in (past_titles or [])[:20]) or "(none yet)"
    song_list = "\n".join(f"- {s}" for s in uniq[:40]) or "(unknown)"
    user_text = (
        "Write YouTube metadata for a long music video: a playlist of COVER songs "
        f"(new {STYLE_WORDS[style]} versions of popular songs) with the lyrics shown "
        "on screen.\n"
        f"Video name chosen by the creator: \"{project_name}\"\n"
        f"Channel name: {channel_name or '(unknown)'}\n"
        f"Songs in the video (in order):\n{song_list}\n\n"
        "Look at the attached thumbnail and weave its mood into the description.\n\n"
        "STRICT REQUIREMENTS:\n"
        f"1. Language: {language_rule}\n"
        "2. title: catchy, <= 100 characters, must say it is a cover playlist and "
        "may name 1-3 of the best-known songs exactly as written above. Different "
        "from every recent title below.\n"
        "3. description: 2-4 short paragraphs; mention the channel name once; say "
        "these are cover versions; invite viewers to comment their favourite song "
        "and to subscribe. Last line = hashtags.\n"
        "3b. Do NOT write a tracklist or timestamps (the tool adds them).\n"
        "4. hashtags: one string of 8-15 lowercase #tags (no spaces inside a tag) "
        f"in the same language; include these: {tags}\n\n"
        f"Recent titles on this channel (avoid repeating):\n{recent}\n\n"
        "Return JSON with EXACTLY these keys: "
        "{\"title\": str, \"description\": str, \"hashtags\": str}")
    content: list[dict] = [{"type": "text", "text": user_text}]
    uri = ym._data_uri(thumbnail_path)
    if uri:
        content.append({"type": "image_url", "image_url": {"url": uri}})
    try:
        resp = client.chat.completions.create(
            model=model, max_tokens=1500, temperature=0.9,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content":
                       "You are a YouTube growth copywriter for a cover-music channel. "
                       "You return STRICT JSON only."},
                      {"role": "user", "content": content}])
        data = json.loads(resp.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001
        say(f"YT-meta: AI lỗi ({type(e).__name__}: {e}) — dùng mẫu soạn sẵn.")
        return finish(fb)
    title = str(data.get("title") or "").strip()[:100]
    desc = str(data.get("description") or "").strip()
    hashtags = str(data.get("hashtags") or "").strip() or tags
    if not title or not desc:
        return finish(fb)
    for tag in tags.split():
        if tag.lower() not in hashtags.lower():
            hashtags = f"{hashtags} {tag}"
    return finish({"title": title, "description": desc, "hashtags": hashtags})
