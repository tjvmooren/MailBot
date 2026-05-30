from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mailbot.chat_controller import ChatController, ChatResponseMode
from mailbot.chat_intents import ChatIntentName, ChatReviewFilter
from mailbot.chat_providers.heuristic_intent_parser import HeuristicChatIntentParser
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


class FakeGmailClient:
    def __init__(self) -> None:
        self.trashed_ids: list[str] = []

    def trash_message(self, message_id: str) -> None:
        self.trashed_ids.append(message_id)

    def message_has_trash_label(self, message_id: str) -> bool:
        return message_id in self.trashed_ids


class FakeScanService:
    def __init__(self, output: ScanOutput) -> None:
        self.output = output

    def unread(self, limit: int | None = None) -> ScanOutput:
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


def test_heuristic_parser_routes_common_requests() -> None:
    parser = HeuristicChatIntentParser()

    unread_intent = parser.parse_intent("show my unread emails")
    search_intent = parser.parse_intent("find recent Microsoft emails")
    review_intent = parser.parse_intent("show only trash candidates")
    trash_intent = parser.parse_intent("trash item 3")

    assert unread_intent.intent == ChatIntentName.UNREAD
    assert search_intent.intent == ChatIntentName.SEARCH
    assert search_intent.gmail_query == "from:microsoft newer_than:30d"
    assert review_intent.intent == ChatIntentName.REVIEW
    assert review_intent.review_filter == ChatReviewFilter.TRASH_CANDIDATES
    assert trash_intent.intent == ChatIntentName.TRASH
    assert trash_intent.selected_ids == [3]


def test_chat_controller_refuses_unsupported_requests_safely() -> None:
    controller = ChatController(
        service=object(),  # type: ignore[arg-type]
        intent_parser=HeuristicChatIntentParser(),
        output_fn=lambda _: None,
    )

    result = controller.process_message("draft a reply to that email")

    assert len(result.messages) == 1
    assert "cannot send, draft, reply, archive, or permanently delete" in result.messages[0]


def test_chat_controller_requires_exact_trash_confirmation(tmp_path: Path) -> None:
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

    preview_result = controller.handle_message("trash item 1")
    yes_result = controller.handle_message("yes")
    execute_result = controller.handle_message("TRASH")

    assert fake_gmail.trashed_ids == ["gmail-123"]
    assert any("type `TRASH`" in message for message in preview_result.messages)
    assert any(
        "cannot send, draft, reply, archive, or permanently delete" in message
        or "I can help with Gmail auth" in message
        for message in yes_result.messages
    )
    assert any("Moved count: 1" in message for message in execute_result.messages)


def test_chat_mode_keeps_full_detailed_output() -> None:
    output = ScanOutput(
        command="unread",
        query="is:unread -in:trash",
        session_id=None,
        messages=[_analyzed_message()],
    )
    controller = ChatController(
        service=FakeScanService(output),  # type: ignore[arg-type]
        intent_parser=HeuristicChatIntentParser(),
        output_fn=lambda _: None,
    )

    result = controller.handle_message(
        "show my unread emails",
        response_mode=ChatResponseMode.CHAT,
    )

    assert any("[1] TRASH_CANDIDATE | Deals Bot | Flash sale" in message for message in result.messages)
    assert any("category=promotional, importance=low" in message for message in result.messages)
    assert any("summary=Promotional sale email." in message for message in result.messages)
