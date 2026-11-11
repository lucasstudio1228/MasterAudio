"""
video/config.py — Toàn bộ tham số & selector cho module Video, gom 1 chỗ.

⚠️  QUAN TRỌNG: Các SELECTOR của ChatGPT và Flow là "best-effort" — 2 site này
đổi giao diện thường xuyên. Khi automation lỗi, sửa selector Ở ĐÂY (không phải
rải rác trong driver). Có thể override qua file JSON video_overrides.json nếu có.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
import shutil
import sys

# ── Thư mục ─────────────────────────────────────────────────────
_ROOT       = Path(__file__).parent.parent.parent          # meditation_mixer/
MEDIA_ROOT  = _ROOT / "media"                              # media/<project_name>/...
PROFILE_DIR = _ROOT / ".browser_profile"                  # Chrome profile (giữ login)
OVERRIDES   = _ROOT / "data" / "video_overrides.json"     # override selector (tùy chọn)

STEM_IMAGE  = "images"    # media/<project>/images/0.png..40.png
STEM_CLIP   = "clips"     # media/<pid>/clips/clip_00.mp4..
STEM_FINAL  = "final"     # media/<pid>/final/final.mp4
STEM_UPLOAD = "uploads"   # media/<pid>/uploads/<file> — nhạc nền upload cho video
STEM_FOOTAGE = "footage"   # media/<pid>/footage/source.* — cảnh quay thật, chèn 1 lần

# Đuôi file audio chấp nhận cho nhạc nền video (ffmpeg đọc trực tiếp được).
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma")


def project_folder_name(project_name: str | None, project_id: int) -> str:
    """
    Tên thư mục media: tên project + id để VỪA dễ đọc VỪA ỔN ĐỊNH.
    Kèm '#<id>' để đổi tên project KHÔNG trỏ nhầm sang folder cũ, và 2 project
    trùng tên KHÔNG dùng chung folder (trước đây gây lỗi render lấy ảnh của
    project khác / bản cũ).
    """
    name = (project_name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return f"{name} (#{project_id})" if name else f"project_{project_id}"

# Thư mục nhóm trong media/: Mix Set (nhiều bài ghép 1 video) và Track lẻ (mỗi
# bài 1 project/video) tách riêng cho dễ theo dõi. Project tạo TRƯỚC khi chia
# nhóm vẫn nằm thẳng trong media/ — không tự di chuyển (đường dẫn file nằm
# trong DB, chuyển sai sẽ làm project mất file).
GROUP_MIXSET = "Mix Set"
GROUP_SINGLE = "Track lẻ"
MEDIA_GROUPS = (GROUP_MIXSET, GROUP_SINGLE)


def media_group(kind: str | None) -> str:
    return GROUP_SINGLE if (kind or "") == "single" else GROUP_MIXSET


# DB của app đi cùng thư mục media thật; MEDIA_ROOT bị đổi (test/thư mục tạm)
# thì không tra DB thật — tránh đặt thư mục theo project không liên quan.
_APP_MEDIA_ROOT = MEDIA_ROOT


def _project_kind(project_id: int, root: Path) -> str | None:
    """Loại project trong DB ("mixset"/"single"); None nếu không tra được."""
    if Path(root) != Path(_APP_MEDIA_ROOT):
        return None
    try:
        from sqlmodel import Session
        from backend.database import engine
        from backend.models import Project
        with Session(engine) as session:
            project = session.get(Project, project_id)
            return (getattr(project, "kind", None) or "mixset") if project else None
    except Exception:  # noqa: BLE001 — DB chưa sẵn sàng/test: dùng chỗ cũ
        return None


def project_dir(project_id: int, project_name: str | None = None, *,
                kind: str | None = None, root: Path | None = None) -> Path:
    """media/<nhóm>/<tên> (#id). Thư mục đã có ở đâu thì dùng đúng chỗ đó
    (project cũ nằm thẳng trong media/); chưa có thì đặt theo nhóm của project."""
    root = Path(root or MEDIA_ROOT)
    folder = project_folder_name(project_name, project_id)
    legacy = root / folder
    if legacy.exists():
        return legacy
    for group in MEDIA_GROUPS:
        if (root / group / folder).exists():
            return root / group / folder
    kind = kind or _project_kind(project_id, root)
    return legacy if kind is None else root / media_group(kind) / folder

def images_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_IMAGE

def clips_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_CLIP

def final_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_FINAL

def uploads_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_UPLOAD

def footage_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_FOOTAGE

def footage_files(project_id: int, project_name: str | None = None) -> list[Path]:
    """Tất cả footage của project, theo thứ tự upload (cũ → mới). Khi ghép,
    footage phát lần lượt theo thứ tự này rồi lặp lại tới hết video."""
    root = footage_dir(project_id, project_name)
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir()
                   if p.is_file() and not p.name.startswith(".")),
                  key=lambda p: (p.stat().st_mtime, p.name))


def footage_file(project_id: int, project_name: str | None = None) -> Path | None:
    """Footage đầu tiên (tương thích code cũ); None nếu chưa có."""
    files = footage_files(project_id, project_name)
    return files[0] if files else None


# ── Tham số sinh ảnh / video ────────────────────────────────────
# Vai trò của 3 ảnh trong quy trình mới (xem VideoParams bên dưới).
IMG_THUMBNAIL  = 0      # ảnh bìa/thumbnail ChatGPT — làm video intro + đăng YouTube
IMG_MAIN_SHEET = 1      # bản phân tích nhân vật chính (main character sheet)
IMG_PET_SHEET  = 2      # bản phân tích linh thú/thú cưng (pet character sheet)
# Hai sheet này được đưa CÙNG LÚC vào Flow cho các clip 01..N, nhờ đó phần thân
# video giữ nguyên một nhân vật + một linh thú nhưng khác bối cảnh.
SHEET_IMAGES = (IMG_MAIN_SHEET, IMG_PET_SHEET)
# Clip 00 dùng CHÍNH thumbnail đã có chữ làm ảnh tham chiếu để tạo video intro.
# Các clip 01..N vẫn chỉ dùng hai bảng phân tích/tham chiếu để giữ nhân vật và
# bối cảnh nhất quán. Clip 00 được ghép đúng một lần tại 00:00, không nằm trong
# cycle lặp.
INTRO_CLIP = 0
INTRO_INGREDIENTS = (IMG_THUMBNAIL,)

IMAGE_ROLES: dict[int, str] = {
    IMG_THUMBNAIL:  "Ảnh bìa (thumbnail)",
    IMG_MAIN_SHEET: "Bảng phân tích nhân vật chính",
    IMG_PET_SHEET:  "Bảng phân tích linh thú / thú cưng",
}

# Thứ tự TẠO ảnh: hai bảng phân tích TRƯỚC, ảnh bìa SAU (đúng thứ tự người dùng
# yêu cầu — duyệt được nhân vật ngay từ tấm đầu, và bìa vẽ sau thì bám theo hai
# bảng đã chốt). Chỉ ảnh hưởng thứ tự chạy, không đổi tên file 0/1/2.png.
IMAGE_ORDER: tuple[int, ...] = (IMG_MAIN_SHEET, IMG_PET_SHEET, IMG_THUMBNAIL)
# Ảnh bìa BẮT BUỘC dựa trên 2 bảng nhân vật: driver đính kèm 1.png + 2.png làm
# ảnh tham chiếu khi tạo ảnh 0 (vì vậy IMAGE_ORDER tạo 2 bảng trước).
THUMBNAIL_REFS: tuple[int, ...] = SHEET_IMAGES
THUMBNAIL_REF_NOTE = (
    " || REFERENCE (mandatory): the two attached images are the MAIN CHARACTER "
    "MODEL SHEET and the COMPANION SHEET (pet or creature) — generate a NEW image "
    "that draws EXACTLY those two characters (identical face, hairstyle, outfit, "
    "instrument, "
    "fur/scale colours, proportions and palette), but as ONE complete illustrated "
    "scene, NOT a design sheet, NO text labels.")

# Chế độ CHỈ CẢNH VẬT (cast_mode="scenery"): ảnh 1/2 là bảng thiết kế BỐI CẢNH
# thay cho bảng nhân vật — tên file 0/1/2.png và luồng chạy giữ nguyên.
IMAGE_ROLES_SCENERY: dict[int, str] = {
    IMG_THUMBNAIL:  "Ảnh bìa (thumbnail)",
    IMG_MAIN_SHEET: "Bảng thiết kế bối cảnh (world sheet)",
    IMG_PET_SHEET:  "Bảng ánh sáng & không khí (mood sheet)",
}
THUMBNAIL_REF_NOTE_SCENERY = (
    " || REFERENCE (mandatory): the two attached images are the ENVIRONMENT "
    "DESIGN SHEET and the ATMOSPHERE / LIGHT SHEET of this place — generate a NEW "
    "image that shows EXACTLY that same place (identical landmarks, architecture, "
    "vegetation, water, materials, palette and light), but as ONE complete "
    "illustrated scene, NOT a design sheet, NO text labels, NO people, NO "
    "characters, NO animals.")

# Bộ prompt luôn dùng KHOÁ CHUỖI (prompts.json là JSON).
IMG_THUMBNAIL_KEY  = str(IMG_THUMBNAIL)
IMG_MAIN_SHEET_KEY = str(IMG_MAIN_SHEET)
IMG_PET_SHEET_KEY  = str(IMG_PET_SHEET)


@dataclass
class VideoParams:
    # ẢNH — chỉ 3 tấm (quy trình mới):
    #   0 = thumbnail (ảnh bìa, tạo 1 lần duy nhất)
    #   1 = main character sheet   2 = pet character sheet
    image_count:     int   = 3
    aspect_ratio:    str   = "16:9"
    image_style:     str   = "animation/cartoon, soft gentle colours, cinematic"

    # Video (Flow / Veo) — clip 00 dùng thumbnail có chữ; các clip còn lại dùng
    # cả hai bảng phân tích/tham chiếu + prompt bối cảnh riêng.
    flow_model:      str   = "Omni 1.1 Flash"
    flow_mode:       str   = "Ingredients"   # Flow có thể hiện tiếng Anh/Việt
    clip_seconds:    int   = 8
    outputs_per_run: int   = 1               # x1 video
    # 2026-09-28: hạ 40 → 20 clip/video (Flow gắn cờ sau ~5–20 clip/ngày).
    # 2026-09-29: Gemini Video khoá «Tạo video» sau ~10 lượt/ngày (Ultra) ⇒ CHƯA
    # nâng lại 40. Nâng thì chỉ đổi 3 số dưới — hồ sơ 20 cảnh được
    # prompt_workflow.extend_scene_count tự viết thêm cảnh còn thiếu.
    scene_clips:     int   = 20              # 20 clip, 20 bối cảnh khác nhau
    # Giữ 2 field này cho API/UI cũ: clip 00 là intro, còn lại chạy cycle random.
    rest_clips:      int   = 19
    min_total_clips: int   = 20
    # Tool Cover: ảnh bìa (0) là GỐC — bảng nhân vật (1) + bảng bối cảnh (2) vẽ
    # TỪ ảnh bìa; clip 00 intro, 01 loop, 02.. các góc quay nhân vật.
    cover:           bool  = False

    # Ghép/mix: blend (crossfade) + làm chậm
    t_window:        int   = 10             # T+10: không lặp trong 10 clip kế
    blend_seconds:   float = 3.0            # crossfade êm (softfade) giữa 2 clip
    slow_speed:      float = 0.7            # tốc độ phát (0.7 = chậm lại, mượt)

    @property
    def total_clips(self) -> int:
        return self.scene_clips


def generate_frame_pairs(image_count: int, total_clips: int,
                         seed: int | None = None) -> list[tuple[int, int]]:
    """
    Sinh danh sách cặp khung (start_img, end_img) cho các clip, sao cho:
      - CLIP ĐẦU TIÊN luôn BẮT ĐẦU từ ảnh 0 (thumbnail): cặp[0] = (0, 1).
      - Mỗi ảnh đều có ÍT NHẤT 1 "đường ra" (cạnh nền vòng tròn 0→1→…→n-1→0),
        để lúc ghép luôn nối tiếp được (ảnh cuối clip = ảnh đầu clip kế → liền mạch).
      - Thêm các cặp NGẪU NHIÊN (vd 4→1, 1→3) cho đa dạng.
    Trả về total_clips cặp; cặp[0] cố định (0→1), phần còn lại trộn ngẫu nhiên.
    """
    import random
    rng = random.Random(seed)
    n = max(image_count, 2)

    first = (0, 1 % n)                                     # clip đầu: 0 → 1
    # cạnh nền vòng tròn còn lại (1→2, …, (n-1)→0) để đảm bảo liền mạch
    rest: list[tuple[int, int]] = [(i, (i + 1) % n) for i in range(1, n)]
    while len(rest) + 1 < total_clips:
        a = rng.randrange(n)
        b = rng.randrange(n)
        if a != b:
            rest.append((a, b))
    rng.shuffle(rest)
    pairs = [first] + rest
    return pairs[:total_clips]


def generate_ingredient_plan(scene_clips: int,
                             sheets: tuple[int, ...] | list[int] | None = None,
                             ) -> list[list[int]]:
    """
    Clip 00 dùng chính thumbnail ChatGPT đã có chữ làm ảnh tham chiếu. Các clip
    01..N dùng hai character/environment sheet (ảnh 1 + ảnh 2); khác nhau bằng
    prompt bối cảnh. Nhờ vậy intro khớp tuyệt đối thumbnail, còn phần thân video
    vẫn giữ nhân vật/bối cảnh ổn định.

    Trả về: [[0], [1, 2], [1, 2], ...].
    (Trước đây hàm này trả list[int] vì mỗi clip 1 ảnh — pipeline 1:1 đã bỏ.)
    """
    count = max(int(scene_clips), 0)
    if not count:
        return []
    ings = list(sheets if sheets is not None else SHEET_IMAGES)
    plan = [list(INTRO_INGREDIENTS)]
    plan.extend(list(ings) for _ in range(count - 1))
    return plan


def clip_ingredient_path(project_id: int, project_name: str | None,
                         index: int) -> Path:
    """Đường dẫn ảnh thực tế gửi cho máy tạo video.

    Flow chọn nguyên liệu theo tên ``0.png``. Thumbnail hoàn chỉnh lại nằm ở
    ``final/thumbnail.png`` nên ta tạo một bản staging mang đúng tên ``0.png``
    trong thư mục clip, không ghi đè ảnh bìa sạch ``images/0.png``.
    """
    idx = int(index)
    if idx != IMG_THUMBNAIL:
        return images_dir(project_id, project_name) / f"{idx}.png"
    source = final_dir(project_id, project_name) / "thumbnail.png"
    if not source.exists():
        source = images_dir(project_id, project_name) / "0.png"
    staged_dir = clips_dir(project_id, project_name) / "_ingredients"
    staged = staged_dir / "0.png"
    if source.exists():
        staged_dir.mkdir(parents=True, exist_ok=True)
        try:
            if (not staged.exists()
                    or staged.stat().st_mtime_ns != source.stat().st_mtime_ns
                    or staged.stat().st_size != source.stat().st_size):
                shutil.copy2(source, staged)
        except OSError:
            shutil.copyfile(source, staged)
    return staged


def intro_clip_needs_refresh(project_id: int, project_name: str | None) -> bool:
    """Project cũ/resume có clip 00 tạo từ sheet thay vì thumbnail hay không."""
    thumb = final_dir(project_id, project_name) / "thumbnail.png"
    clip = clips_dir(project_id, project_name) / "clip_00.mp4"
    if not thumb.exists() or not clip.exists():
        return False
    try:
        if clip.stat().st_mtime_ns < thumb.stat().st_mtime_ns:
            return True
    except OSError:
        return True
    plan_file = clips_dir(project_id, project_name) / "_plan.json"
    try:
        saved = json.loads(plan_file.read_text(encoding="utf-8"))
        plan = saved.get("plan") if isinstance(saved, dict) else None
        return not (isinstance(plan, list) and plan
                    and plan[0] == list(INTRO_INGREDIENTS))
    except Exception:
        # Không có bằng chứng clip cũ đã dùng thumbnail → làm lại intro một lần.
        return True


# ── 10 PHONG CÁCH ẢNH ───────────────────────────────────────────
# Vấn đề: khi prompt mô tả nhân vật quá "thực" (chất liệu vải, màu da, mã màu
# hex) và từ khoá phong cách nằm cuối/quá yếu, Gemini render ra ẢNH THẬT
# (photorealistic). Nên ta ÉP một câu lệnh phong cách mạnh vào ĐẦU mọi prompt
# (nơi model coi trọng nhất) + một câu PHỦ ĐỊNH ở cuối cấm ảnh thật.
# Người dùng chọn 2D hoặc 3D theo từng project → quyết định prefix/negative,
# lời hướng dẫn cho AI viết prompt, và gợi ý phong cách cho clip (Flow/Veo).
# Đổi khi thay đổi định nghĩa phong cách. Cache prompts.json có style_version
# KHÁC giá trị này (hoặc khác style_key) sẽ bị coi là cũ → sinh lại prompt.
STYLE_VERSION = "styles-v8"
DEFAULT_STYLE = "real"   # Tả thực điện ảnh (2026-10-29)

# Mỗi phong cách gồm:
#   icon/label/desc → hiển thị ở UI (bộ chọn cuộn được)
#   prefix   → chèn vào ĐẦU mọi prompt ảnh
#   negative → chèn vào CUỐI mọi prompt ảnh (ép đúng phong cách, cấm phong cách khác)
#              prefix/negative KHÔNG được đọc trực tiếp ở đâu khác ngoài
#              wrap_style() bên dưới; wrap_style được gọi ở
#              gemini_driver._prompt_for lúc GỬI prompt cho Gemini. Vì bọc lúc
#              gửi (không lưu vào prompts.json) nên sửa 2 trường này KHÔNG cần
#              tăng STYLE_VERSION — prompt body trong cache vẫn hợp lệ.
#   brief    → đưa vào lời nhắc AI viết prompt (prompt_gen). Với phong cách TẢ THỰC,
#              brief PHẢI chứa "tả thực" hoặc "photoreal" để prompt_gen bật nhánh
#              photoreal (xem prompt_gen._is_photoreal).
#   motion   → gợi ý phong cách gắn vào prompt chuyển động của clip (Flow/Veo)
STYLES: dict[str, dict[str, str]] = {
    "2d": {
        "icon": "🎨",
        "label": "2D (tranh vẽ / anime / cartoon)",
        "desc": "Tranh vẽ tay 2D, tô màu phẳng, anime/cartoon",
        "prefix": (
            "2D ANIMATED ILLUSTRATION (2D animation / cartoon / anime-style "
            "illustration), soft hand-drawn linework, flat cel-shaded colouring, "
            "gentle pastel colours, cinematic lighting. THIS IS A 2D CARTOON "
            "ILLUSTRATION, NOT A REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: hand-drawn 2D animation / cartoon / anime "
            "(flat cel-shading). ABSOLUTELY NOT photorealistic, NOT a real "
            "photograph (photo/photograph/realistic photo), NO lifelike human "
            "skin/fabric/hair, NO realistic 3D render, NO hyperrealism. When in "
            "doubt, always lean towards flat hand-drawn 2D linework."
        ),
        "brief": (
            "hand-drawn 2D animated illustration / cartoon / anime-style "
            "illustration, flat cel-shading, soft outlines, gentle pastel colours"
        ),
        "motion": "2D hand-drawn animated cartoon look",
    },
    "3d": {
        "icon": "🧊",
        "label": "3D (Pixar / Disney CGI)",
        "desc": "Khối 3D mềm mại kiểu Pixar / Disney",
        "prefix": (
            "3D ANIMATED MOVIE (3D animated movie / Pixar-Disney style / "
            "stylized 3D CGI render), characters and scenery with soft, cute 3D "
            "volumes, gentle shading, warm pastel colours, cinematic lighting. "
            "THIS IS A 3D ANIMATED IMAGE, NOT A REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: Pixar/Disney-style 3D animation (stylized 3D "
            "CGI). ABSOLUTELY NOT photorealistic, NOT a real photograph (photo/"
            "photograph/realistic photo), NO lifelike human skin/fabric/hair, NO "
            "hyperrealism, NOT a flat 2D drawing. When in doubt, always lean "
            "towards stylised animated-film 3D volumes."
        ),
        "brief": (
            "Pixar/Disney-style 3D animated film (stylized 3D CGI), soft cute 3D "
            "volumes, gentle shading, light subsurface scattering, warm pastel colours"
        ),
        "motion": "stylized 3D animated feature film look, soft rounded shapes",
    },
    "ghibli": {
        "icon": "🌿",
        "label": "Ghibli (anime vẽ tay)",
        "desc": "Studio Ghibli, nền màu nước mộng mơ",
        "prefix": (
            "STUDIO GHIBLI-STYLE HAND-PAINTED ANIME (hand-painted Ghibli-style "
            "anime), hand-painted watercolour-like backgrounds, soft billowing "
            "clouds, warm nostalgic light, meticulous nature detail, clear fresh "
            "colours. THIS IS A HAND-PAINTED ANIME ILLUSTRATION, NOT A REAL "
            "PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: hand-painted Ghibli-style anime (painterly, "
            "watercolor backgrounds). ABSOLUTELY NOT photorealistic, NOT a real "
            "photograph, NO 3D CGI, NO hyperrealism. When in doubt, lean towards "
            "dreamy hand-painted watercolour linework."
        ),
        "brief": (
            "hand-painted Studio Ghibli-style anime, hand-painted watercolour "
            "backgrounds, soft clouds, warm nostalgic light, meticulous nature detail"
        ),
        "motion": "hand-painted Japanese anime look, gentle painterly motion",
    },
    "anime": {
        "icon": "✨",
        "label": "Anime điện ảnh (Makoto Shinkai)",
        "desc": "Bầu trời rực rỡ, ánh sáng lung linh",
        "prefix": (
            "MODERN CINEMATIC ANIME (modern cinematic anime, Makoto Shinkai "
            "style), brilliant detailed skies, shimmering lens-flare light, "
            "beautiful saturated colours, cinematic lighting effects, crisp "
            "linework. THIS IS AN ANIME ILLUSTRATION, NOT A REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: modern cinematic anime (Makoto Shinkai). "
            "ABSOLUTELY NOT photorealistic, NOT a real photograph, NO Pixar-style "
            "3D CGI, NO hyperrealism. When in doubt, lean towards anime linework "
            "with brilliant skies."
        ),
        "brief": (
            "modern cinematic anime in the style of Makoto Shinkai, brilliant "
            "detailed skies, lens-flare light, saturated colours, crisp cinematic linework"
        ),
        "motion": "cinematic modern anime look, luminous detailed skies",
    },
    "watercolor": {
        "icon": "🖌️",
        "label": "Màu nước (watercolor)",
        "desc": "Màu loang mềm trên giấy, trong trẻo",
        "prefix": (
            "WATERCOLOUR PAINTING (watercolor painting illustration), soft "
            "pigments bleeding on paper, wet edges, layered transparent washes, "
            "breathing white space, loose brushwork. THIS IS A HAND-PAINTED "
            "WATERCOLOUR, NOT A REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: hand-painted watercolour (watercolor). "
            "ABSOLUTELY NOT photorealistic, NOT a real photograph, NO 3D CGI, NO "
            "hard flat vector lines. When in doubt, lean towards soft pigments "
            "bleeding on paper."
        ),
        "brief": (
            "hand-painted watercolour, soft pigments bleeding on paper, wet edges, "
            "layered transparent washes, breathing white space"
        ),
        "motion": "hand-painted watercolor illustration look, soft bleeding pigments",
    },
    "oil": {
        "icon": "🖼️",
        "label": "Sơn dầu (oil painting)",
        "desc": "Nét cọ impasto dày, màu giàu chiều sâu",
        "prefix": (
            "OIL PAINTING (oil painting), clearly visible thick impasto "
            "brushstrokes, rich deep colours, warm classical-painting light, "
            "thick paint texture. THIS IS A HAND-PAINTED OIL PAINTING, NOT A REAL "
            "PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: hand-painted oil painting (oil painting, "
            "impasto). ABSOLUTELY NOT photorealistic, NOT a real photograph, NO "
            "3D CGI, NO flat digital linework. When in doubt, lean towards thick "
            "oil brushstrokes."
        ),
        "brief": (
            "hand-painted oil painting, thick visible impasto brushstrokes, rich "
            "deep colours, warm classical-painting light"
        ),
        "motion": "oil painting look, visible brushstroke texture",
    },
    "ink": {
        "icon": "🖋️",
        "label": "Thuỷ mặc (ink wash Á Đông)",
        "desc": "Mực loang tối giản, nhiều khoảng trống",
        "prefix": (
            "EAST-ASIAN INK WASH PAINTING (East-Asian ink wash painting, sumi-e), "
            "black ink bleeding on rice paper, generous empty space, minimal "
            "graceful brushstrokes, pale colour accents. THIS IS A HAND-PAINTED "
            "INK PAINTING, NOT A REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: East-Asian ink wash painting (ink wash / "
            "sumi-e). ABSOLUTELY NOT photorealistic, NOT a real photograph, NO 3D "
            "CGI, NO vivid saturated colours. When in doubt, lean towards minimal "
            "bleeding ink with lots of empty space."
        ),
        "brief": (
            "East-Asian ink wash painting (ink wash / sumi-e), ink bleeding on "
            "paper, generous empty space, minimal graceful brushstrokes"
        ),
        "motion": "East-Asian ink wash painting look, flowing ink, minimalist",
    },
    "lofi": {
        "icon": "🎧",
        "label": "Lofi aesthetic",
        "desc": "Cozy hoài niệm, hạt film ấm",
        "prefix": (
            "LOFI ILLUSTRATION (lo-fi anime aesthetic illustration), warm "
            "nostalgic tones, light film-grain noise, soft cozy lamplight, warm "
            "everyday details, soft 2D linework. THIS IS AN ILLUSTRATION, NOT A "
            "REAL PHOTOGRAPH. "
        ),
        "negative": (
            " || REQUIRED STYLE: lofi aesthetic illustration (2D). ABSOLUTELY NOT "
            "photorealistic, NOT a real photograph, NO realistic 3D CGI. When in "
            "doubt, lean towards warm nostalgic 2D illustration linework."
        ),
        "brief": (
            "2D lofi aesthetic illustration, warm nostalgic tones, light film "
            "grain, soft cozy lamplight, warm everyday details"
        ),
        "motion": "lo-fi aesthetic 2D illustration look, cozy warm ambience",
    },
    "real": {
        "icon": "📷",
        "label": "Tả thực điện ảnh (cinematic photoreal)",
        "desc": "Như ảnh chụp / phim điện ảnh thật",
        "prefix": (
            "CINEMATIC PHOTOREALISTIC IMAGE (cinematic photorealistic, "
            "hyperrealistic photo-real render), cinematic lighting, shallow depth "
            "of field / bokeh, true-to-life detail in materials, skin, fabric, "
            "hair and fur, high resolution, film colour grading. THIS IS A "
            "PHOTOREALISTIC IMAGE LIKE A REAL PHOTOGRAPH, NOT A CARTOON. "
        ),
        "negative": (
            " || REQUIRED STYLE: cinematic photorealism (like a real photograph / "
            "live-action film). ABSOLUTELY NO animation, NO cartoon/anime, NO "
            "flat 2D drawing (cel-shading), NO stylised Pixar/Disney 3D, NO "
            "hand-drawn linework or flat colouring. When in doubt, always lean "
            "towards a real photograph with true-to-life detail and cinematic "
            "lighting."
        ),
        "brief": (
            "cinematic photorealistic image, cinematic lighting, shallow depth of "
            "field / bokeh, true-to-life detail in materials, skin, fabric and "
            "hair, film colour grading, high detail"
        ),
        "motion": (
            "cinematic photorealistic live-action look, realistic natural "
            "lighting, shallow depth of field"
        ),
    },
    "vintage": {
        "icon": "📽️",
        "label": "Phim nhựa cổ điển (vintage film)",
        "desc": "Ảnh chụp phim analog cổ, hạt film hoài niệm",
        "prefix": (
            "VINTAGE ANALOG FILM PHOTOGRAPH (vintage analog film photograph, "
            "35mm), photorealistic like a real photo, visible film grain, faded "
            "warm nostalgic tones, light vignette, soft natural light. THIS IS A "
            "REAL VINTAGE-FILM PHOTOGRAPH, NOT A CARTOON. "
        ),
        "negative": (
            " || REQUIRED STYLE: vintage analog film photograph, photorealistic. "
            "ABSOLUTELY NO animation, NO cartoon/anime, NO stylised 3D CGI, NO "
            "painting. When in doubt, lean towards a real film photograph with "
            "grain and nostalgic colours."
        ),
        "brief": (
            "vintage 35mm analog film photograph, photorealistic like a real "
            "photo, film grain, faded nostalgic colours, light vignette"
        ),
        "motion": "vintage analog film photograph look, realistic 35mm film grain",
    },
}


def normalize_style(style: str | None) -> str:
    """Chuẩn hoá key phong cách; giá trị lạ → DEFAULT_STYLE."""
    s = (style or "").strip().lower()
    return s if s in STYLES else DEFAULT_STYLE


def style_info(style: str | None) -> dict[str, str]:
    return STYLES[normalize_style(style)]


def wrap_style(prompt: str, style: str | None = None) -> str:
    """Bọc prompt bằng prefix phong cách + phủ định cấm các phong cách khác.

    Gọi ở gemini_driver._prompt_for cho MỌI prompt ảnh ngay trước khi gửi."""
    s = style_info(style)
    return f"{s['prefix']}{prompt}{s['negative']}"


def wrap_sheet_style(prompt: str, style: str | None = None) -> str:
    """Bọc riêng cho ẢNH 1 & 2 — BẢNG PHÂN TÍCH NHÂN VẬT.

    KHÔNG dùng wrap_style: prefix điện ảnh ("ánh sáng điện ảnh, bokeh, chiều
    sâu trường ảnh") nằm ở đầu prompt sẽ đè bố cục tài liệu và khiến Gemini vẽ
    một CẢNH PHIM thay vì bảng phân tích (đã gặp thật). Ở đây phong cách chỉ
    dùng để tả CÁCH VẼ NHÂN VẬT, còn tấm ảnh vẫn là tài liệu thiết kế: nền
    phẳng, ánh sáng đều, CÓ nhãn chữ tiếng Anh.
    """
    s = style_info(style)
    return (
        "CHARACTER MODEL SHEET (character model sheet / character design "
        "analysis sheet) — THIS IS A CHARACTER DESIGN DOCUMENT, NOT A FILM "
        "FRAME. One single sheet, uniform flat background, even neutral studio "
        "lighting, no environment, no depth of field. "
        f"The characters on the sheet are drawn in this style: {s['brief']}.\n\n"
        f"{prompt}"
        " || It MUST be a CHARACTER MODEL SHEET: document layout, multiple "
        "frames on one sheet, WITH short uppercase English text labels and "
        "callout lines, and a colour-palette strip. ABSOLUTELY DO NOT turn it "
        "into a film scene: no forest/room/landscape background, no bokeh or "
        "background blur, no dramatic cinematic lighting, no smoke or mist, no "
        "cropping down to a single figure. All figures must be THE SAME "
        "character, same proportions, same base line."
    )


# ── Prompt sinh ảnh (chỉnh theo ý muốn) ─────────────────────────
# {topic} = tên project / chủ đề nhạc.  Ảnh 0 là thumbnail.
# 41 ảnh là 41 biến thể của CÙNG một bố cục phim hoạt hình chill. Mỗi prompt
# tự chứa danh tính + bố cục, kể cả khi Gemini mở chat mới; không dựa vào trí
# nhớ hội thoại. Ảnh 0 sạch chữ: tiêu đề thật được chèn ở bước thumbnail/intro.
_CONTINUITY = (
    "This is the next shot of THE SAME chill animated film on the theme '{topic}'. "
    "Keep EXACTLY the same main character: a young person with short, slightly "
    "messy black hair, a loose sage-green hoodie, cream trousers, white shoes and "
    "beige headphones; keep the face, build, outfit and proportions unchanged in "
    "every shot. Keep THE SAME setting: a lakeside wooden cabin in a pine-forest "
    "valley, large window, wooden desk, warm yellow lamp, potted plant, a cup of "
    "tea and a calico cat. LOCKED COMPOSITION: eye-level wide shot, three-quarter "
    "view from the same spot inside the cabin; the character sits at the desk in "
    "the RIGHT third, the cat curled up on the window sill to the character's "
    "right, the tea cup on the left of the desk, the lamp behind the cup, the "
    "plant at the right edge; the lake, pine forest and mountains seen through "
    "the window fill the LEFT half. Do not move the camera, character, objects or "
    "architecture. Same sage-green, cream and amber palette, same soft sunset; "
    "only tiny variations of light and nature. Soft, cozy, lofi, serene, "
    "relaxing, meditation, 16:9 frame; peaceful resting pose and a very slow "
    "tempo. When a reference image is provided, keep continuity with it exactly; "
    "every locked trait above still applies even in a new chat. NO new "
    "characters, NO outfit changes, NO text, logo or watermark. "
)


def _scene(description: str) -> str:
    return _CONTINUITY + description


DEFAULT_PROMPTS: dict[str, str] = {
    IMG_MAIN_SHEET_KEY: _scene(
        "MAIN CHARACTER MODEL SHEET on a flat light-grey background: a full-body "
        "turnaround row FRONT VIEW / SIDE VIEW / BACK VIEW with the same height "
        "and base line, a FACE EXPRESSIONS row of four close-up frames, detail "
        "callouts with uppercase English labels for hair, hands, headphones and "
        "shoes, and a COLOR PALETTE strip of square swatches with hex codes at the "
        "bottom. This is a design document, NOT a film scene."),
    IMG_PET_SHEET_KEY: _scene(
        "COMPANION CREATURE SHEET (pet character sheet) for the companion CALICO "
        "CAT, on a flat light-grey background: turnaround FRONT VIEW / SIDE VIEW / "
        "BACK VIEW, a FACE EXPRESSIONS row of four frames, detail callouts with "
        "uppercase English labels for head, ears, paws and tail, a SIZE MAP box "
        "comparing the cat's height with the main character, and a COLOR PALETTE "
        "strip with hex codes. This is a design document, NOT a film scene."),
    IMG_THUMBNAIL_KEY: _scene(
        "Opening thumbnail background image, ABSOLUTELY NO TEXT. The character is "
        "clear but not too large on the RIGHT, the calico cat curled up beside "
        "them; a calm lake surface with a soft bright area on the LEFT half creates "
        "negative space for software to add the title later. A very faint amber "
        "rim light on the hair."),
}

# Từ khoá healing ghép vào tiêu đề thumbnail (dòng nhỏ). Mỗi lần chọn ngẫu
# nhiên vài từ. Có thể override bằng "thumbnail_keywords" trong overrides.
THUMBNAIL_KEYWORDS = [
    "Meditation", "Zen", "Ambient", "Focus", "Study", "Healing",
    "Relaxing", "Sleep", "Calm", "Stress Relief", "Deep Sleep",
    "Slow Living", "Lofi", "Peaceful Mind",
]

# Prompt mô tả chuyển động cho Flow (áp cho mọi clip; có thể để rỗng).
# Prompt clip 00 (intro): làm SỐNG ĐỘNG đúng ảnh bìa, không bịa cảnh mới.
INTRO_MOTION_PROMPT = (
    "Bring the THUMBNAIL reference image (the full scene, not the "
    "character sheets) to life as the opening shot: reproduce exactly its "
    "composition, framing, scenery, lighting, color palette and the positions "
    "and poses of the character and companion; use the two character sheets "
    "only to keep their identity consistent. Chill meditative film, "
    "very slow tempo, locked camera, no zoom, pan or orbit; only gentle "
    "in-place breathing and micro-motion, soft drifting mist, leaves, water "
    "ripples and light; seamless, no scene cut, no new character, no text"
)

FLOW_MOTION_PROMPT = (
    "chill meditative film, very slow tempo, locked camera; "
    "no zoom, pan or orbit; gentle natural in-place micro-motion of the character, "
    "soft ambient motion of leaves, drifting mist, water ripples and light only; "
    "preserve the exact same character identity, face, hairstyle, outfit, props, "
    "scenery, color palette and original composition; seamless slow loop, "
    "no scene cut, no sudden motion, no dialogue, no lip sync, no new character"
)

# Phủ định CỨNG gắn vào CUỐI mọi prompt clip gửi Flow/Veo (chặn khói/đầu-ngược/
# méo tay…). 2026-09-29: người dùng yêu cầu QUAY VỀ prompt đầy đủ này. Bản gọn
# (veo_safety: tả tích cực, bỏ hex) vẫn bật được bằng override
# "clip_prompt_mode": "safe" nếu Flow/Gemini từ chối "I can't generate that video".
MOTION_NEGATIVE = (
    " || STRICTLY FORBIDDEN (avoid absolutely): NO smoke, steam, visible breath, "
    "fire, embers or mist rising from the mouth, nose, flute or any instrument/"
    "object; NO head or neck turning backwards, 180-degree turns or unnatural "
    "jerks; NO distortion of the face, eyes, teeth, hands or fingers; NO extra or "
    "missing fingers/limbs; NO extra people, limbs or objects appearing; NO "
    "morphing, NO duplicated or split characters; NO outfit or prop changes "
    "mid-shot; NO scene cuts, NO flicker, NO jerky or sudden motion; NO lip "
    "movement as if talking; NO text, logo or watermark. "
    "negative prompt: no smoke, no steam, no vapor, no breath vapor, "
    "no fog from mouth, no smoke from flute or instrument, no fire, no head "
    "spinning, no reversed head, no 180-degree head turn, no face distortion, "
    "no warped or melting hands, no extra fingers, no extra limbs, no morphing, "
    "no duplicated character, no scene cut, no flicker, no text. Keep anatomy "
    "correct, physically plausible, and the character identity fully consistent."
)

# Mở đầu prompt cảnh: 2 ảnh nguyên liệu là BẢNG THIẾT KẾ nhân vật, không phải
# khung hình — chỉ lấy DANH TÍNH rồi dựng 1 cảnh phim liền mạch.
INGREDIENT_LOCK_FULL = (
    "The two ingredient images are CHARACTER MODEL SHEETS (design documents), "
    "NOT film frames: use them only to get the EXACT identity. Build ONE "
    "continuous film scene with the main character from ingredient 1 and the "
    "companion (pet or creature) from ingredient 2, keeping face, hair, outfit, colours, "
    "instrument and props unchanged. ABSOLUTELY NO side-by-side views, no text "
    "labels, no colour-swatch strip, no flat studio backdrop, no duplicated "
    "character."
)

# ── Chế độ dàn cảnh + intro (theo project) ──────────────────────
# cast_mode: "character_pet" (MẶC ĐỊNH — nhân vật chính + linh thú/thú nuôi) hoặc
# "scenery" (chỉ cảnh vật: không nhân vật, không con vật). intro_mode:
# "thumbnail" (MẶC ĐỊNH — intro 00:00 dựng từ chính ảnh thumbnail đã vẽ chữ AI)
# hoặc "font" (chữ font có sẵn đè lên clip 00 như trước).
CAST_MODES = ("character_pet", "scenery")
DEFAULT_CAST_MODE = "character_pet"
INTRO_MODES = ("thumbnail", "font")
DEFAULT_INTRO_MODE = "thumbnail"
# Giây đầu video giữ ẢNH thumbnail (Ken Burns chậm) trước khi tan vào clip 00.
INTRO_THUMBNAIL_SECONDS = 7.0


def normalize_cast_mode(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in CAST_MODES else DEFAULT_CAST_MODE


def normalize_intro_mode(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in INTRO_MODES else DEFAULT_INTRO_MODE


# Dấu nhận biết hồ sơ CHỈ CẢNH VẬT nằm ngay trong prompt (driver nhận ra lúc gửi
# mà không cần tra DB): mọi prompt ảnh/clip của chế độ này đều chứa chuỗi này.
SCENERY_MARK = "SCENERY-ONLY FILM"


def is_scenery_prompt(text: str | None) -> bool:
    return SCENERY_MARK in (text or "")


def images_cast_mode(img_dir) -> str:
    """cast_mode của BỘ ẢNH đã chuẩn bị (images/prompts.json) — nguồn sự thật cho
    driver clip + thumbnail (ảnh đã vẽ theo hồ sơ nào thì clip theo hồ sơ đó)."""
    try:
        data = json.loads((Path(img_dir) / "prompts.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return DEFAULT_CAST_MODE
    return normalize_cast_mode(data.get("cast_mode") if isinstance(data, dict) else None)


def thumbnail_ref_note(prompt: str | None) -> str:
    """Câu tham chiếu gắn vào prompt ảnh bìa 0 (đính kèm ảnh 1 + 2)."""
    if not thumbnail_refs():
        return ""
    return THUMBNAIL_REF_NOTE_SCENERY if is_scenery_prompt(prompt) else THUMBNAIL_REF_NOTE


def wrap_world_sheet_style(prompt: str, style: str | None = None) -> str:
    """Bọc ẢNH 1 & 2 của chế độ chỉ cảnh vật — BẢNG THIẾT KẾ BỐI CẢNH (giống
    wrap_sheet_style nhưng cho nơi chốn: không nhân vật, không con vật)."""
    s = style_info(style)
    return (
        f"{SCENERY_MARK} — ENVIRONMENT DESIGN SHEET (location concept-art sheet) "
        "— THIS IS A DESIGN DOCUMENT OF A PLACE, NOT A FILM FRAME. One single "
        "sheet on a uniform flat light background with several framed panels. "
        f"The scenery in the panels is drawn in this style: {s['brief']}.\n\n"
        f"{prompt}"
        " || It MUST be an ENVIRONMENT DESIGN SHEET: document layout, several "
        "framed panels of THE SAME place on one sheet, WITH short uppercase "
        "English text labels and callout lines, and a colour-palette strip. "
        "ABSOLUTELY NO people, NO human figures or silhouettes, NO characters, "
        "NO animals, NO creatures, NO pets in any panel. Every panel shows THE "
        "SAME location with the same landmarks, materials and palette."
    )


INGREDIENT_LOCK_SCENERY = (
    f"{SCENERY_MARK}: the two ingredient images are ENVIRONMENT DESIGN SHEETS "
    "(design documents of one place), NOT film frames: use them only to get the "
    "EXACT look of the place — landmarks, architecture, vegetation, water, "
    "materials, colours and light. Build ONE continuous film shot of that place "
    "with NO people, NO characters, NO animals, NO creatures. ABSOLUTELY NO "
    "side-by-side panels, no text labels, no colour-swatch strip, no flat "
    "studio backdrop."
)

# 2026-10-27: cảnh vật phải ĐỘNG như phim thường (nước chảy, mây trôi, gió, mưa/
# tuyết, máy quay chuyển động mượt) — KHÔNG khoá máy / siêu chậm như ảnh tĩnh.
INTRO_MOTION_PROMPT_SCENERY = (
    f"{SCENERY_MARK}. Bring the THUMBNAIL reference image (the full scene, not "
    "the design sheets) to life as the opening shot: start exactly on its "
    "composition, framing, scenery, lighting and color palette; use the two "
    "environment sheets only to keep the place consistent. Chill cinematic "
    "film, a LIVING scene, never a still image: the whole place is clearly in "
    "motion — flowing water and ripples, clouds moving across the sky, wind "
    "through leaves, branches and grass, drifting mist, falling petals, "
    "flickering lantern light, moving light and shadows — while the camera makes "
    "one smooth, natural push-in or gentle drift at a normal, unhurried speed; "
    "seamless, no scene cut, no people, no characters, no animals, no text"
)

FLOW_MOTION_PROMPT_SCENERY = (
    f"{SCENERY_MARK}, chill cinematic film, a LIVING scene, never a still image: "
    "clearly visible natural motion — flowing water and ripples, clouds moving "
    "across the sky, wind through trees, leaves and grass, drifting mist, falling "
    "petals, moving light and shadows; one smooth, natural cinematic camera move "
    "at a normal, unhurried speed; preserve the exact same place, landmarks and "
    "color palette; one continuous shot, no scene cut, no camera shake, no "
    "people, no characters, no animals"
)

# Câu chuyển động gắn vào MỌI prompt clip cảnh vật lúc gửi (cả hồ sơ cũ).
SCENERY_LIVING_MOTION = (
    "LIVING SCENE, never a still image: the place is clearly in motion (flowing "
    "water, moving clouds, wind through trees and grass, drifting mist, falling "
    "petals or leaves, moving light and shadows) and the camera makes one smooth, "
    "natural cinematic move at a normal, unhurried speed.")

MOTION_NEGATIVE_SCENERY = (
    " || STRICTLY FORBIDDEN (avoid absolutely): NO people, NO human figures, "
    "faces, hands or silhouettes, NO characters, NO animals, NO pets, NO "
    "creatures appearing at any moment; NO frozen frame or still-image look; NO "
    "morphing of buildings, trees or terrain; NO new objects popping in; NO scene "
    "cuts, NO flicker, NO jerky or sudden motion; NO camera shake; NO text, logo "
    "or watermark. negative prompt: no person, no people, no human, no character, "
    "no silhouette, no animal, no bird close-up, no pet, no creature, no frozen "
    "frame, no static image, no morphing, no scene cut, no flicker, no text. Keep "
    "the place physically plausible and fully consistent with the environment "
    "sheets."
)

# Hồ sơ cảnh vật viết trước 2026-10-27 còn câu khoá máy / siêu chậm → đổi sang
# máy quay chuyển động mượt lúc gửi (không cần viết lại prompt).
_STATIC_SCENERY_RULES = (
    (re.compile(r"\b(camera\s*:\s*)(?:a\s+)?(?:locked(?:[\s-]+off)?|static|fixed)"
                r"(?:\s+(?:camera|shot))?", re.I),
     lambda m: m.group(1) + "one smooth cinematic move"),
    (re.compile(r"\b(?:the\s+)?camera\s+(?:is\s+|stays\s+|remains\s+)?locked(?:[\s-]+off)?"
                r"(?:\s+or\s+moves\s+extremely\s+slowly)?", re.I),
     "the camera moves smoothly"),
    (re.compile(r"\b(?:a\s+)?(?:locked(?:[\s-]+off)?|static|fixed|still)\s+camera\b", re.I),
     "a smoothly moving camera"),
    # «A still frame of…», «Still view of…», «locked shot» (không đụng «no …»).
    (re.compile(r"(?<!no )(?<!never )\b(an?\s+)?(?:locked(?:[\s-]+off)?|static|still)\s+"
                r"(?:frame|view|shot)\b", re.I),
     lambda m: "A gliding shot" if m.group(0)[:1].isupper() else "a gliding shot"),
    (re.compile(r"[,;]?\s*no\s+zoom,?\s+pan\s+or\s+orbit\b", re.I), ""),
    (re.compile(r"\b(an?\s+)?(?:extremely|ultra|very)[\s-]+slow(ly)?\b", re.I),
     lambda m: "smoothly" if m.group(2) else ("a smooth" if m.group(1) else "smooth")),
    (re.compile(r"\bslow[\s-]+motion\b", re.I), "natural motion"),
    (re.compile(r"\bambient\s+motion\s+only\b", re.I), "living natural motion"),
    (re.compile(r"\bonly\s+(?:soft\s+|gentle\s+|subtle\s+)?ambient\s+motion\b", re.I),
     "clearly visible natural motion"),
)


def liven_scenery_motion(text: str) -> str:
    """Bỏ chỉ dẫn khoá máy / siêu chậm trong prompt clip cảnh vật cũ."""
    out = text or ""
    for rx, repl in _STATIC_SCENERY_RULES:
        out = rx.sub(repl, out)
    return re.sub(r"[ \t]{2,}", " ", out)


# Ghép video chỉ cảnh vật: phát ĐÚNG tốc độ thật (phong cách kênh thường làm chậm
# 0.7x — với cảnh vật làm clip trông như đứng hình).
SCENERY_PLAYBACK_SPEED = 1.0


# Chế độ 1 ảnh: prompt dự phòng khi hồ sơ thiếu chuyển động intro / loop.
INTRO_MOTION_PROMPT_SINGLE = (
    "Bring the reference image to life as the opening shot of a cinematic music "
    "video: start exactly on its composition, framing, lighting and colour "
    "palette; keep everything in it unchanged. Realistic natural ambient motion "
    "(light, air, water, leaves) and one very slow, smooth push-in. One "
    "continuous shot, no scene cut, no new people or objects, no text")
LOOP_MOTION_PROMPT_SINGLE = (
    "The SAME place as the reference image with the same framing, lighting and "
    "colour palette, held as a calm living background for a long music video: "
    "the camera stays still; only gentle natural ambient motion of light, air, "
    "water and leaves. Even, steady motion so the clip loops seamlessly. One "
    "continuous shot, no scene cut, no new people or objects, no text")


def intro_motion_prompt(cast_mode: str | None) -> str:
    if single_image():
        return INTRO_MOTION_PROMPT_SINGLE
    return INTRO_MOTION_PROMPT_SCENERY if cast_mode == "scenery" else INTRO_MOTION_PROMPT


def flow_motion_prompt(cast_mode: str | None) -> str:
    return FLOW_MOTION_PROMPT_SCENERY if cast_mode == "scenery" else FLOW_MOTION_PROMPT


def motion_negative(prompt: str | None) -> str:
    """Khối phủ định cuối prompt clip — theo hồ sơ (dấu SCENERY_MARK trong prompt)."""
    return MOTION_NEGATIVE_SCENERY if is_scenery_prompt(prompt) else MOTION_NEGATIVE


def image_roles(cast_mode: str | None) -> dict:
    if single_image():
        return IMAGE_ROLES_COVER_SCENERY if cast_mode == "scenery" else IMAGE_ROLES_COVER
    return IMAGE_ROLES_SCENERY if cast_mode == "scenery" else IMAGE_ROLES

# "full" (mặc định) = prompt đầy đủ ở trên; "safe" = bản gọn veo_safety.
CLIP_PROMPT_MODES = ("full", "safe")
DEFAULT_CLIP_PROMPT_MODE = "full"


# ── Cấu hình trình duyệt ────────────────────────────────────────
@dataclass
class BrowserConfig:
    headless:        bool = False          # phải False để tự đăng nhập lần đầu
    slow_mo_ms:      int  = 120            # làm chậm thao tác cho ổn định
    nav_timeout_ms:  int  = 60_000
    action_timeout_ms: int = 30_000
    # Thời gian tối đa chờ 1 ảnh / 1 clip render xong (giây)
    # Gemini Pro · Mở rộng: đo live 2026-09-30 ~200s/ảnh (180s cũ cắt ngang ảnh
    # đang vẽ). Quá image_wait_sec mà Gemini VẪN đang tạo → chờ tiếp tới
    # image_wait_max_sec thay vì bỏ ảnh (xem gemini_driver._wait_new_image).
    image_wait_sec:  int  = 300
    image_wait_max_sec: int = 720
    clip_wait_sec:   int  = 900            # Veo có thể lâu vài phút
    # Gemini Video (Veo trong app Gemini): đo thật 2026-09-29 ~90s/clip.
    gemini_video_wait_sec: int = 600
    # Cứ tạo bao nhiêu ảnh thì mở CHAT MỚI để cắt ngữ cảnh dài (0 = không cắt).
    # Hội thoại quá dài (vd ảnh ~37/41 cùng 1 chat) khiến Gemini trả chữ/từ chối
    # → time-out. Mỗi prompt tự chứa danh tính nhân vật nên cắt chat vẫn đồng bộ.
    image_new_chat_every: int = 8
    # None = dùng Chromium bundled của Playwright (ổn định nhất, chạy
    # `playwright install chromium` 1 lần). Đặt "chrome" nếu muốn dùng Google
    # Chrome đã cài; "msedge" cho Edge.
    chrome_channel:  str | None = None
    # Đường dẫn TUYỆT ĐỐI tới 1 trình duyệt nhân Chromium khác (vd Cốc Cốc).
    # Playwright không có "channel" cho Cốc Cốc nên phải chỉ thẳng file .exe.
    # None = tự dò Cốc Cốc trong COCCOC_CANDIDATES (xem resolve_browser_exe).
    browser_executable: str | None = None


# Cốc Cốc cài mặc định ở Program Files; bản portable/per-user nằm trong
# LocalAppData. Google Flow CHẶN Chromium bundled của Playwright («hoạt động
# bất thường») nhưng chạy bình thường trên Cốc Cốc → ưu tiên Cốc Cốc nếu có.
COCCOC_CANDIDATES = (
    r"C:\Program Files\CocCoc\Browser\Application\browser.exe",
    r"C:\Program Files (x86)\CocCoc\Browser\Application\browser.exe",
    str(Path.home() / r"AppData\Local\CocCoc\Browser\Application\browser.exe"),
)


# Trình duyệt RIÊNG cho từng site (khoá = tên site truyền vào BrowserSession).
# "" = ép Chromium bundled của Playwright.
#   • gemini: Cốc Cốc tự đóng cửa sổ ngay bước tải ảnh (Playwright báo
#     TargetClosedError; tiến trình browser.exe vẫn sống) → dùng Chromium bundled.
#   • flow  : PHẢI giữ Cốc Cốc, Chromium bundled bị Flow chặn "hoạt động bất thường".
SITE_BROWSER: dict[str, str] = {
    "gemini": "",
    # Cốc Cốc có thể tự đóng ngay sau hộp thoại upload của Suno, làm mất cả
    # trang/context Playwright. Dùng Chrome ổn định hơn; profile Suno vẫn là
    # profile persistent riêng của tool nên không đụng profile Chrome cá nhân.
    "suno": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}

# macOS (2026-11-10): Chrome nằm trong /Applications. Không có Chrome thì tự
# dùng Chromium bundled của Playwright (resolve_browser_exe trả None).
if sys.platform == "darwin":
    SITE_BROWSER["suno"] = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def resolve_browser_exe(site: str | None = None) -> str | None:
    """Trả về .exe trình duyệt sẽ dùng, hoặc None = Chromium bundled.

    Thứ tự: override `browser_executable.<site>` (hoặc SITE_BROWSER[site]) →
    override `browser_executable` trong video_overrides.json →
    BROWSER.browser_executable → tự dò Cốc Cốc. Đặt override = "" (chuỗi rỗng)
    để ÉP dùng Chromium bundled, bỏ qua bước tự dò."""
    overrides = load_overrides()
    if site:
        per_site = overrides.get("browser_executable_by_site") or {}
        ov = per_site.get(site, SITE_BROWSER.get(site))
        if ov is not None:
            ov = str(ov).strip()
            return ov if ov and Path(ov).exists() else None
    ov = overrides.get("browser_executable")
    if ov is not None:
        ov = str(ov).strip()
        return ov if ov and Path(ov).exists() else None
    if BROWSER.browser_executable:
        p = str(BROWSER.browser_executable).strip()
        if Path(p).exists():
            return p
    for cand in COCCOC_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


# Thư mục profile RIÊNG theo site (tên thư mục nằm cạnh project root).
# Dùng khi profile cũ đã bị Google gắn cờ «hoạt động bất thường»: profile SẠCH
# (đăng nhập tay lại 1 lần) thường gỡ được, mà không cần stealth/anti-detect.
# Override qua "profile_dir_by_site" trong video_overrides.json.
#   • chatgpt: profile RIÊNG (2026-10-23, thumbnail ảnh + chữ) — người dùng tự
#     đăng nhập 1 lần qua nút «Mở ChatGPT để đăng nhập»; không đụng profile
#     Gemini/Flow (có chat cá nhân).
SITE_PROFILE: dict[str, str] = {"chatgpt": ".browser_profile_chatgpt",
                               # nhaccuatui (tab ① Tải nhạc gốc) — người dùng tự
                               # đăng nhập 1 lần qua «Mở nhaccuatui để đăng nhập».
                               "nct": ".browser_profile_nct"}


def site_profile_dir(site: str | None) -> Path | None:
    """Thư mục profile cho site, hoặc None = dùng PROFILE_DIR chung."""
    if not site:
        return None
    per_site = load_overrides().get("profile_dir_by_site") or {}
    name = per_site.get(site, SITE_PROFILE.get(site))
    name = str(name).strip() if name else ""
    if not name:
        return None
    p = Path(name)
    return p if p.is_absolute() else (_ROOT / name)


# ── URL ─────────────────────────────────────────────────────────
CHATGPT_URL = "https://chatgpt.com/"
GEMINI_URL  = "https://gemini.google.com/app"
FLOW_URL    = "https://flow.google.com/"
YOUTUBE_STUDIO_URL = "https://studio.youtube.com/"

# Model dùng để tạo ảnh trên Gemini web (chọn trong dropdown model).
# "tư duy mở rộng" = bản thinking/Pro.
GEMINI_IMAGE_MODEL = "3.1 Pro"

# ── Máy tạo CLIP ────────────────────────────────────────────────
# 2026-09-29: thử tạo clip TRỰC TIẾP trên Gemini (chế độ Video) — chạy được nhưng
# Gemini khoá «Tạo video» sau ~10 lượt/ngày ⇒ người dùng chọn QUAY VỀ Flow làm
# mặc định. Gemini vẫn bật được: video_overrides.json "clip_engine": "gemini".
# 2026-10-05: thêm "muse" (Muse.ai qua profile GPM — muse_video.py), thông số nằm
# trong prompt; ảnh tham chiếu/phân tích nhân vật vẫn do Gemini làm.
# 2026-10-05: người dùng chọn Muse.ai làm MẶC ĐỊNH (Flow/Gemini vẫn chọn được
# trong dropdown «Máy tạo clip» hoặc video_overrides.json "clip_engine").
CLIP_ENGINES = ("flow", "gemini", "muse")
CLIP_ENGINE_LABELS = {"flow": "Flow/Veo", "gemini": "Gemini Video", "muse": "Muse.ai"}
DEFAULT_CLIP_ENGINE = "muse"
# Ghi RÕ trong mọi prompt clip gửi Gemini (yêu cầu người dùng): Full HD + 8s.
# Đặt ĐẦU prompt như yêu cầu xuất file: ghi ở cuối như một câu mô tả thì
# Gemini từng VẼ huy hiệu "Full HD 1080p" lên hình (lô thử 2026-09-29, clip 05).
GEMINI_VIDEO_SPEC = ("Create an 8-second video (exactly 8 seconds long) in Full HD 1080p "
                     "resolution (1920x1080), 16:9 landscape. These are file output "
                     "settings only, not part of the picture: keep the frame clean with "
                     "no on-screen text or logos. Scene:")
# Gemini hiện TRẢ file gốc 1280x720 (+ tiếng AAC) dù prompt ghi 1080p (đo thật
# 2026-09-29) → hậu kỳ: BỎ HẲN luồng âm thanh + nâng lên đúng 1920x1080.
GEMINI_VIDEO_OUT_SIZE = (1920, 1080)

# Độ phân giải khi TẢI clip từ Flow (menu Tải xuống có menu con):
#   "720p" = kích thước gốc (tải NGAY, KHÔNG upscale); "1080p"/"4K" = upscale
#   (chờ "Upscaling your video", 4K tốn tín dụng). Omni 1.1 Flash render gốc
#   720p và chỉ có 360p/720p → dùng "720p" để tải trực tiếp, tránh upscale.
#   Override qua "flow_download_resolution" trong video_overrides.json.
FLOW_DOWNLOAD_RESOLUTION = "720p"

# ĐƯỜNG TẢI CHÍNH: lấy thẳng file media của tile qua URL /asb/<id>=<biến thể>,
# KHÔNG dùng cơ chế download của trình duyệt (Cốc Cốc crash 0xC0000005 ngay khi
# Playwright chặn download — lần tải thứ 2 trở đi luôn chết cửa sổ).
# Biến thể lấy từ <video src> khi hover là "mm,22,15" (h264 Baseline ~1.8 Mbps);
# đo thực tế "mm,37,15" cho h264 High ~3.2 Mbps, 1280x720 — CAO HƠN cả bản 720p
# tải qua menu (2.75 Mbps). Override qua "flow_media_variant".
FLOW_MEDIA_VARIANT = "mm,37,15"

# NHỊP THAO TÁC "GIỐNG NGƯỜI" (giây, nghỉ NGẪU NHIÊN trong khoảng min–max).
# Flow gắn cờ «hoạt động bất thường» khi thao tác quá nhanh/quá đều (2026-09-25:
# chặn lại đúng sau 5 clip liên tiếp). Người dùng yêu cầu mọi bước đều nghỉ như
# người thật — RIÊNG ô prompt vẫn dán một lần (người cũng copy-paste).
# Override qua "flow_human_pace" trong video_overrides.json.
# 2026-09-26: profile đang chạy tốt bị chặn NGAY clip đầu dù tài khoản sạch (user
# tự tạo tay trên Cốc Cốc vẫn được) ⇒ nới chậm MỌI bước, kể cả upload/dán prompt/tải.
FLOW_HUMAN_PACE: dict[str, tuple[float, float]] = {
    "ui": (1.5, 4.0),               # bấm menu / chọn option
    "open": (8.0, 20.0),            # vào dashboard, nhìn quanh rồi mới tạo dự án
    "step": (6.0, 14.0),            # xong một việc lớn (thêm nguyên liệu…)
    "upload": (8.0, 18.0),          # giữa hai lần upload ảnh (upload TỪNG ảnh)
    "before_prompt": (4.0, 10.0),   # nhìn ô soạn rồi mới dán prompt
    "before_generate": (15.0, 30.0),  # đọc lại prompt rồi mới bấm Tạo
    "after_render": (10.0, 25.0),   # clip xong, xem thử rồi mới đi tải
    "before_download": (4.0, 10.0),  # rê chuột tới tile rồi mới tải
    "between_clips": (30.0, 30.0),   # yêu cầu vận hành: delay tối đa 30 giây
}
FLOW_MAX_DELAY_SEC = 30.0

# HẠN MỨC MỖI PHIÊN. Nghỉ giống người CHƯA đủ: đo thực tế 2026-09-25, cùng một
# phiên/dự án Flow bị gắn cờ «hoạt động bất thường» sau 5 clip (13:08) rồi 6 clip
# (14:02, đã có nhịp nghỉ). Cờ TỰ HẾT sau ~20–30 phút (13:08 chặn → 13:39 chạy
# lại bình thường). ⇒ Cách chạy trọn 40 clip: mỗi phiên chỉ tạo
# FLOW_CLIPS_PER_SESSION clip rồi ĐÓNG trình duyệt, nghỉ FLOW_SESSION_COOLDOWN
# giây (ngẫu nhiên) và mở phiên mới (dự án Flow mới) làm nốt phần thiếu.
# Nếu vẫn dính thẻ chặn → nghỉ lâu hơn (FLOW_BLOCK_COOLDOWN) rồi thử lại, tối đa
# FLOW_MAX_BLOCK_RETRIES lần. Override qua "flow_clips_per_session",
# "flow_session_cooldown", "flow_block_cooldown", "flow_max_sessions".
FLOW_CLIPS_PER_SESSION = 4
FLOW_SESSION_COOLDOWN: tuple[float, float] = (30.0, 30.0)
FLOW_BLOCK_COOLDOWN: tuple[float, float] = (30.0, 30.0)
FLOW_MAX_BLOCK_RETRIES = 3
FLOW_MAX_SESSIONS = 40


# ── SELECTOR (best-effort — SỬA Ở ĐÂY khi automation lỗi) ────────
# Mỗi giá trị là danh sách selector thử theo thứ tự (fallback dần).
CHATGPT_SELECTORS: dict[str, list[str]] = {
    "prompt_box": [
        "#prompt-textarea",
        "div[contenteditable='true']",
        "textarea[data-testid='prompt-textarea']",
    ],
    "send_button": [
        "button[data-testid='send-button']",
        "#composer-submit-button",
        "button[aria-label*='Send']",
        "button[aria-label*='Gửi']",
    ],
    # Đang trả lời / đang vẽ (nút dừng thay chỗ nút gửi)
    "stop_button": [
        "button[data-testid='stop-button']",
        "button[aria-label*='Stop']",
        "button[aria-label*='Dừng']",
    ],
    # Ô đính kèm (ẩn) trong khung soạn — ưu tiên ô nhận ảnh
    "file_input": [
        "input[type='file'][accept*='image']",
        "form input[type='file']",
        "input[type='file']",
    ],
    # CHƯA đăng nhập: nút Log in / Đăng nhập (tool chỉ NHẬN DIỆN, không bấm)
    "login_button": [
        "button[data-testid='login-button']",
        "a[data-testid='login-button']",
        "button:has-text('Log in')",
        "button:has-text('Đăng nhập')",
    ],
    # ĐÃ đăng nhập: nút hồ sơ tài khoản
    "profile_button": [
        "[data-testid='accounts-profile-button']",
        "button[data-testid='profile-button']",
        "[data-testid='user-menu']",
        "button[aria-label*='profile' i]",
        "button[aria-label*='hồ sơ' i]",
    ],
    # Ảnh sinh ra trong luồng hội thoại
    "generated_image": [
        "img[alt*='Generated']",
        "div[data-testid*='image'] img",
        "img[src*='oaiusercontent']",
    ],
    # Nút tải ảnh (nếu có), hoặc ta lấy src trực tiếp
    "image_download": [
        "a[download]",
        "button[aria-label*='Download']",
        "button[aria-label*='Tải']",
    ],
}

GEMINI_SELECTORS: dict[str, list[str]] = {
    # Dropdown chọn model + option 3.1 Pro (tư duy mở rộng)
    "model_selector": [
        "[data-test-id='bard-mode-menu-button']",
        "button[aria-label*='chọn chế độ']",
        "button[aria-label*='mode picker' i]",
        "button[aria-label*='model']",
    ],
    # Mục menu chọn ĐÚNG BẰNG tên (không dùng *:has-text — dò live 2026-10-13: nó
    # khớp cả mô tả «…suy luận song song mức cao nhất» của Deep Think).
    "model_option_pro": [
        "gem-menu-item:has(:text-is('3.1 Pro'))",
        "[role='menuitem']:has(:text-is('3.1 Pro'))",
        "[role='menuitemradio']:has-text('3.1 Pro')",
        "[role='menuitem']:has-text('3.1 Pro')",
    ],
    # Bước 2: mức suy luận. Dò live 2026-10-13: Gemini BỎ mục «Tư duy mở rộng»,
    # thay bằng mức Thấp / Trung bình / Cao (+ Deep Think) — «Cao» = tư duy mở rộng
    # cũ (nhãn chế độ thành «Pro Cao»). Không thấy mục → driver đóng menu (Escape):
    # menu bỏ ngỏ CHE ô nhập (lỗi «không tìm thấy ô nhập prompt» #35).
    "model_option_thinking": [
        "gem-menu-item:has(:text-is('Cao'))",
        "[role='menuitem']:has(:text-is('Cao'))",
        "gem-menu-item:has(:text-is('High'))",
        "[role='menuitem']:has(:text-is('High'))",
        "[role='menuitemradio']:has-text('Tư duy mở rộng')",
        "[role='menuitem']:has-text('Tư duy mở rộng')",
        "[role='menuitem']:has-text('Extended thinking')",
    ],
    # Ô nhập prompt
    "prompt_box": [
        "div.ql-editor[contenteditable='true']",
        "rich-textarea div[contenteditable='true']",
        "div[contenteditable='true']",
        "textarea",
    ],
    "send_button": [
        "button[aria-label*='Send']",
        "button[aria-label*='Gửi']",
        "button.send-button",
        "button[mattooltip*='Send']",
    ],
    # Ảnh sinh ra trong câu trả lời
    "generated_image": [
        "img[src*='googleusercontent']",
        "generated-image img",
        "img[alt*='image']",
        "message-content img",
    ],
    "image_download": [
        "button[aria-label*='Download']",
        "button[aria-label*='Tải']",
        "a[download]",
    ],
    # Đính kèm ảnh tham chiếu (thumbnail dựa trên 2 bảng nhân vật). Dò live
    # 2026-09-26: nút '+' aria «Nội dung tải lên và công cụ» → mục «Tải tệp lên»
    # (data-test-id local-images-files-uploader-button) mở file chooser.
    "upload_menu": [
        "button[aria-label='Nội dung tải lên và công cụ']",
        "button[aria-label*='tải lên và công cụ']",
        "button[aria-label*='upload' i]",
        "button[aria-label*='Tải tệp']",
        "button[aria-label*='Add files']",
    ],
    "upload_files_item": [
        "[data-test-id='local-images-files-uploader-button']",
        "[role='menuitem']:has-text('Tải tệp lên')",
        "button:has-text('Tải tệp lên')",
        "[role='menuitem']:has-text('Upload files')",
        "button:has-text('Upload files')",
    ],
    # Chip ảnh đã đính kèm trong ô soạn (để chờ upload xong).
    "attachment_preview": [
        "uploader-file-preview img",
        "gem-media-attachment img",
    ],
    # Công cụ «Tạo hình ảnh» (dò live 2026-10-06): mục menuitemcheckbox trong
    # menu «Nội dung tải lên và công cụ». KHÔNG bật → Gemini trả chữ «my image
    # generation tool has been disabled for this request». Không giữ qua lần
    # tải trang/chat mới → phải bật lại mỗi lần.
    "image_tool_item": [
        "[role='menuitemcheckbox']:has-text('Tạo hình ảnh')",
        "[role='menuitemcheckbox']:has-text('Create image')",
        "[role='menuitemcheckbox']:has-text('Images')",
    ],
    # Chip hiện trong ô soạn khi công cụ đã bật.
    "image_tool_chip": [
        "button[aria-label^='Bỏ chọn Hình ảnh']",
        "button[aria-label*='Deselect Image' i]",
    ],
    # Nút tỷ lệ (chỉ hiện khi đã bật «Tạo hình ảnh»), mục = menuitemradio aria '16:9'.
    "image_ratio_button": [
        "button:has-text('Tỷ lệ khung hình')",
        "button:has-text('Aspect ratio')",
    ],
    # Nút "Trò chuyện mới" — cắt ngữ cảnh dài (hội thoại quá dài khiến Gemini
    # hay trả CHỮ/từ chối). Không bấm được → driver reload thẳng GEMINI_URL.
    "new_chat": [
        "button[aria-label*='Cuộc trò chuyện mới']",
        "a[aria-label*='Cuộc trò chuyện mới']",
        "button[aria-label*='Trò chuyện mới']",
        "a[aria-label*='Trò chuyện mới']",
        "button[aria-label*='New chat']",
        "a[aria-label*='New chat']",
        "button[aria-label*='New conversation']",
        "[data-test-id='new-chat-button']",
    ],
    # Nút "⋯" dưới ảnh — aria-label THẬT: "Hiện thêm tuỳ chọn".
    "more_button": [
        "button[aria-label='Hiện thêm tuỳ chọn']",
        "button[aria-label*='thêm tuỳ chọn']",
        "button[aria-label*='tuỳ chọn']",
        "button[aria-label*='tùy chọn']",
        "button[aria-label*='More options']",
        "button[aria-label*='Show more']",
    ],
    # Mục menu "Tải hình ảnh xuống" (thẻ <gem-menu-item role=menuitem>).
    "download_menu_item": [
        "gem-menu-item:has-text('Tải hình ảnh xuống')",
        "[role='menuitem']:has-text('Tải hình ảnh xuống')",
        "*:has-text('Tải hình ảnh xuống')",
        "gem-menu-item:has-text('Download')",
        "[role='menuitem']:has-text('Download')",
    ],
}

# Selector Flow đã kiểm chứng trực tiếp; ưu tiên tiếng Anh và giữ fallback tiếng Việt.
# Gemini chế độ VIDEO — dò live 2026-09-29 (giao diện tiếng Việt, tài khoản Ultra).
GEMINI_VIDEO_SELECTORS: dict[str, list[str]] = {
    # '+' → menu công cụ → «Tạo video» (menuitemcheckbox). Bấm xong Gemini mở
    # trang thư viện Video kèm lớp phủ cdk-overlay-backdrop → Escape để đóng.
    "tools_menu": [
        "button[aria-label='Nội dung tải lên và công cụ']",
        "button[aria-label*='tải lên và công cụ']",
    ],
    "video_tool": [
        "[role='menuitemcheckbox']:has-text('Tạo video')",
        "[role='menuitemcheckbox']:has-text('Create video')",
        "[role='menuitem']:has-text('Tạo video')",
    ],
    # Chip "Video" đang bật trong ô soạn.
    "video_chip": [
        "button[aria-label='Bỏ chọn Video']",
        "button[aria-label*='Deselect Video']",
    ],
    # Nút chế độ: aria «Mở công cụ chọn chế độ, hiện tại là Pro Mở rộng».
    "mode_button": ["[data-test-id='bard-mode-menu-button']"],
    "mode_pro": [
        "gem-menu-item:has(:text-is('3.1 Pro'))",
        "gem-menu-item:has-text('3.1 Pro')",
        "[role='menuitem']:has-text('3.1 Pro')",
    ],
    "mode_thinking": [
        "gem-menu-item:has(:text-is('Cao'))",           # 2026-10-13: thay «Tư duy mở rộng»
        "gem-menu-item:has(:text-is('High'))",
        "gem-menu-item:has-text('Tư duy mở rộng')",
        "[role='menuitem']:has-text('Tư duy mở rộng')",
        "gem-menu-item:has-text('Extended thinking')",
    ],
    # Nút tải tệp NGAY dưới ô soạn ở chế độ Video (mở file chooser).
    "upload_button": [
        "button[aria-label='Tải tệp lên']",
        "button[aria-label*='Upload file']",
    ],
    "attachment_chip": [
        "button[aria-label='đóng tệp đính kèm']",
        "button[aria-label*='Remove file']",
    ],
    "aspect_button": [
        "button[aria-label^='Tỷ lệ khung hình']",
        "button[aria-label^='Aspect ratio']",
    ],
    "aspect_landscape": [
        "[role='menuitem']:has-text('16:9')",
        "[role='menuitemradio']:has-text('16:9')",
        "gem-menu-item:has-text('Ngang')",
        "[role='menuitem']:has-text('Ngang')",
    ],
    "send_button": [
        "button[aria-label='Gửi tin nhắn']",
        "button[aria-label*='Send message']",
    ],
    # Video kết quả: <generated-video><video-player><video src=…usercontent…>
    "result_video": ["generated-video video"],
    "download_button": [
        "button[aria-label='Tải video xuống']",
        "button[aria-label*='Download video']",
    ],
}

FLOW_SELECTORS: dict[str, list[str]] = {
    # Trang giới thiệu có thể xuất hiện trước dashboard.
    "flow_entry": [
        "button:has-text('Create with Google Flow')",
        "a:has-text('Create with Google Flow')",
    ],
    # Tạo dự án mới từ dashboard
    "new_project": [
        "button:has-text('New project')",
        "button:has-text('Dự án mới')",
        "div[role='button']:has-text('Dự án mới')",
        "a:has-text('Dự án mới')",
    ],
    # Mở bảng cài đặt (chip "Video · 720p · 8 giây · x1")
    "settings_trigger": [
        "button[aria-label='Settings trigger']",
        "button[aria-label='Điều kiện kích hoạt cài đặt']",
        # Flow đôi khi render chip Settings KHÔNG phải thẻ <button> (div/role),
        # nên khớp mọi thẻ theo aria-label + qua role engine (khớp accessible
        # name, đã chuẩn hoá khoảng trắng) cho bền với thay đổi UI.
        "[aria-label='Settings trigger']",
        "[aria-label='Điều kiện kích hoạt cài đặt']",
        "role=button[name='Settings trigger']",
        "role=button[name='Điều kiện kích hoạt cài đặt']",
        # Fallback cuối: chip cạnh nút 'Start generation' chứa 'crop_16_9'/tỉ lệ.
        "[role='button']:has-text('crop_16_9')",
    ],
    # Loại đầu ra Ảnh↔Video. Dự án Flow MỚI mặc định ra ẢNH (🍌 Nano Banana),
    # khi đó KHÔNG có radio 'Thành phần' lẫn model Veo/Omni → phải bấm Video
    # trước. Icon material 'videocam' dính liền nhãn nên text là 'videocamVideo'.
    "output_video": [
        "[role='radio']:has-text('videocam')",
        "[role='tab']:has-text('videocam')",
        "[role='button']:has-text('videocam')",
        "[role='radio']:has-text('Video')",
    ],
    # Chế độ Khung hình (start+end frame)
    "mode_frames": [
        "[role='radio']:has-text('Frames')",
        "[role='radio']:has-text('Khung hình')",
    ],
    # Chế độ Thành phần (ingredients: mỗi clip 1 ảnh nguyên liệu)
    "mode_ingredients": [
        "[role='radio']:has-text('Ingredients')",
        "[role='radio']:has-text('Thành phần')",
    ],
    # Nút "+" thêm ảnh nguyên liệu (mở dialog chọn ảnh) trong mode Thành phần
    "add_ingredient": [
        "button[aria-label='Add ingredients to the prompt box']",
        "button[aria-label='Thêm thành phần vào ô nhập câu lệnh']",
    ],
    # Nút xoá chip nguyên liệu đã thêm (icon 'cancel', aria 'Thành phần')
    "remove_ingredient": [
        "button[aria-label='Ingredient']",
        "button[aria-label='Thành phần']",
    ],
    "clear_prompt": [
        "button[aria-label='Clear prompt']",
        "button[aria-label='Xóa câu lệnh']",
        "button[aria-label='Xoá câu lệnh']",
    ],
    # Tỉ lệ 16:9
    "aspect_option_169": [
        "[role='radio']:has-text('16:9')",
    ],
    # Dropdown model. Option model + radio thời lượng được dựng ĐỘNG trong
    # flow_driver._configure_settings từ config.PARAMS.flow_model /
    # clip_seconds (EN+VN), nên không hardcode ở đây nữa.
    "model_selector": [
        "button[aria-label='Select model family']",
        "button[aria-label='Chọn nhóm mô hình']",
    ],
    # Số clip mỗi lượt tạo. Dự án MỚI mặc định x2 (tốn gấp đôi credit) → phải
    # ép x1. Flow không render nhất quán thẻ role='radio' cho nhóm này nên
    # khớp thêm button/role=button/tab với text ĐÚNG BẰNG 'x1'.
    "outputs_x1": [
        "[role='radio']:text-is('x1')",
        "[role='button']:text-is('x1')",
        "button:text-is('x1')",
        "[role='tab']:text-is('x1')",
        "[role='radio']:has-text('x1')",
    ],
    # Upload ảnh vào project: nút "+" trên cùng → menu "Tải lên" (mở file chooser)
    "add_media_menu": [
        "button[aria-label='Add media menu']",
        "button[aria-label='Trình đơn thêm nội dung nghe nhìn']",
    ],
    "upload_menu_item": [
        "[role='menuitem']:has-text('Upload')",
        "[role='menuitem']:has-text('Tải lên')",
    ],
    # Ô Bắt đầu / Kết thúc (mở dialog "Chọn một hình ảnh khung")
    "start_frame_button": [
        "button:text-is('Bắt đầu')",
    ],
    "end_frame_button": [
        "button:text-is('Kết thúc')",
    ],
    # Dialog "Chọn một hình ảnh khung": ô tìm (dự phòng). Ảnh chọn theo tên
    # file "{i}.png" dựng động trong driver ([role='option']:has-text('0.png')).
    "frame_search": [
        "input[placeholder='Search assets']",
        "input[placeholder='Tìm kiếm thành phần']",
    ],
    # Nút xác nhận sau khi chọn ảnh trong dialog khung.
    "frame_confirm": [
        "button:has-text('Add to prompt')",
        "button:has-text('Thêm vào câu lệnh')",
    ],
    # Ô prompt mô tả chuyển động (contenteditable, có placeholder overlay).
    "prompt_box": [
        "div[contenteditable='true']",
        "textarea",
    ],
    # Nút tạo
    "generate_button": [
        "button[aria-label='Start generation']",
        "button[aria-label='Bắt đầu tạo']",
    ],
    # Video kết quả trong lưới
    "result_video": [
        "flow-grid-tile-container:has(flow-video-tile)",
        "video",
    ],
    # Nút ⋯ trên clip đã render (khác Gemini): aria-label 'Tuỳ chọn khác'
    "more_button": [
        "button[aria-label='More options']",
        "button[aria-label='Tuỳ chọn khác']",
    ],
    # Mục 'Tải xuống' trong menu clip → tải trực tiếp (expect_download)
    "video_download": [
        "[role='menuitem']:has-text('Download')",
        "[role='menuitem']:has-text('Tải xuống')",
    ],
}


# Selector YouTube Studio (upload → điền metadata → lưu Draft). RẤT dễ đổi và
# phụ thuộc ngôn ngữ tài khoản (EN/VN) → để cả hai. SỬA Ở ĐÂY khi automation lỗi;
# có thể override qua video_overrides.json ("selectors.youtube.<key>").
YOUTUBE_SELECTORS: dict[str, list[str]] = {
    # Nút "Tạo" (Create) góc trên phải Studio.
    "create_button": [
        "ytcp-button#create-icon",
        "button[aria-label='Create']",
        "button[aria-label='Tạo']",
        "ytcp-button[aria-label='Create']",
        "#create-icon",
    ],
    # Mục "Tải video lên" trong menu Create.
    "upload_menu_item": [
        "tp-yt-paper-item#text-item-0",
        "tp-yt-paper-item:has-text('Upload videos')",
        "tp-yt-paper-item:has-text('Tải video lên')",
        "[role='menuitem']:has-text('Upload video')",
        "[role='menuitem']:has-text('Tải video lên')",
    ],
    # Input file (ẩn) để set video. Dialog upload dùng input[type=file].
    "file_input": [
        "input[type='file']",
    ],
    # ── Hình thu nhỏ (thumbnail) tuỳ chỉnh ──
    # Input file RIÊNG của khung thumbnail (accept ảnh) — KHÔNG được trùng input
    # video (accept video/*), nếu không sẽ set nhầm ảnh vào chỗ video.
    "thumbnail_input": [
        "ytcp-thumbnail-editor input[type='file']",
        "ytcp-thumbnails-compact-editor input[type='file']",
        "input[type='file'][accept*='image']",
        "#file-loader",
    ],
    # Nút "Tải tệp lên / Upload file" mở hộp chọn hình thu nhỏ (khi input ẩn chỉ
    # được dựng sau khi bấm) — dùng kèm expect_file_chooser.
    "thumbnail_button": [
        "ytcp-thumbnail-uploader #select-button",
        "ytcp-thumbnail-editor #still-picker button",
        "ytcp-button:has-text('Upload file')",
        "ytcp-button:has-text('Tải tệp lên')",
        "button:has-text('Upload file')",
        "button:has-text('Tải tệp lên')",
        "button[aria-label*='thumbnail']",
        "button[aria-label*='hình thu nhỏ']",
    ],
    # Dấu hiệu ảnh thu nhỏ ĐÃ được nạp (để xác nhận, không đoán mò).
    "thumbnail_selected": [
        "ytcp-thumbnail-editor img[src^='blob:']",
        "ytcp-thumbnail-editor img[src^='data:']",
        "ytcp-thumbnails-compact-editor img[src^='blob:']",
        "#custom-thumbnail-image",
        "ytcp-still-cell[selected]",
    ],
    # Ô tiêu đề (contenteditable) trong dialog Details.
    "title_box": [
        "ytcp-social-suggestions-textbox[label*='title'] #textbox",
        "ytcp-mention-textbox[label*='title'] #textbox",
        "#title-textarea #textbox",
        "div#textbox[aria-label*='title']",
        "div#textbox[aria-label*='tiêu đề']",
        "#textbox[contenteditable='true']",
    ],
    # Ô mô tả (contenteditable).
    "description_box": [
        "ytcp-social-suggestions-textbox[label*='description'] #textbox",
        "ytcp-mention-textbox[label*='description'] #textbox",
        "#description-textarea #textbox",
        "div#textbox[aria-label*='description']",
        "div#textbox[aria-label*='mô tả']",
    ],
    # Nút "Hiện thêm" (Show more) mở phần cấu hình mở rộng.
    "show_more": [
        "ytcp-button#toggle-button",
        "#toggle-button",
        "button:has-text('Show more')",
        "button:has-text('Hiện thêm')",
        "ytcp-button:has-text('Show more')",
        "ytcp-button:has-text('Hiện thêm')",
    ],
    # "Không, đây không phải nội dung dành cho trẻ em" (Made for kids = No).
    # (DOM thật 2026-10-03: ytkc-made-for-kids-select trong div#audience, radio
    #  name VIDEO_MADE_FOR_KIDS_MFK / VIDEO_MADE_FOR_KIDS_NOT_MFK.)
    "mfk_no": [
        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
        "ytkc-made-for-kids-select tp-yt-paper-radio-button:has-text('không dành cho trẻ em')",
        "ytkc-made-for-kids-select tp-yt-paper-radio-button:has-text(\"not made for kids\")",
    ],
    # Ô nhập Tags (thẻ) — trong phần Show more.
    "tags_box": [
        "ytcp-form-input-container#tags-container #text-input",
        "input[aria-label*='tags']",
        "input[aria-label*='thẻ']",
        "#tags-container input",
    ],
    # Nội dung có yếu tố AI («Sử dụng AI» / Altered content): chọn "Có".
    # DOM thật 2026-10-03: div#altered-content > ytkp-altered-content-select,
    # radio name VIDEO_HAS_ALTERED_CONTENT_YES / _NO.
    # TUYỆT ĐỐI không dùng selector chữ "Có"/"Yes" KHÔNG giới hạn vùng: nút đầu
    # tiên có chữ "Có" trên trang là «Có, nội dung này dành cho trẻ em».
    "altered_content_section": [
        "div#altered-content",
        "ytkp-altered-content-select",
    ],
    "altered_content_yes": [
        "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_YES']",
        "ytkp-altered-content-select tp-yt-paper-radio-button[name$='_YES']",
        "#altered-content tp-yt-paper-radio-button[name$='_YES']",
        "tp-yt-paper-radio-button[name='VIDEO_ALTERED_CONTENT_YES']",
    ],
    # Ngôn ngữ video → dropdown → English (United States).
    # (Studio 2026: component thật là ytcp-form-language-input#language-input —
    #  #video-language chỉ còn là fallback cho bản cũ.)
    "video_language_dropdown": [
        "ytcp-form-language-input#language-input ytcp-dropdown-trigger",
        "#language-input ytcp-dropdown-trigger",
        "#language-input ytcp-text-dropdown-trigger",
        "#language-input",
        "#video-language ytcp-dropdown-trigger",
        "ytcp-form-select#video-language",
        "#video-language",
    ],
    # Lựa chọn theo test-id = mã ngôn ngữ (không phụ thuộc giao diện EN/VN:
    # bản VN hiển thị «Tiếng Anh (Hoa Kỳ)»). "{lang}" được thay bằng mã kênh.
    "video_language_option": [
        "tp-yt-paper-item[test-id='{lang}']",
        "[role='option'][test-id='{lang}']",
    ],
    "video_language_en_us": [
        "tp-yt-paper-item[test-id='en-US']",
        "tp-yt-paper-item:text-is('English (United States)')",
        "tp-yt-paper-item:text-is('Tiếng Anh (Hoa Kỳ)')",
    ],
    # Danh mục video → "Âm nhạc" (Music). Nằm trong phần "Hiện thêm"; kênh nhạc
    # để sai danh mục sẽ bị gợi ý/kiếm tiền lệch nên coi là metadata BẮT BUỘC.
    "category_dropdown": [
        "ytcp-form-select#category ytcp-dropdown-trigger",
        "#category ytcp-dropdown-trigger",
        "#category-container ytcp-dropdown-trigger",
        "ytcp-form-select#category",
        "#category",
    ],
    # test-id không phụ thuộc ngôn ngữ (bản VN hiển thị «Nhạc», không phải «Âm nhạc»).
    "category_music": [
        "tp-yt-paper-item[test-id='CREATOR_VIDEO_CATEGORY_MUSIC']",
        "tp-yt-paper-item:text-is('Music')",
        "tp-yt-paper-item:text-is('Nhạc')",
        "tp-yt-paper-item:text-is('Âm nhạc')",
    ],
    # Tên kênh đang mở trong Studio (thanh điều hướng trái) — chặn upload nhầm
    # kênh khi 1 tài khoản Google có nhiều kênh.
    "studio_channel_name": [
        "ytcp-navigation-drawer #entity-name",
    ],
    # Đóng dialog upload → YouTube TỰ LƯU thành Draft (bản nháp). Đây là bước
    # "lưu nháp": KHÔNG bao giờ bấm Next tới Publish/Xuất bản.
    "close_dialog": [
        # id thật của nút X trên dialog Upload (Studio 2026).
        "ytcp-button#ytcp-uploads-dialog-close-button",
        "#ytcp-uploads-dialog-close-button",
        "ytcp-uploads-dialog button[aria-label='Close']",
        "ytcp-uploads-dialog button[aria-label='Đóng']",
        "ytcp-button#close-button",
        "#close-button",
        "button[aria-label='Close']",
        "button[aria-label='Đóng']",
        "ytcp-icon-button[aria-label='Close']",
    ],
    # Hộp thoại xác nhận sau khi đóng: nút "Lưu bản nháp / Đã lưu dưới dạng nháp"
    # hoặc chỉ cần đóng. Xác nhận đã lưu draft (nếu hiện).
    # (Chỉ để NHẬN BIẾT — không bấm: selector chữ dạng *:has-text sẽ khớp cả
    #  <html> và click vào giữa trang.)
    "saved_draft_confirm": [
        "ytcp-uploads-dialog :text('saved as a draft')",
        "ytcp-uploads-dialog :text('dưới dạng bản nháp')",
    ],
    # Xác nhận đã đăng nhập Studio (avatar / nút Create hiện ra).
    "studio_ready": [
        "ytcp-button#create-icon",
        "#create-icon",
        "button[aria-label='Create']",
        "button[aria-label='Tạo']",
    ],
    # Trạng thái xử lý upload (chờ upload xong mới điền/đóng an toàn).
    "upload_progress": [
        ".progress-label",
        "ytcp-video-upload-progress",
        "span.ytcp-video-upload-progress",
    ],
    # Trạng thái quét bản quyền / vi phạm chính sách ("Checks"). Chỉ được làm
    # tác vụ khác sau khi mục này báo xong (no issues / đã kiểm tra xong).
    "checks_status": [
        "ytcp-video-upload-progress .progress-label",
        "ytcp-checks-status",
        "#checks-status",
        ".ytcp-video-upload-progress",
    ],
}

# Các mẫu chữ (thường hoá) dùng để đọc trạng thái upload + quét chính sách.
# Đọc TEXT chứ không đoán theo thời gian — giao diện EN lẫn VN đều bắt được.
# TÁCH 3 PHA vì chúng kết thúc ở thời điểm khác nhau:
#   tải lên  → bắt buộc chờ xong;
#   quét bản quyền/chính sách ("checks") → bắt buộc chờ xong;
#   xử lý/transcode ("processing") → KHÔNG cần chờ để lưu nháp; video thiền
#   2-3 tiếng có thể xử lý hàng giờ, chờ nữa là kẹt cả dây chuyền.
YOUTUBE_UPLOAD_BUSY_PATTERNS = (          # đang TẢI LÊN
    "uploading", "đang tải lên", "đang tải",
)
YOUTUBE_CHECK_BUSY_PATTERNS = (           # đang QUÉT bản quyền/chính sách
    "checking", "đang kiểm tra", "running checks",
)
YOUTUBE_PROCESS_BUSY_PATTERNS = (         # đang XỬ LÝ video (transcode)
    "processing", "đang xử lý", "xử lý video",
)
YOUTUBE_UPLOAD_DONE_PATTERNS = (          # đã TẢI LÊN xong
    "upload complete", "tải lên xong", "đã tải lên", "upload đã xong",
)
YOUTUBE_CHECK_DONE_PATTERNS = (           # đã QUÉT xong
    "checks complete", "no issues found", "no copyright issues",
    "đã kiểm tra xong", "kiểm tra hoàn tất", "không phát hiện vấn đề",
    "không có vấn đề",
)
# Phát hiện vấn đề bản quyền/chính sách → KHÔNG chặn lưu nháp, nhưng phải cảnh báo.
YOUTUBE_UPLOAD_ISSUE_PATTERNS = (
    "issues found", "copyright", "bản quyền", "vấn đề", "restriction",
    "hạn chế", "claim", "khiếu nại",
)


# ════════════════════════════════════════════════════════════════
# SUNO — Tạo nhạc nền (STEP 0 của pipeline). Nguồn nhạc: Suno.com.
# Người dùng có gói Premier, làm nhạc instrumental relaxing/Lofi, mỗi
# project cần ĐÚNG 15 file WAV tải trực tiếp từ Suno (không MP3, không
# đổi đuôi, không tự convert). Tất cả selector/tham số gom ở đây.
# Cơ sở: docs/suno-ui-observation.md (quan sát UI thật 2026-09-20).
# ════════════════════════════════════════════════════════════════

SUNO_URL = "https://suno.com/create"
# Profile Playwright RIÊNG cho Suno — KHÔNG dùng chung .browser_profile của
# ChatGPT/Flow, KHÔNG dùng chung session với extension Chrome cá nhân.
SUNO_PROFILE_DIR = _ROOT / ".browser_profile_suno"
# State/manifest bền vững cho batch Suno (chống trùng, resume). Ngoài thư mục
# media auto-watch để input chỉ publish khi cả batch sẵn sàng.
SUNO_STAGING_ROOT = _ROOT / "data" / "suno_batches"


@dataclass
class SunoConfig:
    """Tham số STEP 0 (mục E của spec). Đây là DEFAULT; có thể override per-batch
    khi người dùng bấm Start (preset, target, model, limits...)."""
    source:              str  = "suno"          # 'local' (import tay) | 'suno'
    target_tracks:       int  = 15              # ĐÚNG 15 output cho mỗi project
    preset:              str  = "ambient"       # key trong SUNO_PRESETS
    preferred_model:     str  = "v6"            # map tới mục 'v6' (Pro) ở ALL MODELS
    allow_model_fallback: bool = False          # False = không chấp nhận model khác
    instrumental:        bool = True            # True ⇒ để Lyrics TRỐNG (không có toggle)
    allow_bass:          bool = False           # không có UI switch → đưa vào Exclude styles
    allow_drums:         bool = False           # không có UI switch → đưa vào Exclude styles
    generation_strategy: str  = "paired_outputs"  # 2 bài/request nhưng CHỈ chọn 1 (2 bài na ná) → cần 15 request cho 15
    # Thanh trượt More Options (LIVE 2026-10-08, chỉ kéo bằng chuột). Variety 0–4
    # (Off/Normal/…): >0 làm Suno viết lại Styles và tự thêm nhạc cụ → Off. Style
    # Influence cao + Weirdness thấp = bám sát Styles (nhạc cụ chính). Chốt theo
    # thí nghiệm A/B data/_suno_ab (project #24 bamboo flute).
    variety:             int  = 0
    style_influence:     int  = 75
    weirdness:           int  = 30
    # Mỗi Create ra 2 bài: chấm "đúng nhạc cụ chính" (PANNs, 0 credit) rồi giữ bài
    # sạch hơn (A/B 2026-10-08: bài đầu sạch 47% → chọn 1 trong 2 sạch 73%).
    instrument_qc:       bool = True
    # Người dùng chốt 2026-10-08: Max Mode LUÔN bật (tool ép On mỗi lượt Create).
    max_mode:            bool = True
    concurrency_per_account: int = 1            # 1 worker/profile — lock chống 2 worker
    auto_continue_workflow: bool = True         # sau import → chạy tiếp pipeline hiện có
    preferred_duration_seconds_min: int = 180   # 3:00 (soft — Duration Custom)
    preferred_duration_seconds_max: int = 300   # 5:00 (soft)
    minimum_duration_seconds: int = 120         # WAV ngắn hơn 2:00 bị loại
    # ── Ngân sách / chốt chặn an toàn (không tự mua thêm credit) ──
    max_create_actions:      int = 15           # mỗi Create chọn 1 bài → cần 15 lần Create cho 15 bài
    max_generation_credits:  int = 400          # tham khảo: (15 + 5 lượt bù) × 20 credit
    # Đo thật 10 credit/Create; Max Mode LUÔN bật → ×2 (tooltip Suno «Costs 2x credits»).
    # Trần chi thật = max_create_actions + max_replacement_creates; dưới mức này → dừng.
    credits_per_create:      int = 20
    max_new_song_downloads:  int = 15           # trần số WAV tải về/batch
    max_replacement_creates: int = 5            # lượt Create THÊM khi cả 2 bài của 1 lượt đều lỗi (ngắn/hỏng)
    # ── Xác nhận lượt Create được NHẬN (từ 2026-10-12 Suno chặn Create bằng captcha) ──
    # Bình thường bài mới hiện ~3s sau khi bấm. Quá create_accept_sec mà không bài,
    # không captcha, không request tạo bài → báo sớm (không mù chờ 300s).
    create_accept_sec:       int = 90
    # Captcha hiện → đưa cửa sổ Suno lên trước, chờ NGƯỜI DÙNG tự giải tối đa chừng
    # này giây (tool KHÔNG tự giải/vượt captcha). Chờ lâu để lô TẠM DỪNG tại chỗ chứ
    # không gãy: người dùng tích ô lúc nào thì chạy tiếp lúc đó.
    captcha_wait_sec:        int = 3600
    captcha_beep_sec:        int = 180          # bíp nhắc lại mỗi chừng này giây khi còn chờ
    # Giãn nhịp: 2 lượt Create liên tiếp cách nhau tối thiểu chừng này giây
    # (2026-10-12: 4 lượt trong 21s → Suno bật ô «Verify you are human»).
    create_min_gap_sec:      int = 30


# Preset lưu NGUYÊN VĂN Styles + Exclusions tiếng Anh đã gửi (mục E). KHÔNG
# sửa chữ. Lyrics luôn để trống ở chế độ instrumental. allow_bass/allow_drums
# đưa vào Exclude styles vì Suno không có switch bật/tắt stem lúc tạo.
SUNO_PRESETS: dict[str, dict] = {
    "ambient": {
        "label": "Ambient (beatless, no drum — slow tempo)",
        "styles": (
            "Instrumental ambient music, beatless and drumless, very slow tempo "
            "around 55-65 BPM, soft evolving pads and gentle textures, warm and "
            "spacious atmosphere, long sustained tones, subtle melodic movement, "
            "deep reverb and wide stereo field, calm meditative mood, smooth "
            "gradual build, soft natural ending. "
            "Purely instrumental, no vocals, no accompaniment."
        ),
        "exclusions": (
            "vocals, singing, spoken word, humming, chanting, choir, vocal samples, "
            "drums, percussion, kick, snare, hi-hats, 808, beat, "
            "harsh distortion, aggressive drops"
        ),
    },
    "lofi": {
        "label": "Lofi (beat nhẹ — slow tempo)",
        "styles": (
            "Instrumental lo-fi music with a gentle, laid-back beat, slow tempo "
            "around 70-80 BPM, soft mellow chords, warm tape and vinyl texture, "
            "relaxed swing groove, subtle rounded bassline, dusty drums kept light "
            "and soft, cozy late-night mood, smooth transitions, soft natural "
            "ending. Purely instrumental, no vocals."
        ),
        "exclusions": (
            "vocals, singing, spoken word, humming, chanting, choir, vocal samples, "
            "harsh distortion, aggressive drops, loud percussion, heavy 808"
        ),
    },
}


# Selector Suno — best-effort từ 1 lần quan sát DOM thật (docs/suno-ui-observation.md
# §4). Suno đổi UI thường xuyên → mọi step driver phải BÁO UI_CHANGED khi thiếu
# selector, KHÔNG im lặng bỏ qua. Override qua suno_overrides.json / video_overrides.
SUNO_SELECTORS: dict[str, list[str]] = {
    # Tab chế độ tạo (role=tab). Spec gọi "Custom/Advanced" = tab "Advanced".
    "mode_advanced_tab": [
        "[role=tab][aria-selected]:has-text('Advanced')",
        "button[role=tab]:has-text('Advanced')",
        "[role=tab]:has-text('Advanced')",
    ],
    "mode_simple_tab": [
        "button[role=tab]:has-text('Simple')",
        "[role=tab]:has-text('Simple')",
    ],
    # Nút hiển thị model hiện tại (góc trên-phải panel, text = version vd 'v6').
    # Đang ở custom model thì nút chỉ có ẢNH BÌA (không chữ). Driver định vị
    # CHÍNH bằng hình học (SunoDriver._model_button: nút aria-haspopup cùng hàng,
    # ngay phải pill credits); list này chỉ là DỰ PHÒNG. LIVE 2026-10-12 nút có
    # aria-haspopup='dialog' (trước 'menu' — selector 'menu' cũ giờ khớp nhầm
    # nút ⋯ của danh sách bài nên đã bỏ).
    "model_button": [
        "button[aria-haspopup='dialog']:right-of(button[aria-label^='Credits remaining'])",
        "button:has-text('v6-mini')",
        "button:has-text('v6-wild')",
        "button:has-text('v6')",
    ],
    # Mục chọn model 'v6' trong hộp ALL MODELS (LIVE 2026-10-12: grid/row;
    # trước đó menu/menuitemradio).
    "model_option_v6": [
        "[role=grid][aria-label='Model'] [role=row]:has-text('Powerful. Versatile. Refined.')",
        "[role=menuitemradio]:has-text('Powerful. Versatile. Refined.')",
        "[role=menuitem]:has-text('v6'):not(:has-text('mini')):not(:has-text('wild'))",
        "[role=option]:has-text('v6'):not(:has-text('mini')):not(:has-text('wild'))",
    ],
    # Ô tiêu đề bài (tùy chọn) — KHÔNG bắt buộc.
    "song_title": [
        "input[placeholder='Song Title (Optional)']",
        "input[placeholder*='Song Title']",
    ],
    # Workflow Cover mới: upload xong phải qua Editor, đặt Tempo rồi lưu thành
    # bài mới; chỉ sau Remix → Cover mới xuất hiện Song Title/Lyrics.
    "open_in_editor": [
        "button:text-is('Open in Editor')",
        "[role=button]:text-is('Open in Editor')",
        "[role=menuitem]:has-text('Open in Editor')",
        "div.hxc-menu-item:has-text('Open in Editor')",
        "div.context-menu-item:has-text('Open in Editor')",
        "button.hxc-btn-base:has-text('Open in Editor')",
        "button:has-text('Open in editor')",
    ],
    "editor_tempo": [
        "input[aria-label*='Tempo' i]",
        "input[aria-label*='BPM' i]",
        "input[name*='tempo' i]",
        "input[name*='bpm' i]",
        "input[placeholder*='Tempo' i]",
        "input[placeholder*='BPM' i]",
        "[role=spinbutton][aria-label*='Tempo' i]",
        "[role=spinbutton][aria-label*='BPM' i]",
    ],
    "save_as_new_song": [
        "button:text-is('Save as New Song')",
        "[role=button]:text-is('Save as New Song')",
        "[role=menuitem]:has-text('Save as New Song')",
        "button:has-text('Save as new song')",
    ],
    # Lyrics editor — ĐỂ TRỐNG = instrumental. LIVE 2026-10-08: ô Lyrics thật là
    # Lexical contenteditable div[role=textbox][aria-label='Lyrics editor']
    # (placeholder "leave this empty for instrumental"). TUYỆT ĐỐI không dùng
    # textarea 'Cowriter prompt' (data-cowrite-input) — đó là ô NHỜ SUNO VIẾT LỜI.
    "lyrics_editor": [
        "[role=textbox][aria-label='Lyrics editor']",
        "div[contenteditable='true'][aria-label='Lyrics editor']",
        "div.lyrics-editor-content[contenteditable='true']",
    ],
    # Ô Styles (textarea, 1000 ký tự). Placeholder là VÍ DỤ XOAY VÒNG (đổi liên
    # tục) → KHÔNG match theo placeholder. Neo theo data-testid ổn định của
    # wrapper (verified LIVE 2026-09-21), rồi maxlength=1000 làm dự phòng.
    "styles_box": [
        "[data-testid='create-form-styles-wrapper'] textarea",
        "textarea[maxlength='1000']",
        "textarea[placeholder*='city pop']",
        "textarea[placeholder*='epic build-up']",
        "textarea[data-testid='tag-input-textarea']",
    ],
    # Nút mở "More Options".
    "more_options": [
        "[role=button]:has-text('More Options')",
        "button:has-text('More Options')",
        "*:has-text('More Options')",
    ],
    # Exclude styles — nơi ghi 'no drums, no bass...' khi allow_*=False.
    "exclude_styles": [
        "input[placeholder='Exclude styles']",
        "input[placeholder*='Exclude']",
    ],
    # Duration: LUÔN Auto (người dùng chốt) — tool chỉ KIỂM (suno_driver.
    # ensure_duration_auto / _JS_DURATION_STATE), không bấm Custom.
    # Max Mode Off/On.
    "max_mode_off": ["button:has-text('Off')"],
    "max_mode_on":  ["button:has-text('On')"],
    # Thanh trượt sáng tạo trong More Options (LIVE 2026-10-08, role=slider).
    "slider_weirdness": ["[role=slider][aria-label='Weirdness']"],
    "slider_style_influence": ["[role=slider][aria-label='Style Influence']"],
    "slider_variety": ["[role=slider][aria-label='Variety']"],
    # Nút Create.
    "create_button": [
        "button[aria-label='Create song']",
        "button:has-text('Create')",
    ],
    "clear_form": ["button[aria-label='Clear all form inputs']"],
    # Credits còn lại (đọc để tiền-kiểm ngân sách).
    "credits": ["button[aria-label^='Credits remaining']"],
    # Workspace (gom 15 bài của project vào 1 workspace nếu UI hỗ trợ).
    "new_workspace": ["*:has-text('Create new workspace')"],
    # Danh sách bài + hành động per-song.
    "song_row_play": ["div[role=button][aria-label^='Play ']"],
    "song_more_options": ["button[aria-label='More options']",
                          "button[aria-label^='More options for']"],
    # Menu ⋯ → Download → dialog format WAV.
    "download_entry": [
        "div.hxc-menu-item:has-text('Download')",
        "div.context-menu-item:has-text('Download')",
        "button[aria-label='Download']",
        "[role=menuitem]:has-text('Download')",
        "*:has-text('Download')",
    ],
    # Hộp thoại Download là DANH SÁCH TOGGLE (không phải bấm-là-tải): mỗi định
    # dạng là 1 <button>, được chọn khi bên trong có 1 <svg> dấu tích. Mặc định
    # MP3 đang bật. Quy trình chuẩn: BẬT WAV, TẮT MP3, rồi bấm nút 'Download'
    # cuối. Các nút này CHỈ nhận chuột thật (JS/locator.click không đăng ký).
    "download_dialog": [
        "[role=dialog]:has-text('Download')",
    ],
    "download_format_wav": [
        "[role=dialog] button:has-text('WAV')",
        "button:has-text('WAV')",
    ],
    "download_format_mp3": [
        "[role=dialog] button:has-text('MP3')",
        "button:has-text('MP3')",
    ],
    "download_confirm": [
        "[role=dialog] button:text-is('Download')",
        "[role=dialog] button:text-is('Confirm')",
        "[role=dialog] button:has-text('Download')",
        "button:text-is('Download')",
        "button:text-is('Confirm')",
        "button:has-text('Download')",
    ],
    # Giữ selector cũ để tương thích ngược (không còn dùng trực tiếp).
    "download_wav": [
        "[role=dialog] button:has-text('WAV')",
        "button:has-text('WAV')",
        "*:has-text('WAV')",
    ],

    # ── Luồng tải WAV MỚI qua Suno Studio (verified LIVE 2026-09-21) ─────
    # Quy trình: ⋯ → Edit → Open in Studio → Single-track → (chờ load) →
    # Export → Full Song → (chờ 'Song Saved') → Go to Song → (chờ hết
    # 'Preparing song for playback...') → ⋯ → Download → WAV.
    # Menu ⋯ dùng cấu trúc `div.context-menu-item > button.hxc-btn-base`;
    # từ 2026-09-30 Suno đổi sang `div.hxc-menu-item` (submenu có thêm class
    # `hxc-submenu-trigger`) → selector mới đặt TRƯỚC, giữ selector cũ dự phòng.
    # (KHÔNG phải role=menuitem). Submenu 'Edit' mở khi HOVER.
    "studio_edit_menu": [
        "div.hxc-menu-item:has-text('Edit')",
        "div.context-menu-item:has-text('Edit')",
        "button.hxc-btn-base:has-text('Edit')",
    ],
    # 'Open in Studio' nằm trong submenu Edit (kèm badge 'New' → text là
    # 'Open in StudioNew', :has-text khớp chuỗi con nên vẫn trúng).
    "open_in_studio": [
        "div.hxc-menu-item:has-text('Open in Studio')",
        "div.context-menu-item:has-text('Open in Studio')",
        "button.hxc-btn-base:has-text('Open in Studio')",
    ],
    # Hộp thoại chọn kiểu mở trong Studio: Single-track (Use the full mix,
    # MIỄN PHÍ) vs Multi-track (Separate stems, 50 credits → KHÔNG dùng).
    # Dialog có aria-label 'Edit <title> in Studio'.
    "studio_single_track": [
        "[role=dialog] button:has-text('Single-track')",
        "[role=dialog] button:has-text('Single track')",
        "[role=dialog] [role=button]:has-text('Single')",
        "button:has-text('Single-track')",
        "button:has-text('Single track')",
        "[role=button]:has-text('Single')",
    ],
    # Nút Export (góc trên-phải editor Studio). Là tín hiệu 'đã load xong'.
    "studio_export_menu": [
        "button[aria-label='Export menu']",
        "button:has-text('Export')",
    ],
    # Menu Export → 'Full Song' (còn có 'Selected Time Range', 'Multitrack').
    "studio_full_song": [
        "div.hxc-menu-item:has-text('Full Song')",
        "div.context-menu-item:has-text('Full Song')",
        "button.hxc-btn-base:has-text('Full Song')",
    ],
    # Thông báo 'Song Saved' sau khi render xong (best-effort, để chờ/log).
    "studio_song_saved": [
        "text=Song Saved",
        "*:has-text('Song Saved')",
    ],
    # Nút 'Go to Song' xuất hiện cùng/ sau 'Song Saved'.
    "studio_go_to_song": [
        "button:has-text('Go to Song')",
        "a:has-text('Go to Song')",
        "[role=button]:has-text('Go to Song')",
        "div.hxc-menu-item:has-text('Go to Song')",
        "div.context-menu-item:has-text('Go to Song')",
    ],
    # Trạng thái 'Preparing song for playback...' — CHỜ tới khi biến mất mới tải.
    "studio_preparing": [
        "text=Preparing song for playback",
        "*:has-text('Preparing song for playback')",
    ],
}


def load_overrides() -> dict:
    """Đọc video_overrides.json nếu có để override selector/tham số."""
    try:
        if OVERRIDES.exists():
            return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_override(key: str, value) -> None:
    """Ghi 1 khoá cấp cao vào video_overrides.json, giữ nguyên các khoá khác."""
    data = load_overrides()
    data[key] = value
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_selector_overrides(site: str, discovered: dict[str, list[str]]) -> None:
    """Ghi/gộp các selector đã dò được vào video_overrides.json dưới
    selectors.<site>.<key>. KHÔNG xoá các key khác (giữ nguyên override sẵn có
    của site khác/tham số khác). Dùng cho tính năng tự cập nhật selector khi
    giao diện Suno đổi."""
    if not discovered:
        return
    data = load_overrides()
    selectors = data.setdefault("selectors", {})
    site_sel = selectors.setdefault(site, {})
    for key, value in discovered.items():
        site_sel[key] = value if isinstance(value, list) else [value]
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clip_engine() -> str:
    """'flow' (mặc định) | 'gemini' | 'muse' — máy tạo clip tự động."""
    v = str(load_overrides().get("clip_engine") or DEFAULT_CLIP_ENGINE).strip().lower()
    return v if v in CLIP_ENGINES else DEFAULT_CLIP_ENGINE


# Máy tạo ẢNH (bảng nhân vật + ảnh bìa). Muse.ai MẶC ĐỊNH từ 2026-10-08 (yêu cầu
# người dùng); máy chính hỏng (hết lượt / lỗi máy chủ / bộ lọc / mất đăng nhập) →
# TỰ chuyển máy còn lại (Gemini) vẽ lại TOÀN BỘ ảnh từ đầu cho cả bộ đồng nhất.
# Override: image_engine="gemini" (Gemini chính, Muse dự phòng),
# image_fallback=false (tắt chuyển dự phòng; khoá cũ image_muse_fallback vẫn đọc).
# Tool Cover 2026-10-29: «ảnh bìa cho chatgpt làm» → ChatGPT web vẽ ảnh bìa gốc
# (chế độ 1 ảnh), Muse.ai dự phòng. Quy trình cũ 3 ảnh: ChatGPT không vẽ bảng nhân
# vật → luôn rơi về Muse.
IMAGE_ENGINES = ("chatgpt", "gemini", "muse")
IMAGE_ENGINE_LABELS = {"chatgpt": "ChatGPT", "gemini": "Gemini", "muse": "Muse.ai"}
DEFAULT_IMAGE_ENGINE = "chatgpt"


def image_engine() -> str:
    """'chatgpt' (mặc định, dự phòng Muse) | 'muse' (dự phòng Gemini) | 'gemini'
    (dự phòng Muse) — máy tạo ảnh."""
    v = str(load_overrides().get("image_engine") or DEFAULT_IMAGE_ENGINE).strip().lower()
    return v if v in IMAGE_ENGINES else DEFAULT_IMAGE_ENGINE


def image_fallback_engine() -> str:
    """Máy dự phòng: Muse → Gemini; ChatGPT / Gemini → Muse."""
    return "gemini" if image_engine() == "muse" else "muse"


def image_fallback() -> bool:
    """Máy ảnh chính không tạo được → chuyển máy dự phòng (mặc định BẬT)."""
    ov = load_overrides()
    v = ov.get("image_fallback", ov.get("image_muse_fallback", True))
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def image_engine_label(engine: str | None = None) -> str:
    return IMAGE_ENGINE_LABELS.get(engine or image_engine(), "Muse.ai")


def clip_prompt_mode() -> str:
    """'full' (mặc định: prompt đầy đủ + STRICTLY FORBIDDEN) | 'safe' (veo_safety)."""
    v = str(load_overrides().get("clip_prompt_mode") or DEFAULT_CLIP_PROMPT_MODE).strip().lower()
    return v if v in CLIP_PROMPT_MODES else DEFAULT_CLIP_PROMPT_MODE


def get_selectors(site: str) -> dict[str, list[str]]:
    """site = 'chatgpt' | 'gemini' | 'flow'. Merge override (nếu có) lên default."""
    table = {
        "chatgpt": CHATGPT_SELECTORS,
        "gemini":  GEMINI_SELECTORS,
        "gemini_video": GEMINI_VIDEO_SELECTORS,
        "flow":    FLOW_SELECTORS,
        "youtube": YOUTUBE_SELECTORS,
        "suno":    SUNO_SELECTORS,
    }
    base = dict(table.get(site, FLOW_SELECTORS))
    ov = load_overrides().get("selectors", {}).get(site, {})
    for k, v in ov.items():
        base[k] = v if isinstance(v, list) else [v]
    return base


# Instance mặc định dùng chung
# Tool Cover (2026-10-29): ảnh bìa (0) vẽ TRƯỚC, không cần tham chiếu → bảng
# phân tích nhân vật (1) + bảng tham chiếu bối cảnh (2) vẽ TỪ ảnh bìa (đính kèm
# 0.png) → thumbnail ChatGPT có chữ làm reference trực tiếp cho clip 00; các
# clip 01–05 dùng ảnh 1 + 2. Thumbnail cũng là ảnh đăng YouTube. Clip 01 cùng bối cảnh lặp nhẹ,
# clip 02–05 = 4 góc quay nhân vật. Ghép: intro 1 lần rồi
# trộn 01–05 đến hết set nhạc. VideoParams() (3 ảnh / 20 clip) = quy trình cũ.
PARAMS  = VideoParams(image_count=3, scene_clips=6, rest_clips=5, min_total_clips=6,
                      cover=True)
LOOP_CLIP = 1
COVER_ANGLE_CLIPS = (2, 3, 4, 5)

IMAGE_ROLES_COVER: dict[int, str] = {
    IMG_THUMBNAIL:  "Ảnh bìa (thumbnail)",
    IMG_MAIN_SHEET: "Bảng phân tích nhân vật (vẽ từ ảnh bìa)",
    IMG_PET_SHEET:  "Bảng tham chiếu bối cảnh (vẽ từ ảnh bìa)",
}
IMAGE_ROLES_COVER_SCENERY: dict[int, str] = {
    IMG_THUMBNAIL:  "Ảnh bìa (thumbnail)",
    IMG_MAIN_SHEET: "Bảng thiết kế bối cảnh (vẽ từ ảnh bìa)",
    IMG_PET_SHEET:  "Bảng ánh sáng & không khí (vẽ từ ảnh bìa)",
}


def single_image(params: "VideoParams | None" = None) -> bool:
    """True = chế độ tool Cover: ảnh bìa là GỐC (vẽ không cần tham chiếu), hai
    bảng vẽ từ ảnh bìa. (Tên cũ từ thời chỉ có 1 ảnh — giữ để khỏi đổi API.)"""
    p = params or PARAMS
    return bool(getattr(p, "cover", False)) or int(getattr(p, "image_count", 3) or 3) == 1


def thumbnail_refs() -> tuple[int, ...]:
    """Ảnh tham chiếu đính kèm khi vẽ ảnh bìa (tool Cover: không có)."""
    return () if single_image() else THUMBNAIL_REFS


def image_refs(index: int) -> tuple[int, ...]:
    """Ảnh phải có TRƯỚC và được đính kèm khi vẽ ảnh `index`.
    Tool Cover: bảng 1/2 vẽ từ ảnh bìa 0. Quy trình cũ: ảnh bìa vẽ từ 2 bảng."""
    if single_image():
        return (IMG_THUMBNAIL,) if index in SHEET_IMAGES else ()
    return THUMBNAIL_REFS if index == IMG_THUMBNAIL else ()


def image_order() -> tuple[int, ...]:
    """Thứ tự vẽ ảnh: tool Cover bìa trước (0, 1, 2); quy trình cũ 2 bảng trước."""
    return (IMG_THUMBNAIL, *SHEET_IMAGES) if single_image() else IMAGE_ORDER
BROWSER = BrowserConfig()
SUNO    = SunoConfig()
