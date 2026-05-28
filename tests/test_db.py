from datetime import datetime, timezone

from mailbot.db import SessionStore
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


def test_session_store_persists_actionable_results(tmp_path) -> None:
    store = SessionStore(tmp_path / "mailbot.db")
    parsed = ParsedMessage(
        message_id="gmail-123",
        thread_id="thread-123",
        sender="Deals Bot",
        sender_email="deals@example.com",
        subject="Flash sale",
        received_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        snippet="Save 20% today.",
        body_preview="Save 20% today on limited-time offers.",
    )
    classification = MessageClassification(
        category=MailCategory.PROMOTIONAL,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.LOW,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary="Promotional sale email.",
        rationale="Low-value bulk marketing message.",
    )
    analyzed = AnalyzedMessage(
        parsed=parsed,
        classification=classification,
        protection=ProtectionCheck(reasons=[]),
        final_recommendation=Recommendation.TRASH_CANDIDATE,
    )

    session_id = store.save_session(
        command="cleanup",
        query="is:unread -in:trash",
        results=[analyzed],
        actionable=True,
    )

    latest_session = store.get_latest_actionable_session()
    rows = store.get_session_results(session_id, display_ids=[1])

    assert latest_session is not None
    assert latest_session.session_id == session_id
    assert latest_session.command == "cleanup"
    assert rows[0].allow_trash is True
    assert rows[0].subject == "Flash sale"

    store.mark_trashed(session_id, [1])
    updated_rows = store.get_session_results(session_id, display_ids=[1])
    assert updated_rows[0].trashed_at is not None
