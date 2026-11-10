"""SQLModel table definitions."""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class AppSettings(SQLModel, table=True):
    """Singleton — luôn chỉ có 1 row với id=1."""
    id: int = Field(default=1, primary_key=True)
    api_key: Optional[str] = None           # stored, never returned to client
    api_base_url: str = "https://api.shopaikey.com/v1"   # Cover Audio Tool: giữ setting tool gốc
    api_model: str = "gpt-4.1"
    # GPMLogin Local Automation API — dùng để bật profile + lấy CDP endpoint.
    # Cổng mặc định 9495 (user xác nhận); có thể chỉnh trong UI nếu máy khác.
    gpm_api_base_url: str = "http://localhost:9495"
    # (Tùy chọn) đường dẫn GPMLoginGlobal.exe để tự mở app nếu chưa chạy.
    gpm_exe_path: Optional[str] = None
    # Thumbnail AI qua API (ShopAIKey…): model ảnh vẽ cả ảnh lẫn chữ tiêu đề.
    # Key RIÊNG (khác key OpenAI ở trên), không bao giờ trả về client.
    thumb_api_key: Optional[str] = None
    thumb_api_base_url: str = "https://api.shopaikey.com"
    thumb_api_model: str = "gemini-3-pro-image-preview"
    thumb_ai_enabled: bool = False      # chỉ có hiệu lực khi đã có key
    thumb_ai_verify: bool = True        # đọc lại chữ bằng model vision trước khi dùng
    thumb_engine: str = "pil"           # máy vẽ thumbnail: chatgpt | api | pil
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    # api_key, api_base_url, api_model ĐÃ XÓA — dùng AppSettings toàn cục

    # Video: ý tưởng lưu sẵn để AI viết prompt (dùng cho cả tạo tay lẫn auto).
    video_idea: str = ""
    # Tự động dựng video ngay sau khi render mix Audio xong. Project MỚI mặc
    # định BẬT (model default True); project CŨ giữ TẮT (migration set 0).
    auto_video: bool = True
    # Phong cách ảnh/video: "2d" (tranh vẽ/anime/cartoon) hoặc "3d" (Pixar CGI).
    # Mặc định 2D cho cả project mới lẫn cũ (migration set '2d').
    video_style: str = "real"
    # Intro 00:00: "thumbnail" (MẶC ĐỊNH — ảnh thumbnail đã vẽ chữ AI chuyển động
    # rồi tan vào clip 00) hoặc "font" (chữ font có sẵn đè lên clip 00 như cũ).
    intro_mode: str = "thumbnail"
    # Dàn cảnh: "character_pet" (MẶC ĐỊNH — nhân vật chính + linh thú/thú nuôi)
    # hoặc "scenery" (chỉ cảnh vật, không nhân vật, không con vật).
    cast_mode: str = "character_pet"
    # Cách trình bày footage: auto = theo cast_mode; character_screen = nhân vật
    # xem TV/máy chiếu; screen_only = chỉ TV/màn chiếu, không có nhân vật.
    footage_scene_mode: str = "auto"
    # Công cụ cover dùng chung cho Track lẻ / Mix Set / project tạo tay:
    # True = từng bài để AI tự chọn nhạc cụ sáng tạo phù hợp; False = không thêm.
    # Lưu ở project để những SourceSong được thêm thủ công về sau vẫn theo đúng
    # lựa chọn ban đầu, thay vì luôn quay lại mặc định AI.
    creative_instrument: bool = True

    # ── Auto-upload YouTube ──
    # Loại nhạc cụ + phong cách nhạc: dùng để tra ChannelMapping → chọn đúng
    # profile GPMLogin (kênh YouTube) khi đăng nháp video.
    instrument: str = ""
    music_style: str = ""
    # Kênh YouTube người dùng CHỌN (ChannelMapping.id) — kênh nào cũng nhận nhạc
    # cụ/thể loại nào (2026-10-25). NULL = tự tra theo (instrument, music_style).
    channel_id: Optional[int] = Field(default=None, index=True)
    # Tool Cover: dòng nhạc của bài (VN / US-UK / Chinese…) — cùng thể loại để tự
    # chọn kênh khi chưa chọn kênh (2026-10-28).
    music_line: str = ""
    # Nguồn nhạc nền cho project: "local" (import WAV thủ công như cũ — MẶC ĐỊNH,
    # giữ nguyên hành vi hiện có) hoặc "suno" (tạo 15 bài WAV từ Suno ở STEP 0).
    music_source: str = "local"
    # Ý tưởng / loại nhạc cụ cho STEP 0 (Suno). AI dựa vào đây viết Styles prompt
    # (LUÔN nhạc không lời). Lưu sẵn để textbox nhớ giữa các phiên (giống video_idea).
    suno_idea: str = ""
    # Tự động đăng nháp lên YouTube ngay sau khi render video xong. Mặc định TẮT
    # (an toàn — người dùng bật khi đã cấu hình mapping). Project cũ: migration 0.
    auto_upload: bool = False
    # Nhóm project cùng 1 lô sản xuất hàng loạt (batch). NULL = tạo lẻ như cũ.
    batch_id: Optional[str] = Field(default=None, index=True)
    # Loại project (2026-11-04): "mixset" (nhiều bài ghép 1 video — mặc định,
    # gồm mọi project cũ) | "single" (Track lẻ: 1 bài = 1 project, video riêng).
    # Quyết định nhóm trên trang Projects + thư mục media/Mix Set | media/Track lẻ.
    kind: str = Field(default="mixset", index=True)
    # Tổng thời lượng mix (= độ dài video) + crossfade. Lưu theo project để CẢ
    # bấm Start Mix tay LẪN chuỗi tự động Suno→Mix→Video dùng đúng 1 con số.
    mix_duration_minutes: float = 120.0
    mix_crossfade_seconds: float = 5.0
    # Thùng rác: xoá project = chuyển dữ liệu vào Trash/ + đặt deleted_at; sau
    # 7 ngày mới xoá vĩnh viễn (backend/project_trash.py). NULL = đang dùng.
    deleted_at: Optional[datetime] = None
    trash_dir: Optional[str] = None


