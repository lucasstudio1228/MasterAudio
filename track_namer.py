"""Đặt tên track: gọi OpenAI API, tự động fallback rule-based khi lỗi/không có key."""
import json
import random

from openai import OpenAI

import console  # noqa: F401 - bật UTF-8 output khi import
from api_config import APIConfig

_FALLBACK_MODEL = "gpt-4o-mini"

ADJECTIVES = ["Silent", "Deep", "Soft", "Still", "Gentle", "Clear", "Warm",
              "Flowing", "Sacred", "Ancient", "Golden", "Misty", "Serene",
              "Quiet", "Luminous", "Drifting", "Vast", "Pure"]
NOUNS = ["River", "Forest", "Mountain", "Lake", "Valley", "Sky", "Dawn",
         "Dusk", "Garden", "Temple", "Meadow", "Shore", "Path", "Breath",
         "Horizon", "Silence", "Current", "Bloom"]


def _fallback_names(count: int) -> list[str]:
    """Không cần API: tổ hợp adjective + noun, đảm bảo đủ và unique."""
    names: set[str] = set()
    attempts = 0
    while len(names) < count and attempts < count * 20:
        names.add(f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}")
        attempts += 1
    # Nếu vẫn thiếu (count > số tổ hợp): thêm suffix số
    suffix = 1
    while len(names) < count:
        names.add(f"Inner Peace {suffix}")
        suffix += 1
    return list(names)[:count]


def generate_names(count: int, config: APIConfig) -> list[str]:
    """Trả về đúng `count` tên track unique."""
    return generate_names_with_source(count, config)[0]


def generate_names_with_source(count: int, config: APIConfig) -> tuple[list[str], str]:
    """Như generate_names nhưng kèm nguồn ('OpenAI API' | 'rule-based') để hiển thị."""
    if count <= 0:
        return [], "rule-based"

    if config.is_configured():
        try:
            client = OpenAI(**config.to_client_kwargs())
            model = config.model
            if not model or model.lower().startswith("claude"):
                model = _FALLBACK_MODEL
            response = client.chat.completions.create(
                model=model,
                max_tokens=max(1000, count * 25),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Generate exactly {count} unique meditation track names in English.\n"
                        "Rules:\n"
                        "- Each name: 2–4 evocative words\n"
                        "- Themes: nature, stillness, light, depth, water, space, breath\n"
                        "- NO duplicates, NO numbering, NO quotes inside\n"
                        "- Return ONLY a valid JSON array of strings.\n"
                        "- No markdown, no preamble, no explanation.\n"
                        f'Example: ["Morning Stillness", "Deep River Flow", ...]'
                    )
                }]
            )
            text = (response.choices[0].message.content or "").strip()
            # Strip markdown code fences nếu có
            text = text.replace("```json", "").replace("```", "").strip()
            names = json.loads(text)
            if (isinstance(names, list) and
                    all(isinstance(n, str) for n in names) and
                    len(names) >= count and
                    len(set(names)) >= count):
                # preserve order, dedupe
                return list(dict.fromkeys(names))[:count], "OpenAI API"
            print(f"  ⚠️  API trả về {len(names) if isinstance(names, list) else '?'} "
                  f"tên không hợp lệ, dùng fallback rule-based")
        except Exception as e:                       # noqa: BLE001 - naming không được chặn render
            print(f"  ⚠️  API naming failed ({e}), dùng fallback rule-based")

    return _fallback_names(count), "rule-based"
