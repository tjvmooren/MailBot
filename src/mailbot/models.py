from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MailCategory(StrEnum):
    PROMOTIONAL = "promotional"
    NEWSLETTER = "newsletter"
    SOCIAL = "social"
    TRANSACTIONAL = "transactional"
    PERSONAL = "personal"
    JOB_EMPLOYER = "job_employer"
    SCHOOL_PROFESSOR = "school_professor"
    BANK_FINANCE = "bank_finance"
    GOVERNMENT_LEGAL = "government_legal"
    SECURITY_ACCOUNT = "security_account"
    SPAM = "spam"
    OTHER = "other"


class SenderType(StrEnum):
    HUMAN_INDIVIDUAL = "human_individual"
    ORGANIZATION = "organization"
    AUTOMATED_SYSTEM = "automated_system"
    UNKNOWN = "unknown"


class ImportanceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Recommendation(StrEnum):
    KEEP = "keep"
    REVIEW = "review"
    TRASH_CANDIDATE = "trash_candidate"


class MessageClassification(BaseModel):
    category: MailCategory
    sender_type: SenderType
    importance: ImportanceLevel
    trash_recommendation: Recommendation
    contains_deadline_or_offer: bool = False
    contains_action_required_language: bool = False
    summary: str = Field(..., max_length=200)
    rationale: str = Field(..., max_length=300)


@dataclass(slots=True)
class ParsedMessage:
    message_id: str
    thread_id: str
    sender: str
    sender_email: str
    subject: str
    received_at: datetime
    snippet: str
    body_preview: str
    label_ids: list[str] = field(default_factory=list)
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def combined_text(self) -> str:
        return " ".join(
            part for part in [self.subject, self.snippet, self.body_preview] if part
        ).strip()


@dataclass(slots=True)
class ProtectionCheck:
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)


@dataclass(slots=True)
class AnalyzedMessage:
    parsed: ParsedMessage
    classification: MessageClassification
    protection: ProtectionCheck
    final_recommendation: Recommendation

    @property
    def safe_to_trash(self) -> bool:
        return (
            self.final_recommendation == Recommendation.TRASH_CANDIDATE
            and not self.protection.blocked
        )
