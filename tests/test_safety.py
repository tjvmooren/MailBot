from datetime import datetime, timezone

from mailbot.models import (
    ImportanceLevel,
    MailCategory,
    MessageClassification,
    ParsedMessage,
    Recommendation,
    SenderType,
)
from mailbot.safety import evaluate_protection, finalize_recommendation


def _message(**overrides: object) -> ParsedMessage:
    defaults = {
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "sender": "Alex Recruiter",
        "sender_email": "alex.recruiter@example.com",
        "subject": "Interview next steps",
        "received_at": datetime(2026, 5, 28, tzinfo=timezone.utc),
        "snippet": "Please confirm your interview availability.",
        "body_preview": "Please confirm your interview availability before Friday.",
        "headers": {},
    }
    defaults.update(overrides)
    return ParsedMessage(**defaults)


def test_protected_job_email_downgrades_trash_recommendation() -> None:
    classification = MessageClassification(
        category=MailCategory.JOB_EMPLOYER,
        sender_type=SenderType.HUMAN_INDIVIDUAL,
        importance=ImportanceLevel.HIGH,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=True,
        contains_action_required_language=True,
        summary="Interview scheduling email.",
        rationale="The email concerns hiring and needs review.",
    )

    protection = evaluate_protection(_message(), classification)

    assert protection.blocked
    assert "job/employer message" in protection.reasons
    assert "personal human email" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW


def test_safe_newsletter_can_remain_trash_candidate() -> None:
    message = _message(
        sender="Weekly Deals",
        sender_email="newsletter@example.com",
        subject="Weekly deals roundup",
        snippet="Top offers from this week.",
        body_preview="Browse this week's top offers and unsubscribe anytime.",
        headers={"list-unsubscribe": "<mailto:unsubscribe@example.com>"},
    )
    classification = MessageClassification(
        category=MailCategory.NEWSLETTER,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.LOW,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary="Weekly promotional newsletter.",
        rationale="Bulk marketing content with low importance.",
    )

    protection = evaluate_protection(message, classification)

    assert not protection.blocked
    assert (
        finalize_recommendation(classification, protection)
        == Recommendation.TRASH_CANDIDATE
    )
