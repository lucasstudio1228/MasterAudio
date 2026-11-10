"""Tải audio Suno công khai mà không gửi URL/cookie tới dịch vụ thứ ba.

WAV có sẵn được lưu nguyên byte. Nguồn M4A/Opus/MP3 chỉ được giải mã sang
PCM float WAV (không filter/resample/master) và luôn được ghi nhãn compatibility
WAV, không giả là WAV master chính thức của Suno.
"""
from __future__ import annotations

import html as html_lib
import base64
import hashlib
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from backend.video.suno_verify import probe_audio, sha256_file


class SunoWavDownloadError(RuntimeError):
    pass


_PAGE_HOSTS = {"suno.com", "www.suno.com"}
_MEDIA_HOSTS = {
    "cdn1.suno.ai", "cdn2.suno.ai", "cdn-o.suno.com",
    "d2lwuy8qc234o3.cloudfront.net",
}
_IMAGE_HOSTS = {"cdn1.suno.ai", "cdn2.suno.ai", "cdn-o.suno.com"}
_AUDIO_EXTENSIONS = (".wav", ".wave", ".m4a", ".mp3", ".ogg", ".opus", ".mp4")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_URL_RE = re.compile(r"https?(?::|\\u003a)(?:/|\\/){2}[^\"'<>\s]+", re.I)
_RIGHTS_PATH = "/api/mango/rights"
_ENCRYPTED_CLIP = "https://d2lwuy8qc234o3.cloudfront.net/1/clip/{song_id}.m4a"


@dataclass(frozen=True)
class PublicSong:
    song_id: str
    title: str
    page_url: str
    media_urls: tuple[str, ...]


def _normalise_url(value: str) -> str:
    return (html_lib.unescape(value).replace("\\u003a", ":")
            .replace("\\u0026", "&").replace("\\/", "/").rstrip("\\"))


def _safe_page_url(raw: str) -> str:
    raw = (raw or "").strip()
    if _UUID_RE.fullmatch(raw):
        raw = f"https://suno.com/song/{raw}"
    parsed = urlparse(raw)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _PAGE_HOSTS:
        raise SunoWavDownloadError("Chỉ nhận link HTTPS của suno.com hoặc ID bài Suno.")
    if not (parsed.path.startswith("/song/") or parsed.path.startswith("/s/")
            or parsed.path.startswith("/hook/")):
        raise SunoWavDownloadError("Link phải là /song/…, /s/… hoặc /hook/… của Suno.")
    return raw


def _safe_media_url(raw: str) -> str:
    url = _normalise_url(raw)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _MEDIA_HOSTS:
        raise SunoWavDownloadError(f"Từ chối media ngoài CDN Suno: {host or 'không rõ'}")
    if not parsed.path.lower().endswith(_AUDIO_EXTENSIONS):
        raise SunoWavDownloadError("URL CDN không phải file audio được hỗ trợ.")
    return url


