from __future__ import annotations

import os

from mailbot.config import AppConfig

from .base import ChatIntentParser
from .heuristic_intent_parser import HeuristicChatIntentParser
from .openai_intent_parser import OpenAIChatIntentParser


class HybridChatIntentParser(ChatIntentParser):
    def __init__(
        self,
        primary: ChatIntentParser | None,
        fallback: ChatIntentParser,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        if primary is None:
            self.description = fallback.description
        else:
            self.description = f"{primary.description} with {fallback.description} fallback"

    def parse_intent(self, user_message: str):
        if self.primary is not None:
            try:
                return self.primary.parse_intent(user_message)
            except Exception:
                pass
        return self.fallback.parse_intent(user_message)


def build_chat_intent_parser(config: AppConfig) -> ChatIntentParser:
    fallback = HeuristicChatIntentParser()
    if config.provider.lower() != "openai":
        return fallback

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback

    try:
        primary = OpenAIChatIntentParser(api_key=api_key, model=config.openai.model)
    except Exception:
        return fallback
    return HybridChatIntentParser(primary=primary, fallback=fallback)
