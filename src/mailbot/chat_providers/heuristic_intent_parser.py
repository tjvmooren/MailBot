from __future__ import annotations

import re

from mailbot.chat_intents import ChatIntent, ChatIntentName, ChatReviewFilter

from .base import ChatIntentParser

_LIMIT_RE = re.compile(r"\b(?:limit|show|top)\s+(\d+)\b", re.IGNORECASE)
_TRASH_ID_RE = re.compile(
    r"\b(?:trash|delete)\s+(?:item|items|id|ids|message|messages)?\s*([0-9,\sand]+)",
    re.IGNORECASE,
)
_QUERY_OPERATOR_RE = re.compile(
    r"\b(?:from:|to:|subject:|newer_than:|older_than:|is:|label:|has:|in:)",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(
    r"\b(send|draft|reply|respond|archive|permanent(?:ly)? delete|delete forever)\b",
    re.IGNORECASE,
)
_EXIT_WORDS = {"exit", "quit", "q"}


class HeuristicChatIntentParser(ChatIntentParser):
    description = "heuristic"

    def parse_intent(self, user_message: str) -> ChatIntent:
        text = (user_message or "").strip()
        lowered = text.lower()
        limit = _extract_limit(text)

        if lowered in _EXIT_WORDS:
            return ChatIntent(intent=ChatIntentName.EXIT)

        if not text:
            return ChatIntent(
                intent=ChatIntentName.HELP,
                clarification_question="What would you like to do in MailBot chat?",
            )

        if _UNSUPPORTED_RE.search(text):
            return ChatIntent(
                intent=ChatIntentName.UNSUPPORTED,
                unsupported_reason=(
                    "MailBot can read, search, review, recommend cleanup, and move "
                    "selected safe messages to Gmail Trash. It cannot send, draft, "
                    "reply, archive, or permanently delete emails."
                ),
            )

        if any(term in lowered for term in ("help", "what can you do", "commands")):
            return ChatIntent(intent=ChatIntentName.HELP)

        if any(term in lowered for term in ("auth", "authenticate", "login", "sign in")):
            return ChatIntent(intent=ChatIntentName.AUTH)

        if any(term in lowered for term in ("show my unread", "unread emails", "unread messages")):
            return ChatIntent(intent=ChatIntentName.UNREAD, limit=limit)

        if "important" in lowered:
            return ChatIntent(intent=ChatIntentName.IMPORTANT, limit=limit)

        if any(term in lowered for term in ("clean up", "cleanup", "safely clean")):
            return ChatIntent(intent=ChatIntentName.CLEANUP, limit=limit)

        review_filter = _detect_review_filter(lowered)
        if review_filter is not None:
            return ChatIntent(
                intent=ChatIntentName.REVIEW,
                review_filter=review_filter,
            )

        if any(term in lowered for term in ("review", "latest cleanup", "latest results")):
            return ChatIntent(
                intent=ChatIntentName.REVIEW,
                review_filter=review_filter,
            )

        trash_match = _TRASH_ID_RE.search(text)
        if trash_match:
            selected_ids = _parse_ids(trash_match.group(1))
            if not selected_ids:
                return ChatIntent(
                    intent=ChatIntentName.TRASH,
                    clarification_question="Which display ID should I move to Gmail Trash?",
                )
            return ChatIntent(
                intent=ChatIntentName.TRASH,
                selected_ids=selected_ids,
            )

        if _QUERY_OPERATOR_RE.search(text) or any(
            term in lowered for term in ("search", "find", "look for", "show me")
        ):
            gmail_query = _extract_search_query(text, lowered)
            if gmail_query is None:
                return ChatIntent(
                    intent=ChatIntentName.SEARCH,
                    clarification_question="What Gmail query should I search for?",
                )
            return ChatIntent(
                intent=ChatIntentName.SEARCH,
                gmail_query=gmail_query,
                limit=limit,
            )

        return ChatIntent(
            intent=ChatIntentName.UNSUPPORTED,
            unsupported_reason=(
                "I can help with Gmail auth, unread mail, important mail, cleanup "
                "recommendations, search, review, and safe trash moves."
            ),
        )


def _extract_limit(text: str) -> int | None:
    match = _LIMIT_RE.search(text)
    if not match:
        return None
    try:
        limit = int(match.group(1))
    except ValueError:
        return None
    return limit if limit > 0 else None


def _detect_review_filter(lowered: str) -> ChatReviewFilter | None:
    if "trash candidate" in lowered or "trash candidates" in lowered:
        return ChatReviewFilter.TRASH_CANDIDATES
    if "protected" in lowered:
        return ChatReviewFilter.PROTECTED
    if re.search(r"\b(?:only review|review items|review messages)\b", lowered):
        return ChatReviewFilter.REVIEW
    if re.search(r"\bkeep\b", lowered):
        return ChatReviewFilter.KEEP
    return None


def _extract_search_query(text: str, lowered: str) -> str | None:
    quoted_match = re.search(r'"([^"]+)"', text)
    if quoted_match:
        return quoted_match.group(1).strip()

    stripped_search_prefix = re.sub(
        r"^(?:search(?: for)?|find|look for|show(?: me)?)\s+",
        "",
        lowered,
        count=1,
    ).strip()

    if _QUERY_OPERATOR_RE.search(stripped_search_prefix):
        return stripped_search_prefix

    sender_match = re.search(
        r"\b(?:find|show(?: me)?|search(?: for)?|look for)\s+recent\s+([a-z0-9._-]+)\s+emails?\b",
        lowered,
    )
    if sender_match:
        sender = sender_match.group(1)
        return f"from:{sender} newer_than:30d"

    sender_match = re.search(
        r"\b(?:recent)\s+([a-z0-9._-]+)\s+emails?\b",
        lowered,
    )
    if sender_match:
        sender = sender_match.group(1)
        return f"from:{sender} newer_than:30d"

    sender_match = re.search(
        r"\b(?:find|show(?: me)?|search(?: for)?|look for)\s+([a-z0-9._-]+)\s+emails?\b",
        lowered,
    )
    if sender_match:
        sender = sender_match.group(1)
        return f"from:{sender}"

    from_match = re.search(r"\bfrom\s+([a-z0-9._-]+)\b", lowered)
    if from_match:
        sender = from_match.group(1)
        if "recent" in lowered:
            return f"from:{sender} newer_than:30d"
        return f"from:{sender}"

    return None


def _parse_ids(raw_ids: str) -> list[int]:
    values = re.split(r"[,\s]+|and", raw_ids)
    parsed_ids: list[int] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        if not cleaned.isdigit():
            continue
        numeric = int(cleaned)
        if numeric > 0 and numeric not in parsed_ids:
            parsed_ids.append(numeric)
    return parsed_ids
