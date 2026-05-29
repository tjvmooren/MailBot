from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from mailbot.exceptions import MailBotError


class VoiceError(MailBotError):
    """Base error for MailBot voice mode."""


class SpeechToTextError(VoiceError):
    """Raised when speech-to-text fails or is unavailable."""


class TextToSpeechError(VoiceError):
    """Raised when text-to-speech fails or is unavailable."""


class SpeechToTextProvider(ABC):
    @abstractmethod
    def transcribe_once(self) -> str:
        """Capture one utterance and return recognized text."""


class TextToSpeechProvider(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak assistant text aloud."""


class SpeechPlaybackAction(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    TOGGLE_PAUSE = "toggle_pause"


class SpeechPlaybackControl(ABC):
    @abstractmethod
    def describe_controls(self) -> str:
        """Return a short user-facing description of speech playback controls."""

    @abstractmethod
    def poll(self) -> SpeechPlaybackAction:
        """Return any pending playback control action."""