class BatchRun(SQLModel, table=True):
    """
    1 lô sản xuất hàng loạt N sản phẩm YouTube. Orchestrator (backend/batch_service.py)
    tạo N Project cùng batch_key rồi chạy TUẦN TỰ trọn pipeline từng project
    (Suno → Mix → Video → đăng nháp). Bảng này là state tiến độ + tham số để UI
    theo dõi i/N và để resume/hủy an toàn.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_key: str = Field(index=True, unique=True)   # UUID lô
    total: int = 0                                    # N sản phẩm
    completed: int = 0                                # số project đã xong chuỗi
    failed: int = 0                                   # số project lỗi/blocking
    # running | completed | cancelled | blocked | failed
    status: str = Field(default="running")
    current_index: int = 0                            # đang chạy project thứ (0-based)
    current_project_id: Optional[int] = None
    params_json: str = "{}"                           # snapshot tham số lô
    message: str = ""                                 # mô tả trạng thái cho UI
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class Track(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    filename: str
    filepath: str               # absolute path trên disk
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str                 # WAV / FLAC / MP3
    is_lossy: bool
    subtype: Optional[str] = None
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class DeletedTrack(SQLModel, table=True):
    """Ảnh chụp Track trước khi xoá để có thể khôi phục mà không chép file audio."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    original_track_id: int = Field(index=True)
    filename: str
    filepath: str
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str
    is_lossy: bool
    subtype: Optional[str] = None
    added_at: datetime
    # Snapshot CoverSong (không gồm id/track_id); rỗng nếu track ngoài.
    cover_song_json: str = ""
    deleted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True)


