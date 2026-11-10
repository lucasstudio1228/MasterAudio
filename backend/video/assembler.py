"""
video/assembler.py — Nối các clip (đã shuffle) thành 1 video dài, cắt đúng
duration audio và gắn nhạc mix vào. Xuất MP4 (H.264 + AAC) chuẩn YouTube.

Clip do Veo tạo có sẵn tiếng — ta BỎ tiếng clip, dùng nhạc từ bản mix audio.
"""
from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
from typing import Callable, Optional


# Footage chỉ xuất hiện bên trong TV/máy chiếu. Grade riêng này cố ý mạnh hơn
# grade toàn video: màu phim lạnh/ấm nhẹ, giảm saturation rõ rệt và hạt phim
# temporal để cảnh phát trên màn hình không trông như video số quá sạch.
FOOTAGE_SCREEN_GRADE = (
    "eq=saturation=0.40:contrast=1.10:brightness=-0.025:gamma=0.96,"
    "colorbalance=rs=0.025:gs=-0.010:bs=-0.035,"
    "noise=alls=12:allf=t+u,vignette=PI/5"
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_duration(path: str) -> float:
    """Đọc duration (giây) bằng ffprobe."""
    r = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr.strip()[:200]}")
    return float(json.loads(r.stdout)["format"]["duration"])


def probe_fps(path: str) -> float:
    """FPS trung bình của stream video đầu tiên; 0 nếu file không đọc được."""
    r = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "json", path,
    ])
    if r.returncode != 0:
        return 0.0
    try:
        rate = json.loads(r.stdout)["streams"][0]["avg_frame_rate"]
        a, b = str(rate).split("/", 1)
        return float(a) / float(b) if float(b) else 0.0
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _probe_resolution(path: str) -> tuple[int, int]:
    """(width, height) của stream video đầu tiên bằng ffprobe."""
    r = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path,
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr.strip()[:200]}")
    st = json.loads(r.stdout)["streams"][0]
    return int(st["width"]), int(st["height"])


