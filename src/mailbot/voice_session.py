from __future__ import annotations

import re
import time
from typing import Callable

from .chat_controller import ChatController
from .voice import (
    KeyboardSpeechPlaybackControl,
    SpeechPlaybackAction,
    SpeechPlaybackControl,
    SpeechToTextError,
    SpeechToTextProvider,
    TextToSpeechError,
    TextToSpeechProvider,
)


class VoiceSession:
    def __init__(
        self,
        controller: ChatController,
        speech_to_text: SpeechToTextProvider,
        text_to_speech: TextToSpeechProvider,
        playback_control: SpeechPlaybackControl | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.controller = controller
        self.speech_to_text = speech_to_text
        self.text_to_speech = text_to_speech
        self.playback_control = playback_control or KeyboardSpeechPlaybackControl()
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.sleep_fn = sleep_fn or time.sleep

    def run(self) -> int:
        self._emit(
            [
                "MailBot voice is ready.",
                "Press Enter to start listening, or type `exit`, `quit`, or `q` to leave.",
                "Voice mode reuses the same safe chat controller and Gmail safety rules.",
                self.playback_control.describe_controls(),
            ]
        )
        while True:
            try:
                prompt = self.input_fn("Press Enter to speak, or type exit: ").strip()
            except EOFError:
                self._emit(["Voice session ended."])
                return 0

            if prompt.lower() in {"exit", "quit", "q"}:
                self._emit(["Ending MailBot voice."])
                return 0

            self._emit(["Listening..."])
            try:
                heard_text = self.speech_to_text.transcribe_once().strip()
            except SpeechToTextError as exc:
                self._emit([f"Voice input unavailable: {exc}"])
                continue

            if not heard_text:
                self._emit(["I didn't catch that. Please try again."])
                continue

            normalized_text = _normalize_voice_input(heard_text)
            self._emit([f"You said: {heard_text}"])
            result = self.controller.handle_message(normalized_text)
            self._emit(result.messages)
            _speak_response(
                self.text_to_speech,
                result.messages,
                self.output_fn,
                self.playback_control,
                self.sleep_fn,
            )
            if result.exit_requested:
                return 0

    def _emit(self, messages: list[str]) -> None:
        for message in messages:
            self.output_fn(message)


def _normalize_voice_input(recognized_text: str) -> str:
    if recognized_text.strip().lower() == "trash":
        return "TRASH"
    return recognized_text.strip()


def _speak_response(
    text_to_speech: TextToSpeechProvider,
    messages: list[str],
    output_fn: Callable[[str], None],
    playback_control: SpeechPlaybackControl,
    sleep_fn: Callable[[float], None],
) -> None:
    chunks = _speech_chunks(messages)
    if not chunks:
        return

    paused = False
    pause_announced = False
    for chunk in chunks:
        action = playback_control.poll()
        if action == SpeechPlaybackAction.STOP:
            output_fn("Speech interrupted. The full text is still shown above.")
            return
        if action == SpeechPlaybackAction.TOGGLE_PAUSE:
            paused = not paused

        while paused:
            if not pause_announced:
                output_fn("Speech paused. Press `P` to resume or `S` to skip the rest.")
                pause_announced = True
            sleep_fn(0.1)
            action = playback_control.poll()
            if action == SpeechPlaybackAction.STOP:
                output_fn("Speech interrupted. The full text is still shown above.")
                return
            if action == SpeechPlaybackAction.TOGGLE_PAUSE:
                paused = False
                pause_announced = False
                output_fn("Speech resumed.")

        try:
            text_to_speech.speak(chunk)
        except TextToSpeechError as exc:
            output_fn(f"Voice output unavailable: {exc}")
            return


def _speech_chunks(messages: list[str], max_chars: int = 180) -> list[str]:
    chunks: list[str] = []
    for message in messages:
        cleaned = " ".join(message.strip().split())
        if not cleaned:
            continue
        for sentence in _split_into_sentences(cleaned):
            if len(sentence) <= max_chars:
                chunks.append(sentence)
                continue
            chunks.extend(_split_long_sentence(sentence, max_chars=max_chars))
    return chunks


def _split_into_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def _split_long_sentence(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(",", 0, max_chars)
        if split_at < max_chars // 2:
            split_at = remaining.rfind(" ", 0, max_chars)
        if split_at < max_chars // 2:
            split_at = max_chars
        chunk = remaining[:split_at].strip(" ,")
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip(" ,")
    return chunks
