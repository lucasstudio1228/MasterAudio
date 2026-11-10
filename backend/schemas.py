"""Pydantic request/response schemas (tách khỏi DB models)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- Settings ----------
class AppSettingsRead(BaseModel):
    api_base_url: str
    api_model: str
    has_api_key: bool          # TRUE nếu key đã được set, KHÔNG trả key thật


class AppSettingsUpdate(BaseModel):
    api_key: Optional[str] = None          # None = không thay đổi key
    clear_api_key: bool = False            # True = xóa key hiện tại
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None


class ApiTestRequest(BaseModel):
    """Giá trị tạm để test (nếu bỏ trống → dùng cấu hình đã lưu trong DB).

    Cho phép test TRƯỚC khi lưu: gửi lên key/base_url/model đang gõ trong form.
    """
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None


class ApiTestResult(BaseModel):
    ok: bool
    model: str                             # model đã dùng để gọi thử
    base_url: str
    latency_ms: Optional[int] = None       # thời gian phản hồi khi thành công
    reply: Optional[str] = None            # nội dung model trả về (rút gọn)
    error_type: Optional[str] = None       # tên exception khi lỗi
    error: Optional[str] = None            # thông điệp lỗi thân thiện
    retryable: bool = False                # True nếu nên chờ & thử lại (vd 524)


class ThumbAISettingsRead(BaseModel):
    base_url: str
    model: str
    enabled: bool
    verify: bool
    has_api_key: bool          # KHÔNG trả key thật
    protocol: str              # gemini | images | chat (suy từ tên model)
    models: list[dict]         # model gợi ý + giá ShopAIKey
    engine: str = "chatgpt"    # máy vẽ thumbnail khi ghép video: chatgpt | api | pil
    engines: list[dict] = []
    chatgpt_logged_in: Optional[bool] = None   # lần kiểm tra gần nhất (None = chưa rõ)
    chatgpt_checked_at: Optional[str] = None


class ThumbAISettingsUpdate(BaseModel):
    api_key: Optional[str] = None          # None/rỗng = giữ key cũ
    clear_api_key: bool = False
    base_url: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    verify: Optional[bool] = None
    engine: Optional[str] = None           # chatgpt | api | pil


class ThumbAITestRequest(BaseModel):
    api_key: Optional[str] = None          # rỗng → key đã lưu
    base_url: Optional[str] = None
    model: Optional[str] = None


class ThumbAITestResult(BaseModel):
    ok: bool
    base_url: str
    model: str
    model_available: Optional[bool] = None  # model có trong danh sách của key?
    image_models: list[str] = []            # model ảnh key dùng được
    model_count: int = 0
    latency_ms: Optional[int] = None
    error: Optional[str] = None


# ---------- Projects ----------
IntroMode = Literal["thumbnail", "font"]
CastMode = Literal["character_pet", "scenery"]
FootageSceneMode = Literal["auto", "character_screen", "screen_only"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    # Nhạc cụ chủ đạo (khoá cho bảng nhân vật, cảnh clip và Styles Suno) +
    # thể loại nhạc. Rỗng = AI tự suy từ tên/mô tả như trước.
    instrument: str = ""
    music_style: str = ""
    # Tool Cover: dòng nhạc (VN / US-UK / Chinese…) — cùng thể loại để tự chọn kênh.
    music_line: str = ""
    # Kênh YouTube đăng nháp (ChannelMapping.id) — độc lập với nhạc cụ/thể loại.
    # None = tự chọn (AI phân loại theo nhạc cụ ở bước Suno / khớp ánh xạ cũ).
    channel_id: Optional[int] = None
    # Phong cách ảnh: "channel"/rỗng = theo phong cách riêng của kênh YouTube
    # (xoay vòng trong cặp phong cách của kênh); key cụ thể = giữ nguyên.
    video_style: Optional[str] = None
    # Intro 00:00: "thumbnail" (mặc định — ảnh thumbnail chuyển động) | "font".
    intro_mode: IntroMode = "thumbnail"
    # Dàn cảnh: "character_pet" (mặc định — nhân vật + linh thú) | "scenery".
    cast_mode: CastMode = "character_pet"
    footage_scene_mode: FootageSceneMode = "auto"
    creative_instrument: bool = True
    # Nhóm: "mixset" (nhiều bài ghép 1 video) | "single" (Track lẻ — 1 bài 1 video).
    kind: Literal["mixset", "single"] = "mixset"


class ProjectBulkDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    # False (mặc định) = chuyển vào Thùng rác 7 ngày; True = xoá vĩnh viễn ngay.
    permanent: bool = False


class ProjectRename(BaseModel):
    name: str
    # none = chỉ đổi tên · text = vẽ lại chữ thumbnail + ghép lại video ·
    # regen = tạo lại ảnh bìa (Muse.ai, dự phòng Gemini) + clip 00 rồi như text
    refresh: Optional[str] = "none"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    video_idea: Optional[str] = None
    auto_video: Optional[bool] = None
    video_style: Optional[str] = None
    intro_mode: Optional[IntroMode] = None
    cast_mode: Optional[CastMode] = None
    footage_scene_mode: Optional[FootageSceneMode] = None
    creative_instrument: Optional[bool] = None
    instrument: Optional[str] = None
    music_style: Optional[str] = None
    music_line: Optional[str] = None
    channel_id: Optional[int] = None         # null = bỏ chọn (tự khớp như cũ)
    auto_upload: Optional[bool] = None
    music_source: Optional[str] = None       # "local" | "suno"
    suno_idea: Optional[str] = None          # ý tưởng/nhạc cụ cho STEP 0 (Suno)
    # Tổng thời lượng mix/video (phút) + crossfade (giây) — dùng cả cho auto.
    mix_duration_minutes: Optional[float] = Field(default=None, ge=1, le=720)
    mix_crossfade_seconds: Optional[float] = Field(default=None, ge=1, le=60)
    # Đổi nhóm Mix Set ↔ Track lẻ — thư mục media được chuyển theo.
    kind: Optional[Literal["mixset", "single"]] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    created_at: datetime
    track_count: int = 0
    mix_count: int = 0
    video_idea: str = ""
    auto_video: bool = False
    video_style: str = "real"
    intro_mode: str = "thumbnail"
    cast_mode: str = "character_pet"
    footage_scene_mode: str = "auto"
    creative_instrument: bool = True
    instrument: str = ""
    music_style: str = ""
    music_line: str = ""
    auto_upload: bool = False
    music_source: str = "local"
    suno_idea: str = ""
    batch_id: Optional[str] = None
    kind: str = "mixset"
    mix_duration_minutes: float = 120.0
    mix_crossfade_seconds: float = 5.0
    # Kênh đã chọn (None = chưa chọn) + kênh THỰC SỰ dùng khi đăng nháp (đã chọn,
    # hoặc khớp theo nhạc cụ + thể loại với project cũ chưa chọn).
    channel_id: Optional[int] = None
    effective_channel_id: Optional[int] = None
    effective_channel_name: str = ""


# ---------- Batch (sản xuất hàng loạt) ----------
class BatchCreate(BaseModel):
    count: int = Field(ge=1, le=50)
    # Nhạc cụ + thể loại nhạc: giữ nguyên cả lô, là đầu vào của mọi prompt
    # (Suno/Gemini/Flow). Kênh đăng nháp = channel_id (không cần khớp).
    instrument: str = ""   # tool Cover: không dùng nhạc cụ (chỉ thể loại)
    music_style: str = Field(min_length=1)
    music_line: str = ""   # dòng nhạc cả lô (VN / US-UK…) — để tự chọn kênh
    channel_name: str = ""
    # Kênh YouTube cho cả lô — kênh nào cũng nhận nhạc cụ/thể loại nào.
    channel_id: Optional[int] = None
    # Style ảnh — key trong video/config.STYLES (2d/3d/…/real) giữ nguyên cả lô,
    # hoặc "channel" = theo phong cách riêng của kênh (xoay vòng trong lô).
    video_style: str = "channel"
    # Intro + dàn cảnh giữ nguyên cả lô (mặc định: thumbnail động + nhân vật/linh thú).
    intro_mode: IntroMode = "thumbnail"
    cast_mode: CastMode = "character_pet"
    # Chủ đề tuỳ chọn (giữ mạch); rỗng = AI tự chọn 1 mạch chung.
    theme: str = ""
    # Ngân sách Suno mỗi project (mặc định theo SunoConfig nếu bỏ trống).
    target_tracks: Optional[int] = None
    max_create_actions: Optional[int] = None
    # Tổng thời lượng mix/video (phút) cho MỖI project trong lô.
    mix_duration_minutes: float = Field(default=120.0, ge=1, le=720)


class BatchProjectItem(BaseModel):
    project_id: int
    name: str
    suno_phase: Optional[str] = None
    suno_message: Optional[str] = None


class MissingLyricsItem(BaseModel):
    source_id: int
    project_id: int
    query: str
    title: str = ""
    artist: str = ""


class BatchRunResponse(BaseModel):
    batch_key: str
    total: int
    completed: int
    failed: int
    status: str
    current_index: int
    current_project_id: Optional[int] = None
    message: str = ""
    params: dict = {}
    created_at: datetime
    updated_at: datetime
    projects: list[BatchProjectItem] = []
    missing_lyrics: list[MissingLyricsItem] = []


# ---------- Tracks ----------
class TrackResponse(BaseModel):
    id: int
    project_id: int
    filename: str
    filepath: str
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str
    is_lossy: bool
    subtype: Optional[str] = None
    added_at: datetime


class ScanRequest(BaseModel):
    folder_path: str
    recursive: bool = False


class AddFileRequest(BaseModel):
    file_path: str


class ScanResult(BaseModel):
    added: int
    skipped_duplicates: int
    skipped_errors: int
    error_files: list[str]
    total_tracks: int
    warning_lossy: bool


# ---------- Mixes ----------
class MixCreate(BaseModel):
    duration_minutes: float = 120.0
    crossfade_seconds: float = 5.0
    # sample_rate = 0 → auto (max native của library, không upsample giả).
    sample_rate: int = 0
    # bit_depth mặc định 32 (float, không nén) — chất lượng tối đa.
    bit_depth: int = 32
    title: Optional[str] = None
    # Đo + cân bằng độ to giữa các bài (cover/loudness_balance.py). False = cách cũ.
    loudness_balance: bool = True


class MixResponse(BaseModel):
    id: int
    project_id: int
    title: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    duration_minutes: float
    crossfade_seconds: float
    sample_rate: int
    bit_depth: int
    output_dir: Optional[str] = None
    total_duration_seconds: Optional[float] = None
    track_count: Optional[int] = None
    error_message: Optional[str] = None
    # Trạng thái realtime từ job_manager (nếu đang chạy)
    progress_percent: float = 0.0
    progress_step: int = 0
    progress_step_name: str = ""


# ---------- Detail ----------
class ProjectDetailResponse(ProjectResponse):
    tracks: list[TrackResponse] = []
    mixes: list[MixResponse] = []
