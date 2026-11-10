"""
cover/overlay.py — Lớp chữ trên video cover (đốt thẳng vào hình bằng libass).

Bố cục:
- INTRO/THUMBNAIL: Track lẻ hiện tên bài thật lớn; Mix Set hiện playlist trong
  vài giây mở đầu.
- SAU INTRO, GÓC TRÁI: chỉ tên bài đang phát, không hiện tên ca sĩ.
- DƯỚI GIỮA: lời 3 dòng — câu trước (mờ) / câu đang hát (to, sáng) / câu kế
  (mờ). Đổi theo từng câu, không chạy chữ kiểu karaoke.
- Không đè lên intro thumbnail (chỉ hiện sau lead_seconds).

Thời gian: mix_info.json (cover/mix.py) cho biết mỗi đoạn bắt đầu ở giây nào
của mix và ứng với giây nào trong file bài (src_offset_seconds); audio mix bắt
đầu đúng 00:00 của video → giây mix = giây video.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .lyrics import load_lines

DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"
FONT = "Segoe UI"           # có đủ dấu tiếng Việt, sẵn trên mọi máy Windows
ROW_MAX = 9                 # số bài hiện cùng lúc (mỗi bài 2 dòng: tên / ca sĩ); nhiều hơn → bảng tự cuộn
LINE_HOLD_SEC = 2.0         # câu hát còn hiện thêm sau khi hát xong (nếu chưa tới câu sau)
# Sóng âm (equalizer) sát đáy video — kích thước theo khung 1920×1080.
WAVE_BARS = 64              # số cột
WAVE_W, WAVE_H = 1536, 90   # rộng / cao cả dải cột
WAVE_BOTTOM = 12            # cách mép dưới
WAVE_ALPHA = 0.7            # độ đậm của cột trắng
WAVE_LIFT = 0.06            # có sóng âm → lời bài hát nhích lên (tỉ lệ chiều cao)
PREVIEW_SEC = 4.0           # đoạn nhạc dạo: hiện trước câu sắp hát (mờ) tối đa bấy nhiêu giây
# Bề rộng ước lượng 1 ký tự (× cỡ chữ, Segoe UI đậm) — hơi dư để không tràn.
_NARROW = set("iljtfrI.,:;'!|·()[] ")
_WIDE = set("MWmwOQGDHNU@%")


def _text_w(text: str, size: float) -> float:
    w = 0.0
    for ch in text:
        if ch in _NARROW:
            w += 0.30
        elif ch in _WIDE:
            w += 0.80
        elif ch.isupper() or ch.isdigit():
            w += 0.64
        else:
            w += 0.54
    return w * size

Log = Optional[Callable[[str, float], None]]

TEXT = {
    "vi": {"header": "DANH SÁCH BÀI HÁT"},
    "en": {"header": "TRACKLIST"},
}


# ---------------- tiện ích ASS ----------------
def _ts(t: float) -> str:
    t = max(t, 0.0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _fit(text: str, size: float, width: float) -> str:
    """Cắt chữ (thêm …) cho vừa bề rộng ước lượng."""
    if _text_w(text, size) <= width:
        return text
    while len(text) > 4 and _text_w(text + "…", size) > width:
        text = text[:-1]
    return text.rstrip(" ·-–") + "…"


def _fit_size(text: str, base: float, width: float, floor: float) -> int:
    """Câu dài → thu nhỏ cỡ chữ cho vừa 1 dòng (không xuống dòng)."""
    need = _text_w(text, base)
    return int(base if need <= width else max(base * width / need, floor))


def _rrect(x: float, y: float, w: float, h: float, r: float) -> str:
    """Hình chữ nhật bo góc (lệnh vẽ ASS \\p1)."""
    x2, y2, k = x + w, y + h, r * 0.45
    p = lambda v: str(int(round(v)))  # noqa: E731
    return (f"m {p(x + r)} {p(y)} l {p(x2 - r)} {p(y)} "
            f"b {p(x2 - k)} {p(y)} {p(x2)} {p(y + k)} {p(x2)} {p(y + r)} "
            f"l {p(x2)} {p(y2 - r)} b {p(x2)} {p(y2 - k)} {p(x2 - k)} {p(y2)} {p(x2 - r)} {p(y2)} "
            f"l {p(x + r)} {p(y2)} b {p(x + k)} {p(y2)} {p(x)} {p(y2 - k)} {p(x)} {p(y2 - r)} "
            f"l {p(x)} {p(y + r)} b {p(x)} {p(y + k)} {p(x + k)} {p(y)} {p(x + r)} {p(y)}")


# ---------------- dữ liệu ----------------
def _load_cover_data(project_id: int) -> tuple[dict, dict[int, dict]]:
    """→ (settings, {song_id: {title, artist, lines}})."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute("SELECT language, show_tracklist, show_lyrics, show_waveform "
                           "FROM coversettings WHERE project_id = ?", (project_id,)).fetchone()
        kind_row = conn.execute("SELECT name, kind FROM project WHERE id=?",
                                (project_id,)).fetchone()
        songs = conn.execute(
            "SELECT cs.id, cs.title, cs.artist, cs.lyrics_json, t.filepath "
            "FROM coversong cs JOIN track t ON t.id=cs.track_id "
            "WHERE cs.project_id = ? ORDER BY cs.position, cs.id",
            (project_id,)).fetchall()
    finally:
        conn.close()
    from .genre_defaults import effective_cover
    try:
        from backend.video import channel_looks, thumbnail_recipe, thumbnail
        mapping = channel_looks.mapping_for_project(project_id) or {}
        recipe = thumbnail_recipe.recipe_for_project(
            project_id, kind_row[0] if kind_row else None) or {}
        font = thumbnail.font_entry(recipe.get("font_lock") or recipe.get("font"))
        channel_name = (channel_looks.channel_name_for_project(project_id)
                        or recipe.get("channel_name") or "MUSIC CHANNEL").strip()
        channel_font = (font or {}).get("label") or FONT
    except Exception:
        channel_name, channel_font = "MUSIC CHANNEL", FONT
    cfg = {"language": effective_cover(project_id, DB_PATH)["language"],
           "kind": "single" if kind_row and kind_row[1] == "single" else "mixset",
           "channel_name": channel_name, "channel_font": channel_font,
           # Quy chuẩn video cover: luôn có tên bài hiện tại và lyrics.
           "show_tracklist": True,
           "show_lyrics": True,
           "show_waveform": bool(row[3]) if row else True}
    return cfg, {sid: {"title": t, "artist": a, "lines": load_lines(j),
                       "filepath": filepath}
                 for sid, t, a, j, filepath in songs}


