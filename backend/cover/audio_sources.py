"""Nguồn nhạc gốc dự phòng: Zing MP3 → YouTube Music → YouTube.

Mọi kết quả được chấm bằng ``song_match`` theo «Tên bài - Ca sĩ» người dùng
nhập: bản ĐÚNG TÊN + ĐÚNG CA SĨ (official) luôn được chọn trước; bản khác ca sĩ
/ Remix chỉ là phương án cuối và được ghi chú để người dùng nghe kiểm tra.
Karaoke/Beat/Instrumental không bao giờ được chọn.

Chỉ làm việc với trang/nội dung công khai.  Không nhúng khoá API riêng, không
giải mã DRM.  Mọi file được chuẩn hoá thành MP3 bằng yt-dlp/FFmpeg để phần tempo
phía sau nhận một đầu vào thống nhất.
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from . import nct, song_match
from .lyrics_web import dismiss_zing_popups

ZING_SEARCH = "https://zingmp3.vn/tim-kiem/tat-ca?q={}"
YTMUSIC_SEARCH = "https://music.youtube.com/search?q={}#songs"
YTMUSIC_RESOLVE = 4          # số ứng viên YouTube Music mở ra đọc tên ca sĩ

PROVIDER_LABEL = {"nhaccuatui": "NhacCuaTui", "zingmp3": "Zing MP3",
                  "ytmusic": "YouTube Music", "youtube": "YouTube"}
QUALITY = {"zingmp3": "zing_mp3", "ytmusic": "ytmusic_mp3", "youtube": "youtube_mp3"}


class AudioSourceError(RuntimeError):
    pass


def _query_parts(query: str) -> tuple[str, str]:
    clean = nct.official_query(query)
    parts = nct.split_query(clean)
    return (parts[0] if parts else clean, parts[1] if len(parts) > 1 else "")


def _pick(query: str, rows: list[dict]) -> dict | None:
    """Official trước; chỉ khi không có mới nhận Remix/Cover."""
    return nct.pick_best(query, rows) or nct.pick_best(
        query, rows, allow_variants=True)


def zing_rows(page, query: str, log=print) -> list[dict]:
    """Các kết quả bài hát công khai trên giao diện Zing MP3 (lỗi → [])."""
    title, artist = _query_parts(query)
    q = " ".join(x for x in (title, artist) if x)
    try:
        log(f"đang tìm trên Zing MP3 «{q}»…")
        page.goto(ZING_SEARCH.format(urllib.parse.quote(q)),
                  wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2200)
        dismiss_zing_popups(page, log=log)
        rows = page.evaluate(r"""() => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href*="/bai-hat/"]')) {
            const href = a.href || '';
            if (!href || seen.has(href)) continue;
            let row = a;
            for (let i=0; i<7 && row.parentElement; i++, row=row.parentElement) {
              const lines=(row.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
              if (lines.length >= 2 && lines.length <= 12) break;
            }
            const lines=(row.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean)
              .filter(x=>!/^(premium|vip|khác|more|\.\.\.)$/i.test(x));
            const title=(a.innerText||lines[0]||'').trim();
            const artist=((row.querySelector('.subtitle,.artist,.artists,[class*="artist"]')||{}).innerText
              || lines.find(x=>x!==title) || '').trim();
            if (title) { seen.add(href); out.push({title,artist,url:href}); }
          }
          return out;
        }""") or []
    except Exception as exc:  # giao diện đổi thì chuyển nguồn tiếp theo
        log(f"Zing MP3 chưa dùng được ({type(exc).__name__}) — thử nguồn khác…")
        return []
    for r in rows:
        r["provider"] = "zingmp3"
    return rows


def find_zing(page, query: str, log=print) -> dict | None:
    """Bài Zing MP3 khớp nhất (đúng tên + ca sĩ trước)."""
    row, _ = song_match.choose(query, zing_rows(page, query, log=log))
    return row


def ytmusic_rows(query: str, log=print, resolve: int = YTMUSIC_RESOLVE) -> list[dict]:
    """Tìm mục «Bài hát» trên YouTube Music (bản phát hành chính thức, có tên ca
    sĩ chuẩn). Trang tìm chỉ trả tên bài, nên mở thêm vài ứng viên đúng tên để
    đọc ca sĩ/thời lượng."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - lỗi cài đặt
        raise AudioSourceError("Thiếu yt-dlp; hãy cài requirements.txt") from exc
    search = " ".join(x for x in _query_parts(query) if x)
    log(f"đang tìm trên YouTube Music «{search}»…")
    base = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with YoutubeDL({**base, "extract_flat": True}) as ydl:
            data = ydl.extract_info(
                YTMUSIC_SEARCH.format(urllib.parse.quote(search)), download=False) or {}
    except Exception as exc:  # noqa: BLE001
        log(f"YouTube Music chưa dùng được ({type(exc).__name__}) — thử nguồn khác…")
        return []
    rows: list[dict] = []
    for item in data.get("entries") or []:
        if len(rows) >= resolve:
            break
        title, vid = (item or {}).get("title") or "", (item or {}).get("id") or ""
        if not vid or song_match.evaluate(query, title).tier == song_match.NONE:
            continue
        try:
            with YoutubeDL(base) as ydl:
                full = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}",
                                        download=False, process=False) or {}
        except Exception:  # noqa: BLE001 - bài bị chặn vùng/xoá → bỏ qua
            continue
        duration = full.get("duration") or 0
        if duration and not 45 <= float(duration) <= 1200:
            continue
        artists = full.get("artists") or ([full["artist"]] if full.get("artist") else [])
        rows.append({
            "title": full.get("track") or title,
            "artist": ", ".join(artists) or full.get("channel") or "",
            "channel": full.get("channel") or "",
            "url": f"https://music.youtube.com/watch?v={vid}",
            "duration": duration, "provider": "ytmusic",
        })
    return rows


