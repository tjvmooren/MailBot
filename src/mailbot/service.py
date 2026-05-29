from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .db import SessionStore, StoredSession, StoredSessionResult
from .exceptions import MailBotError
from .gmail_client import GmailClient
from .models import (
    AnalyzedMessage,
    ImportanceLevel,
    MailCategory,
    MessageClassification,
    ParsedMessage,
    Recommendation,
    SenderType,
)
from .providers import LLMProvider, LLMProviderError, build_provider
from .safety import evaluate_protection, finalize_recommendation

IMPORTANCE_SORT_ORDER = {
    ImportanceLevel.LOW: 0,
    ImportanceLevel.MEDIUM: 1,
    ImportanceLevel.HIGH: 2,
}

BLOCKED_TRASH_CATEGORIES = {
    MailCategory.JOB_EMPLOYER.value,
    MailCategory.SCHOOL_PROFESSOR.value,
    MailCategory.BANK_FINANCE.value,
    MailCategory.GOVERNMENT_LEGAL.value,
    MailCategory.SECURITY_ACCOUNT.value,
    MailCategory.PERSONAL.value,
}


@dataclass(slots=True)
class ScanOutput:
    command: str
    query: str
    messages: list[AnalyzedMessage]
    session_id: int | None
    provider_warning: str | None = None


@dataclass(slots=True)
class ReviewOutput:
    session: StoredSession
    results: list[StoredSessionResult]
    filter_name: str | None = None


@dataclass(slots=True)
class TrashBlockedItem:
    result: StoredSessionResult
    reasons: list[str]


@dataclass(slots=True)
class TrashPreview:
    session: StoredSession
    requested_ids: list[int]
    selected: list[StoredSessionResult]
    eligible: list[StoredSessionResult]
    blocked: list[TrashBlockedItem]
    already_trashed: list[StoredSessionResult]
    missing_ids: list[int]

    @property
    def requested_count(self) -> int:
        return len(self.requested_ids)

    @property
    def eligible_count(self) -> int:
        return len(self.eligible)


@dataclass(slots=True)
class TrashFailure:
    display_id: int
    gmail_message_id: str
    subject: str
    stage: str
    error: str


@dataclass(slots=True)
class TrashExecutionResult:
    requested_count: int
    eligible_count: int
    moved: list[StoredSessionResult]
    verified: list[StoredSessionResult]
    failures: list[TrashFailure]