def overlay_title_on_clip(clip_path: str, title: str, subtitle: str,
                          out_path: str, log=None, *, style: Optional[dict] = None,
                          bg_hint: Optional[str] = None) -> bool:
    """Chồng tiêu đề (giống thumbnail) lên TOÀN BỘ clip intro → `out_path`.

    Cần thiết vì clip_00 do Veo tạo LẠI cảnh từ ảnh 0 nên chữ nướng sẵn trong
    ảnh 0 KHÔNG còn ở video. Vẽ chữ bằng font thật (Pillow) ra PNG trong suốt
    rồi ffmpeg overlay → intro video có tiêu đề đúng chính tả, khớp thumbnail.
    Trả True nếu tạo được clip có tiêu đề; False (không phá pipeline) nếu lỗi —
    caller nên fallback dùng clip gốc."""
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    title = (title or "").strip()
    if not title:
        return False
    try:
        from . import thumbnail
    except Exception as e:
        _log(f"intro-title: không import được thumbnail ({e})")
        return False
    try:
        w, h = _probe_resolution(clip_path)
    except Exception as e:
        _log(f"intro-title: không đọc được kích thước clip ({e})")
        return False

    png = Path(out_path).with_suffix(".title.png")
    if not thumbnail.render_title_overlay_png(
            w, h, title, subtitle or "", png, log=log, style=style, bg_hint=bg_hint):
        return False

    # Cần độ dài clip để co lịch animation cho vừa clip ngắn. Lỗi probe → 8s.
    try:
        clip_dur = probe_duration(clip_path)
    except Exception:
        clip_dur = 8.0

    # Animation tiêu đề: fade-IN + trượt LÊN nhẹ → GIỮ ngắn → fade-OUT SỚM.
    # Tiêu đề phải BIẾN MẤT hẳn TRƯỚC khi intro crossfade sang cảnh kế (không
    # để chữ dính sang cảnh mới), nên fade-out đặt ở KHOẢNG ĐẦU clip chứ KHÔNG
    # ở cuối clip. Với clip intro thô ~8s: hiện ~0.8s + giữ ~2s + tắt ~1s →
    # chữ tắt hẳn quanh giây 3.8, còn xa điểm chuyển cảnh (~giây 7.3).
    # QUAN TRỌNG: PNG tĩnh chỉ có 1 frame ở t=0 nên fade sẽ kẹt alpha=0 mãi →
    # phải `-loop 1` để ảnh thành stream có timestamp chạy; overlay `shortest=1`
    # để dừng theo clip (nếu chỉ `-shortest` output sẽ encode vô hạn).
    d = 0.8                                   # thời lượng fade-in (giây)
    hold = 2.0                                # giữ hiển thị (giây)
    d_out = 1.0                               # thời lượng fade-out (giây)
    # Clip quá ngắn → co toàn bộ lịch lại, chừa 0.3s để chữ tắt hẳn trước cuối.
    total_show = d + hold + d_out
    if total_show > clip_dur - 0.3:
        k = max(0.2, (clip_dur - 0.3) / total_show)
        d *= k; hold *= k; d_out *= k
    slide = max(12, int(h * 0.045))           # quãng trượt lên (px)
    out_st = d + hold                         # fade-out bắt đầu ngay sau lúc giữ
    # Hướng trượt theo bố cục chữ: trái → từ trái vào, phải → từ phải vào,
    # trên → rơi xuống, dưới / kiểu cũ → trượt lên.
    layout = (style or {}).get("layout") if style else None
    move = f"{slide}*(1-t/{d})"
    x_expr, y_expr = "0", f"if(lt(t\\,{d})\\,{move}\\,0)"
    if layout == "left":
        x_expr, y_expr = f"if(lt(t\\,{d})\\,-{move}\\,0)", "0"
    elif layout == "right":
        x_expr, y_expr = f"if(lt(t\\,{d})\\,{move}\\,0)", "0"
    elif layout == "top":
        y_expr = f"if(lt(t\\,{d})\\,-{move}\\,0)"
    r = _run([
        "ffmpeg", "-y", "-i", clip_path, "-loop", "1", "-i", str(png),
        "-filter_complex",
        f"[1:v]scale={w}:{h},format=rgba,"
        f"fade=t=in:st=0:d={d}:alpha=1,"
        f"fade=t=out:st={out_st:.3f}:d={d_out}:alpha=1[t];"
        f"[0:v][t]overlay=x={x_expr}:y={y_expr}:shortest=1:format=auto",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
    ])
    try:
        png.unlink(missing_ok=True)
    except Exception:
        pass
    if r.returncode != 0:
        _log(f"intro-title: ffmpeg overlay lỗi: {r.stderr.strip()[-300:]}")
        return False
    _log(f"intro-title: đã chồng tiêu đề '{title}' lên clip intro")
    return True


