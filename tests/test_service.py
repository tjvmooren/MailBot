from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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


class FakeGmailClient:
    def __init__(self) -> None:
        self.trashed_ids: list[str] = []
        self.trash_labels: dict[str, list[str]] = {}
        self.verification_sequences: dict[str, list[bool]] = {}
        self.verification_calls: dict[str, int] = {}

    def trash_message(self, message_id: str) -> None:
        self.trashed_ids.append(message_id)
        self.trash_labels[message_id] = ["TRASH"]

    def message_has_trash_label(self, message_id: str) -> bool:
        self.verification_calls[message_id] = (
            self.verification_calls.get(message_id, 0) + 1
        )
        sequence = self.verification_sequences.get(message_id)
        if sequence:
            return sequence.pop(0)
        return "TRASH" in self.trash_labels.get(message_id, [])


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
    sender_email: str = "deals@example.com",
    subject: str = "Flash sale",
    category: MailCategory = MailCategory.PROMOTIONAL,
    sender_type: SenderType = SenderType.AUTOMATED_SYSTEM,
    importance: ImportanceLevel = ImportanceLevel.LOW,
    model_recommendation: Recommendation = Recommendation.TRASH_CANDIDATE,
    final_recommendation: Recommendation = Recommendation.TRASH_CANDIDATE,
    protected_reasons: list[str] | None = None,
    has_attachments: bool = False,
    attachment_names: list[str] | None = None,
) -> AnalyzedMessage:
    parsed = ParsedMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender=sender,
        sender_email=sender_email,
        subject=subject,
        received_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        snippet="Snippet",
        body_preview="Body preview",
        has_attachments=has_attachments,
        attachment_names=attachment_names or [],
    )
    classification = MessageClassification(
        category=category,
        sender_type=sender_type,
        importance=importance,
        trash_recommendation=model_recommendation,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary=f"Summary for {subject}",
        rationale=f"Rationale for {subject}",
    )
    return AnalyzedMessage(
        parsed=parsed,
        classification=classification,
        protection=ProtectionCheck(reasons=protected_reasons or []),
        final_recommendation=final_recommendation,
    )


def test_review_reads_latest_actionable_session_from_sqlite_only(tmp_path: Path) -> None:
    service = MailBotService(_config(tmp_path))
    service.gmail = FakeGmailClient()

    keep_message = _analyzed_message(
        message_id="gmail-keep",
        subject="Security alert",
        category=MailCategory.SECURITY_ACCOUNT,
        importance=ImportanceLevel.HIGH,
        model_recommendation=Recommendation.KEEP,
        final_recommendation=Recommendation.KEEP,
        protected_reasons=["security/account alert"],
    )
    trash_message = _analyzed_message(message_id="gmail-trash", subject="Sale today")

    service.store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[trash_message, keep_message],
        actionable=True,
    )

    review_all = service.review()
    review_trash = service.review(filter_name="trash_candidates")

    assert review_all.session.command == "cleanup"
    assert [item.display_id for item in review_all.results] == [1, 2]
    assert len(review_trash.results) == 1
    assert review_trash.results[0].gmail_message_id == "gmail-trash"


def test_move_to_trash_verifies_by_gmail_message_id(tmp_path: Path) -> None:
    service = MailBotService(_config(tmp_path))
    fake_gmail = FakeGmailClient()
    service.gmail = fake_gmail

    trash_message = _analyzed_message(message_id="gmail-trash", subject="Promo")
    blocked_message = _analyzed_message(
        message_id="gmail-blocked",
        subject="Job note",
        category=MailCategory.JOB_EMPLOYER,
        sender_type=SenderType.HUMAN_INDIVIDUAL,
        model_recommendation=Recommendation.REVIEW,
        final_recommendation=Recommendation.REVIEW,
        protected_reasons=["job/employer message"],
    )
    service.store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[trash_message, blocked_message],
        actionable=True,
    )

    preview = service.prepare_trash([1, 2])
    result = service.move_to_trash(preview)
    stored_rows = service.store.get_session_results(preview.session.session_id, [1])

    assert preview.requested_count == 2
    assert preview.eligible_count == 1
    assert len(preview.blocked) == 1
    assert fake_gmail.trashed_ids == ["gmail-trash"]
    assert len(result.moved) == 1
    assert len(result.verified) == 1
    assert result.failures == []
    assert stored_rows[0].trashed_at is not None


def test_move_to_trash_retries_verification_without_sleeping_in_tests(
    tmp_path: Path,
) -> None:
    sleep_calls: list[float] = []
    service = MailBotService(_config(tmp_path), verification_sleep=sleep_calls.append)
    fake_gmail = FakeGmailClient()
    fake_gmail.verification_sequences["gmail-retry"] = [False, False, True]
    service.gmail = fake_gmail

    retry_message = _analyzed_message(message_id="gmail-retry", subject="Retry promo")
    service.store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[retry_message],
        actionable=True,
    )

    preview = service.prepare_trash([1])
    result = service.move_to_trash(preview)

    assert len(result.moved) == 1
    assert len(result.verified) == 1
    assert result.failures == []
    assert sleep_calls == [1.0, 2.0]
    assert fake_gmail.verification_calls["gmail-retry"] == 3


def test_move_to_trash_reports_verification_failure_after_retries(
    tmp_path: Path,
) -> None:
    sleep_calls: list[float] = []
    service = MailBotService(_config(tmp_path), verification_sleep=sleep_calls.append)
    fake_gmail = FakeGmailClient()
    fake_gmail.verification_sequences["gmail-never"] = [False, False, False]
    service.gmail = fake_gmail

    retry_message = _analyzed_message(message_id="gmail-never", subject="Never verify")
    service.store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[retry_message],
        actionable=True,
    )

    preview = service.prepare_trash([1])
    result = service.move_to_trash(preview)

    assert len(result.moved) == 1
    assert len(result.verified) == 0
    assert len(result.failures) == 1
    assert result.failures[0].stage == "verify"
    assert "after 3 verification attempts" in result.failures[0].error
    assert sleep_calls == [1.0, 2.0]
