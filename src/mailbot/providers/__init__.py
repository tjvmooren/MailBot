from __future__ import annotations

import os

from mailbot.config import AppConfig

from .base import LLMProvider, LLMProviderError
from .openai_provider import OpenAIProvider


def build_provider(config: AppConfig) -> LLMProvider:
    provider_name = config.provider.lower()
    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMProviderError("OPENAI_API_KEY is not set.")
        return OpenAIProvider(api_key=api_key, model=config.openai.model)
    raise LLMProviderError(
        f"Unsupported provider `{config.provider}`. Add an implementation under src/mailbot/providers/."
    )


__all__ = ["LLMProvider", "LLMProviderError", "build_provider"]