def _safe_image_url(raw: str) -> str:
    url = _normalise_url(raw)
    parsed = urlparse(url)
    if ((parsed.scheme != "https") or (parsed.hostname or "").lower() not in _IMAGE_HOSTS
            or not parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
        raise SunoWavDownloadError("URL ảnh không thuộc CDN Suno hợp lệ.")
    return url


class _CheckedRedirects(HTTPRedirectHandler):
    """Kiểm tra *từng* Location trước khi urllib kết nối tới host mới."""
    def __init__(self, validator):
        super().__init__()
        self.validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_checked(req: Request, validator, timeout: int):
    return build_opener(_CheckedRedirects(validator)).open(req, timeout=timeout)


def parse_public_song(page_url: str, body: str) -> PublicSong:
    final_url = _safe_page_url(page_url)
    decoded = html_lib.unescape(body or "")
    song_match = _UUID_RE.search(final_url) or _UUID_RE.search(decoded)
    if not song_match:
        raise SunoWavDownloadError("Không xác định được ID bài Suno.")
    song_id = song_match.group(0).lower()
    candidates: list[str] = []
    for match in _URL_RE.finditer(decoded):
        try:
            safe = _safe_media_url(match.group(0))
        except SunoWavDownloadError:
            continue
        if "sil-100.mp3" not in safe.lower() and safe not in candidates:
            candidates.append(safe)
    # Trang chứa nhạc im lặng của player và đôi khi cả đề xuất bài khác. Chỉ
    # nhận media đúng UUID bài; fallback /clip/ dành cho Hook/định dạng mới.
    matching = [u for u in candidates if song_id in u.lower()]
    if matching:
        candidates = matching
    else:
        candidates = [u for u in candidates if "/clip/" in urlparse(u).path.lower()]
    if not candidates:
        raise SunoWavDownloadError(
            "Suno không công khai audio cho link này (bài có thể private, bị xoá hoặc asset lỗi).")
    title_match = re.search(
        r'<meta\s+(?:property|name)=["\'](?:og:title|twitter:title)["\']\s+content=["\']([^"\']+)',
        decoded, re.I)
    title = html_lib.unescape(title_match.group(1)).strip() if title_match else song_match.group(0)
    return PublicSong(song_id, title, final_url, tuple(candidates))


def fetch_public_song(raw_url: str, timeout: int = 30) -> PublicSong:
    url = _safe_page_url(raw_url)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 CoverNhac/1.0", "Accept": "text/html"})
    with _open_checked(req, _safe_page_url, timeout) as response:
        final_url = response.geturl()
        _safe_page_url(final_url)  # chặn redirect ra host khác
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise SunoWavDownloadError("Trang Suno lớn bất thường; đã dừng để bảo vệ bộ nhớ.")
    return parse_public_song(final_url, body.decode("utf-8", errors="replace"))


def _safe_filename(value: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return clean[:120] or "Suno song"


def _available_path(folder: Path, stem: str) -> Path:
    first = folder / f"{stem}.wav"
    if not first.exists():
        return first
    for n in range(2, 1000):
        candidate = folder / f"{stem} ({n}).wav"
        if not candidate.exists():
            return candidate
    raise SunoWavDownloadError("Có quá nhiều file trùng tên trong thư mục tải.")


def _download_file(url: str, path: Path, timeout: int = 180) -> None:
    safe = _safe_media_url(url)
    req = Request(safe, headers={"User-Agent": "Mozilla/5.0 CoverNhac/1.0", "Referer": "https://suno.com/"})
    total = 0
    with _open_checked(req, _safe_media_url, timeout) as response, path.open("wb") as out:
        _safe_media_url(response.geturl())
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 1536 * 1024 * 1024:
                raise SunoWavDownloadError("File vượt quá giới hạn an toàn 1.5 GB.")
            out.write(chunk)
    if total < 1024:
        raise SunoWavDownloadError("Asset Suno trả về quá nhỏ hoặc rỗng.")


def _is_wave(path: Path) -> bool:
    with path.open("rb") as stream:
        head = stream.read(12)
    return len(head) == 12 and head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE"


def _decode_pcm_float(source: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SunoWavDownloadError("Không tìm thấy ffmpeg để giải mã audio sang WAV.")
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
         "-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "pcm_f32le", str(output)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0 or not output.exists():
        raise SunoWavDownloadError(f"ffmpeg không giải mã được audio: {(result.stderr or '')[-400:]}")


def _decode_pcm16(source: Path, output: Path) -> None:
    """Giải mã toàn bộ container một lần, không thu realtime/resample/filter."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SunoWavDownloadError("Không tìm thấy ffmpeg để giải mã audio sang WAV.")
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
         "-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", "pcm_s16le", str(output)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0 or not output.exists():
        raise SunoWavDownloadError(
            f"ffmpeg không giải mã được audio đã mở khóa: {(result.stderr or '')[-500:]}")


def _b64(value: str) -> bytes:
    raw = (value or "").strip()
    raw += "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw, altchars=b"-_", validate=True)
    except Exception as exc:
        raise SunoWavDownloadError("Suno trả khóa audio không đúng định dạng.") from exc


def _unwrap_rights(rights: dict, song_id: str, auth_token: str = "") -> tuple[bytes, bytes]:
    """Mở key/IV đúng như player Suno; không ghi token hoặc key ra log/ổ đĩa."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise SunoWavDownloadError("Thiếu thư viện cryptography để mở khóa audio Suno.") from exc
    seed = auth_token.strip() or str(rights.get("glt") or "").strip()
    if not seed:
        raise SunoWavDownloadError("Suno không trả guest token và phiên đăng nhập chưa có token.")
    wrapping_key = hashlib.sha256(seed.encode("utf-8")).digest()
    aad = song_id.encode("utf-8")

    def unwrap(name: str) -> bytes:
        wrapped = _b64(str(rights.get(name) or ""))
        if len(wrapped) <= 28:
            raise SunoWavDownloadError(f"Khóa {name} Suno quá ngắn.")
        try:
            return AESGCM(wrapping_key).decrypt(wrapped[:12], wrapped[12:], aad)
        except Exception as exc:
            raise SunoWavDownloadError(
                "Không mở được khóa audio; phiên Suno có thể vừa hết hạn.") from exc

    key, iv = unwrap("key"), unwrap("iv")
    if len(key) not in (16, 24, 32) or len(iv) != 16:
        raise SunoWavDownloadError(
            f"Kích thước key/IV Suno không hợp lệ ({len(key)}/{len(iv)} byte).")
    return key, iv


def _decrypt_ctr_file(source: Path, output: Path, key: bytes, iv: bytes) -> None:
    """AES-CTR liên tục; chunk bất kỳ vẫn giữ nguyên counter giữa các lần đọc."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise SunoWavDownloadError("Thiếu thư viện cryptography để giải mã audio Suno.") from exc
    decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
    with source.open("rb") as inp, output.open("wb") as out:
        while True:
            chunk = inp.read(1024 * 1024 + 7)  # cố ý lệch block để kiểm tra continuity
            if not chunk:
                break
            out.write(decryptor.update(chunk))
        out.write(decryptor.finalize())


def _capture_rights_from_page(page, song_id: str, timeout_sec: int = 35) -> tuple[dict, str]:
    """Để chính player Suno xin quyền rồi chỉ đọc response + bearer của request đó."""
    captured: dict[str, object] = {"rights": None, "token": ""}

    def on_response(response) -> None:  # Playwright sync callback
        if _RIGHTS_PATH not in response.url or int(response.status) != 200:
            return
        try:
            request_body = response.request.post_data_json or {}
            content_id = ((request_body.get("content_params") or {}).get("content_id") or "")
            if str(content_id).lower() != song_id.lower():
                return
            headers = response.request.all_headers()
            authorization = str(headers.get("authorization") or "")
            if authorization.lower().startswith("bearer "):
                captured["token"] = authorization[7:].strip()
            captured["rights"] = response.json()
        except Exception:
            return

    page.on("response", on_response)
    try:
        page.goto(f"https://suno.com/song/{song_id}", wait_until="domcontentloaded",
                  timeout=90000)
        # Reload bảo đảm key cache của trang trước không che mất request rights.
        page.reload(wait_until="domcontentloaded", timeout=90000)
        play = page.locator(
            "button[aria-label='Play'], button[aria-label*='Play'], "
            "button[title='Play'], button[title*='Play']").first
        try:
            play.wait_for(state="visible", timeout=15000)
            play.click(timeout=10000)
        except Exception:
            # Nút player đáy trang đôi khi chỉ có SVG; media play sẽ kích hoạt
            # sau khi click card/ảnh bài hát.
            page.locator("audio").first.evaluate("a => a.play().catch(() => {})")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline and not captured["rights"]:
            page.wait_for_timeout(250)
        if not captured["rights"]:
            raise SunoWavDownloadError(
                "Không bắt được quyền audio từ player Suno. Hãy đăng nhập lại rồi thử lại.")
        return dict(captured["rights"]), str(captured["token"] or "")
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


def download_authorized_song_as_wav(page, song_id: str, destination: Path) -> dict:
    """Tải asset mã hóa của bài được phép, giải mã liên tục rồi tạo WAV PCM16.

    Không chạy audio theo thời gian thực, không qua Studio và không dùng dịch vụ
    tải bên thứ ba. Chỉ dùng rights mà player Suno cấp cho phiên hiện tại.
    """
    if not _UUID_RE.fullmatch((song_id or "").strip()):
        raise SunoWavDownloadError("ID bài Suno không hợp lệ.")
    song_id = song_id.lower()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rights, token = _capture_rights_from_page(page, song_id)
    key, iv = _unwrap_rights(rights, song_id, token)
    asset_url = _ENCRYPTED_CLIP.format(song_id=song_id)
    with tempfile.TemporaryDirectory(prefix="suno-direct-") as temp_dir:
        encrypted = Path(temp_dir) / "source.encrypted"
        clear = Path(temp_dir) / "source.m4a"
        verified = Path(temp_dir) / "verified.wav"
        _download_file(asset_url, encrypted)
        _decrypt_ctr_file(encrypted, clear, key, iv)
        _decode_pcm16(clear, verified)
        meta = probe_audio(verified)
        if (not meta.get("has_audio") or meta.get("codec_name") != "pcm_s16le"
                or float(meta.get("duration") or 0) < 1):
            raise SunoWavDownloadError(f"WAV sau giải mã không hợp lệ: {meta}")
        destination.unlink(missing_ok=True)
        shutil.move(str(verified), str(destination))
    return {
        "song_id": song_id, "title": "", "path": str(destination),
        "filename": destination.name, "mode": "authorized_continuous_decrypt_pcm16",
        "source_url": f"https://suno.com/song/{song_id}",
        "sha256": sha256_file(destination), "codec": meta.get("codec_name"),
        "sample_rate": meta.get("sample_rate"), "channels": meta.get("channels"),
        "duration_seconds": meta.get("duration"),
    }


def _page_song_metadata(page, song_id: str) -> dict:
    """Đọc dữ liệu hiển thị của đúng trang bài; không chạy chỉ dẫn trong lyrics."""
    data = page.evaluate("""() => {
      const meta = (selector) => document.querySelector(selector)?.content || '';
      const copy = document.querySelector(
        'button[aria-label="Copy lyrics to clipboard"], button[title="Copy lyrics to clipboard"]');
      let lyrics = '';
      if (copy) {
        const box = copy.parentElement;
        const lines = box ? [...box.querySelectorAll('p')].map(p => p.innerText) : [];
        lyrics = lines.join('\\n').trim() || (box?.innerText || '').trim();
      }
      return {
        title: meta('meta[property="og:title"]') || document.title.replace(/ by .*$/, ''),
        image_url: meta('meta[property="og:image"]'),
        lyrics,
      };
    }""") or {}
    title = str(data.get("title") or song_id).strip()
    image_url = str(data.get("image_url") or "").strip()
    lyrics = str(data.get("lyrics") or "").replace("\r", "").strip()
    # Nút copy nằm cùng box với button; loại nhãn nút nếu UI đưa vào innerText.
    lyrics = re.sub(r"\n?Copy lyrics to clipboard\s*$", "", lyrics, flags=re.I).strip()
    return {"title": title, "image_url": image_url, "lyrics": lyrics}


def _download_image(url: str, path: Path, timeout: int = 90) -> None:
    safe = _safe_image_url(url)
    req = Request(safe, headers={"User-Agent": "Mozilla/5.0 CoverNhac/1.0",
                                 "Referer": "https://suno.com/"})
    with _open_checked(req, _safe_image_url, timeout) as response:
        body = response.read(25 * 1024 * 1024 + 1)
    if not (100 < len(body) <= 25 * 1024 * 1024):
        raise SunoWavDownloadError("Ảnh Suno rỗng hoặc vượt giới hạn 25 MB.")
    path.write_bytes(body)


def download_authorized_song_package(page, song_id: str, folder: Path) -> dict:
    """Tạo bộ WAV + JPEG + lyrics TXT + ZIP cho một bài được phép tải."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # Hàm WAV điều hướng tới đúng trang và để trang mở để lấy metadata.
    probe_wav = folder / f".{song_id}.downloading.wav"
    info = download_authorized_song_as_wav(page, song_id, probe_wav)
    metadata = _page_song_metadata(page, song_id)
    stem = _safe_filename(metadata["title"])
    suffix = 1
    while (folder / f"{stem}.zip").exists() or (folder / f"{stem}.wav").exists():
        suffix += 1
        stem = _safe_filename(f"{metadata['title']} ({suffix})")
    wav = folder / f"{stem}.wav"
    txt = folder / f"{stem}.txt"
    image = folder / f"{stem}.jpeg"
    archive = folder / f"{stem}.zip"
    probe_wav.replace(wav)
    try:
        txt.write_text(metadata["lyrics"], encoding="utf-8")
        image_url = metadata["image_url"]
        if image_url:
            _download_image(image_url, image)
        temp_zip = archive.with_suffix(".zip.part")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.write(wav, wav.name)
            bundle.write(txt, txt.name)
            if image.exists():
                bundle.write(image, image.name)
        temp_zip.replace(archive)
    except Exception:
        archive.with_suffix(".zip.part").unlink(missing_ok=True)
        raise
    info.update({
        "title": metadata["title"], "path": str(wav), "filename": wav.name,
        "lyrics_path": str(txt), "image_path": str(image) if image.exists() else "",
        "zip_path": str(archive), "zip_filename": archive.name,
    })
    return info


def resolve_song_id(raw: str) -> str:
    value = (raw or "").strip()
    direct = _UUID_RE.search(value)
    if direct:
        return direct.group(0).lower()
    return fetch_public_song(value).song_id


def save_authorized_audio_bytes(body: bytes, destination: Path) -> dict:
    """Lưu response audio lấy từ phiên Suno đã đăng nhập thành WAV đã kiểm tra.

    Dùng cho endpoint phát chính thức (audiopipe), không dùng để tự giải mã blob
    CDN mã hóa. Nếu response đã là WAV thì giữ nguyên byte; nếu là container
    audio hợp lệ thì chỉ decode sang PCM float, không filter/resample/master.
    """
    if len(body) < 1024:
        raise SunoWavDownloadError("Luồng audio Suno trả về quá nhỏ hoặc rỗng.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="suno-authorized-") as temp_dir:
        source = Path(temp_dir) / "authorized-audio.bin"
        verified = Path(temp_dir) / "verified.wav"
        source.write_bytes(body)
        if _is_wave(source):
            shutil.copyfile(source, verified)
            mode = "official_wav_exact"
        else:
            _decode_pcm_float(source, verified)
            mode = "compatible_pcm_from_authorized_stream"
        if not _is_wave(verified):
            raise SunoWavDownloadError("Luồng Suno không tạo được container WAV hợp lệ.")
        meta = probe_audio(verified)
        if not meta.get("has_audio") or not str(meta.get("codec_name") or "").startswith("pcm_"):
            raise SunoWavDownloadError("WAV từ luồng Suno không có audio PCM hợp lệ.")
        destination.unlink(missing_ok=True)
        shutil.move(str(verified), str(destination))
    return {
        "path": str(destination), "filename": destination.name, "mode": mode,
        "sha256": sha256_file(destination), "codec": meta.get("codec_name"),
        "sample_rate": meta.get("sample_rate"), "channels": meta.get("channels"),
        "duration_seconds": meta.get("duration"),
    }


def save_pcm_f32_chunks(chunks: Iterable[bytes], sample_rate: int, channels: int,
                        frames: int, destination: Path) -> dict:
    """Ghi các chunk PCM float32 interleaved do Web Audio của Suno player trả.

    Không resample/filter/master. Ghi file tạm, kiểm tra ffprobe rồi mới thay file
    đích để job bị dừng giữa chừng không làm hỏng WAV cũ.
    """
    if sample_rate < 8000 or sample_rate > 384000 or channels not in (1, 2):
        raise SunoWavDownloadError(
            f"Thông số PCM player không hợp lệ: {sample_rate} Hz, {channels} kênh.")
    data_size = int(frames) * channels * 4
    if data_size < sample_rate * channels * 4:
        raise SunoWavDownloadError("PCM player ngắn bất thường (dưới 1 giây).")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".player.part")
    written = 0
    try:
        # WAVE_FORMAT_IEEE_FLOAT (3) + fact chunk; RIFF size = file size - 8.
        header = (
            b"RIFF" + struct.pack("<I", 48 + data_size) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 3, channels, sample_rate,
                                    sample_rate * channels * 4, channels * 4, 32) +
            b"fact" + struct.pack("<II", 4, int(frames)) +
            b"data" + struct.pack("<I", data_size)
        )
        with temp.open("wb") as stream:
            stream.write(header)
            for chunk in chunks:
                stream.write(chunk)
                written += len(chunk)
        if written != data_size:
            raise SunoWavDownloadError(
                f"PCM player thiếu dữ liệu: nhận {written} byte, cần {data_size} byte.")
        meta = probe_audio(temp)
        if (not meta.get("has_audio") or meta.get("codec_name") != "pcm_f32le"
                or meta.get("sample_rate") != sample_rate or meta.get("channels") != channels):
            raise SunoWavDownloadError(f"ffprobe không xác nhận đúng PCM player: {meta}")
        destination.unlink(missing_ok=True)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "path": str(destination), "filename": destination.name,
        "mode": "captured_pcm_from_authorized_player",
        "sha256": sha256_file(destination), "codec": "pcm_f32le",
        "sample_rate": sample_rate, "channels": channels,
        "duration_seconds": frames / sample_rate,
    }


def download_song_as_wav(song: PublicSong, folder: Path,
                         allow_compatible: bool = True,
                         destination: Path | None = None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    wav_urls = [u for u in song.media_urls if urlparse(u).path.lower().endswith((".wav", ".wave"))]
    dest = Path(destination) if destination is not None else _available_path(
        folder, _safe_filename(song.title))
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = ""
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="suno-wav-") as temp_dir:
        verified = Path(temp_dir) / "verified-output.wav"
        candidates = wav_urls if not allow_compatible else list(song.media_urls)
        if not candidates:
            raise SunoWavDownloadError(
                "Suno chỉ công khai nguồn nén; bài này không có WAV gốc để lưu nguyên byte.")
        # Thử WAV trước, sau đó audio progressive, cuối cùng MP4 công khai của
        # đúng bài. Stream mã hóa/fragment hỏng sẽ bị ffmpeg từ chối và bỏ qua.
        candidates.sort(key=lambda u: (
            0 if urlparse(u).path.lower().endswith((".wav", ".wave")) else
            2 if urlparse(u).path.lower().endswith(".mp4") else 1))
        for index, chosen in enumerate(candidates):
            source = Path(temp_dir) / (f"source-{index}" + (Path(urlparse(chosen).path).suffix or ".audio"))
            try:
                _download_file(chosen, source)
                if _is_wave(source):
                    shutil.copyfile(source, verified)
                    mode = "official_wav_exact"
                elif allow_compatible:
                    _decode_pcm_float(source, verified)
                    mode = "compatible_pcm_from_public_source"
                if mode:
                    break
            except Exception as exc:
                verified.unlink(missing_ok=True)
                errors.append(str(exc)[:180])
        if not mode:
            detail = f" Chi tiết: {' | '.join(errors[-2:])}" if errors else ""
            raise SunoWavDownloadError(
                "Không có asset WAV/audio công khai nào giải mã được; không tạo WAV giả." + detail)
        if not _is_wave(verified):
            raise SunoWavDownloadError("File tạo ra không phải container WAV hợp lệ.")
        meta = probe_audio(verified)
        if not meta.get("has_audio") or not str(meta.get("codec_name") or "").startswith("pcm_"):
            raise SunoWavDownloadError("WAV không có audio PCM hợp lệ.")
        # Chỉ thay file đích sau khi WAV tạm đã xác thực đầy đủ.
        dest.unlink(missing_ok=True)
        shutil.move(str(verified), str(dest))
    return {
        "song_id": song.song_id, "title": song.title, "path": str(dest),
        "filename": dest.name, "mode": mode, "source_url": song.page_url,
        "sha256": sha256_file(dest), "codec": meta.get("codec_name"),
        "sample_rate": meta.get("sample_rate"), "channels": meta.get("channels"),
        "duration_seconds": meta.get("duration"),
    }


def download_song_id_to_path(song_id: str, destination: Path,
                             allow_compatible: bool = True) -> dict:
    """Fallback cho workflow: dùng đúng ID đã Create, không tạo lại bài."""
    if not _UUID_RE.fullmatch((song_id or "").strip()):
        raise SunoWavDownloadError("ID bài Suno không hợp lệ.")
    song = fetch_public_song(f"https://suno.com/song/{song_id.strip()}")
    return download_song_as_wav(song, destination.parent, allow_compatible,
                                destination=destination)


def download_many(urls: Iterable[str], folder: Path, allow_compatible: bool = True) -> list[dict]:
    rows = [u.strip() for u in urls if u and u.strip()]
    if not rows:
        raise SunoWavDownloadError("Hãy dán ít nhất một link hoặc ID bài Suno.")
    if len(rows) > 20:
        raise SunoWavDownloadError("Mỗi lượt tải tối đa 20 bài.")
    results: list[dict] = []
    for raw in dict.fromkeys(rows):
        try:
            song = fetch_public_song(raw)
            results.append({"ok": True, **download_song_as_wav(song, folder, allow_compatible)})
        except Exception as exc:  # một bài lỗi không làm mất các bài đã tải xong
            results.append({"ok": False, "input": raw, "error": str(exc)[:800]})
    return results
