from __future__ import annotations

import re
from email.utils import parseaddr

from .models import (
    MailCategory,
    MessageClassification,
    ParsedMessage,
    ProtectionCheck,
    Recommendation,
    SenderType,
)

PROTECTED_CATEGORY_REASONS = {
    MailCategory.JOB_EMPLOYER: "job/employer message",
    MailCategory.SCHOOL_PROFESSOR: "school/professor message",
    MailCategory.BANK_FINANCE: "bank/finance message",
    MailCategory.GOVERNMENT_LEGAL: "government/legal message",
    MailCategory.SECURITY_ACCOUNT: "security/account alert",
}

LOW_RISK_MARKETING_CATEGORIES = {
    MailCategory.PROMOTIONAL,
    MailCategory.NEWSLETTER,
    MailCategory.SOCIAL,
}

REAL_DEADLINE_OR_OFFER_RE = re.compile(
    r"\b(interview|application|offer letter|job offer|employment offer|recruiter|"
    r"professor|assignment|due date|deadline|respond by|response required|accept by|"
    r"appointment|meeting request|scheduled for)\b",
    re.IGNORECASE,
)
REAL_ACTION_REQUIRED_RE = re.compile(
    r"\b(action required|invoice|bill(?:ing)?|payment required|payment due|payment warning|"
    r"past due|overdue|account locked|password reset|login attempt|verify your account|"
    r"verify your identity|tax(?:es)?|legal notice|government notice|insurance|"
    r"document signature|signature required|review and sign|sign this document|"
    r"restore your project)\b",
    re.IGNORECASE,
)
MARKETING_URGENCY_RE = re.compile(
    r"\b(limited time offer|sale ends|act now|last chance|don't miss out|deal expires|"
    r"save today|exclusive offer|order now|shop now)\b",
    re.IGNORECASE,
)
PROTECTED_SENDER_HINT_RE = re.compile(
    r"\b(recruit(?:er|ing)?|career|application|professor|registrar|admissions|"
    r"billing|invoice|payment|statement|tax|legal|court|insurance|security|"
    r"password|login|verify(?:ication)?)\b",
    re.IGNORECASE,
)
PROTECTED_DOMAIN_HINT_RE = re.compile(r"\.(?:edu|gov|mil)\b", re.IGNORECASE)

AUTOMATED_SENDER_MARKERS = (
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "notification",
    "notifications",
    "alerts",
    "billing",
    "support",
    "info",
    "hello",
    "team",
    "careers",
    "jobs",
)

PERSON_NAME_RE = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){1,2}$")
EMAIL_NAME_RE = re.compile(r"^[a-z]+(?:[._-][a-z]+)+$")


def evaluate_protection(
    message: ParsedMessage, classification: MessageClassification
) -> ProtectionCheck:
    reasons: list[str] = []
    combined_text = message.combined_text
    has_personal_sender_signal = (
        classification.sender_type == SenderType.HUMAN_INDIVIDUAL or _looks_human(message)
    )
    has_protected_context = _has_protected_context(message, classification)

    protected_category_reason = PROTECTED_CATEGORY_REASONS.get(classification.category)
    if protected_category_reason:
        reasons.append(protected_category_reason)

    if has_personal_sender_signal:
        reasons.append("personal human email")

    if message.has_attachments:
        reasons.append("contains attachment(s)")

    if _has_real_deadline_or_offer_signal(message, classification, has_protected_context):
        reasons.append("contains real deadline/interview/application language")

    if _has_real_action_required_signal(message, classification, has_protected_context):
        reasons.append("contains real action-required language")

    return ProtectionCheck(reasons=_dedupe(reasons))


def finalize_recommendation(
    classification: MessageClassification, protection: ProtectionCheck
) -> Recommendation:
    if (
        protection.blocked
        and classification.trash_recommendation == Recommendation.TRASH_CANDIDATE
    ):
        return Recommendation.REVIEW
    return classification.trash_recommendation


def _looks_human(message: ParsedMessage) -> bool:
    if "list-unsubscribe" in message.headers or "list-id" in message.headers:
        return False

    sender_text = f"{message.sender} {message.sender_email}".lower()
    if any(marker in sender_text for marker in AUTOMATED_SENDER_MARKERS):
        return False

    display_name, sender_email = parseaddr(f"{message.sender} <{message.sender_email}>")
    display_name = display_name.strip()
    if display_name and PERSON_NAME_RE.match(display_name):
        return True

    local_part = sender_email.split("@", 1)[0].lower() if "@" in sender_email else ""
    return bool(local_part and EMAIL_NAME_RE.match(local_part))


def _has_real_deadline_or_offer_signal(
    message: ParsedMessage,
    classification: MessageClassification,
    has_protected_context: bool,
) -> bool:
    if REAL_DEADLINE_OR_OFFER_RE.search(message.combined_text):
        return True
    return bool(
        classification.contains_deadline_or_offer
        and not _is_low_risk_marketing_urgency(message, classification, has_protected_context)
    )


def _has_real_action_required_signal(
    message: ParsedMessage,
    classification: MessageClassification,
    has_protected_context: bool,
) -> bool:
    if REAL_ACTION_REQUIRED_RE.search(message.combined_text):
        return True
    return bool(
        classification.contains_action_required_language
        and not _is_low_risk_marketing_urgency(message, classification, has_protected_context)
    )


def _is_low_risk_marketing_urgency(
    message: ParsedMessage,
    classification: MessageClassification,
    has_protected_context: bool,
) -> bool:
    return (
        classification.category in LOW_RISK_MARKETING_CATEGORIES
        and MARKETING_URGENCY_RE.search(message.combined_text) is not None
        and not has_protected_context
    )


def _has_protected_context(
    message: ParsedMessage, classification: MessageClassification
) -> bool:
    sender_text = f"{message.sender} {message.sender_email}"
    return any(
        (
            classification.category in PROTECTED_CATEGORY_REASONS,
            classification.sender_type == SenderType.HUMAN_INDIVIDUAL,
            _looks_human(message),
            message.has_attachments,
            REAL_DEADLINE_OR_OFFER_RE.search(message.combined_text) is not None,
            REAL_ACTION_REQUIRED_RE.search(message.combined_text) is not None,
            PROTECTED_SENDER_HINT_RE.search(sender_text) is not None,
            PROTECTED_DOMAIN_HINT_RE.search(message.sender_email) is not None,
        )
    )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
