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

DEADLINE_OR_OFFER_RE = re.compile(
    r"\b(deadline|due by|respond by|interview|offer|next steps|expires?|accept by|"
    r"scheduled for|appointment|meeting request)\b",
    re.IGNORECASE,
)
ACTION_REQUIRED_RE = re.compile(
    r"\b(action required|verify|confirm|approve|complete your|submit|review and sign|"
    r"follow up|urgent|reply needed|please respond)\b",
    re.IGNORECASE,
)

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

    protected_category_reason = PROTECTED_CATEGORY_REASONS.get(classification.category)
    if protected_category_reason:
        reasons.append(protected_category_reason)

    if classification.sender_type == SenderType.HUMAN_INDIVIDUAL or _looks_human(message):
        reasons.append("personal human email")

    if message.has_attachments:
        reasons.append("contains attachment(s)")

    if classification.contains_deadline_or_offer or DEADLINE_OR_OFFER_RE.search(
        combined_text
    ):
        reasons.append("contains deadline/interview/offer language")

    if classification.contains_action_required_language or ACTION_REQUIRED_RE.search(
        combined_text
    ):
        reasons.append("contains action-required language")

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


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
