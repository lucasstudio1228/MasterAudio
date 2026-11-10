"""Cấu hình endpoint OpenAI API (GPT) tập trung một chỗ.

Trước đây dùng Anthropic (Claude); nay chuyển sang OpenAI GPT. Vẫn đọc được env
var cũ (ANTHROPIC_*) để tương thích ngược, ưu tiên OPENAI_*.
"""
import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def _env(*names: str) -> str | None:
    """Trả về env var đầu tiên có giá trị trong danh sách tên."""
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


@dataclass
class APIConfig:
    api_key: str | None = field(
        default_factory=lambda: _env("OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
    base_url: str = field(
        default_factory=lambda: _env("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")
        or DEFAULT_BASE_URL)
    model: str = field(
        default_factory=lambda: _env("OPENAI_MODEL", "ANTHROPIC_MODEL")
        or DEFAULT_MODEL)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def to_client_kwargs(self) -> dict:
        """Return kwargs để pass vào openai.OpenAI().

        SDK OpenAI nối '/chat/completions' vào base_url, nên base_url PHẢI có
        sẵn '/v1' (vd 'https://api.openai.com/v1' hoặc proxy '.../v1').
        """
        kwargs = {"base_url": (self.base_url or DEFAULT_BASE_URL)}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return kwargs

    @classmethod
    def from_cli(
        cls,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> "APIConfig":
        """CLI option thắng env var; env var thắng default."""
        cfg = cls()
        if api_key:
            cfg.api_key = api_key
        if base_url:
            cfg.base_url = base_url
        if model:
            cfg.model = model
        return cfg
