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


def _classification(**overrides: object) -> MessageClassification:
    defaults = {
        "category": MailCategory.OTHER,
        "sender_type": SenderType.UNKNOWN,
        "importance": ImportanceLevel.MEDIUM,
        "trash_recommendation": Recommendation.REVIEW,
        "contains_deadline_or_offer": False,
        "contains_action_required_language": False,
        "summary": "Message requires review.",
        "rationale": "Default test classification.",
    }
    defaults.update(overrides)
    return MessageClassification(**defaults)


def test_promotional_sale_with_marketing_urgency_can_remain_trash_candidate() -> None:
    message = _message(
        sender="Weekly Deals",
        sender_email="newsletter@example.com",
        subject="Last chance: limited time offer",
        snippet="Act now before the deal expires.",
        body_preview="Limited time offer. Act now, shop now, and save today before the sale ends.",
        headers={"list-unsubscribe": "<mailto:unsubscribe@example.com>"},
    )
    classification = _classification(
        category=MailCategory.PROMOTIONAL,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.LOW,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=True,
        contains_action_required_language=True,
        summary="Promotional sale email.",
        rationale="Bulk marketing content with urgency language.",
    )

    protection = evaluate_protection(message, classification)

    assert not protection.blocked
    assert (
        finalize_recommendation(classification, protection)
        == Recommendation.TRASH_CANDIDATE
    )


def test_job_email_with_action_required_remains_protected() -> None:
    message = _message(
        sender="Alex Recruiter",
        sender_email="alex.recruiter@example.com",
        subject="Action required for your application",
        snippet="Please respond about your interview times.",
        body_preview="Action required: complete your application and confirm your interview availability before the deadline.",
    )
    classification = _classification(
        category=MailCategory.JOB_EMPLOYER,
        sender_type=SenderType.HUMAN_INDIVIDUAL,
        importance=ImportanceLevel.HIGH,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=True,
        contains_action_required_language=True,
        summary="Recruiting follow-up.",
        rationale="Employment-related message.",
    )

    protection = evaluate_protection(message, classification)

    assert protection.blocked
    assert "job/employer message" in protection.reasons
    assert "personal human email" in protection.reasons
    assert "contains real deadline/interview/application language" in protection.reasons
    assert "contains real action-required language" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW


def test_security_alert_remains_protected() -> None:
    message = _message(
        sender="Platform Notifications",
        sender_email="security@example.com",
        subject="Account locked after login attempt",
        snippet="Verify your account and reset your password.",
        body_preview="We detected a login attempt. Verify your account and complete a password reset.",
    )
    classification = _classification(
        category=MailCategory.SECURITY_ACCOUNT,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.HIGH,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_action_required_language=True,
        summary="Security alert.",
        rationale="Account access warning.",
    )

    protection = evaluate_protection(message, classification)

    assert protection.blocked
    assert "security/account alert" in protection.reasons
    assert "contains real action-required language" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW


def test_email_with_attachment_remains_protected() -> None:
    message = _message(
        sender="Weekly Deals",
        sender_email="newsletter@example.com",
        subject="Exclusive offer with attachment",
        snippet="Last chance to save today.",
        body_preview="Exclusive offer. Shop now before the sale ends.",
        headers={"list-unsubscribe": "<mailto:unsubscribe@example.com>"},
        has_attachments=True,
        attachment_names=["coupon.pdf"],
    )
    classification = _classification(
        category=MailCategory.PROMOTIONAL,
        sender_type=SenderType.AUTOMATED_SYSTEM,
        importance=ImportanceLevel.LOW,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=True,
        contains_action_required_language=True,
        summary="Promotional sale email with attachment.",
        rationale="Bulk marketing content with an attachment.",
    )

    protection = evaluate_protection(message, classification)

    assert protection.blocked
    assert "contains attachment(s)" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW


def test_personal_human_email_remains_protected() -> None:
    message = _message(
        sender="Jamie Smith",
        sender_email="jamie.smith@example.com",
        subject="Please respond by tomorrow",
        snippet="Need your answer on the schedule.",
        body_preview="Please respond by tomorrow so I can finalize the schedule.",
    )
    classification = _classification(
        category=MailCategory.OTHER,
        sender_type=SenderType.HUMAN_INDIVIDUAL,
        importance=ImportanceLevel.MEDIUM,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        contains_deadline_or_offer=True,
        contains_action_required_language=True,
        summary="Personal follow-up email.",
        rationale="Human sender asking for a reply.",
    )

    protection = evaluate_protection(message, classification)

    assert protection.blocked
    assert "personal human email" in protection.reasons
    assert "contains real action-required language" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW


def test_finance_billing_email_remains_protected() -> None:
    message = _message(
        sender="Billing Center",
        sender_email="billing@bank.example.com",
        subject="Invoice overdue",
        snippet="Payment required immediately.",
        body_preview="Your bill is overdue. Payment required to avoid service interruption.",
    )
    classification = _classification(
        category=MailCategory.TRANSACTIONAL,
        sender_type=SenderType.ORGANIZATION,
        importance=ImportanceLevel.HIGH,
        trash_recommendation=Recommendation.TRASH_CANDIDATE,
        summary="Billing reminder.",
        rationale="Finance-related payment notice.",
    )

    protection = evaluate_protection(message, classification)

    assert protection.blocked
    assert "contains real action-required language" in protection.reasons
    assert finalize_recommendation(classification, protection) == Recommendation.REVIEW
