from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ChatIntentName(StrEnum):
    AUTH = "auth"
    UNREAD = "unread"
    IMPORTANT = "important"
    CLEANUP = "cleanup"
    SEARCH = "search"
    REVIEW = "review"
    TRASH = "trash"
    DETAILS = "details"
    CANDIDATES = "candidates"
    IMPORTANT_FOLLOW_UP = "important_follow_up"
    NEXT = "next"
    STOP = "stop"
    HELP = "help"
    EXIT = "exit"
    UNSUPPORTED = "unsupported"


class ChatReviewFilter(StrEnum):
    TRASH_CANDIDATES = "trash_candidates"
    PROTECTED = "protected"
    REVIEW = "review"
    KEEP = "keep"


class ChatIntent(BaseModel):
    intent: ChatIntentName
    limit: int | None = None
    gmail_query: str | None = None
    selected_ids: list[int] = Field(default_factory=list)
    review_filter: ChatReviewFilter | None = None
    clarification_question: str | None = None
    unsupported_reason: str | None = None

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification_question)