class TrackStem(SQLModel, table=True):
    """
    1-1 với Track. Lưu trạng thái separation + volume settings.
    Tự động tạo khi track được add vào project.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    track_id:   int   = Field(foreign_key="track.id",
                               unique=True, index=True)
    project_id: int   = Field(foreign_key="project.id", index=True)

    # Trạng thái separation
    status:       str           = Field(default="pending")
    started_at:   Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str]    = None

    # Volume mỗi stem — range 0.0 (mute) → 2.0 (double)
    # Default 1.0 = giữ nguyên
    vol_other:  float = Field(default=1.0)   # Âm nhạc
    vol_bass:   float = Field(default=1.0)   # Âm trầm
    vol_drums:  float = Field(default=1.0)   # Trống
    vol_vocals: float = Field(default=1.0)   # Giọng hát

    # Độ mạnh khử noise THỦ CÔNG mỗi stem — range 0.0 → 1.0
    # 0.0 = chỉ khử tự động theo volume (mặc định, giữ hành vi cũ)
    # >0  = ép khử mạnh hơn kể cả khi stem để nguyên
    # Strength thực tế lúc mix = max(clip(1 - volume), den_*)
    den_other:  float = Field(default=0.0)   # Âm nhạc
    den_bass:   float = Field(default=0.0)   # Âm trầm
    den_drums:  float = Field(default=0.0)   # Trống
    den_vocals: float = Field(default=0.0)   # Giọng hát

    # Gain LUFS normalization đã apply (linear scalar)
    # Lưu để reference, không dùng lại
    lufs_gain_applied: Optional[float] = None

    # Waveform thumbnail — JSON string
    # Format: {"other": [0.1, 0.3,...], "bass": [...], ...}
    # 200 RMS points mỗi stem, normalized 0-1
    waveform_data: Optional[str] = None


class Mix(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str                  # auto-generated or user override
    status: str = "pending"     # pending | running | completed | failed
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    # Settings snapshot
    duration_minutes: float = 60.0
    crossfade_seconds: float = 15.0
    # 0 = auto (max native, không upsample giả) — được resolve khi tạo mix.
    sample_rate: int = 0
    # 32-bit float (không nén) = chất lượng tối đa.
    bit_depth: int = 32
    # Output
    output_dir: Optional[str] = None    # absolute path
    total_duration_seconds: Optional[float] = None
    track_count: Optional[int] = None
    error_message: Optional[str] = None


class ChannelMapping(SQLModel, table=True):
    """
    Kênh YouTube → 1 profile GPMLogin. Project CHỌN kênh qua project.channel_id
    (kênh nào cũng nhận nhạc cụ/thể loại nào — 2026-10-25); instrument +
    music_style chỉ là gợi ý để tự chọn kênh cho project chưa chọn. Khi đăng nháp,
    tool lấy profile + tên kênh + hashtag mặc định + ngôn ngữ từ dòng này.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    instrument: str = Field(default="", index=True)    # gợi ý, vd "piano" (có thể trống)
    music_style: str = Field(default="", index=True)   # gợi ý, vd "lofi" (có thể trống)
    # Tool Cover: dòng nhạc của kênh thay cho nhạc cụ — "VN" | "US-UK" | "Chinese"
    # | "Japanese"… hoặc tự nhập (2026-10-28). Tách cột riêng để không lọt vào
    # prompt ảnh như nhạc cụ.
    music_line: str = ""
    gpm_profile_id: str                          # UUID profile GPMLogin
    channel_name: str = ""                       # tên kênh (đưa vào description)
    default_hashtags: str = ""                   # vd "#relax #healing #sleep"
    language: str = "en-US"                      # ngôn ngữ video (Anh–Mỹ)
    # Phong cách kênh (video/channel_looks: look có sẵn hoặc "custom_…" tự nhập).
    # Chọn tự do — nhiều kênh dùng chung 1 look được (từ 2026-10-26).
    visual_look: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class YoutubeUpload(SQLModel, table=True):
    """
    Bộ nhớ các lần đăng (nháp) — để AI viết title/description ĐA DẠNG, không
    trùng lặp với các video trước của cùng kênh, và để hiển thị lịch sử ở UI.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    profile_id: str = Field(index=True)          # profile GPMLogin đã dùng
    channel_name: str = ""
    title: str = ""
    description: str = ""
    hashtags: str = ""
    video_path: str = ""
    status: str = "draft"                        # luôn "draft" — không publish
    error_message: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class SunoBatch(SQLModel, table=True):
    """
    STEP 0 — 1 batch = 1 lần tạo nhạc nền cho 1 project trên Suno.
    Đây là STATE BỀN VỮNG (manifest) để chống trùng + resume: nếu tool bị
    tắt/lỗi giữa chừng, đọc lại batch + candidates để biết đã Create/tải/import
    tới đâu, KHÔNG tạo lại / tải lại / import lại.

    idempotency_key: khoá duy nhất theo (project, lần chạy) — dùng để không
    khởi tạo 2 batch song song cho cùng project.
    lock_token/lock_expires_at: chốt để 1 profile Suno không bị 2 worker điều
    khiển cùng lúc (concurrency_per_account=1).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    idempotency_key: str = Field(index=True)     # vd f"suno:{project_id}:{ts}"

    # ── Vòng đời (phases + blocking states — mục I của spec) ──
    #   PREFLIGHT / GENERATING / DOWNLOADING / VALIDATING / READY_FOR_IMPORT /
    #   IMPORTED / DOWNSTREAM_RUNNING / COMPLETED
    #   + WAITING_FOR_LOGIN / WAITING_FOR_HUMAN / SUBMISSION_UNCERTAIN /
    #     INSUFFICIENT_GENERATION_CREDITS / INSUFFICIENT_DOWNLOAD_ALLOWANCE /
    #     UI_CHANGED / FAILED / PAUSED / CANCELLED
    phase: str = Field(default="PREFLIGHT")
    message: str = ""                            # mô tả trạng thái cho UI
    error_message: Optional[str] = None

    # ── Snapshot cấu hình lúc Start (JSON) — để resume dùng đúng tham số ──
    preset: str = "relaxing_flute"
    model: str = "v6"
    target_tracks: int = 15
    config_json: str = "{}"                      # SunoConfig + styles/exclusions đã gửi

    # ── Bộ đếm (mục J) ──
    generated_candidates: int = 0
    selected_tracks: int = 0
    downloaded_tracks: int = 0
    validated_tracks: int = 0
    imported_tracks: int = 0
    create_actions_used: int = 0                 # số lần đã bấm Create (trần max_create_actions)

    # ── Lock chống 2 worker ──
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None

    # Thư mục staging chứa WAV (ngoài vùng media auto-watch cho tới khi import).
    staging_dir: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class SunoCandidate(SQLModel, table=True):
    """
    1 candidate = 1 bài Suno trả về (mỗi request paired_outputs thường trả 2).
    Lưu song_id thật của Suno để chống trùng tuyệt đối. 'selected' = bài được
    chọn cho project; chỉ bài selected mới được tải WAV + import.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="sunobatch.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)

    song_id: str = Field(index=True)             # ID bài trên Suno (chống trùng)
    request_id: Optional[str] = None             # gộp candidate cùng 1 request
    title: str = ""
    duration_seconds: Optional[float] = None     # đọc từ UI (chưa xác thực)

    selected: bool = False                       # được chọn cho project chưa
    is_spare: bool = False                        # candidate dư (không tải/import)

    # ── Tải WAV ──
    download_status: str = "pending"             # pending|downloading|downloaded|failed
    wav_path: Optional[str] = None               # file WAV THẬT trong staging
    sha256: Optional[str] = None                 # chống trùng nội dung
    download_error: Optional[str] = None

    # ── Xác thực kỹ thuật (ffprobe) — TÁCH khỏi thẩm định nội dung ──
    validation_status: str = "not_verified"      # not_verified|valid|invalid
    validation_error: Optional[str] = None
    verified_duration_seconds: Optional[float] = None
    verified_sample_rate: Optional[int] = None
    verified_channels: Optional[int] = None
    # Không có analyzer nội dung ⇒ giữ 'not_verified', KHÔNG bịa "đã nghe & chọn".
    content_qc_status: str = "not_verified"

    imported: bool = False                       # đã gọi import vào project chưa
    track_id: Optional[int] = None               # Track.id sau khi import

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════ TOOL COVER NHẠC ═══════════════════════
# Phần riêng của tool cover: lời bài hát + cài đặt video cover theo project.
# Tách bảng riêng để KHÔNG đụng schema lõi copy từ tool cũ.

class CoverSettings(SQLModel, table=True):
    """1-1 với Project: các lựa chọn riêng của video cover."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", unique=True, index=True)
    # Ngôn ngữ của CẢ video (lời, tiêu đề, mô tả): "vi" | "en" — không trộn.
    # "" = theo dòng nhạc của project (US-UK → en, còn lại → vi) — 2026-10-29.
    language: str = ""
    # Kiểu cover: "ballad" | "remix" | "acoustic" | "original" — dùng cho
    # thumbnail, mô tả YouTube và gợi ý cảnh. "" = theo thể loại project
    # (Deep House/Mix Việt → remix, Ballad/R&B/Lofi → ballad…), xem
    # backend/cover/genre_defaults.py.
    cover_style: str = ""
    # (cũ) Lặp lại danh sách cho đủ mix_duration_minutes — mixset giờ luôn phát
    # mỗi bài 1 lần, trường này chỉ còn giữ cho DB cũ, không dùng nữa.
    repeat_to_fill: bool = False
    # Lớp phủ trên video
    show_tracklist: bool = True
    show_lyrics: bool = True
    # Sóng âm nhảy theo nhạc ở dưới lời (2026-10-29)
    show_waveform: bool = True
    # Dòng phụ trên thumbnail (rỗng = tự đặt theo thể loại + kiểu cover + ngôn ngữ).
    thumb_subtitle: str = ""
    # Model Whisper dùng căn lời (large-v3 chính xác nhất; medium nhanh hơn).
    whisper_model: str = "large-v3"
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class CoverSong(SQLModel, table=True):
    """1-1 với Track: tên bài, ca sĩ, lời và thời gian từng câu."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    track_id: int = Field(foreign_key="track.id", unique=True, index=True)
    position: int = 0                      # thứ tự phát trong video (0 = đầu)
    title: str = ""                        # tên hiển thị trên danh sách bài
    artist: str = ""                       # ca sĩ/nhóm gốc (cho mô tả YouTube)
    lyrics_text: str = ""                  # lời thô người dùng dán (mỗi câu 1 dòng)
    # JSON: [{"text": str, "start": float, "end": float}] — giây, tính từ đầu FILE bài
    lyrics_json: str = "[]"
    # none | queued | running | done | failed | edited (người dùng đã sửa tay)
    align_status: str = "none"
    align_error: Optional[str] = None
    aligned_at: Optional[datetime] = None
    # Bài gốc (tab ① Tải nhạc gốc) mà bản cover này hát lại — None = nhập tay.
    source_id: Optional[int] = Field(default=None, index=True)
    # Tempo đã chỉnh cho BẢN COVER (tab ② — nút OK): 1.0 = giữ nguyên bản Suno.
    # File Track.filepath đã là bản đổi tempo; bản Suno gốc cất ở tempo_backup_path.
    tempo_factor: float = 1.0
    tempo_backup_path: str = ""
    # Chỉnh tay độ to bài này khi Mix (dB, cộng thêm sau cân bằng tự động) — tab ④.
    mix_gain_db: float = 0.0


class SourceSong(SQLModel, table=True):
    """Bài GỐC tải từ nhaccuatui (tab ① Tải nhạc gốc): link, lời, file nhạc gốc.
    Là nguồn cho bước Suno Cover; bản cover nhập vào sẽ mang sẵn tên/ca sĩ/lời."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    position: int = 0
    query: str = ""                        # người dùng gõ: «tên bài – ca sĩ» hoặc link
    title: str = ""                        # tên bài đọc từ nhaccuatui
    artist: str = ""                       # ca sĩ gốc
    nct_url: str = ""                      # link trang bài nhaccuatui
    file_path: str = ""                    # file nhạc gốc đã tải (media/<project>/nhac_goc/…)
    # Bản người dùng đã chủ động xuất sau khi chỉnh tempo. Suno chỉ nhận file này,
    # không nhận thẳng file_path, để không vô tình bỏ qua lựa chọn tempo trên UI.
    tempo_factor: float = 1.0               # tốc độ phát: 0.2× (chậm 5 lần) … 5×
    tempo_file_path: str = ""               # media/<project>/da_chinh_tempo/*.wav
    quality: str = ""                      # high | normal | lossless | ""
    lyrics_text: str = ""                  # lời lấy từ nhaccuatui (sửa được)
    # Kết quả bước 6: ChatGPT web đổi space thành _ + font Unicode nghệ thuật.
    # Tách khỏi lời gốc để sửa/tải lại lời không làm mất dữ liệu nguồn.
    lyrics_suno_text: str = ""
    # Prompt Styles đã được API AI phân tích cho Suno Cover. Signature giúp tự
    # viết lại khi project đổi thể loại/mô tả/cover_style/giọng hát.
    suno_style_prompt: str = ""
    suno_style_signature: str = ""
    # "" (người dùng tự dán) | Zing MP3 | NhacCuaTui | LRCLIB | Google/link web.
    lyrics_source: str = ""
    # queued | running | done | lyrics_only (có lời, chưa tải được file) |
    # login (cần đăng nhập) | not_found | failed | stopped |
    # manual (người dùng tự nhập bản cover + lời, không tải từ nhaccuatui)
    status: str = "queued"
    error: Optional[str] = None
    # Bước Suno Cover: "" | redo (bản cover cũ bị lỗi, chờ tạo lại) | queued |
    # running | done | rejected (Suno từ chối — bản quyền) | failed | stopped
    suno_status: str = ""
    suno_error: Optional[str] = None
    # id các bài Suno đã tạo cho bài này (phẩy ngăn cách) — thử lại chỉ TẢI về,
    # không bấm Create lần nữa (không tốn credit lại).
    suno_song_ids: str = ""
    # Bài trung gian đã Save as New Song trong Editor. Lưu ngay để nếu Remix →
    # Cover lỗi thì lần sau tiếp tục từ bài này, không upload/tạo bản Edit trùng.
    suno_editor_song_id: str = ""
    # 0 = tự động theo thể loại; 20..300 = BPM người dùng chọn khi tạo lại.
    suno_editor_bpm: int = 0
    # True = lượt tạo lại chỉ đổi Tempo; giữ nguyên Styles/lyrics/giọng/nhạc cụ.
    # Giữ qua lỗi để resume vẫn không tự viết lại prompt.
    suno_preserve_style: bool = False
    vocal_gender: str = ""                 # "" (tự đoán) | male | female | duet
    # Nhạc cụ nổi bật tùy chọn cho riêng bài cover; rỗng = không ép nhạc cụ.
    featured_instrument: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
