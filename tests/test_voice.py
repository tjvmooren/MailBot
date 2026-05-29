from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from mailbot.chat_controller import ChatController, ChatTurnResult
from mailbot.chat_providers.heuristic_intent_parser import HeuristicChatIntentParser
from mailbot.voice import SpeechPlaybackAction
from mailbot.config import (
    AppConfig,
    GmailSettings,
    LimitSettings,
    OpenAISettings,
    QuerySettings,
)
from mailbot.models import (
    AnalyzedMessage,
    ImportanceLevel,
    MailCategory,
    MessageClassification,
    ParsedMessage,
    ProtectionCheck,
    Recommendation,
    SenderType,
)
from mailbot.service import MailBotService
from mailbot.voice_session import VoiceSession


class FakeGmailClient:
    def __init__(self) -> None:
        self.trashed_ids: list[str] = []

    def trash_message(self, message_id: str) -> None:
        self.trashed_ids.append(message_id)

    def message_has_trash_label(self, message_id: str) -> bool:
        return message_id in self.trashed_ids


class FakeSpeechToText:
    def __init__(self, phrases: list[str]) -> None:
        self.phrases = deque(phrases)

    def transcribe_once(self) -> str:
        return self.phrases.popleft()


class FakeTextToSpeech:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakePlaybackControl:
    def __init__(self, actions: list[SpeechPlaybackAction] | None = None) -> None:
        self.actions = deque(actions or [])

    def describe_controls(self) -> str:
        return "fake controls"

    def poll(self) -> SpeechPlaybackAction:
        if self.actions:
            return self.actions.popleft()
        return SpeechPlaybackAction.CONTINUE


class SpyController:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def handle_message(self, user_text: str) -> ChatTurnResult:
        self.messages.append(user_text)
        return ChatTurnResult(messages=[f"assistant heard: {user_text}"])


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        provider="openai",
        database_path=tmp_path / "mailbot.db",
        gmail=GmailSettings(
            credentials_path=tmp_path / "credentials.json",
            token_path=tmp_path / "token.json",
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
        ),
        queries=QuerySettings(
            unread="is:unread -in:trash",
            important="is:important -in:trash",
            cleanup="is:unread -in:trash",
        ),
        limits=LimitSettings(default_list_limit=10, body_preview_chars=600),
        openai=OpenAISettings(model="gpt-5-mini"),
    )


def _analyzed_message() -> AnalyzedMessage:
    parsed = ParsedMessage(
        message_id="gmail-123",
        thread_id="thread-123",
        sender="Deals Bot",
        sender_email="deals@example.com",
        subject="Flash sale",
        received_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        snippet="Save today.",
        body_preview="Exclusive offer. Shop now.",
    )
    classification = MessageClassification(
        category=MailCategory.PROMOTIONAL,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.LOW,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary="Promotional sale email.",
        rationale="Bulk marketing content.",
    )
    return AnalyzedMessage(
        parsed=parsed,
        classification=classification,
        protection=ProtectionCheck(reasons=[]),
        final_recommendation=Recommendation.TRASH_CANDIDATE,
    )


def test_voice_session_uses_transport_neutral_handle_message() -> None:
    outputs: list[str] = []
    controller = SpyController()
    stt = FakeSpeechToText(["show my unread emails"])
    tts = FakeTextToSpeech()
    prompts = iter(["", "exit"])

    session = VoiceSession(
        controller=controller,  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        playback_control=FakePlaybackControl(),
        input_fn=lambda _prompt: next(prompts),
        output_fn=outputs.append,
        sleep_fn=lambda _: None,
    )

    exit_code = session.run()

    assert exit_code == 0
    assert controller.messages == ["show my unread emails"]
    assert any("You said: show my unread emails" in message for message in outputs)
    assert tts.spoken == ["assistant heard: show my unread emails"]


def test_voice_session_requires_trash_confirmation_word(tmp_path) -> None:
    outputs: list[str] = []
    service = MailBotService(_config(tmp_path), verification_sleep=lambda _: None)
    fake_gmail = FakeGmailClient()
    service.gmail = fake_gmail
    service.store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[_analyzed_message()],
        actionable=True,
    )
    controller = ChatController(
        service=service,
        intent_parser=HeuristicChatIntentParser(),
        output_fn=lambda _: None,
    )
    stt = FakeSpeechToText(["trash item 1", "yes", "trash"])
    tts = FakeTextToSpeech()
    prompts = iter(["", "", "", "exit"])

    session = VoiceSession(
        controller=controller,
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        playback_control=FakePlaybackControl(),
        input_fn=lambda _prompt: next(prompts),
        output_fn=outputs.append,
        sleep_fn=lambda _: None,
    )

    exit_code = session.run()

    assert exit_code == 0
    assert fake_gmail.trashed_ids == ["gmail-123"]
    assert any("Confirmation phrase required: TRASH" in message for message in outputs)
    assert any("Moved count: 1" in message for message in outputs)


def test_voice_session_can_interrupt_remaining_spoken_output() -> None:
    outputs: list[str] = []
    controller = SpyController()
    controller_result = ChatTurnResult(
        messages=[
            "First sentence.",
            "Second sentence that should not be spoken.",
        ]
    )

    def _handle_message(user_text: str) -> ChatTurnResult:
        controller.messages.append(user_text)
        return controller_result

    controller.handle_message = _handle_message  # type: ignore[method-assign]
    stt = FakeSpeechToText(["show me something"])
    tts = FakeTextToSpeech()
    prompts = iter(["", "exit"])
    playback = FakePlaybackControl(
        actions=[SpeechPlaybackAction.CONTINUE, SpeechPlaybackAction.STOP]
    )

    session = VoiceSession(
        controller=controller,  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        playback_control=playback,
        input_fn=lambda _prompt: next(prompts),
        output_fn=outputs.append,
        sleep_fn=lambda _: None,
    )

    exit_code = session.run()

    assert exit_code == 0
    assert tts.spoken == ["First sentence."]
    assert any("Speech interrupted." in message for message in outputs)


def test_voice_session_can_pause_and_resume_spoken_output() -> None:
    outputs: list[str] = []
    controller = SpyController()
    controller_result = ChatTurnResult(messages=["First sentence.", "Second sentence."])

    def _handle_message(user_text: str) -> ChatTurnResult:
        controller.messages.append(user_text)
        return controller_result

    controller.handle_message = _handle_message  # type: ignore[method-assign]
    stt = FakeSpeechToText(["show me something"])
    tts = FakeTextToSpeech()
    prompts = iter(["", "exit"])
    playback = FakePlaybackControl(
        actions=[
            SpeechPlaybackAction.CONTINUE,
            SpeechPlaybackAction.TOGGLE_PAUSE,
            SpeechPlaybackAction.TOGGLE_PAUSE,
            SpeechPlaybackAction.CONTINUE,
        ]
    )

    session = VoiceSession(
        controller=controller,  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        playback_control=playback,
        input_fn=lambda _prompt: next(prompts),
        output_fn=outputs.append,
        sleep_fn=lambda _: None,
    )

    exit_code = session.run()

    assert exit_code == 0
    assert tts.spoken == ["First sentence.", "Second sentence."]
    assert any("Speech paused." in message for message in outputs)
    assert any("Speech resumed." in message for message in outputs)
