"""
video/channel_looks.py — "Phong cách kênh": mỗi kênh YouTube một bộ nhận diện
(thumbnail · ảnh · màu & chuyển cảnh video). Từ 2026-10-26 người dùng CHỌN TỰ DO
(2 kênh dùng chung 1 phong cách được) và TỰ NHẬP thêm phong cách (bảng customlook:
tên + mô tả → câu thẩm mỹ tiếng Anh, kế thừa font/màu/video của 1 look gốc, chọn
kiểu ảnh trong config.STYLES). Kênh mới để trống vẫn tự gán look CÒN TRỐNG.

Vì sao: trước đây MỌI video của MỌI kênh dùng chung 1 kiểu chữ (vàng 3D, thân
teal), 1 bố cục (chữ trái – nhân vật phải), 1 font, 1 bộ từ khoá phụ đề và cả
kênh khác nhau vẫn chung phong cách ảnh → thumbnail dập khuôn.

Mỗi LOOK gồm:
  styles       → các phong cách ảnh (config.STYLES) thuộc riêng look này. Các
                 look CÓ SẴN không chung phong cách ảnh nào (look tự nhập thì
                 người dùng tự chọn kiểu ảnh, trùng look khác cũng được).
  treatments / fonts / sub_fonts / palettes / layouts
               → bộ "nguyên liệu" thumbnail. Mỗi video bốc 1 tổ hợp khác các video
                 trước của kênh (xem thumbnail_recipe.py).
  keywords / subtitles → dòng phụ đề riêng giọng kênh.
  signature / env_moods → câu tả thẩm mỹ chung + bảng màu môi trường gửi model ảnh.
  grade / transitions / blend_seconds / slow_speed
               → cách hoàn thiện VIDEO riêng của kênh (màu, tốc độ). Chuyển cảnh:
                 từ 2026-10-25 MỌI look là "softfade" (hoà tan êm có easing,
                 2.6-3.2s) — hblur / trượt ngang / nháy đen bị chê "gấp quá,
                 không chill". Recipe cũ ghi kiểu cũ → finish_for tự về softfade.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)

# Thứ tự ưu tiên khi tự gán look cho kênh mới (không khớp từ khoá nào).
LOOK_ORDER = ["ink_zen", "cozy_lofi", "cinematic", "painterly", "bright"]

LOOKS: dict[str, dict] = {
    "ink_zen": {
        "icon": "🖌️",
        "label": "Thuỷ mặc thiền",
        "desc": "Tranh thuỷ mặc / màu nước, chữ thư pháp + ấn triện đỏ, màu dịu lạnh",
        "styles": ["ink", "watercolor"],
        "match": ["bamboo", "flute", "chinese", "guzheng", "erhu", "dizi", "xiao",
                  "pipa", "tea", "taoist", "zen", "oriental", "竹", "古"],
        "layouts": ["left", "right", "top", "bottom"],
        "treatments": ["ink_seal", "soft_glow", "clean_shadow", "framed"],
        "fonts": ["mistral", "lucida_calligraphy", "segoe_print_bold",
                  "brush_script", "rage_italic", "papyrus", "kunstler_script",
                  "vivaldi", "freestyle_script", "monotype_corsiva", "pristina",
                  "edwardian_script"],
        "sub_fonts": ["gabriola", "palatino", "segoe_print", "corsiva_sub",
                      "constantia"],
        "palettes": {
            "vermilion": {"face_top": (250, 246, 236), "face_bottom": (232, 220, 198),
                          "extrude": (60, 30, 26), "outline": (22, 18, 16),
                          "glow": (255, 236, 200), "sub": (245, 240, 228),
                          "accent": (186, 40, 36), "shade": (14, 12, 10)},
            "jade": {"face_top": (236, 248, 240), "face_bottom": (170, 214, 190),
                     "extrude": (18, 58, 48), "outline": (8, 24, 20),
                     "glow": (160, 230, 200), "sub": (232, 245, 238),
                     "accent": (176, 46, 38), "shade": (6, 20, 16)},
            "indigo": {"face_top": (238, 242, 252), "face_bottom": (176, 190, 228),
                       "extrude": (24, 30, 70), "outline": (10, 12, 32),
                       "glow": (170, 190, 255), "sub": (236, 240, 250),
                       "accent": (196, 52, 40), "shade": (8, 10, 26)},
            "charcoal": {"face_top": (250, 250, 248), "face_bottom": (214, 214, 210),
                         "extrude": (40, 40, 38), "outline": (14, 14, 13),
                         "glow": (235, 235, 230), "sub": (246, 246, 244),
                         "accent": (186, 40, 36), "shade": (10, 10, 10)},
            "sepia": {"face_top": (252, 246, 232), "face_bottom": (226, 200, 164),
                      "extrude": (72, 48, 28), "outline": (28, 18, 10),
                      "glow": (250, 224, 180), "sub": (248, 242, 230),
                      "accent": (170, 60, 40), "shade": (18, 12, 8)},
            "pine": {"face_top": (238, 246, 238), "face_bottom": (182, 206, 184),
                     "extrude": (16, 48, 36), "outline": (6, 20, 14),
                     "glow": (176, 214, 186), "sub": (236, 244, 238),
                     "accent": (182, 52, 40), "shade": (4, 16, 10)},
            "plum": {"face_top": (248, 242, 246), "face_bottom": (206, 180, 198),
                     "extrude": (58, 26, 48), "outline": (24, 10, 20),
                     "glow": (214, 176, 202), "sub": (246, 240, 244),
                     "accent": (190, 48, 44), "shade": (16, 6, 14)},
            "slate": {"face_top": (242, 246, 250), "face_bottom": (190, 204, 216),
                      "extrude": (32, 44, 56), "outline": (12, 18, 24),
                      "glow": (196, 214, 230), "sub": (240, 244, 248),
                      "accent": (184, 44, 38), "shade": (8, 12, 16)},
        },
        "seal_glyphs": ["静", "禅", "竹", "和", "心"],
        "keywords": ["Zen", "Meditation", "Inner Peace", "Tea Ceremony", "Taoist Calm",
                     "Deep Focus", "Mindfulness", "Healing", "Sleep", "Stillness",
                     "Ancient Harmony", "Qi Flow"],
        "subtitles": ["{kw1} · {kw2} · {kw3}", "Ancient {instrument} for {kw1}",
                      "{hours} of {instrument} · {kw1}", "{kw1} & {kw2} — {instrument}"],
        "signature": ("traditional East Asian ink-wash aesthetic: generous empty mist "
                      "and paper-like sky, brush-stroke silhouettes of bamboo, pine or "
                      "distant peaks, a restrained palette with one small warm accent"),
        "env_moods": ["monochrome ink greys with a single vermilion accent",
                      "pale jade green and mist white",
                      "indigo dusk with silver mist",
                      "warm rice-paper beige with charcoal ink"],
        "grade": ("eq=saturation=0.84:contrast=1.04:brightness=0.01,"
                  "colorbalance=bs=0.04:bm=0.02:rh=-0.02,vignette=angle=PI/5"),
        "transitions": ["softfade"],
        "blend_seconds": 3.2,
        "slow_speed": 0.68,
    },
    "cozy_lofi": {
        "icon": "☕",
        "label": "Lofi ấm cúng",
        "desc": "Lofi / 2D ấm áp, chữ tròn mập viền dày hoặc thẻ kính mờ, màu pastel",
        "styles": ["lofi", "2d"],
        "match": ["lofi", "lo-fi", "piano", "chill", "study", "jazz", "rain",
                  "cafe", "coffee", "beats", "hip hop", "night"],
        "layouts": ["left", "right", "top", "bottom"],
        "treatments": ["outline_pop", "banner", "soft_glow", "clean_shadow"],
        "fonts": ["cooper_black", "berlin_sans_demi", "ink_free", "eras_bold",
                  "bauhaus_93", "bradley_hand", "lucida_handwriting", "snap_itc"],
        "sub_fonts": ["berlin_sans", "ink_free_sub", "candara", "corbel",
                      "trebuchet"],
        "palettes": {
            "peach_plum": {"face_top": (255, 236, 214), "face_bottom": (255, 176, 140),
                           "extrude": (92, 40, 72), "outline": (52, 20, 44),
                           "glow": (255, 150, 120), "sub": (255, 244, 236),
                           "accent": (255, 128, 110), "shade": (40, 16, 34)},
            "mint_navy": {"face_top": (232, 255, 244), "face_bottom": (140, 232, 196),
                          "extrude": (20, 40, 84), "outline": (12, 22, 52),
                          "glow": (120, 240, 200), "sub": (240, 255, 250),
                          "accent": (110, 220, 180), "shade": (10, 18, 40)},
            "lilac_night": {"face_top": (246, 236, 255), "face_bottom": (200, 170, 255),
                            "extrude": (52, 30, 92), "outline": (28, 16, 56),
                            "glow": (190, 150, 255), "sub": (248, 242, 255),
                            "accent": (255, 190, 120), "shade": (22, 12, 44)},
            "honey_teal": {"face_top": (255, 248, 226), "face_bottom": (255, 206, 120),
                           "extrude": (16, 62, 66), "outline": (8, 30, 32),
                           "glow": (255, 196, 110), "sub": (252, 250, 240),
                           "accent": (90, 206, 196), "shade": (6, 24, 26)},
            "rose_coffee": {"face_top": (255, 242, 238), "face_bottom": (240, 176, 164),
                            "extrude": (74, 44, 34), "outline": (34, 20, 15),
                            "glow": (248, 180, 168), "sub": (254, 246, 242),
                            "accent": (210, 140, 96), "shade": (24, 14, 10)},
            "butter_sage": {"face_top": (255, 252, 230), "face_bottom": (246, 224, 140),
                            "extrude": (46, 66, 46), "outline": (20, 30, 20),
                            "glow": (238, 226, 150), "sub": (250, 252, 242),
                            "accent": (150, 190, 140), "shade": (14, 22, 14)},
            "berry_cream": {"face_top": (255, 244, 248), "face_bottom": (232, 160, 190),
                            "extrude": (66, 24, 52), "outline": (32, 10, 24),
                            "glow": (240, 166, 196), "sub": (255, 246, 250),
                            "accent": (255, 206, 130), "shade": (22, 8, 18)},
            "dusk_amber": {"face_top": (255, 240, 224), "face_bottom": (250, 186, 112),
                           "extrude": (44, 32, 70), "outline": (20, 14, 34),
                           "glow": (252, 180, 120), "sub": (252, 244, 236),
                           "accent": (164, 146, 235), "shade": (14, 10, 26)},
        },
        "keywords": ["Study", "Chill", "Relax", "Focus", "Rainy Night", "Cozy Vibes",
                     "Late Night", "Work", "Coffee Break", "Sleep", "Calm Beats",
                     "Reading"],
        "subtitles": ["{instrument} Lofi to {kw1} & {kw2}", "{kw1} · {kw2} · {kw3}",
                      "{hours} of Lofi {instrument}", "lofi {instrument} for {kw1}"],
        "signature": ("cozy lofi mood: warm lived-in details, soft window or city "
                      "lights in the distance, nostalgic pastel glow, calm and "
                      "comforting atmosphere"),
        "env_moods": ["peach and plum sunset pastels", "mint and navy night",
                      "lilac sky with amber window lights", "warm honey and teal"],
        "grade": ("eq=saturation=1.05:contrast=0.96:gamma=1.03,"
                  "colorbalance=rs=0.04:rm=0.03:bh=-0.03,vignette=angle=PI/5"),
        "transitions": ["softfade"],
        "blend_seconds": 3.0,
        "slow_speed": 0.72,
    },
    "cinematic": {
        "icon": "🎬",
        "label": "Điện ảnh",
        "desc": "Tả thực / film cổ điển, chữ serif sang trọng, khung mảnh, màu teal–cam",
        "styles": ["real", "vintage"],
        "match": ["guitar", "acoustic", "cinematic", "ambient", "nature", "ocean",
                  "forest", "documentary", "travel"],
        "layouts": ["left", "right", "top", "bottom"],
        "treatments": ["clean_shadow", "framed", "gold3d", "banner"],
        "fonts": ["perpetua_titling", "bodoni_bold", "palatino_bold", "copperplate",
                  "castellar", "engravers_mt", "felix_titling", "modern_no20",
                  "georgia_bold", "times_bold"],
        "sub_fonts": ["copperplate_light", "gill_sans", "georgia", "lucida_sans",
                      "franklin_cond"],
        "palettes": {
            "ivory_gold": {"face_top": (252, 246, 232), "face_bottom": (232, 206, 150),
                           "extrude": (40, 32, 20), "outline": (12, 10, 8),
                           "glow": (255, 214, 140), "sub": (236, 228, 212),
                           "accent": (212, 170, 90), "shade": (6, 6, 6)},
            "steel": {"face_top": (240, 246, 250), "face_bottom": (190, 206, 220),
                      "extrude": (20, 30, 40), "outline": (6, 10, 14),
                      "glow": (170, 210, 240), "sub": (220, 230, 238),
                      "accent": (120, 170, 210), "shade": (4, 8, 12)},
            "ember": {"face_top": (255, 240, 224), "face_bottom": (240, 170, 110),
                      "extrude": (50, 24, 12), "outline": (16, 8, 4),
                      "glow": (255, 140, 60), "sub": (240, 224, 210),
                      "accent": (230, 120, 60), "shade": (10, 4, 2)},
            "sand_slate": {"face_top": (250, 246, 238), "face_bottom": (218, 202, 176),
                           "extrude": (34, 40, 46), "outline": (10, 14, 16),
                           "glow": (226, 210, 184), "sub": (238, 234, 226),
                           "accent": (130, 150, 164), "shade": (6, 8, 10)},
            "moss_bronze": {"face_top": (248, 246, 234), "face_bottom": (206, 196, 150),
                            "extrude": (30, 40, 26), "outline": (10, 14, 8),
                            "glow": (210, 200, 150), "sub": (234, 234, 222),
                            "accent": (170, 130, 70), "shade": (6, 8, 4)},
            "copper_night": {"face_top": (252, 240, 230), "face_bottom": (226, 160, 110),
                             "extrude": (26, 30, 44), "outline": (8, 10, 16),
                             "glow": (236, 150, 96), "sub": (236, 228, 220),
                             "accent": (190, 110, 70), "shade": (4, 6, 10)},
            "pearl_charcoal": {"face_top": (252, 252, 252), "face_bottom": (216, 218, 222),
                               "extrude": (26, 28, 30), "outline": (8, 8, 9),
                               "glow": (226, 228, 232), "sub": (240, 240, 242),
                               "accent": (176, 180, 186), "shade": (4, 4, 5)},
            "wine_gold": {"face_top": (252, 244, 236), "face_bottom": (224, 190, 136),
                          "extrude": (52, 18, 28), "outline": (20, 6, 10),
                          "glow": (238, 196, 140), "sub": (240, 230, 222),
                          "accent": (150, 40, 54), "shade": (12, 4, 6)},
        },
        "uppercase": True,
        "keywords": ["Deep Relaxation", "Stress Relief", "Meditation", "Sleep", "Focus",
                     "Calm Mind", "Healing", "Nature Ambience", "Mindfulness",
                     "Peaceful Evening"],
        "subtitles": ["{kw1} | {kw2}", "{hours} · {instrument} {kw1}",
                      "{instrument} for {kw1} and {kw2}", "{kw1} · {kw2} · {kw3}"],
        "signature": ("cinematic film still: natural textures, clear foreground, "
                      "midground and background layering, realistic atmospheric "
                      "haze and depth"),
        "env_moods": ["teal and orange film tones", "cool steel blue with warm practical lights",
                      "deep ember amber and long shadows", "muted earthy greens and stone greys"],
        "grade": ("eq=contrast=1.07:saturation=0.93,"
                  "colorbalance=rs=-0.05:bs=0.06:rh=0.05:bh=-0.05"),
        "transitions": ["softfade"],
        "blend_seconds": 3.0,
        "slow_speed": 0.7,
    },
    "painterly": {
        "icon": "🌸",
        "label": "Tranh truyện cổ tích",
        "desc": "Ghibli / sơn dầu, chữ cổ tích 3D vàng hoặc phát sáng, màu tươi ấm",
        "styles": ["ghibli", "oil"],
        "match": ["harp", "healing", "spa", "fantasy", "celtic", "dream", "fairy",
                  "nature", "sleep"],
        "layouts": ["left", "right", "top", "bottom"],
        "treatments": ["gold3d", "soft_glow", "framed", "outline_pop"],
        "fonts": ["harrington", "bell_mt_bold", "high_tower", "segoe_script_bold",
                  "old_english", "garamond_bold", "goudy_stout", "book_antiqua",
                  "colonna", "matura_script", "imprint_shadow"],
        "sub_fonts": ["bell_mt", "goudy", "garamond", "book_antiqua_sub",
                      "lucida_bright", "century"],
        "palettes": {
            "gold_teal": {"face_top": (255, 248, 214), "face_bottom": (255, 190, 58),
                          "extrude": (13, 46, 37), "outline": (6, 20, 15),
                          "glow": (255, 176, 32), "sub": (242, 249, 245),
                          "accent": (255, 190, 58), "shade": (10, 20, 15)},
            "rose_cream": {"face_top": (255, 246, 240), "face_bottom": (246, 168, 170),
                           "extrude": (90, 34, 48), "outline": (40, 12, 20),
                           "glow": (255, 170, 180), "sub": (255, 246, 244),
                           "accent": (240, 140, 150), "shade": (30, 10, 16)},
            "sky_sun": {"face_top": (255, 255, 240), "face_bottom": (255, 214, 110),
                        "extrude": (24, 62, 110), "outline": (10, 26, 52),
                        "glow": (255, 230, 140), "sub": (240, 248, 255),
                        "accent": (110, 180, 255), "shade": (8, 20, 40)},
            "emerald_gold": {"face_top": (255, 250, 222), "face_bottom": (246, 206, 96),
                             "extrude": (10, 56, 42), "outline": (4, 24, 18),
                             "glow": (250, 200, 110), "sub": (240, 250, 244),
                             "accent": (72, 186, 140), "shade": (2, 18, 14)},
            "autumn_ochre": {"face_top": (255, 246, 226), "face_bottom": (240, 176, 84),
                             "extrude": (84, 36, 20), "outline": (36, 14, 8),
                             "glow": (248, 170, 90), "sub": (250, 240, 228),
                             "accent": (190, 72, 46), "shade": (26, 10, 6)},
            "lavender_honey": {"face_top": (252, 246, 255), "face_bottom": (214, 186, 246),
                               "extrude": (56, 38, 92), "outline": (24, 16, 42),
                               "glow": (222, 190, 250), "sub": (250, 246, 254),
                               "accent": (250, 200, 110), "shade": (16, 10, 30)},
            "peach_forest": {"face_top": (255, 248, 238), "face_bottom": (250, 196, 152),
                             "extrude": (22, 58, 40), "outline": (8, 24, 16),
                             "glow": (250, 190, 150), "sub": (248, 250, 244),
                             "accent": (120, 186, 120), "shade": (4, 18, 12)},
            "cream_cobalt": {"face_top": (255, 254, 246), "face_bottom": (244, 232, 186),
                             "extrude": (18, 46, 104), "outline": (8, 18, 44),
                             "glow": (246, 230, 180), "sub": (242, 248, 255),
                             "accent": (72, 136, 226), "shade": (4, 12, 32)},
        },
        "keywords": ["Healing", "Relaxing", "Dreamy", "Sleep", "Peaceful Mind",
                     "Soothing", "Spa", "Calm", "Soft Melodies", "Stress Relief",
                     "Gentle Morning"],
        "subtitles": ["{kw1} {instrument} Music", "Soothing {instrument} · {kw1} · {kw2}",
                      "{kw1} · {kw2} · {kw3}", "{hours} of {kw1} Melodies"],
        "signature": ("storybook painterly fantasy: lush hand-painted nature, luminous "
                      "clouds and gentle shafts of light"),
        "env_moods": ["lush emerald and gold", "rose and cream blossom",
                      "sky blue and sunflower yellow", "autumn crimson and ochre"],
        "grade": ("eq=saturation=1.08:gamma=1.02,colorbalance=rm=0.03:bm=-0.02,"
                  "vignette=angle=PI/6"),
        "transitions": ["softfade"],
        "blend_seconds": 2.8,
        "slow_speed": 0.7,
    },
    "bright": {
        "icon": "✨",
        "label": "Rực rỡ năng động",
        "desc": "Anime / 3D, chữ neon hoặc viền dày, màu bão hoà mạnh, chuyển cảnh trượt",
        "styles": ["anime", "3d"],
        "match": ["anime", "kawaii", "game", "pop", "edm", "synth", "happy",
                  "energy", "workout", "morning"],
        "layouts": ["left", "right", "top", "bottom"],
        "treatments": ["neon", "outline_pop", "gold3d", "banner"],
        "fonts": ["segoe_ui_black", "gill_sans_ultra", "showcard", "tw_cen_cond_xb",
                  "segoe_ui_bold", "impact", "arial_black", "elephant", "forte",
                  "wide_latin", "franklin_demi", "gill_sans_ultra_cond",
                  "trebuchet_bold"],
        "sub_fonts": ["segoe_semibold", "tw_cen", "verdana", "rockwell",
                      "gill_cond"],
        "palettes": {
            "neon_cyan": {"face_top": (235, 255, 255), "face_bottom": (110, 240, 255),
                          "extrude": (10, 40, 80), "outline": (4, 16, 36),
                          "glow": (0, 220, 255), "sub": (240, 255, 255),
                          "accent": (255, 80, 200), "shade": (4, 10, 30)},
            "candy": {"face_top": (255, 245, 250), "face_bottom": (255, 140, 200),
                      "extrude": (80, 20, 90), "outline": (40, 8, 50),
                      "glow": (255, 90, 190), "sub": (255, 250, 252),
                      "accent": (255, 220, 80), "shade": (30, 6, 36)},
            "citrus": {"face_top": (255, 255, 230), "face_bottom": (255, 210, 40),
                       "extrude": (120, 40, 10), "outline": (50, 16, 4),
                       "glow": (255, 170, 0), "sub": (255, 252, 236),
                       "accent": (60, 220, 140), "shade": (30, 12, 2)},
            "lime_violet": {"face_top": (248, 255, 230), "face_bottom": (190, 240, 80),
                            "extrude": (56, 20, 96), "outline": (26, 8, 46),
                            "glow": (176, 236, 60), "sub": (250, 255, 244),
                            "accent": (186, 110, 255), "shade": (18, 6, 34)},
            "aqua_coral": {"face_top": (236, 255, 252), "face_bottom": (120, 232, 220),
                           "extrude": (14, 60, 70), "outline": (6, 26, 32),
                           "glow": (90, 230, 220), "sub": (240, 255, 254),
                           "accent": (255, 112, 98), "shade": (4, 20, 26)},
            "magenta_gold": {"face_top": (255, 240, 250), "face_bottom": (250, 110, 190),
                             "extrude": (62, 12, 70), "outline": (28, 6, 32),
                             "glow": (252, 90, 186), "sub": (255, 246, 252),
                             "accent": (255, 212, 70), "shade": (20, 4, 24)},
            "electric_orange": {"face_top": (255, 246, 232), "face_bottom": (255, 152, 46),
                                "extrude": (18, 26, 86), "outline": (8, 12, 38),
                                "glow": (255, 140, 30), "sub": (255, 248, 240),
                                "accent": (60, 200, 255), "shade": (6, 10, 28)},
            "mint_fuchsia": {"face_top": (240, 255, 246), "face_bottom": (130, 240, 180),
                             "extrude": (74, 14, 66), "outline": (34, 6, 30),
                             "glow": (110, 238, 170), "sub": (244, 255, 248),
                             "accent": (255, 80, 180), "shade": (24, 4, 22)},
        },
        "keywords": ["Positive Energy", "Good Vibes", "Focus", "Relax", "Morning Boost",
                     "Uplifting", "Study", "Chill", "Fresh Start", "Happy Mood"],
        "subtitles": ["{kw1}! {instrument} {kw2}", "{kw1} · {kw2}",
                      "{hours} of {kw1} {instrument}", "{instrument} for {kw1} & {kw2}"],
        "signature": ("vivid key-art look: saturated complementary colours, crisp rim "
                      "light, bold clean silhouettes and a sense of uplifting energy"),
        "env_moods": ["electric cyan and magenta", "candy pink and sky blue",
                      "citrus yellow and emerald", "violet and aqua"],
        "grade": "eq=saturation=1.16:contrast=1.04",
        "transitions": ["softfade"],
        "blend_seconds": 2.6,
        "slow_speed": 0.74,
    },
}

# Mapping/project cũ chưa có look → coi như look "legacy" (chữ vàng 3D cũ) cho tới
# khi được gán. Không dùng cho kênh mới.
DEFAULT_LOOK = "painterly"


# ── Phong cách người dùng tự nhập (bảng customlook trong app.db) ─────
CUSTOM_PREFIX = "custom_"
CUSTOM_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS customlook ("
    "key TEXT PRIMARY KEY, icon TEXT DEFAULT '🎨', label TEXT NOT NULL, "
    "description TEXT DEFAULT '', signature TEXT DEFAULT '', "
    "base TEXT DEFAULT 'painterly', styles TEXT DEFAULT '[]', "
    "created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
MAX_LABEL = 40
MAX_DESC = 400
_CUSTOM_CACHE: dict = {"path": None, "looks": None}


def _db_path() -> str:
    from backend.database import DB_PATH
    return str(DB_PATH)


def invalidate_custom_looks() -> None:
    _CUSTOM_CACHE.update(path=None, looks=None)


def _resolve_custom(row: dict) -> dict:
    """Dòng customlook → look đầy đủ: nguyên liệu thumbnail + hoàn thiện video kế
    thừa look gốc; tên / mô tả / câu thẩm mỹ / kiểu ảnh là của người dùng."""
    import json
    from . import config
    base_key = row.get("base") if row.get("base") in LOOKS else DEFAULT_LOOK
    base = LOOKS[base_key]
    try:
        styles = json.loads(row.get("styles") or "[]")
    except ValueError:
        styles = []
    styles = [s for s in styles if isinstance(s, str) and s in config.STYLES]
    return {
        **base,
        "icon": (row.get("icon") or "").strip() or "🎨",
        "label": row.get("label") or row["key"],
        "desc": (row.get("description") or "").strip() or base["desc"],
        "user_desc": (row.get("description") or "").strip(),
        "styles": styles or list(base["styles"]),
        "signature": (row.get("signature") or "").strip() or base["signature"],
        "match": [],
        "base": base_key,
        "custom": True,
    }


def custom_looks() -> dict[str, dict]:
    """{key: look} các phong cách tự nhập (cache tới lần ghi kế tiếp)."""
    path = _db_path()
    if _CUSTOM_CACHE["looks"] is not None and _CUSTOM_CACHE["path"] == path:
        return _CUSTOM_CACHE["looks"]
    rows: list[dict] = []
    try:
        con = _db()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM customlook ORDER BY created_at, key").fetchall()]
        except sqlite3.OperationalError:          # chưa có bảng (DB cũ)
            rows = []
        finally:
            con.close()
    except Exception as e:
        log.warning("Đọc phong cách tự nhập lỗi: %s", e)
    looks = {r["key"]: _resolve_custom(r) for r in rows}
    _CUSTOM_CACHE.update(path=path, looks=looks)
    return looks


def _custom_look(key: str) -> Optional[dict]:
    return custom_looks().get(key) if key.startswith(CUSTOM_PREFIX) else None


def look_info(key: str | None) -> dict:
    k = (key or "").strip()
    return LOOKS.get(k) or _custom_look(k) or LOOKS[DEFAULT_LOOK]


def valid_look(key: str | None) -> bool:
    k = (key or "").strip()
    return k in LOOKS or _custom_look(k) is not None


def base_look(key: str | None) -> str:
    """Look gốc (có sẵn) của 1 look — phong cách tự nhập → look nó kế thừa."""
    k = (key or "").strip()
    if k in LOOKS:
        return k
    lk = _custom_look(k)
    return lk["base"] if lk else DEFAULT_LOOK


def look_of_style(style_key: str | None) -> Optional[str]:
    """Look CÓ SẴN sở hữu phong cách ảnh này (mỗi phong cách thuộc đúng 1 look)."""
    s = (style_key or "").strip().lower()
    return next((k for k, v in LOOKS.items() if s in v["styles"]), None)


def catalog() -> list[dict]:
    """Danh sách look cho UI (không gồm dữ liệu nội bộ dài): look có sẵn rồi tới
    phong cách tự nhập (kèm mô tả gốc + look gốc để sửa)."""
    from . import config

    def entry(key: str, lk: dict) -> dict:
        return {
            "key": key, "icon": lk["icon"], "label": lk["label"], "desc": lk["desc"],
            "styles": [{"key": s, "label": config.STYLES[s]["label"],
                        "icon": config.STYLES[s]["icon"]} for s in lk["styles"]],
            "transitions": list(lk["transitions"]),
            "custom": bool(lk.get("custom")),
            "base": lk.get("base", key),
            "user_desc": lk.get("user_desc", ""),
        }
    return ([entry(k, LOOKS[k]) for k in LOOK_ORDER]
            + [entry(k, lk) for k, lk in custom_looks().items()])


def clean_custom(label: str, description: str, base: str, styles: list[str] | None,
                  icon: str) -> dict:
    from . import config
    label = " ".join((label or "").split())
    if not label:
        raise ValueError("Nhập tên phong cách.")
    if len(label) > MAX_LABEL:
        raise ValueError(f"Tên phong cách tối đa {MAX_LABEL} ký tự.")
    description = (description or "").strip()
    if len(description) > MAX_DESC:
        raise ValueError(f"Mô tả tối đa {MAX_DESC} ký tự.")
    base = (base or "").strip() or DEFAULT_LOOK
    if base not in LOOKS:
        raise ValueError(f"Phong cách gốc '{base}' không tồn tại.")
    bad = [s for s in (styles or []) if s not in config.STYLES]
    if bad:
        raise ValueError(f"Kiểu ảnh không tồn tại: {', '.join(bad)}.")
    styles = list(dict.fromkeys(styles or [])) or list(LOOKS[base]["styles"])
    icon = (icon or "").strip()[:8] or "🎨"
    return {"label": label, "description": description, "base": base,
            "styles": styles, "icon": icon}


def create_custom_look(label: str, description: str = "", base: str = DEFAULT_LOOK,
                       styles: list[str] | None = None, icon: str = "🎨",
                       signature: str = "") -> str:
    """Thêm phong cách tự nhập → key mới. `signature` = câu thẩm mỹ TIẾNG ANH (đã
    dịch từ mô tả) gửi model ảnh; rỗng → dùng câu của look gốc."""
    import json
    import secrets
    v = clean_custom(label, description, base, styles, icon)
    con = _db()
    try:
        con.execute(CUSTOM_TABLE_SQL)
        if any(v["label"].lower() == (lk["label"] or "").lower()
               for lk in [*LOOKS.values(), *custom_looks().values()]):
            raise ValueError(f"Đã có phong cách tên «{v['label']}».")
        key = CUSTOM_PREFIX + secrets.token_hex(4)
        con.execute(
            "INSERT INTO customlook (key, icon, label, description, signature, base, styles) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, v["icon"], v["label"], v["description"], (signature or "").strip(),
             v["base"], json.dumps(v["styles"])))
        con.commit()
    finally:
        con.close()
        invalidate_custom_looks()
    return key


def update_custom_look(key: str, label: str, description: str = "",
                       base: str = DEFAULT_LOOK, styles: list[str] | None = None,
                       icon: str = "🎨", signature: Optional[str] = None) -> None:
    """Sửa phong cách tự nhập. signature=None → giữ câu tiếng Anh đã lưu."""
    import json
    if not _custom_look(key):
        raise KeyError(key)
    v = clean_custom(label, description, base, styles, icon)
    if any(k != key and v["label"].lower() == (lk["label"] or "").lower()
           for k, lk in [*LOOKS.items(), *custom_looks().items()]):
        raise ValueError(f"Đã có phong cách tên «{v['label']}».")
    con = _db()
    try:
        con.execute(
            "UPDATE customlook SET icon=?, label=?, description=?, base=?, styles=?"
            + (", signature=?" if signature is not None else "") + " WHERE key=?",
            (v["icon"], v["label"], v["description"], v["base"], json.dumps(v["styles"]),
             *(((signature or "").strip(),) if signature is not None else ()), key))
        con.commit()
    finally:
        con.close()
        invalidate_custom_looks()


def delete_custom_look(key: str) -> int:
    """Xoá phong cách tự nhập; kênh đang dùng → về look gốc của nó. Trả số kênh đổi."""
    lk = _custom_look(key)
    if not lk:
        raise KeyError(key)
    con = _db()
    try:
        cur = con.execute("UPDATE channelmapping SET visual_look=? WHERE visual_look=?",
                          (lk["base"], key))
        moved = cur.rowcount or 0
        con.execute("DELETE FROM customlook WHERE key=?", (key,))
        con.commit()
    finally:
        con.close()
        invalidate_custom_looks()
    return moved


def _text_of(mapping: dict) -> str:
    return " ".join(str(mapping.get(k) or "") for k in
                    ("instrument", "music_style", "channel_name")).lower()


def _score(look_key: str, mapping: dict) -> int:
    text = _text_of(mapping)
    return sum(1 for w in LOOKS[look_key]["match"]
               if re.search(rf"(?<![a-z]){re.escape(w)}", text))


def pick_free_look(mapping: dict, taken: set[str]) -> str:
    """Look hợp nhất với kênh trong số look CHƯA kênh nào dùng. Hết look trống
    (>5 kênh) → look ít kênh dùng nhất (vẫn khớp từ khoá nếu được)."""
    free = [k for k in LOOK_ORDER if k not in taken]
    pool = free or LOOK_ORDER
    best = max(pool, key=lambda k: (_score(k, mapping), -pool.index(k)))
    if not free:
        log.warning("Đã hết look riêng (%d kênh > %d look) — kênh '%s' dùng chung look %s.",
                    len(taken) + 1, len(LOOKS), mapping.get("channel_name"), best)
    return best


def _db():
    import sqlite3
    from backend.database import DB_PATH
    # Bản clone mới chưa có data/ → tạo thư mục để lỗi «no such table»
    # rơi vào except của nơi gọi (trả mặc định) thay vì văng lỗi mở file.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def ensure_channel_looks(con=None) -> dict[int, str]:
    """Gán look cho mọi kênh chưa có (theo thứ tự id, mỗi kênh 1 look khác nhau).
    Trả {mapping_id: look}. An toàn gọi nhiều lần."""
    own = con is None
    con = con or _db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM channelmapping ORDER BY id").fetchall()]
        if rows and "visual_look" not in rows[0]:
            return {}
        taken = {r["visual_look"] for r in rows if valid_look(r.get("visual_look"))}
        changed = False
        for r in rows:
            if valid_look(r.get("visual_look")):
                continue
            lk = pick_free_look(r, taken)
            r["visual_look"] = lk
            taken.add(lk)
            con.execute("UPDATE channelmapping SET visual_look=? WHERE id=?", (lk, r["id"]))
            changed = True
            log.info("Gán phong cách kênh '%s' → %s", r.get("channel_name"), lk)
        if changed:
            con.commit()
        return {r["id"]: r["visual_look"] for r in rows}
    except Exception as e:
        log.warning("ensure_channel_looks lỗi: %s", e)
        return {}
    finally:
        if own:
            con.close()


def channel_key(mapping: dict | None) -> str:
    """Khoá kênh cho sổ thumbnail (ổn định khi đổi tên kênh)."""
    if not mapping:
        return "none"
    return f"map{mapping.get('id')}"


def mapping_for_project(project_id: int) -> Optional[dict]:
    """ChannelMapping của project — kênh người dùng chọn (channel_id), chưa chọn thì
    theo instrument + music_style — đã có look."""
    from .service import _read_project_fields
    try:
        row = _read_project_fields(project_id)
    except Exception:
        row = {}
    if not row:
        return None
    return mapping_for(row.get("instrument") or "", row.get("music_style") or "",
                       channel_id=row.get("channel_id"),
                       music_line=row.get("music_line") or "")


def channel_name_for_project(project_id: int) -> str:
    """Tên kênh luôn có cho watermark/thumbnail.

    Project cũ hoặc track lẻ chưa gán channel_id: dùng kênh đã cấu hình đầu tiên
    thay vì in nhãn chung chung ``MUSIC CHANNEL``.
    """
    m = mapping_for_project(project_id)
    name = str((m or {}).get("channel_name") or "").strip()
    if name:
        return name
    con = _db()
    try:
        row = con.execute(
            "SELECT channel_name FROM channelmapping "
            "WHERE trim(coalesce(channel_name,'')) <> '' ORDER BY id LIMIT 1"
        ).fetchone()
    except Exception:
        row = None
    finally:
        con.close()
    return str(row["channel_name"] if row else "MUSIC CHANNEL").strip()


def mapping_for(instrument: str, music_style: str,
                channel_id: Optional[int] = None,
                music_line: str = "") -> Optional[dict]:
    from .service import _lookup_channel_by_id, _lookup_channel_mapping
    m = _lookup_channel_by_id(channel_id) if channel_id else None
    if m:
        return _with_look(m)
    if not any((x or "").strip() for x in (instrument, music_style, music_line)):
        return None
    m = _lookup_channel_mapping(instrument, music_style, music_line)
    if not m and (instrument or "").strip():
        # Chưa rõ thể loại → nếu chỉ 1 kênh cùng nhạc cụ thì đó là kênh của project.
        from .music_spec import same_instrument
        con = _db()
        try:
            rows = [dict(r) for r in con.execute("SELECT * FROM channelmapping").fetchall()
                    if same_instrument(r["instrument"] or "", instrument)]
        except Exception:
            rows = []
        finally:
            con.close()
        m = rows[0] if len(rows) == 1 else None
    return _with_look(m) if m else None


def _with_look(m: dict) -> dict:
    if not valid_look(m.get("visual_look")):
        looks = ensure_channel_looks()
        m["visual_look"] = looks.get(m.get("id"), DEFAULT_LOOK)
    return m


def look_for_project(project_id: int) -> tuple[Optional[dict], str]:
    """(mapping, look_key). Project không thuộc kênh nào → look theo phong cách
    ảnh của chính nó (để thumbnail vẫn có bộ nguyên liệu phù hợp)."""
    m = mapping_for_project(project_id)
    if m:
        return m, m["visual_look"]
    con = _db()
    try:
        row = con.execute("SELECT video_style FROM project WHERE id=?",
                          (project_id,)).fetchone()
    except Exception:
        row = None
    finally:
        con.close()
    return None, look_of_style(row["video_style"] if row else None) or DEFAULT_LOOK


def other_channel_looks(mapping_id: int | None) -> set[str]:
    con = _db()
    try:
        rows = con.execute("SELECT id, visual_look FROM channelmapping").fetchall()
    except Exception:
        return set()
    finally:
        con.close()
    return {r["visual_look"] for r in rows
            if r["id"] != mapping_id and valid_look(r["visual_look"])}


def recent_channel_styles(mapping: dict, limit: int = 6) -> list[str]:
    """Phong cách ảnh các project gần nhất của kênh (mới → cũ): project đã CHỌN
    kênh này + project cũ chưa chọn kênh nhưng khớp (nhạc cụ, thể loại)."""
    ins = (mapping.get("instrument") or "").strip().lower()
    sty = (mapping.get("music_style") or "").strip().lower()
    con = _db()
    try:
        try:
            rows = con.execute(
                "SELECT video_style FROM project WHERE deleted_at IS NULL AND "
                "(channel_id=? OR (channel_id IS NULL AND lower(trim(instrument))=? "
                "AND lower(trim(music_style))=?)) ORDER BY id DESC LIMIT ?",
                (mapping.get("id"), ins, sty, limit)).fetchall()
        except sqlite3.OperationalError:          # DB chưa có cột channel_id
            rows = con.execute(
                "SELECT video_style FROM project WHERE deleted_at IS NULL "
                "AND lower(trim(instrument))=? AND lower(trim(music_style))=? "
                "ORDER BY id DESC LIMIT ?", (ins, sty, limit)).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [r["video_style"] for r in rows if r["video_style"]]


def style_for_channel(mapping: dict, recent: list[str] | None = None,
                      offset: int = 0) -> str:
    """Phong cách ảnh cho video MỚI của kênh: xoay vòng trong các phong cách của
    look kênh, tránh lặp lại phong cách của video ngay trước (offset để lô N
    project xoay tiếp)."""
    styles = look_info(mapping.get("visual_look"))["styles"]
    recent = recent if recent is not None else recent_channel_styles(mapping)
    last = next((s for s in recent if s in styles), None)
    start = (styles.index(last) + 1) if last else 0
    return styles[(start + offset) % len(styles)]


def resolve_video_style(requested: str | None, instrument: str, music_style: str,
                        offset: int = 0, channel_id: Optional[int] = None,
                        music_line: str = "") -> str:
    """'channel'/rỗng → phong cách theo look kênh; key cụ thể → giữ nguyên."""
    from . import config
    req = (requested or "").strip().lower()
    if req and req != "channel":
        return config.normalize_style(req)
    m = mapping_for(instrument, music_style, channel_id=channel_id, music_line=music_line)
    if not m:
        return config.DEFAULT_STYLE
    return style_for_channel(m, offset=offset)


def realign_style(project_id: int, project_name: str | None, video_style: str | None,
                  instrument: str, music_style: str,
                  channel_id: Optional[int] = None) -> Optional[str]:
    """Phong cách ảnh mới khi project CHƯA có ảnh mà phong cách hiện tại lại thuộc
    phong cách của KÊNH KHÁC (để các kênh không giống nhau). None = giữ nguyên."""
    from . import config
    m = mapping_for(instrument, music_style, channel_id=channel_id)
    if not m:
        return None
    try:
        if any(config.images_dir(project_id, project_name).glob("*.png")):
            return None
    except Exception:
        return None
    if (video_style or "").strip().lower() in look_info(m.get("visual_look"))["styles"]:
        return None                               # đã là kiểu ảnh của kênh mình
    cur = look_of_style(video_style)
    if cur == m.get("visual_look") or cur not in other_channel_looks(m.get("id")):
        return None
    return style_for_channel(m)


def finish_for(look_key: str | None, recipe: dict | None = None) -> dict:
    """Cách hoàn thiện video theo look: grade ffmpeg, kiểu chuyển cảnh, blend, tốc độ."""
    lk = look_info(look_key)
    trans = (recipe or {}).get("transition")
    if trans not in lk["transitions"]:
        trans = lk["transitions"][0]
    return {"grade": lk["grade"], "transition": trans,
            "blend_seconds": float(lk["blend_seconds"]),
            "slow_speed": float(lk["slow_speed"])}


__all__ = ["LOOKS", "LOOK_ORDER", "DEFAULT_LOOK", "look_info", "valid_look",
           "base_look", "custom_looks", "create_custom_look", "update_custom_look",
           "delete_custom_look", "invalidate_custom_looks",
           "look_of_style", "catalog", "pick_free_look", "ensure_channel_looks",
           "channel_key", "mapping_for_project", "mapping_for", "look_for_project",
           "other_channel_looks", "recent_channel_styles", "style_for_channel",
           "resolve_video_style", "realign_style", "finish_for"]
