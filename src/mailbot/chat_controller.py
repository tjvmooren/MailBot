from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .chat_intents import ChatIntent, ChatIntentName, ChatReviewFilter
from .chat_providers.base import ChatIntentParser
from .service import MailBotService, ReviewOutput, ScanOutput, TrashExecutionResult, TrashPreview


@dataclass(slots=True)
class ChatTurnResult:
    exit_requested: bool = False
    messages: list[str] = field(default_factory=list)


class ChatController:
    def __init__(
        self,
        service: MailBotService,
        intent_parser: ChatIntentParser,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.service = service
        self.intent_parser = intent_parser
        self.input_fn = input_fn
        self.output_fn = output_fn
        self._pending_trash_preview: TrashPreview | None = None

    def run(self) -> int:
        self._emit(
            [
                "MailBot chat is ready.",
                f"Intent routing: {self.intent_parser.description}.",
                "Ask for unread mail, cleanup, review, search, or safe trash moves. "
                "Type `exit`, `quit`, or `q` to leave.",
            ]
        )
        while True:
            try:
                user_message = self.input_fn("mailbot chat> ").strip()
            except EOFError:
                self._emit(["Chat ended."])
                return 0

            result = self.handle_message(user_message)
            self._emit(result.messages)
            if result.exit_requested:
                return 0

    def handle_message(self, user_message: str) -> ChatTurnResult:
        text = (user_message or "").strip()
        if not text:
            return ChatTurnResult(messages=["What would you like to do?"])

        if text in {"TRASH"}:
            if self._pending_trash_preview is None:
                return ChatTurnResult(
                    messages=["There is no pending trash action to confirm right now."]
                )
            preview = self._pending_trash_preview
            self._pending_trash_preview = None
            result = self.service.move_to_trash(preview)
            return ChatTurnResult(messages=self._format_trash_result(result))

        intent = self.intent_parser.parse_intent(text)

        if intent.intent == ChatIntentName.EXIT:
            messages = []
            if self._pending_trash_preview is not None:
                self._pending_trash_preview = None
                messages.append("Pending trash confirmation cleared.")
            messages.append("Ending MailBot chat.")
            return ChatTurnResult(exit_requested=True, messages=messages)

        if intent.intent == ChatIntentName.UNSUPPORTED:
            return ChatTurnResult(
                messages=[intent.unsupported_reason or self._help_message()]
            )

        if intent.needs_clarification:
            return ChatTurnResult(
                messages=[intent.clarification_question or "Can you clarify that request?"]
            )

        if self._pending_trash_preview is not None and intent.intent != ChatIntentName.TRASH:
            pending_message = "Pending trash confirmation cleared."
            self._pending_trash_preview = None
        else:
            pending_message = ""

        result = self._dispatch_intent(intent)
        if pending_message:
            result.messages.insert(0, pending_message)
        return result

    def process_message(self, user_message: str) -> ChatTurnResult:
        """Backward-compatible alias for the transport-neutral message handler."""
        return self.handle_message(user_message)

    def _dispatch_intent(self, intent: ChatIntent) -> ChatTurnResult:
        if intent.intent == ChatIntentName.HELP:
            return ChatTurnResult(messages=[self._help_message()])

        if intent.intent == ChatIntentName.AUTH:
            email_address = self.service.authenticate()
            return ChatTurnResult(
                messages=[
                    f"Gmail authentication completed for {email_address}.",
                    "This app has Gmail read/modify access only. It does not send or draft email.",
                ]
            )

        if intent.intent == ChatIntentName.UNREAD:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.unread(limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                )
            )

        if intent.intent == ChatIntentName.IMPORTANT:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.important(limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                )
            )

        if intent.intent == ChatIntentName.CLEANUP:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.cleanup(limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                    recommendation_only=True,
                )
            )

        if intent.intent == ChatIntentName.SEARCH:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.search(query=intent.gmail_query or "", limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                )
            )

        if intent.intent == ChatIntentName.REVIEW:
            return ChatTurnResult(
                messages=self._format_review_output(
                    self.service.review(filter_name=intent.review_filter.value if intent.review_filter else None)
                )
            )

        if intent.intent == ChatIntentName.TRASH:
            preview = self.service.prepare_trash(intent.selected_ids)
            messages = self._format_trash_preview(preview)
            if not preview.eligible:
                self._pending_trash_preview = None
                return ChatTurnResult(messages=messages)
            self._pending_trash_preview = preview
            messages.append(
                "Recommendation confirmed as safe to proceed. To move the eligible IDs to Gmail Trash only, type `TRASH`."
            )
            return ChatTurnResult(messages=messages)

        return ChatTurnResult(messages=[self._help_message()])

    def _format_scan_output(
        self,
        output: ScanOutput,
        source_label: str,
        recommendation_only: bool = False,
    ) -> list[str]:
        messages = [source_label]
        if output.provider_warning:
            messages.append(f"Warning: {output.provider_warning}")
        messages.append(
            f"{output.command.title()} found {len(output.messages)} message(s) for `{output.query}`."
        )
        if output.session_id is not None:
            messages.append(f"Saved actionable session {output.session_id}.")
        if recommendation_only:
            messages.append(
                "Recommendation only. No messages were moved or deleted."
            )
        if not output.messages:
            messages.append("No matching Gmail messages were found.")
            return messages

        for display_id, message in enumerate(output.messages, start=1):
            messages.append(
                f"[{display_id}] {message.final_recommendation.value.upper()} | "
                f"{message.parsed.sender} | {message.parsed.subject}"
            )
            messages.append(
                f"  category={message.classification.category.value}, "
                f"importance={message.classification.importance.value}"
            )
            messages.append(f"  summary={message.classification.summary}")
            if message.protection.reasons:
                messages.append(
                    "  protected reasons=" + ", ".join(message.protection.reasons)
                )
        return messages

    def _format_review_output(self, output: ReviewOutput) -> list[str]:
        messages = [
            f"Showing stored SQLite review output from session {output.session.session_id}.",
            f"Session type: {output.session.command}",
            f"Query: {output.session.query}",
        ]
        if output.filter_name:
            filter_label = output.filter_name.replace("_", " ")
            messages.append(f"Filter: {filter_label}")
        if not output.results:
            messages.append("No stored messages matched that review filter.")
            return messages

        for item in output.results:
            messages.append(
                f"[{item.display_id}] {item.final_recommendation.upper()} | "
                f"{item.sender} | {item.subject}"
            )
            messages.append(
                f"  category={item.category}, importance={item.importance}"
            )
            messages.append(f"  summary={item.summary or '(empty)'}")
            if item.rationale:
                messages.append(f"  rationale={item.rationale}")
            if item.protected_reasons:
                messages.append(
                    "  protected reasons=" + ", ".join(item.protected_reasons)
                )
        return messages

    def _format_trash_preview(self, preview: TrashPreview) -> list[str]:
        messages = [
            f"Latest actionable session: {preview.session.command} "
            f"(session {preview.session.session_id}).",
            "This action moves messages to Gmail Trash only. It does not permanently delete them.",
            f"Requested IDs: {', '.join(str(item) for item in preview.requested_ids)}",
            f"Eligible count: {preview.eligible_count}",
        ]
        if preview.missing_ids:
            messages.append(
                "Unknown IDs: " + ", ".join(str(item) for item in preview.missing_ids)
            )
        if preview.blocked:
            messages.append("Blocked IDs:")
            for item in preview.blocked:
                messages.append(
                    f"  [{item.result.display_id}] {item.result.subject} "
                    f"({'; '.join(item.reasons)})"
                )
        if preview.already_trashed:
            messages.append("Already trashed IDs:")
            for item in preview.already_trashed:
                messages.append(f"  [{item.display_id}] {item.subject}")
        if preview.eligible:
            messages.append("Eligible IDs:")
            for item in preview.eligible:
                messages.append(
                    f"  [{item.display_id}] {item.subject} - {item.summary}"
                )
        else:
            messages.append("No selected IDs are eligible to move.")
        messages.append("Confirmation phrase required: TRASH")
        return messages

    def _format_trash_result(self, result: TrashExecutionResult) -> list[str]:
        messages = [
            "Trash action completed.",
            f"Requested count: {result.requested_count}",
            f"Eligible count: {result.eligible_count}",
            f"Moved count: {len(result.moved)}",
            f"Verified in trash count: {len(result.verified)}",
            "Messages were moved to Gmail Trash only, not permanently deleted.",
        ]
        if result.failures:
            messages.append("Failures:")
            for failure in result.failures:
                messages.append(
                    f"  [{failure.display_id}] {failure.subject} "
                    f"(stage={failure.stage}, gmail_id={failure.gmail_message_id}): "
                    f"{failure.error}"
                )
        return messages

    @staticmethod
    def _help_message() -> str:
        return (
            "Try requests like `show my unread emails`, `what can I safely clean up?`, "
            "`review my latest cleanup results`, `show only trash candidates`, "
            "`find recent Microsoft emails`, or `trash item 3`."
        )

    def _emit(self, messages: list[str]) -> None:
        for message in messages:
            self.output_fn(message)