def _segments(info: dict, total: float) -> list[dict]:
    """Đoạn phát trong mix + mốc đổi bài (giữa vùng crossfade)."""
    tracks = info.get("tracks") or []
    segs = []
    for i, t in enumerate(tracks):
        start = float(t["start_seconds"])
        end = float(t["end_seconds"])
        nxt = tracks[i + 1] if i + 1 < len(tracks) else None
        switch_out = ((float(nxt["start_seconds"]) + end) / 2) if nxt else total
        segs.append({"key": t.get("song_id") or t.get("source_file") or t["name"],
                     "song_id": t.get("song_id"), "name": t["name"],
                     "artist": t.get("artist") or "",
                     "start": start, "end": end,
                     "src_off": float(t.get("src_offset_seconds") or 0.0),
                     "switch_out": switch_out})
    for i, s in enumerate(segs):
        s["switch_in"] = segs[i - 1]["switch_out"] if i else 0.0
    return segs


# ---------------- dựng file .ass ----------------
def build_ass(info: dict, songs: dict[int, dict], cfg: dict, total: float,
              start_after: float, W: int = 1920, H: int = 1080) -> str:
    seg = _segments(info, total)
    lang = cfg.get("language") if cfg.get("language") in TEXT else "vi"
    sx = W / 1920.0
    ev: list[str] = []

    def add(layer, t0, t1, style, text, fade_in=False):
        t0 = max(t0, start_after)
        if t1 - t0 < 0.05:
            return
        fx = r"{\fad(600,0)}" if fade_in or abs(t0 - start_after) < 0.01 else ""
        ev.append(f"Dialogue: {layer},{_ts(t0)},{_ts(t1)},{style},,0,0,0,,{fx}{text}")

    # Dấu nhận diện kênh luôn hiện xuyên suốt video, kể cả intro. Font lấy từ
    # khoá font của kênh nên mọi video cùng kênh có cùng một kiểu chữ.
    channel = _esc(cfg.get("channel_name") or "MUSIC CHANNEL")
    ev.append(
        f"Dialogue: 8,{_ts(0)},{_ts(total)},Channel,,0,0,0,,"
        rf"{{\an9\pos({W - 54 * sx:.0f},{42 * sx:.0f})\fad(500,0)}}{channel}")

    # ---- Tên bài đang phát ở góc (không hiện ca sĩ) ----
    # Playlist đầy đủ chỉ nằm trên thumbnail/intro vài giây đầu; sau đó video
    # thoáng, chỉ còn tên bài hiện tại. Track lẻ cũng dùng nhãn gọn này sau intro.
    if cfg.get("show_tracklist", True) and seg:
        for s in seg:
            t0, t1 = s["switch_in"], s["switch_out"]
            if t1 <= start_after:
                continue
            title = _esc(songs.get(s["song_id"], {}).get("title") or s["name"])
            px, py, pw, ph = 54 * sx, 48 * sx, 720 * sx, 82 * sx
            inner = pw - 44 * sx
            add(1, t0, t1, "Panel",
                r"{\an7\pos(0,0)\1c&H080808&\1a&H58&\p1}"
                + _rrect(px, py, pw, ph, 18 * sx) + r"{\p0}", fade_in=True)
            fs = _fit_size(title, 36 * sx, inner, 22 * sx)
            add(2, t0, t1, "NowTitle",
                rf"{{\pos({px + 22 * sx:.0f},{py + ph / 2:.0f})\fs{fs}}}"
                + _fit(title, fs, inner), fade_in=True)

    # ---- Lời 3 dòng (dưới giữa) ----
    if cfg.get("show_lyrics", True):
        cx = W / 2
        avail = W - 2 * 110 * sx
        lift = WAVE_LIFT if cfg.get("show_waveform") else 0.0   # chừa chỗ cho sóng âm
        y_prev, y_cur, y_next = H * (0.795 - lift), H * (0.868 - lift), H * (0.935 - lift)

        def put(t0, t1, which, text):
            if not text:
                return
            base, floor, style, y = {
                "prev": (40 * sx, 26 * sx, "LyDim", y_prev),
                "cur": (60 * sx, 34 * sx, "LyCur", y_cur),
                "next": (40 * sx, 26 * sx, "LyDim", y_next),
            }[which]
            fs = _fit_size(text, base, avail, floor)
            add(3, t0, t1, style, rf"{{\pos({cx:.0f},{y:.0f})\fs{fs}}}" + _esc(text))

        for s in seg:
            lines = songs.get(s["song_id"], {}).get("lines") or []
            mix = []
            limit = s["switch_out"]
            for l in lines:
                t0 = s["start"] + (l["start"] - s["src_off"])
                t1 = s["start"] + (l["end"] - s["src_off"])
                if t0 < s["start"] - 0.05 or t0 >= s["end"]:
                    continue
                # Vùng crossfade: lời mỗi bài chỉ hiện trong khung của bài đó
                # (đổi bài ở giữa crossfade, cùng lúc bảng danh sách đổi dòng) →
                # câu cuối bài trước và câu đầu bài sau không bao giờ đè nhau.
                t0 = max(t0, s["switch_in"])
                if min(t1, limit) - t0 < 0.3:
                    continue
                mix.append({"text": l["text"], "t0": t0, "t1": min(t1, s["end"])})
            for i, l in enumerate(mix):
                nxt = mix[i + 1]["t0"] if i + 1 < len(mix) else limit
                show_end = min(max(l["t1"], l["t0"] + 0.4) + LINE_HOLD_SEC, nxt, limit)
                prev = mix[i - 1]["text"] if i else ""
                following = mix[i + 1]["text"] if i + 1 < len(mix) else ""
                put(l["t0"], show_end, "prev", prev)
                put(l["t0"], show_end, "cur", l["text"])
                put(l["t0"], show_end, "next", following)
                # Nhạc dạo giữa 2 câu: chỉ hé lộ câu sắp hát (mờ) ngay trước khi vào.
                if following and nxt - show_end > 0.3:
                    put(max(show_end, nxt - PREVIEW_SEC), nxt, "next", following)
            if mix:   # nhạc dạo đầu bài
                a = max(s["switch_in"], mix[0]["t0"] - PREVIEW_SEC)
                put(a, mix[0]["t0"], "next", mix[0]["text"])

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Panel,{FONT},20,&H58000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Head,{FONT},{int(30 * sx)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,{2 * sx:.1f},0,1,0,0,7,0,0,0,1
Style: Row,{FONT},{int(29 * sx)},&H40FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,0,{1 * sx:.1f},4,0,0,0,1
Style: RowCur,{FONT},{int(31 * sx)},&H0060D8FF,&H00FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,{1 * sx:.1f},4,0,0,0,1
Style: Artist,{FONT},{int(23 * sx)},&H70FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,0,{1 * sx:.1f},4,0,0,0,1
Style: ArtistCur,{FONT},{int(23 * sx)},&H20A8E8FF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,0,{1 * sx:.1f},4,0,0,0,1
Style: NowTitle,{FONT},{int(36 * sx)},&H00FFFFFF,&H00FFFFFF,&H80000000,&H70000000,-1,0,0,0,100,100,{0.5 * sx:.1f},0,1,{2 * sx:.1f},{1 * sx:.1f},4,0,0,0,1
Style: LyCur,{FONT},{int(60 * sx)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{3 * sx:.1f},{2 * sx:.1f},5,0,0,0,1
Style: LyDim,{FONT},{int(40 * sx)},&H60FFFFFF,&H00FFFFFF,&H60000000,&HB0000000,0,0,0,0,100,100,0,0,1,{2 * sx:.1f},{1 * sx:.1f},5,0,0,0,1
Style: Channel,{cfg.get('channel_font') or FONT},{int(34 * sx)},&H00F4E3C1,&H00FFFFFF,&H90000000,&H70000000,-1,1,0,0,100,100,{1.4 * sx:.1f},0,1,{2.5 * sx:.1f},{2 * sx:.1f},9,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return head + "\n".join(ev) + "\n"


# ---------------- đốt vào video ----------------
def _has_audio(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return bool((r.stdout or "").strip())


def _probe_size(path: str) -> tuple[int, int, float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return (int(st.get("width") or 1920), int(st.get("height") or 1080),
            float((d.get("format") or {}).get("duration") or 0))


def wave_filter(W: int, H: int, start_after: float) -> str:
    """Đồ thị filter: cột sóng âm nhảy theo nhạc (phổ tần, bass bên trái) đặt sát
    đáy, chỉ hiện sau intro. Vào [0:v]+[0:a] → ra [wv] (chưa có chữ)."""
    sx = W / 1920.0
    cell = max(4, int(WAVE_W * sx) // WAVE_BARS)
    bw, bh = cell * WAVE_BARS, max(20, int(WAVE_H * sx) // 2 * 2)
    gap = max(2, cell // 4)
    y = H - bh - int(WAVE_BOTTOM * sx)
    return (
        # 16 kHz → phổ chỉ tới 8 kHz, cột cao thấp rõ theo nhịp/giọng
        f"[0:a]aresample=16000,showfreqs=s={WAVE_BARS}x{bh}:mode=bar:ascale=log:"
        f"fscale=log:win_size=2048:averaging=2:colors=white,format=gray,"
        f"scale={bw}x{bh}:flags=neighbor,"
        # khe hở giữa các cột (lưới dọc, không kẻ ngang)
        f"drawgrid=w={cell}:h=100000:y=-50000:t={gap}:c=black,"
        f"split[wg1][wg2];"
        # màu trắng tinh, độ trong theo chiều cao cột
        f"[wg1]format=rgba,lutrgb=r=255:g=255:b=255[wc];"
        f"[wg2]lut=y=val*{WAVE_ALPHA}[wm];[wc][wm]alphamerge[wa];"
        f"[0:v][wa]overlay=x=(W-w)/2:y={y}:eof_action=pass:"
        f"enable='gte(t,{start_after:.2f})'[wv]")


def _burn(src: Path, ass_name: str, out: Path, duration: float, log: Log,
          encoder: str, wave: str = "") -> subprocess.CompletedProcess:
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
             "-cq", "19", "-b:v", "0", "-maxrate", "30M", "-bufsize", "60M"]
            if encoder == "nvenc" else
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"])
    if wave:   # sóng âm vẽ trước, chữ (danh sách + lời) đè lên trên
        vf = ["-filter_complex", f"{wave};[wv]ass={ass_name}[v]", "-map", "[v]", "-map", "0:a"]
    else:
        vf = ["-vf", f"ass={ass_name}", "-map", "0:v:0", "-map", "0:a?"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src.name,
           *vf, *venc, "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", out.name]
    # Chạy trong thư mục video → tên file ngắn, khỏi lo dấu cách/chữ có dấu
    # trong đường dẫn khi đưa vào filter ass=.
    p = subprocess.Popen(cmd, cwd=str(src.parent), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    last = -1
    for line in p.stdout:
        if line.startswith("out_time_us=") and duration > 0:
            try:
                pct = min(int(line.split("=")[1]) / 1e6 / duration, 1.0) * 100
            except ValueError:
                continue
            if log and int(pct) >= last + 5:
                last = int(pct)
                log(f"Đang in tên bài + lyrics lên video… {last}%", pct)
    err = p.stderr.read()
    p.wait()
    return subprocess.CompletedProcess(cmd, p.returncode, "", err)


def apply_overlay(project_id: int, video_path: str, audio_path: str,
                  lead_seconds: float, log: Log = None) -> dict:
    """Đốt lớp chữ vào final.mp4 (thay tại chỗ).

    Watermark tên kênh là bắt buộc và luôn chạy từ 00:00 đến hết video ở góc
    trên bên phải, kể cả khi người dùng tắt các lớp tracklist/lyrics/waveform.
    """
    say = log or (lambda m, p: None)
    video = Path(video_path)
    info_path = Path(audio_path).with_name(Path(audio_path).stem + "_info.json")
    cfg, songs = _load_cover_data(project_id)
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
    elif cfg.get("kind") == "single" and songs:
        # Bài lẻ dùng thẳng WAV cover/master, không cần tạo mix_info.json.
        W, H, single_duration = _probe_size(str(video))
        audio_resolved = os.path.normcase(os.path.abspath(audio_path))
        song_id, song = next(
            ((sid, data) for sid, data in songs.items()
             if data.get("filepath") and os.path.normcase(os.path.abspath(data["filepath"]))
             == audio_resolved), next(iter(songs.items())))
        info = {"audio": {"total_duration_seconds": single_duration}, "tracks": [{
            "song_id": song_id, "name": song.get("title") or Path(audio_path).stem,
            "artist": "", "source_file": audio_path, "start_seconds": 0.0,
            "end_seconds": single_duration, "src_offset_seconds": 0.0,
        }]}
        say("Track lẻ: dùng trực tiếp file cover/master và timeline lyrics một bài.", 0)
    else:
        raise RuntimeError("Không có mix_info.json để dựng playlist. Hãy tạo Mix trước.")
    W, H, dur = _probe_size(str(video))
    start_after = lead_seconds + 1.0 if lead_seconds else 0.0
    if cfg["show_waveform"] and not _has_audio(str(video)):
        cfg["show_waveform"] = False   # video không có tiếng → không có gì để vẽ sóng
    wave = wave_filter(W, H, start_after) if cfg["show_waveform"] else ""
    missing = [s["name"] for s in info.get("tracks") or []
               if not (songs.get(s.get("song_id"), {}).get("lines"))]
    if missing and cfg["show_lyrics"]:
        raise RuntimeError(
            f"Không thể dựng video: {len(set(missing))} bài chưa có lyrics đã căn — "
            + ", ".join(dict.fromkeys(missing)))
    ass = build_ass(info, songs, cfg, dur or float(info["audio"]["total_duration_seconds"]),
                    start_after, W, H)
    ass_path = video.with_name("overlay.ass")
    ass_path.write_text(ass, encoding="utf-8-sig")

    raw = video.with_name("_final_raw.mp4")
    if raw.exists():
        raw.unlink()
    os.replace(video, raw)
    try:
        r = _burn(raw, ass_path.name, video, dur, log, "nvenc", wave)
        if r.returncode != 0:
            say("Card đồ hoạ không mã hoá được (NVENC) → dùng CPU, chậm hơn…", 0)
            r = _burn(raw, ass_path.name, video, dur, log, "x264", wave)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "").strip()[-400:])
    except Exception:
        if video.exists():
            video.unlink()
        os.replace(raw, video)   # trả lại video gốc (chưa có chữ) — không mất công dựng
        raise
    raw.unlink(missing_ok=True)
    return {"applied": True, "ass": str(ass_path), "waveform": bool(wave),
            "watermark": cfg.get("channel_name") or "MUSIC CHANNEL",
            "watermark_position": "top-right"}