class MailBotService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.gmail = GmailClient(config.gmail)
        self.store = SessionStore(config.database_path)
        self._provider: LLMProvider | None = None
        self._provider_error: str | None = None
        self._provider_loaded = False

    def authenticate(self) -> str:
        return self.gmail.authenticate()

    def unread(self, limit: int | None = None) -> ScanOutput:
        return self._scan(
            command="unread",
            query=self.config.queries.unread,
            limit=limit or self.config.limits.default_list_limit,
            actionable=False,
        )

    def important(self, limit: int | None = None) -> ScanOutput:
        return self._scan(
            command="important",
            query=self.config.queries.important,
            limit=limit or self.config.limits.default_list_limit,
            actionable=False,
        )

    def cleanup(self, limit: int | None = None) -> ScanOutput:
        output = self._scan(
            command="cleanup",
            query=self.config.queries.cleanup,
            limit=limit or self.config.limits.default_list_limit,
            actionable=False,
        )
        output.messages.sort(key=self._cleanup_sort_key)
        output.session_id = self.store.save_session(
            output.command, output.query, output.messages, actionable=True
        )
        return output

    def search(self, query: str, limit: int | None = None) -> ScanOutput:
        return self._scan(
            command="search",
            query=query,
            limit=limit or self.config.limits.default_list_limit,
            actionable=True,
        )

    def review(self, filter_name: str | None = None) -> ReviewOutput:
        session = self.store.get_latest_actionable_session()
        if session is None:
            raise ValueError(
                "No actionable session found. Run `mailbot cleanup` or `mailbot search` first."
            )

        results = self.store.get_session_results(session.session_id)
        filtered_results = [
            result for result in results if _matches_review_filter(result, filter_name)
        ]
        return ReviewOutput(
            session=session,
            results=filtered_results,
            filter_name=filter_name,
        )

    def prepare_trash(self, display_ids: list[int]) -> TrashPreview:
        session = self.store.get_latest_actionable_session()
        if session is None:
            raise ValueError(
                "No actionable session found. Run `mailbot cleanup` or `mailbot search` first."
            )

        rows = self.store.get_session_results(session.session_id, display_ids=display_ids)
        rows_by_id = {row.display_id: row for row in rows}
        selected = [rows_by_id[item_id] for item_id in display_ids if item_id in rows_by_id]
        missing_ids = [item_id for item_id in display_ids if item_id not in rows_by_id]

        already_trashed = [row for row in selected if row.trashed_at]
        eligible: list[StoredSessionResult] = []
        blocked: list[TrashBlockedItem] = []
        for row in selected:
            if row.trashed_at:
                continue
            block_reasons = _trash_block_reasons(row)
            if block_reasons:
                blocked.append(TrashBlockedItem(result=row, reasons=block_reasons))
            else:
                eligible.append(row)

        return TrashPreview(
            session=session,
            requested_ids=display_ids,
            selected=selected,
            eligible=eligible,
            blocked=blocked,
            already_trashed=already_trashed,
            missing_ids=missing_ids,
        )

    def move_to_trash(self, preview: TrashPreview) -> TrashExecutionResult:
        moved: list[StoredSessionResult] = []
        verified: list[StoredSessionResult] = []
        failures: list[TrashFailure] = []

        for result in preview.eligible:
            try:
                self.gmail.trash_message(result.gmail_message_id)
                moved.append(result)
            except MailBotError as exc:
                failures.append(
                    TrashFailure(
                        display_id=result.display_id,
                        gmail_message_id=result.gmail_message_id,
                        subject=result.subject,
                        stage="move",
                        error=str(exc),
                    )
                )

        if moved:
            self.store.mark_trashed(
                preview.session.session_id, [result.display_id for result in moved]
            )
        for result in moved:
            try:
                if self.gmail.message_has_trash_label(result.gmail_message_id):
                    verified.append(result)
                else:
                    failures.append(
                        TrashFailure(
                            display_id=result.display_id,
                            gmail_message_id=result.gmail_message_id,
                            subject=result.subject,
                            stage="verify",
                            error="Message does not have the TRASH label after trash().",
                        )
                    )
            except MailBotError as exc:
                failures.append(
                    TrashFailure(
                        display_id=result.display_id,
                        gmail_message_id=result.gmail_message_id,
                        subject=result.subject,
                        stage="verify",
                        error=str(exc),
                    )
                )

        return TrashExecutionResult(
            requested_count=preview.requested_count,
            eligible_count=preview.eligible_count,
            moved=moved,
            verified=verified,
            failures=failures,
        )

    def _scan(
        self, command: str, query: str, limit: int, actionable: bool
    ) -> ScanOutput:
        provider = self._get_provider()
        provider_warning = self._provider_error
        parsed_messages = self.gmail.fetch_messages(
            query=query,
            limit=limit,
            body_preview_chars=self.config.limits.body_preview_chars,
        )

        analyzed_messages: list[AnalyzedMessage] = []
        provider_failed = provider is None

        for message in parsed_messages:
            if provider is not None and not provider_failed:
                try:
                    classification = provider.classify_message(message)
                except LLMProviderError as exc:
                    provider_warning = str(exc)
                    provider_failed = True
                    classification = _fallback_classification(message, provider_warning)
            else:
                classification = _fallback_classification(
                    message, provider_warning or "No LLM provider is configured."
                )

            protection = evaluate_protection(message, classification)
            final_recommendation = finalize_recommendation(classification, protection)
            analyzed_messages.append(
                AnalyzedMessage(
                    parsed=message,
                    classification=classification,
                    protection=protection,
                    final_recommendation=final_recommendation,
                )
            )

        session_id: int | None = None
        if actionable:
            session_id = self.store.save_session(
                command=command,
                query=query,
                results=analyzed_messages,
                actionable=True,
            )

        return ScanOutput(
            command=command,
            query=query,
            messages=analyzed_messages,
            session_id=session_id,
            provider_warning=provider_warning,
        )

    def _get_provider(self) -> LLMProvider | None:
        if self._provider_loaded:
            return self._provider

        self._provider_loaded = True
        try:
            self._provider = build_provider(self.config)
        except LLMProviderError as exc:
            self._provider_error = str(exc)
            self._provider = None
        return self._provider

    @staticmethod
    def _cleanup_sort_key(message: AnalyzedMessage) -> tuple[int, int, float]:
        return (
            0 if message.safe_to_trash else 1,
            IMPORTANCE_SORT_ORDER[message.classification.importance],
            -message.parsed.received_at.timestamp(),
        )


def _fallback_classification(
    message: ParsedMessage, reason: str
) -> MessageClassification:
    subject = (message.subject or "(no subject)")[:80]
    summary = f"LLM classification unavailable for: {subject}"
    return MessageClassification(
        category=MailCategory.OTHER,
        sender_type=SenderType.UNKNOWN,
        importance=ImportanceLevel.MEDIUM,
        trash_recommendation=Recommendation.REVIEW,
        contains_deadline_or_offer=False,
        contains_action_required_language=False,
        summary=summary[:200],
        rationale=(reason or "Manual review is required.")[:300],
    )


def _matches_review_filter(
    result: StoredSessionResult, filter_name: str | None
) -> bool:
    if filter_name is None:
        return True
    if filter_name == "trash_candidates":
        return result.final_recommendation == Recommendation.TRASH_CANDIDATE.value
    if filter_name == "protected":
        return bool(result.protected_reasons)
    if filter_name == "review":
        return result.final_recommendation == Recommendation.REVIEW.value
    if filter_name == "keep":
        return result.final_recommendation == Recommendation.KEEP.value
    raise ValueError(f"Unsupported review filter: {filter_name}")


def _trash_block_reasons(result: StoredSessionResult) -> list[str]:
    reasons: list[str] = []
    if result.final_recommendation != Recommendation.TRASH_CANDIDATE.value:
        reasons.append(
            f"recommendation is {result.final_recommendation.upper()}, not TRASH_CANDIDATE"
        )
    if result.category in BLOCKED_TRASH_CATEGORIES:
        reasons.append(f"protected category: {result.category}")
    if result.has_attachments:
        reasons.append("contains attachment(s)")
    reasons.extend(result.protected_reasons)
    if not result.allow_trash:
        reasons.append("stored safety rules did not mark this message safe to trash")
    return list(dict.fromkeys(reasons))
