from __future__ import annotations

import msvcrt

from .base import SpeechPlaybackAction, SpeechPlaybackControl


class KeyboardSpeechPlaybackControl(SpeechPlaybackControl):
    def describe_controls(self) -> str:
        return "While MailBot is speaking, press `P` to pause/resume or `S` to skip the rest."

    def poll(self) -> SpeechPlaybackAction:
        while msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in {"p", "P"}:
                return SpeechPlaybackAction.TOGGLE_PAUSE
            if key in {"s", "S"}:
                return SpeechPlaybackAction.STOP
        return SpeechPlaybackAction.CONTINUE
