from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from mailbot.chat_controller import ChatController, ChatResponseMode, ChatTurnResult
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
from mailbot.service import MailBotService, ScanOutput
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
        self.messages: list[tuple[str, ChatResponseMode]] = []

    def handle_message(
        self,
        user_text: str,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> ChatTurnResult:
        self.messages.append((user_text, response_mode))
        return ChatTurnResult(messages=[f"assistant heard: {user_text}"])


class FakeScanService:
    def __init__(self, output: ScanOutput) -> None:
        self.output = output
        self.calls: list[tuple[str, int | None]] = []

    def unread(self, limit: int | None = None) -> ScanOutput:
        self.calls.append(("unread", limit))
        return self.output

    def important(self, limit: int | None = None) -> ScanOutput:
        self.calls.append(("important", limit))
        return self.output

    def cleanup(self, limit: int | None = None) -> ScanOutput:
        self.calls.append(("cleanup", limit))
        return self.output


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


def _analyzed_message(
    *,
    message_id: str = "gmail-123",
    sender: str = "Deals Bot",
    subject: str = "Flash sale",
    summary: str = "Promotional sale email.",
    category: MailCategory = MailCategory.PROMOTIONAL,
    importance: ImportanceLevel = ImportanceLevel.LOW,
    final_recommendation: Recommendation = Recommendation.TRASH_CANDIDATE,
    protected_reasons: list[str] | None = None,
) -> AnalyzedMessage:
    parsed = ParsedMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender=sender,
        sender_email="deals@example.com",
        subject=subject,
        received_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        snippet="Save today.",
        body_preview="Exclusive offer. Shop now.",
    )
    classification = MessageClassification(
        category=category,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=importance,
        trash_recommendation=final_recommendation,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary=summary,
        rationale="Bulk marketing content.",
    )
    return AnalyzedMessage(
        parsed=parsed,
        classification=classification,
        protection=ProtectionCheck(reasons=protected_reasons or []),
        final_recommendation=final_recommendation,
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
    assert controller.messages == [("show my unread emails", ChatResponseMode.VOICE)]
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

    def _handle_message(
        user_text: str,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> ChatTurnResult:
        controller.messages.append((user_text, response_mode))
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

    def _handle_message(
        user_text: str,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> ChatTurnResult:
        controller.messages.append((user_text, response_mode))
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


def test_chat_controller_summarizes_long_results_in_voice_mode() -> None:
    output = ScanOutput(
        command="cleanup",
        query="is:unread -in:trash",
        session_id=7,
        messages=[
            _analyzed_message(
                message_id="gmail-1",
                sender="Deals Bot",
                subject="Flash sale",
                summary="Promotional sale email.",
                final_recommendation=Recommendation.TRASH_CANDIDATE,
            ),
            _analyzed_message(
                message_id="gmail-2",
                sender="Weekly News",
                subject="Digest",
                summary="Newsletter needs a quick manual look.",
                category=MailCategory.NEWSLETTER,
                final_recommendation=Recommendation.REVIEW,
                importance=ImportanceLevel.MEDIUM,
            ),
            _analyzed_message(
                message_id="gmail-3",
                sender="Security Team",
                subject="Account alert",
                summary="Security alert should be kept.",
                category=MailCategory.SECURITY_ACCOUNT,
                final_recommendation=Recommendation.KEEP,
                importance=ImportanceLevel.HIGH,
                protected_reasons=["security/account alert"],
            ),
            _analyzed_message(
                message_id="gmail-4",
                sender="Coupons Daily",
                subject="Save today",
                summary="Another safe promotional cleanup candidate.",
                final_recommendation=Recommendation.TRASH_CANDIDATE,
            ),
        ],
    )
    controller = ChatController(
        service=FakeScanService(output),  # type: ignore[arg-type]
        intent_parser=HeuristicChatIntentParser(),
        output_fn=lambda _: None,
    )

    result = controller.handle_message(
        "what can I safely clean up?",
        response_mode=ChatResponseMode.VOICE,
    )

    assert any("Live Gmail fetch and classification complete." in message for message in result.messages)
    assert any("Recommendation only. No messages were moved or deleted." in message for message in result.messages)
    assert any("Total: 4. Important or protected: 1. Review: 1. Trash candidates: 2." in message for message in result.messages)
    assert any("Top items:" in message for message in result.messages)
    assert any("Say details to hear more." in message for message in result.messages)
    assert not any("category=" in message for message in result.messages)


def test_voice_follow_up_commands_use_cached_results_without_new_service_calls() -> None:
    output = ScanOutput(
        command="cleanup",
        query="is:unread -in:trash",
        session_id=8,
        messages=[
            _analyzed_message(
                message_id="gmail-1",
                sender="Deals Bot",
                subject="Flash sale",
                summary="Promotional sale email.",
                final_recommendation=Recommendation.TRASH_CANDIDATE,
            ),
            _analyzed_message(
                message_id="gmail-2",
                sender="Weekly News",
                subject="Digest",
                summary="Newsletter needs a quick manual look.",
                category=MailCategory.NEWSLETTER,
                final_recommendation=Recommendation.REVIEW,
                importance=ImportanceLevel.MEDIUM,
            ),
            _analyzed_message(
                message_id="gmail-3",
                sender="Security Team",
                subject="Account alert",
                summary="Security alert should be kept.",
                category=MailCategory.SECURITY_ACCOUNT,
                final_recommendation=Recommendation.KEEP,
                importance=ImportanceLevel.HIGH,
                protected_reasons=["security/account alert"],
            ),
            _analyzed_message(
                message_id="gmail-4",
                sender="Coupons Daily",
                subject="Save today",
                summary="Another safe promotional cleanup candidate.",
                final_recommendation=Recommendation.TRASH_CANDIDATE,
            ),
        ],
    )
    service = FakeScanService(output)
    controller = ChatController(
        service=service,  # type: ignore[arg-type]
        intent_parser=HeuristicChatIntentParser(),
        output_fn=lambda _: None,
    )

    controller.handle_message("what can I safely clean up?", response_mode=ChatResponseMode.VOICE)
    details = controller.handle_message("details", response_mode=ChatResponseMode.VOICE)
    next_result = controller.handle_message("next", response_mode=ChatResponseMode.VOICE)
    candidates = controller.handle_message("candidates", response_mode=ChatResponseMode.VOICE)
    important = controller.handle_message("important", response_mode=ChatResponseMode.VOICE)
    stop = controller.handle_message("stop", response_mode=ChatResponseMode.VOICE)
    after_stop = controller.handle_message("next", response_mode=ChatResponseMode.VOICE)

    assert service.calls == [("cleanup", None)]
    assert any("Showing 1 through 3 of 4." in message for message in details.messages)
    assert any("Say next to hear more." in message for message in details.messages)
    assert any("Showing 4 through 4 of 4." in message for message in next_result.messages)
    assert any("Showing 1 through 2 of 2." in message for message in candidates.messages)
    assert all("trash candidate" in message.lower() or "cleanup candidates" in message.lower() or "showing" in message.lower() or "end of the current list" in message.lower() for message in candidates.messages)
    assert any("Security Team" in message for message in important.messages)
    assert any("Okay. I will stop there." in message for message in stop.messages)
    assert any("I do not have recent voice results" in message for message in after_stop.messages)