def _write_concat_list(sequence: list[str], list_path: Path) -> None:
    """File cho ffmpeg concat demuxer. Escape dấu ' trong path."""
    lines = []
    for p in sequence:
        safe = str(Path(p).resolve()).replace("'", r"'\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_video(list_path: Path, tmp_video: Path,
                  reencode: bool) -> None:
    """Nối clip (chỉ hình, bỏ tiếng). Thử -c copy trước, fallback re-encode."""
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path), "-an"]
    if not reencode:
        r = _run(base + ["-c:v", "copy", str(tmp_video)])
        if r.returncode == 0:
            return
        # copy thất bại (clip không đồng nhất) → re-encode
    r = _run(base + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", str(tmp_video),
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat lỗi: {r.stderr.strip()[-300:]}")


def _mux_audio(tmp_video: Path, audio_path: str, out_path: Path,
               target_seconds: float, reencode_video: bool) -> None:
    """Gắn nhạc mix; cắt theo duration audio; xuất MP4 cuối."""
    vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-pix_fmt", "yuv420p"] if reencode_video else ["-c:v", "copy"]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(tmp_video),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        *vcodec,
        "-c:a", "aac", "-b:a", "320k",
        "-t", f"{target_seconds:.3f}",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux lỗi: {r.stderr.strip()[-300:]}")


def _mux_visual_list(list_path: Path, audio_path: str, out_path: Path,
                     target_seconds: float) -> None:
    """Mux concat-list hình với audio; danh sách dài nằm trong file, không ở CLI."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k",
        "-t", f"{target_seconds:.3f}",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux visual list lỗi: {r.stderr.strip()[-400:]}")


# Kiểu chuyển cảnh xfade cho phép (phong cách kênh chọn trong số này).
# "softfade" = hoà tan ÊM: crossfade có easing smoothstep (vào/ra từ từ, không
# giật ở đầu/cuối như fade tuyến tính). Từ 2026-10-25 mọi look dùng kiểu này
# (người dùng: hblur/trượt ngang/nháy đen "gấp quá, không chill"); các kiểu cũ
# giữ lại cho tương thích.
XFADE_TRANSITIONS = ("softfade", "fade", "fadegrays", "fadeblack", "fadewhite",
                     "hblur", "smoothleft", "smoothright", "dissolve")
# P của xfade chạy 1 → 0 (1 = còn nguyên clip A). s = smoothstep(P).
_SOFTFADE_EXPR = "A*(P*P*(3-2*P))+B*(1-P*P*(3-2*P))"
# Crossfade không được dài quá ~nửa clip (2 lần hoà tan chồng nhau trong 1 clip).
_MAX_BLEND_FRACTION = 0.45


def _xfade(transition: str, duration: float, offset: float) -> str:
    """Chuỗi filter xfade cho 1 lần chuyển cảnh."""
    if transition == "softfade":
        kind = f"custom:expr={_SOFTFADE_EXPR}"
    else:
        kind = transition
    return f"xfade=transition={kind}:duration={duration}:offset={offset:.3f}"


def _present_footage(src: str, dest: Path, width: int, height: int, fps: int,
                     background: Optional[str]) -> None:
    """Đặt 1 footage vào khung TV/máy chiếu (màu phim, giảm saturation, hạt film)."""
    dur = probe_duration(src)
    screen_w, screen_h = int(width * 0.43) // 2 * 2, int(height * 0.43) // 2 * 2
    screen_x, screen_y = int(width * 0.53), int(height * 0.18)
    border = max(12, int(width * 0.008))
    if background and Path(background).exists():
        if Path(background).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            inputs = ["-loop", "1", "-framerate", str(fps), "-i", background, "-i", src]
        else:
            inputs = ["-stream_loop", "-1", "-i", background, "-i", src]
        base = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps}[bg];")
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=0x111318:s={width}x{height}:r={fps}",
                  "-i", src]
        base = "[0:v]format=yuv420p[bg];"
        screen_x = (width - screen_w) // 2
    filt = (
        base
        + f"[1:v]scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease,"
          f"pad={screen_w}:{screen_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
          f"{FOOTAGE_SCREEN_GRADE}[screen];"
        + f"[bg]drawbox=x={screen_x-border}:y={screen_y-border}:"
          f"w={screen_w+border*2}:h={screen_h+border*2}:color=0x090909:t=fill,"
          f"drawbox=x={screen_x-border}:y={screen_y-border}:"
          f"w={screen_w+border*2}:h={screen_h+border*2}:color=0xD8B86A:t={max(3,border//4)}[frame];"
        + f"[frame][screen]overlay={screen_x}:{screen_y}:shortest=1,"
          f"fps={fps},format=yuv420p[vout]")
    rr = _run(["ffmpeg", "-y", *inputs, "-filter_complex", filt, "-map", "[vout]",
               "-an", "-t", f"{dur:.3f}", "-c:v", "libx264",
               "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(dest)])
    if rr.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo cảnh TV/máy chiếu lỗi: {rr.stderr[-400:]}")


def _closed_loop(sources: list[str], durs: list[float], norm: str, D: float,
                 transition: str, script: Path, dest: Path) -> float:
    """Nối sources bằng xfade + nối lại nguồn đầu → đoạn lặp khép kín (frame
    cuối = frame đầu) để stream-copy lặp không bị giật. Trả độ dài đoạn lặp."""
    seq, seq_durs = [*sources, sources[0]], [*durs, durs[0]]
    inputs: list[str] = []
    for c in seq:
        inputs += ["-i", c]
    m = len(sources)
    parts = [f"[{i}:v]{norm}[v{i}]" for i in range(m + 1)]
    prev, running = "v0", seq_durs[0]
    for k in range(1, m + 1):
        parts.append(f"[{prev}][v{k}]{_xfade(transition, D, max(0.0, running - D))}[x{k}]")
        prev = f"x{k}"
        running += seq_durs[k] - D
    seconds = max(sum(durs) - m * D, 0.1)
    parts.append(f"[{prev}]trim=start={D:.3f}:end={D + seconds:.3f},"
                 "setpts=PTS-STARTPTS[vout]")
    script.write_text(";".join(parts) + "\n", encoding="utf-8")
    r = _run(["ffmpeg", "-y", *inputs, "-filter_complex_script", str(script),
              "-map", "[vout]", "-an", "-c:v", "libx264", "-preset", "medium",
              "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo vòng lặp lỗi: {r.stderr.strip()[-500:]}")
    return seconds


def assemble_video_blend(
    clip_sequence: list[str],
    audio_path: str,
    output_path: str,
    target_seconds: Optional[float] = None,
    blend_seconds: float = 1.0,
    slow_speed: float = 0.7,
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    grade: str = "",
    transition: str = "softfade",
    lead_image: Optional[str] = None,
    lead_seconds: float = 7.0,
    footage_path: Optional[str] = None,
    footage_mode: str = "screen_only",
    footage_background: Optional[str] = None,
    footage_paths: Optional[list[str]] = None,
) -> dict:
    """
    Phát clip đầu đúng một lần làm intro. Các clip còn lại tạo thành visual
    cycle khép kín bằng `xfade` + làm chậm, rồi lặp đến đúng độ dài audio.

    - Mỗi clip: setpts=PTS/slow_speed (chậm lại) + nội suy chuyển động quang học
      lên 60fps + chuẩn hoá về width×height@fps
      (+ `grade` = chuỗi filter màu ffmpeg theo phong cách kênh, rỗng = giữ màu).
    - Nối bằng xfade `transition` dài `blend_seconds` giữa 2 clip liên tiếp
      (tự rút ngắn nếu vượt 45% clip ngắn nhất).
    - `lead_image` (intro_mode="thumbnail"): video MỞ ĐẦU bằng chính ảnh
      thumbnail (frame 00:00 = thumbnail nguyên vẹn, chữ AI giữ nguyên), đẩy máy
      rất chậm (Ken Burns 1.0 → 1.06, smoothstep) trong `lead_seconds` giây rồi
      hoà tan vào clip 00. Ảnh không qua `grade` để giống hệt thumbnail.
    - `footage_paths` (hoặc 1 `footage_path`): cảnh quay đời thật người dùng
      upload (2026-11-09). Khi có footage: intro → mỗi clip AI chạy đúng MỘT
      lần → fade → các footage (khung TV, màu phim, giảm saturation, hạt film)
      nối nhau thành vòng lặp, chạy đi chạy lại tới HẾT video. Âm thanh
      footage bị bỏ, nhạc giữ nguyên.
    """
    if not clip_sequence:
        raise ValueError("clip_sequence rỗng.")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Không thấy audio: {audio_path}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if target_seconds is None:
        target_seconds = probe_duration(audio_path)

    def _p(msg: str, pct: float):
        if progress_cb:
            progress_cb(msg, pct)

    n = len(clip_sequence)
    if n < 2:
        raise ValueError("Cần ít nhất 2 clip: 1 intro và 1 clip cho cycle.")
    _p(f"Đo độ dài {n} clip", 10.0)
    intro_clip = clip_sequence[0]
    cycle_clips = clip_sequence[1:]
    # Cảnh cuối đã được prompt riêng cho TV/máy chiếu. Khi có footage, dùng cảnh
    # này đúng một lần làm nền trình chiếu và không cho màn hình trống lặp lại.
    footage_srcs = [f for f in (footage_paths or ([footage_path] if footage_path else []))
                    if f and Path(f).exists()]
    if (footage_srcs and footage_background and len(cycle_clips) > 1):
        try:
            bg_resolved = Path(footage_background).resolve()
            if Path(cycle_clips[-1]).resolve() == bg_resolved:
                cycle_clips = cycle_clips[:-1]
        except OSError:
            pass
    intro_dur = probe_duration(intro_clip) / slow_speed
    cycle_durs = [probe_duration(c) / slow_speed for c in cycle_clips]
    presented: list[str] = []
    for i, src in enumerate(footage_srcs):
        _p(f"Footage {i + 1}/{len(footage_srcs)}: khung màn chiếu + màu phim + hạt film", 12.0)
        dest = out.parent / f"_footage_presented_{i:02d}.mp4"
        _present_footage(src, dest, width, height, fps, footage_background)
        presented.append(str(dest))
    footage = bool(presented)
    footage_durs = [probe_duration(f) for f in presented]
    D = max(0.1, min(float(blend_seconds),
                     _MAX_BLEND_FRACTION * min(
                         [intro_dur, *cycle_durs, *footage_durs])))
    lead = lead_image if lead_image and Path(lead_image).exists() else None
    L = max(float(lead_seconds or 0.0), D / _MAX_BLEND_FRACTION) if lead else 0.0

    grade = (grade or "").strip().strip(",")
    transition = transition if transition in XFADE_TRANSITIONS else "softfade"
    # Chỉ `fps=60` sẽ lặp khung 24/30fps và vẫn giật, đặc biệt sau setpts 0.7x.
    # minterpolate dựng frame trung gian theo motion vectors; vì chỉ xử lý vài
    # clip cycle ngắn rồi loop bằng stream-copy nên không phải nội suy cả video
    # dài hàng giờ.
    smooth = (f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:"
              "me_mode=bidir:vsbmc=1" if fps >= 50 else f"fps={fps}")
    def _norm(speed: float) -> str:
        return (f"setpts=(PTS-STARTPTS)/{speed},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"{smooth}," + (f"{grade}," if grade else "")
                + "format=yuv420p,settb=AVTB")
    norm = _norm(slow_speed)
    # Footage đã ở đúng khung/FPS → không nội suy lại, chỉ chuẩn hoá + grade kênh.
    footage_norm = (f"setpts=PTS-STARTPTS,scale={width}:{height}:"
                    f"force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
                    + (f"{grade}," if grade else "") + "format=yuv420p,settb=AVTB")
    # 1) Intro: clip_00 -> fade vào clip đầu cycle. Cắt đúng lúc clip cycle
    # đạt source-time D; visual cycle bên dưới cũng bắt đầu tại frame đó.
    # Có lead_image: [thumbnail L giây] → fade → clip_00 → fade → clip đầu cycle.
    intro_script = out.parent / "_intro_filter.txt"
    intro_video = out.parent / "_visual_intro.mp4"
    # Xây chuỗi mở đầu: [thumbnail?] → clip 00 → [footage?] → clip đầu cycle.
    # `intro_total` dừng đúng tại cuối transition vào cycle; footage vì nằm ở
    # đây nên không bao giờ bị đưa vào danh sách cycle lặp.
    intro_inputs: list[str] = []
    intro_parts: list[str] = []
    pre_sources: list[tuple[str, float]] = []
    input_idx = 0
    if lead:
        frames = max(int(round(L * fps)), 2)
        x = f"min(on/{frames - 1},1)"
        zoom = f"1+0.06*{x}*{x}*(3-2*{x})"
        intro_parts.append(
            f"[{input_idx}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase"
            ":flags=lanczos,"
            f"crop={width * 2}:{height * 2},setsar=1,"
            f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={width}x{height}:fps={fps},"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS,"
            "format=yuv420p,settb=AVTB[vl]")
        intro_inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{L:.3f}",
                         "-i", str(lead)]
        pre_sources.append(("vl", L))
        input_idx += 1

    intro_parts.append(f"[{input_idx}:v]{norm}[v0]")
    intro_inputs += ["-i", intro_clip]
    pre_sources.append(("v0", intro_dur))
    input_idx += 1
    intro_parts.append(f"[{input_idx}:v]{norm}[v1]")
    intro_inputs += ["-i", cycle_clips[0]]

    prev, running = pre_sources[0]
    transition_no = 0
    for label, src_dur in pre_sources[1:]:
        transition_no += 1
        out_label = f"ip{transition_no}"
        intro_parts.append(
            f"[{prev}][{label}]{_xfade(transition, D, max(0.0, running - D))}"
            f"[{out_label}]")
        prev = out_label
        running += src_dur - D
    intro_total = running
    intro_parts.append(
        f"[{prev}][v1]{_xfade(transition, D, max(0.0, intro_total - D))}[ix]")
    if lead:
        _p(f"Tạo intro: thumbnail {L:.0f}s → clip 00 (chỉ một lần)", 20.0)
    else:
        _p("Tạo intro từ clip 00 (chỉ một lần)", 20.0)
    intro_parts.append(
        f"[ix]trim=start=0:end={intro_total:.3f},setpts=PTS-STARTPTS[vout]")
    intro_script.write_text(";".join(intro_parts) + "\n", encoding="utf-8")
    r = _run([
        "ffmpeg", "-y", *intro_inputs,
        "-filter_complex_script", str(intro_script),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(intro_video),
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo intro lỗi: {r.stderr.strip()[-500:]}")

    # 2) Cycle chỉ gồm clip 01..N. Nối thêm clip đầu cycle ở cuối để fade
    # khép kín. clip_00 tuyệt đối không có mặt trong cycle được lặp.
    cycle_inputs = [*cycle_clips, cycle_clips[0]]
    cycle_input_durs = [*cycle_durs, cycle_durs[0]]
    inputs: list[str] = []
    for c in cycle_inputs:
        inputs += ["-i", c]
    m = len(cycle_clips)
    parts = [f"[{i}:v]{norm}[v{i}]" for i in range(m + 1)]
    prev = "v0"
    running = cycle_input_durs[0]
    for k in range(1, m + 1):
        offset = max(0.0, running - D)
        outlbl = f"x{k}"
        parts.append(f"[{prev}][v{k}]{_xfade(transition, D, offset)}[{outlbl}]")
        prev = outlbl
        running = running + cycle_input_durs[k] - D

    # Cắt từ D đến đúng điểm clip đầu vừa fade vào xong. Frame đầu/cuối cùng
    # thuộc cùng thời điểm của clip đầu, vì vậy khi loop không có hard cut.
    cycle_seconds = max(sum(cycle_durs) - m * D, 0.1)
    parts.append(
        f"[{prev}]trim=start={D:.3f}:end={D + cycle_seconds:.3f},"
        "setpts=PTS-STARTPTS[vout]")

    filter_script = out.parent / "_cycle_filter.txt"
    cycle_video = out.parent / "_visual_cycle.mp4"
    filter_script.write_text(";".join(parts) + "\n", encoding="utf-8")

    _p(f"Nội suy chuyển động {fps} FPS + tạo visual cycle T+5 + fade", 45.0)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex_script", str(filter_script),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(cycle_video),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo visual cycle lỗi: {r.stderr.strip()[-500:]}")

    # 3) Danh sách hình.
    #    Không footage: intro một lần, sau đó lặp cycle 01..N tới hết nhạc.
    #    Có footage: intro → cycle 01..N đúng MỘT lần → fade → vòng lặp footage
    #    chạy đi chạy lại tới hết nhạc.
    loop_script = out.parent / "_footage_filter.txt"
    footage_loop = out.parent / "_footage_loop.mp4"
    bridge = out.parent / "_visual_bridge.mp4"
    footage_start = None
    footage_loop_seconds = 0.0
    if footage:
        _p(f"Nối {len(presented)} footage thành vòng lặp", 65.0)
        footage_loop_seconds = _closed_loop(presented, footage_durs, footage_norm, D,
                                            transition, loop_script, footage_loop)
        # Clip cuối của lượt chạy duy nhất fade thẳng vào footage (không quay
        # lại clip đầu cycle). Bridge kết thúc đúng tại frame đầu vòng footage.
        offset = max(0.0, cycle_seconds - 2 * D)
        footage_start = intro_total + offset
        if footage_start >= target_seconds:
            _p("⚠ Nhạc ngắn hơn phần clip AI chạy 1 lần — footage không kịp xuất hiện.", 68.0)
        _p("Clip AI chạy 1 lần → chuyển sang footage", 70.0)
        r = _run(["ffmpeg", "-y", "-i", str(cycle_video), "-i", str(footage_loop),
                  "-filter_complex",
                  "[0:v]settb=AVTB,format=yuv420p[a];[1:v]settb=AVTB,format=yuv420p[b];"
                  f"[a][b]{_xfade(transition, D, offset)}[vout]",
                  "-map", "[vout]", "-an", "-c:v", "libx264", "-preset", "medium",
                  "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                  str(bridge)])
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg nối clip → footage lỗi: {r.stderr.strip()[-500:]}")
        left = target_seconds - (footage_start + footage_loop_seconds)
        reps = max(0, math.ceil(max(left, 0.0) / footage_loop_seconds)) + 1
        parts_list = [str(intro_video), str(bridge)] + [str(footage_loop)] * reps
    else:
        repeats = max(1, math.ceil(max(target_seconds - intro_total, 0.0)
                                   / cycle_seconds) + 1)
        parts_list = [str(intro_video)] + [str(cycle_video)] * repeats
    visual_list = out.parent / "_visual_list.txt"
    _write_concat_list(parts_list, visual_list)
    _p("Lặp cycle 01..N + gắn nhạc", 75.0)
    _mux_visual_list(visual_list, audio_path, out, target_seconds)

    for temp in (intro_script, intro_video, filter_script, cycle_video,
                 visual_list, loop_script, footage_loop, bridge, *map(Path, presented)):
        try:
            temp.unlink()
        except OSError:
            pass

    dur = probe_duration(str(out))
    _p("Hoàn thành", 100.0)
    return {"output": str(out), "duration": dur, "clip_count": n,
            "fps": fps, "motion_interpolation": fps >= 50,
            "lead_image": str(lead) if lead else None,
            "lead_seconds": round(L, 3) if lead else 0.0,
            "footage": presented or None,
            "footage_source": footage_srcs or None,
            "footage_mode": footage_mode if footage else None,
            "footage_look": ({"style": "cinematic film", "saturation": 0.40,
                               "grain": 12, "vignette": True}
                              if footage else None),
            "footage_clips": len(presented),
            "footage_position": "loop_to_end" if footage else None,
            "footage_start": round(footage_start, 3) if footage_start is not None else None,
            "footage_loop_seconds": round(footage_loop_seconds, 3) if footage else 0.0,
            "clips_play_once": bool(footage)}


def assemble_video(
    clip_sequence: list[str],
    audio_path: str,
    output_path: str,
    target_seconds: Optional[float] = None,
    reencode_concat: bool = False,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> dict:
    """
    clip_sequence : danh sách đường dẫn clip đã shuffle (đủ phủ audio).
    audio_path    : file nhạc (mix.wav).
    output_path   : file MP4 cuối.
    target_seconds: độ dài mong muốn (mặc định = duration audio).

    Return: {output, duration, clip_count}
    """
    if not clip_sequence:
        raise ValueError("clip_sequence rỗng.")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Không thấy audio: {audio_path}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if target_seconds is None:
        target_seconds = probe_duration(audio_path)

    def _p(msg: str, pct: float):
        if progress_cb:
            progress_cb(msg, pct)

    work = out.parent
    list_path = work / "_concat_list.txt"
    tmp_video = work / "_concat_video.mp4"

    _p("Chuẩn bị danh sách clip", 10.0)
    _write_concat_list(clip_sequence, list_path)

    _p("Nối clip (ffmpeg)", 40.0)
    _concat_video(list_path, tmp_video, reencode=reencode_concat)

    _p("Gắn nhạc & xuất MP4", 80.0)
    # Nếu concat đã copy được thì video giữ codec clip; mux copy video luôn.
    _mux_audio(tmp_video, audio_path, out, target_seconds,
               reencode_video=False)

    _p("Dọn tạm", 95.0)
    for f in (list_path, tmp_video):
        try:
            f.unlink()
        except OSError:
            pass

    dur = probe_duration(str(out))
    _p("Hoàn thành", 100.0)
    return {
        "output": str(out),
        "duration": dur,
        "clip_count": len(clip_sequence),
    }
