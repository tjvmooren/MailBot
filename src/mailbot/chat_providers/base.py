from __future__ import annotations

from abc import ABC, abstractmethod

from mailbot.chat_intents import ChatIntent
from mailbot.exceptions import MailBotError


class ChatIntentParserError(MailBotError):
    """Raised when conversational intent parsing fails."""


class ChatIntentParser(ABC):
    description = "unknown"

    @abstractmethod
    def parse_intent(self, user_message: str) -> ChatIntent:
        """Return a constrained chat intent for a user message."""
