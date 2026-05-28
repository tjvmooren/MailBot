from __future__ import annotations

from abc import ABC, abstractmethod

from mailbot.exceptions import MailBotError
from mailbot.models import MessageClassification, ParsedMessage


class LLMProviderError(MailBotError):
    """Raised when a provider cannot classify a message."""


class LLMProvider(ABC):
    @abstractmethod
    def classify_message(self, message: ParsedMessage) -> MessageClassification:
        """Return a structured classification for a parsed email."""