def _youtube_candidates(query: str, limit: int = 12) -> list[dict]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - lỗi cài đặt
        raise AudioSourceError("Thiếu yt-dlp; hãy cài requirements.txt") from exc
    search = nct.official_query(query)
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "noplaylist": True,
    }
    with YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{search}", download=False) or {}
    rows: list[dict] = []
    for item in data.get("entries") or []:
        if not item or item.get("is_live"):
            continue
        duration = item.get("duration") or 0
        if duration and not 45 <= float(duration) <= 1200:
            continue
        vid = item.get("url") or item.get("id") or ""
        if vid and not str(vid).startswith("http"):
            vid = f"https://www.youtube.com/watch?v={vid}"
        rows.append({
            "title": item.get("title") or "",
            "artist": item.get("channel") or item.get("uploader") or "",
            "url": vid, "duration": duration,
        })
    return rows


def youtube_rows(query: str, log=print) -> list[dict]:
    log(f"đang tìm trên YouTube «{nct.official_query(query)}»…")
    rows = _youtube_candidates(query)
    for r in rows:
        r["provider"] = "youtube"
    return rows


def find_youtube(query: str, log=print) -> dict | None:
    row, _ = song_match.choose(query, youtube_rows(query, log=log))
    return row


def _download_with_ytdlp(url: str, dest: Path, stem: str) -> Path:
    from yt_dlp import YoutubeDL
    dest.mkdir(parents=True, exist_ok=True)
    template = str((dest / f"{nct.safe_name(stem)}.%(ext)s").resolve())
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bestaudio/best", "outtmpl": template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3",
            "preferredquality": "320",
        }],
    }
    with YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    expected = dest / f"{nct.safe_name(stem)}.mp3"
    if expected.is_file() and expected.stat().st_size > 32_000:
        return expected
    matches = sorted(dest.glob(f"{nct.safe_name(stem)}*.mp3"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        raise AudioSourceError("Nguồn không trả về file MP3 hợp lệ.")
    return matches[0]


def _provider_rows(provider: str, page, query: str, log) -> list[dict]:
    if provider == "zingmp3":
        return zing_rows(page, query, log=log)
    if provider == "ytmusic":
        return ytmusic_rows(query, log=log)
    try:
        return youtube_rows(query, log=log)
    except AudioSourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        log(f"YouTube chưa dùng được ({type(exc).__name__}).")
        return []


def tag(row: dict, match: song_match.Match, query: str) -> dict:
    """Ghi hạng khớp + ghi chú (để hiện cảnh báo nếu không đúng tên + ca sĩ)."""
    row["match_tier"] = match.tier
    row["match_note"] = "" if match.tier == song_match.EXACT else (
        f"⚠ Không tìm thấy bản đúng «{song_match.describe(query)}»; đã dùng "
        f"«{row.get('title', '')} – {row.get('artist', '')}» "
        f"({PROVIDER_LABEL.get(row.get('provider', ''), row.get('provider', ''))}, "
        f"{match.label}) — hãy nghe kiểm tra.")
    return row


def find_best(page, query: str, log=print, providers=("zingmp3", "ytmusic", "youtube"),
              ) -> list[tuple[dict, song_match.Match]]:
    """Tìm trên TOÀN BỘ nguồn rồi xếp official/khớp nhất lên trước.

    Không dừng khi Zing có kết quả EXACT: trang có thể tìm đúng metadata nhưng
    URL audio hết hạn/chặn tải. Giữ sẵn ứng viên YouTube Music/YouTube giúp bước
    tải chuyển nguồn ngay mà không phải chạy lại cả job.
    """
    pool: list[tuple[dict, song_match.Match]] = []
    for provider in providers:
        try:
            rows = _provider_rows(provider, page, query, log)
        except AudioSourceError as exc:
            log(f"{PROVIDER_LABEL.get(provider, provider)} chưa dùng được ({exc}) — "
                "giữ các nguồn đã tìm thấy và tiếp tục…")
            continue
        ranked = song_match.rank(query, rows)
        pool.extend(ranked)
    # sort ổn định: cùng hạng thì nguồn tìm trước (Zing > YT Music > YouTube) thắng
    pool.sort(key=lambda rm: rm[1].tier, reverse=True)
    return pool


def download_row(row: dict, dest: Path, stem: str) -> tuple[Path, str]:
    path = _download_with_ytdlp(row["url"], dest, stem)
    return path, QUALITY.get(row.get("provider", ""), "youtube_mp3")


def download_fallback(page, dest: Path, stem: str, query: str, log=print,
                      candidates: list[tuple[dict, song_match.Match]] | None = None,
                      ) -> tuple[Path, str, dict]:
    """Zing MP3 → YouTube Music → YouTube, chọn theo «Tên bài - Ca sĩ»: bản đúng
    tên + ca sĩ trước; Remix/khác ca sĩ chỉ khi không còn gì hơn (kèm ghi chú
    ``match_note``). ``candidates`` = kết quả ``find_best`` đã tìm sẵn."""
    errors: list[str] = []
    pool = candidates if candidates is not None else find_best(page, query, log=log)
    if not pool:
        raise AudioSourceError(
            f"Không tìm thấy «{song_match.describe(query)}» trên Zing MP3/YouTube Music/"
            "YouTube (đã bỏ qua bản Karaoke/Beat và bài khác tên).")
    # Không cắt ở 6: một nguồn có thể trả nhiều URL metadata đúng nhưng tất cả
    # cùng hết hạn. Giới hạn 18 vẫn tránh tải vô hạn, đồng thời đủ để chạm tới
    # ứng viên của nguồn kế tiếp.
    for row, match in pool[:18]:
        name = PROVIDER_LABEL.get(row.get("provider", ""), "nguồn dự phòng")
        log(f"tải từ {name}: «{row.get('title', '')} – {row.get('artist', '')}» "
            f"({match.label})…")
        try:
            path, quality = download_row(row, dest, stem)
            return path, quality, tag(row, match, query)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {str(exc)[:160]}")
            log(f"{name} không tải được — thử ứng viên tiếp theo…")
    raise AudioSourceError("Không tải được từ Zing MP3/YouTube Music/YouTube. "
                           + "; ".join(errors))


def metadata_from_query(query: str) -> dict:
    title, artist = _query_parts(query)
    return {"title": title, "artist": artist, "url": "", "lyrics": ""}
