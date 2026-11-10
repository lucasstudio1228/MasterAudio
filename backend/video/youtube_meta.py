"""
video/youtube_meta.py — Sinh title / description / hashtags cho video bằng AI
(GPT-4o vision), ĐỌC thumbnail để mô tả đúng nội dung.

Yêu cầu của người dùng:
  • Ngôn ngữ: Anh–Mỹ (US English).
  • AI phải "có bộ nhớ": tránh trùng lặp với các title đã đăng trước của cùng
    kênh (past_titles) → mỗi video một title/description KHÁC nhau.
  • Description phải "soi tên kênh" → nhắc channel_name.
  • Nếu thiếu API key / không đọc được ảnh → vẫn trả metadata mặc định hợp lý
    (dựa trên tên kênh + nhạc cụ + phong cách) để pipeline không gãy.
  • Description KHÔNG kèm tracklist / mốc thời gian (bỏ từ 2026-10-07, theo
    yêu cầu người dùng). `tracklist=` chỉ còn là tuỳ chọn; upload không truyền.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

_FALLBACK_MODEL = "gpt-4o-mini"     # hỗ trợ vision + JSON mode

# Giới hạn cứng của YouTube cho ô mô tả.
DESCRIPTION_LIMIT = 5000
TRACKLIST_HEADER = "🎧 Tracklist (chapters):"


# ── Tracklist: track lẻ + khung thời gian ────────────────────────────────

def _fmt_timestamp(seconds: float) -> str:
    """Mốc chương YouTube: M:SS khi < 1 giờ, H:MM:SS khi >= 1 giờ.

    KHÔNG dùng MM:SS kiểu '117:23' của mix_tracklist.txt — YouTube không nhận
    phút > 59 nên chương sẽ không hiện."""
    total = int(max(float(seconds), 0.0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load_tracklist(audio_path: Optional[str]) -> list[dict]:
    """Đọc danh sách track lẻ (name + start_seconds) của bản mix dùng cho video.

    Nguồn: `<stem>_info.json` do `metadata_writer.write_metadata` ghi cạnh file
    nhạc (vd `outputs/<project>/<mix>/mix_info.json`). Audio là file upload thủ
    công (không qua bước Mix) ⇒ không có file này ⇒ trả [] (không đoán mò)."""
    if not audio_path:
        return []
    p = Path(audio_path)
    info = p.with_name(f"{p.stem}_info.json")
    if not info.exists():
        return []
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries: list[dict] = []
    for i, t in enumerate(data.get("tracks") or []):
        try:
            start = float(t.get("start_seconds"))
        except (TypeError, ValueError):
            continue
        name = str(t.get("name") or "").strip() or f"Track {i + 1}"
        entries.append({"start_seconds": max(start, 0.0), "name": name})
    entries.sort(key=lambda e: e["start_seconds"])
    return entries


def format_tracklist(entries: list[dict], max_chars: int = 3000) -> str:
    """Khối tracklist cho description. Dòng đầu LUÔN 0:00 (điều kiện để YouTube
    bật chapters). Quá dài thì cắt bớt track cuối và ghi chú rõ."""
    if not entries:
        return ""
    lines = [TRACKLIST_HEADER]
    for i, e in enumerate(entries):
        ts = "0:00" if i == 0 else _fmt_timestamp(e["start_seconds"])
        lines.append(f"{ts} {i + 1:02d}. {e['name']}")
    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block
    kept: list[str] = [lines[0]]
    for line in lines[1:]:
        if len("\n".join(kept)) + len(line) + 40 > max_chars:
            break
        kept.append(line)
    kept.append(f"(+{len(lines) - len(kept)} tracks)")
    return "\n".join(kept)


def insert_tracklist(description: str, block: str,
                     limit: int = DESCRIPTION_LIMIT) -> str:
    """Chèn khối tracklist vào description, GIỮ dòng hashtag ở cuối cùng."""
    if not block:
        return description
    body = (description or "").rstrip()
    lines = body.split("\n")
    tail = ""
    # Dòng cuối là hashtag → tracklist chèn TRƯỚC nó.
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].lstrip().startswith("#"):
        tail = lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    parts = ["\n".join(lines).rstrip(), block]
    if tail:
        parts.append(tail)
    out = "\n\n".join(p for p in parts if p)
    return out[:limit].rstrip()


def _log_fn(log):
    def _l(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _l


def _data_uri(image_path: str) -> Optional[str]:
    p = Path(image_path)
    if not p.exists():
        return None
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return None
    return f"data:{mime};base64,{b64}"


def _fallback_meta(channel_name: str, instrument: str, music_style: str,
                   default_hashtags: str) -> dict:
    ch = (channel_name or "our channel").strip()
    instr = (instrument or "relaxing").strip()
    style = (music_style or "healing").strip()
    title = f"Peaceful {instr.title()} Music for {style.title()} & Relaxation"
    description = (
        f"Relax and unwind with soothing {instr} music from {ch}. "
        f"This {style} soundscape is perfect for meditation, deep sleep, "
        f"stress relief, studying and calming the mind.\n\n"
        f"Thank you for listening to {ch}. Subscribe for more healing music.\n\n"
        f"{default_hashtags}".strip()
    )
    return {
        "title": title[:100],
        "description": description,
        "hashtags": (default_hashtags or "#relaxingmusic #healing #meditation"),
    }


def generate_metadata(
    thumbnail_path: str,
    channel_name: str,
    instrument: str,
    music_style: str,
    default_hashtags: str,
    past_titles: list[str],
    api_config,
    log=None,
    tracklist: Optional[list[dict]] = None,
) -> dict:
    """Trả {'title','description','hashtags'} (US English). Không raise —
    lỗi/khuyết cấu hình → dùng _fallback_meta.

    `tracklist`: [{'name', 'start_seconds'}] của các track lẻ trong bản mix
    (xem `load_tracklist`). Chỉ chèn khi được truyền — upload mặc định KHÔNG truyền."""
    _log = _log_fn(log)
    block = format_tracklist(tracklist or [])
    if block:
        _log(f"YT-meta: chèn tracklist {len(tracklist)} track vào mô tả.")

    def _finish(meta: dict) -> dict:
        meta["description"] = insert_tracklist(meta["description"], block)
        return meta

    fallback = _fallback_meta(channel_name, instrument, music_style,
                              default_hashtags)

    if not api_config or not getattr(api_config, "api_key", None):
        _log("YT-meta: chưa có API key — dùng metadata mặc định.")
        return _finish(fallback)

    try:
        from openai import OpenAI
    except Exception as e:
        _log(f"YT-meta: không import được openai ({e}) — dùng mặc định.")
        return _finish(fallback)

    model = getattr(api_config, "model", None) or _FALLBACK_MODEL
    if model.lower().startswith("claude"):
        model = _FALLBACK_MODEL

    try:
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        _log(f"YT-meta: không tạo được client ({e}) — dùng mặc định.")
        return _finish(fallback)

    recent = "\n".join(f"- {t}" for t in (past_titles or [])[:20]) or "(none yet)"
    sys_msg = (
        "You are a YouTube growth copywriter for a relaxing / healing music "
        "channel. You write in natural US English. You return STRICT JSON only."
    )
    user_text = (
        "Write metadata for a new long-form relaxing music video.\n"
        f"Channel name: {channel_name or '(unknown)'}\n"
        f"Instrument: {instrument or '(unspecified)'}\n"
        f"Music style / mood: {music_style or '(unspecified)'}\n"
        f"Default hashtags to include: {default_hashtags or '(none)'}\n\n"
        "Look at the attached thumbnail image and describe the actual scene "
        "(setting, mood, colors) in the description.\n\n"
        "STRICT REQUIREMENTS:\n"
        "1. Language: US English.\n"
        "2. title: catchy, <= 100 characters, evokes calm/healing/sleep/focus. "
        "Must be DIFFERENT from every recent title below (no duplicates, vary "
        "wording and angle).\n"
        "3. description: 3-5 short paragraphs. Mention the channel name "
        f"\"{channel_name}\" naturally at least once. Describe the thumbnail "
        "scene. Suggest uses (sleep, study, meditation, spa, stress relief). "
        "End with a subscribe call-to-action and then the hashtags on the last "
        "line.\n"
        "3b. Do NOT write any tracklist, track names, chapter list or "
        "timestamps in the description.\n"
        "4. hashtags: a single string of 8-15 space-separated #tags relevant to "
        "the instrument, style and relaxation niche; include the default "
        "hashtags. Lowercase, no spaces inside a tag.\n\n"
        "Recent titles already used on this channel (AVOID repeating these):\n"
        f"{recent}\n\n"
        "Return JSON with EXACTLY these keys: "
        "{\"title\": str, \"description\": str, \"hashtags\": str}"
    )

    content: list[dict] = [{"type": "text", "text": user_text}]
    uri = _data_uri(thumbnail_path)
    if uri:
        content.append({"type": "image_url", "image_url": {"url": uri}})
    else:
        _log("YT-meta: không đọc được thumbnail — viết mô tả không ảnh.")

    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1500,
            temperature=0.9,          # đa dạng, tránh trùng lặp
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": content},
            ],
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text)
    except Exception as e:
        _log(f"YT-meta: gọi AI thất bại ({type(e).__name__}: {e}) — dùng mặc định.")
        return _finish(fallback)

    title = str(data.get("title") or "").strip()[:100]
    description = str(data.get("description") or "").strip()
    hashtags = str(data.get("hashtags") or "").strip()
    if not title or not description:
        _log("YT-meta: AI trả thiếu title/description — dùng mặc định.")
        return _finish(fallback)
    if not hashtags:
        hashtags = default_hashtags or "#relaxingmusic #healing #meditation"
    # Đảm bảo hashtag mặc định luôn có mặt.
    if default_hashtags:
        for tag in default_hashtags.split():
            if tag and tag.lower() not in hashtags.lower():
                hashtags = f"{hashtags} {tag}".strip()
    return _finish({"title": title, "description": description,
                    "hashtags": hashtags})
