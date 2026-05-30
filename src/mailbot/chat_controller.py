from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable

from .chat_intents import ChatIntent, ChatIntentName, ChatReviewFilter
from .chat_providers.base import ChatIntentParser
from .service import MailBotService, ReviewOutput, ScanOutput, TrashExecutionResult, TrashPreview


@dataclass(slots=True)
class ChatTurnResult:
    exit_requested: bool = False
    messages: list[str] = field(default_factory=list)


class ChatResponseMode(StrEnum):
    CHAT = "chat"
    VOICE = "voice"


@dataclass(slots=True)
class VoiceResultItem:
    display_id: int
    sender: str
    subject: str
    final_recommendation: str
    category: str
    importance: str
    summary: str
    protected_reasons: list[str] = field(default_factory=list)

    @property
    def important_or_protected(self) -> bool:
        return self.importance == "high" or bool(self.protected_reasons)


@dataclass(slots=True)
class VoiceResultContext:
    label: str
    items: list[VoiceResultItem]
    active_filter: str = "all"
    next_index: int = 0


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
        self._voice_context: VoiceResultContext | None = None

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

    def handle_message(
        self,
        user_message: str,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> ChatTurnResult:
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

        if response_mode == ChatResponseMode.VOICE:
            follow_up_result = self._handle_voice_follow_up(text)
            if follow_up_result is not None:
                if self._pending_trash_preview is not None:
                    self._pending_trash_preview = None
                    follow_up_result.messages.insert(0, "Pending trash confirmation cleared.")
                return follow_up_result

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

        result = self._dispatch_intent(intent, response_mode=response_mode)
        if pending_message:
            result.messages.insert(0, pending_message)
        return result

    def process_message(
        self,
        user_message: str,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> ChatTurnResult:
        """Backward-compatible alias for the transport-neutral message handler."""
        return self.handle_message(user_message, response_mode=response_mode)

    def _dispatch_intent(
        self,
        intent: ChatIntent,
        response_mode: ChatResponseMode,
    ) -> ChatTurnResult:
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
                    response_mode=response_mode,
                )
            )

        if intent.intent == ChatIntentName.IMPORTANT:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.important(limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                    response_mode=response_mode,
                )
            )

        if intent.intent == ChatIntentName.CLEANUP:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.cleanup(limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                    recommendation_only=True,
                    response_mode=response_mode,
                )
            )

        if intent.intent == ChatIntentName.SEARCH:
            return ChatTurnResult(
                messages=self._format_scan_output(
                    self.service.search(query=intent.gmail_query or "", limit=intent.limit),
                    source_label="Live Gmail fetch and classification complete.",
                    response_mode=response_mode,
                )
            )

        if intent.intent == ChatIntentName.REVIEW:
            return ChatTurnResult(
                messages=self._format_review_output(
                    self.service.review(filter_name=intent.review_filter.value if intent.review_filter else None),
                    response_mode=response_mode,
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
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> list[str]:
        if response_mode == ChatResponseMode.VOICE:
            return self._format_voice_scan_output(
                output,
                source_label=source_label,
                recommendation_only=recommendation_only,
            )

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

    def _format_review_output(
        self,
        output: ReviewOutput,
        response_mode: ChatResponseMode = ChatResponseMode.CHAT,
    ) -> list[str]:
        if response_mode == ChatResponseMode.VOICE:
            return self._format_voice_review_output(output)

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

    def _handle_voice_follow_up(self, text: str) -> ChatTurnResult | None:
        follow_up = _detect_voice_follow_up(text)
        if follow_up is None:
            return None

        if follow_up == ChatIntentName.STOP:
            self._voice_context = None
            return ChatTurnResult(
                messages=[
                    "Okay. I will stop there.",
                    "Ask for unread, cleanup, search, or review whenever you want a new summary.",
                ]
            )

        if self._voice_context is None:
            return ChatTurnResult(
                messages=[
                    "I do not have recent voice results to expand yet.",
                    "Ask for unread, cleanup, search, or review first.",
                ]
            )

        if follow_up == ChatIntentName.DETAILS:
            return ChatTurnResult(
                messages=self._voice_page_messages(
                    filter_name="all",
                    intro="Here are more details from your last result set.",
                    reset=True,
                )
            )

        if follow_up == ChatIntentName.CANDIDATES:
            return ChatTurnResult(
                messages=self._voice_page_messages(
                    filter_name="candidates",
                    intro="Here are the cleanup candidates from your last result set.",
                    reset=True,
                )
            )

        if follow_up == ChatIntentName.IMPORTANT_FOLLOW_UP:
            return ChatTurnResult(
                messages=self._voice_page_messages(
                    filter_name="important",
                    intro="Here are the important or protected messages from your last result set.",
                    reset=True,
                )
            )

        if follow_up == ChatIntentName.NEXT:
            filter_name = self._voice_context.active_filter
            if self._voice_context.next_index <= 0:
                return ChatTurnResult(
                    messages=[
                        "Say details, candidates, or important first so I know which list to continue.",
                    ]
                )
            label = {
                "all": "last result set",
                "candidates": "cleanup candidates",
                "important": "important or protected messages",
            }.get(filter_name, "current list")
            return ChatTurnResult(
                messages=self._voice_page_messages(
                    filter_name=filter_name,
                    intro=f"Here are the next items from the {label}.",
                    reset=False,
                )
            )

        return None

    def _format_voice_scan_output(
        self,
        output: ScanOutput,
        source_label: str,
        recommendation_only: bool,
    ) -> list[str]:
        items = [
            VoiceResultItem(
                display_id=display_id,
                sender=message.parsed.sender,
                subject=message.parsed.subject,
                final_recommendation=message.final_recommendation.value,
                category=message.classification.category.value,
                importance=message.classification.importance.value,
                summary=message.classification.summary,
                protected_reasons=list(message.protection.reasons),
            )
            for display_id, message in enumerate(output.messages, start=1)
        ]
        self._voice_context = (
            VoiceResultContext(
                label=f"your last live Gmail {output.command} results",
                items=items,
            )
            if items
            else None
        )
        messages = [source_label]
        if output.provider_warning:
            messages.append(f"Warning: {output.provider_warning}")

        if output.command == "search":
            messages.append(f"I found {len(items)} message(s) for your Gmail search.")
        else:
            messages.append(f"I found {len(items)} {output.command} message(s).")
        if recommendation_only:
            messages.append(
                "Recommendation only. No messages were moved or deleted."
            )
        if not items:
            messages.append("No matching Gmail messages were found.")
            return messages

        if len(items) > 3:
            messages.extend(self._voice_summary_messages(items))
            messages.append(
                "Say details to hear more. Say candidates to hear cleanup candidates. "
                "Say important to hear important or protected messages."
            )
            return messages

        messages.extend(self._voice_detail_lines(items))
        return messages

    def _format_voice_review_output(self, output: ReviewOutput) -> list[str]:
        items = [
            VoiceResultItem(
                display_id=item.display_id,
                sender=item.sender,
                subject=item.subject,
                final_recommendation=item.final_recommendation,
                category=item.category,
                importance=item.importance,
                summary=item.summary or "(empty)",
                protected_reasons=list(item.protected_reasons),
            )
            for item in output.results
        ]
        self._voice_context = (
            VoiceResultContext(
                label=f"your last stored review session {output.session.session_id}",
                items=items,
            )
            if items
            else None
        )
        messages = [
            f"Showing stored SQLite review output from session {output.session.session_id}.",
            f"Session type: {output.session.command}. Query: {output.session.query}",
        ]
        if output.filter_name:
            filter_label = output.filter_name.replace("_", " ")
            messages.append(f"Filter: {filter_label}.")
        if not items:
            messages.append("No stored messages matched that review filter.")
            return messages

        if len(items) > 3:
            messages.extend(self._voice_summary_messages(items))
            messages.append(
                "Say details to hear more. Say candidates to hear cleanup candidates. "
                "Say important to hear important or protected messages."
            )
            return messages

        messages.extend(self._voice_detail_lines(items))
        return messages

    def _voice_summary_messages(self, items: list[VoiceResultItem]) -> list[str]:
        important_count = sum(1 for item in items if item.important_or_protected)
        review_count = sum(1 for item in items if item.final_recommendation == "review")
        candidate_count = sum(
            1 for item in items if item.final_recommendation == "trash_candidate"
        )
        messages = [
            (
                f"Total: {len(items)}. Important or protected: {important_count}. "
                f"Review: {review_count}. Trash candidates: {candidate_count}."
            )
        ]
        notable_items = sorted(
            items,
            key=lambda item: (_voice_notable_rank(item), item.display_id),
        )[:3]
        messages.append("Top items:")
        for item in notable_items:
            messages.append(self._voice_item_brief(item))
        return messages

    def _voice_page_messages(
        self,
        filter_name: str,
        intro: str,
        reset: bool,
    ) -> list[str]:
        if self._voice_context is None:
            return [
                "I do not have recent voice results to expand yet.",
                "Ask for unread, cleanup, search, or review first.",
            ]

        items = self._voice_filtered_items(filter_name)
        if not items:
            label = {
                "all": "messages",
                "candidates": "cleanup candidates",
                "important": "important or protected messages",
            }.get(filter_name, "items")
            return [f"There are no {label} in {self._voice_context.label}."]

        if reset or self._voice_context.active_filter != filter_name:
            self._voice_context.active_filter = filter_name
            self._voice_context.next_index = 0

        start = self._voice_context.next_index
        if start >= len(items):
            return ["That is the end of the current list."]

        end = min(start + 3, len(items))
        self._voice_context.next_index = end

        messages = [
            intro,
            f"Showing {start + 1} through {end} of {len(items)}.",
        ]
        messages.extend(self._voice_detail_lines(items[start:end]))
        if end < len(items):
            messages.append("Say next to hear more.")
        else:
            messages.append("That is the end of the current list.")
        return messages

    def _voice_filtered_items(self, filter_name: str) -> list[VoiceResultItem]:
        if self._voice_context is None:
            return []
        if filter_name == "all":
            return list(self._voice_context.items)
        if filter_name == "candidates":
            return [
                item
                for item in self._voice_context.items
                if item.final_recommendation == "trash_candidate"
            ]
        if filter_name == "important":
            return [
                item for item in self._voice_context.items if item.important_or_protected
            ]
        return list(self._voice_context.items)

    def _voice_detail_lines(self, items: list[VoiceResultItem]) -> list[str]:
        return [self._voice_item_detail(item) for item in items]

    def _voice_item_brief(self, item: VoiceResultItem) -> str:
        recommendation = _pretty_label(item.final_recommendation)
        return (
            f"[{item.display_id}] {recommendation}. {item.sender}. "
            f"{item.subject}. {item.summary}"
        )

    def _voice_item_detail(self, item: VoiceResultItem) -> str:
        detail = (
            f"[{item.display_id}] {_pretty_label(item.final_recommendation)} from "
            f"{item.sender}. {item.subject}. Category {_pretty_label(item.category)}. "
            f"Importance {_pretty_label(item.importance)}. Summary: {item.summary}"
        )
        if item.protected_reasons:
            detail += (
                ". Protected because " + ", ".join(item.protected_reasons)
            )
        return detail


def _detect_voice_follow_up(text: str) -> ChatIntentName | None:
    lowered = " ".join(text.lower().split())
    if lowered in {"details", "detail", "tell me more", "hear more", "more details"}:
        return ChatIntentName.DETAILS
    if lowered in {"candidates", "candidate", "cleanup candidates", "trash candidates"}:
        return ChatIntentName.CANDIDATES
    if lowered in {"important", "important messages", "important ones"}:
        return ChatIntentName.IMPORTANT_FOLLOW_UP
    if lowered in {"next", "next one", "next items", "continue", "go on", "more"}:
        return ChatIntentName.NEXT
    if lowered in {"stop", "stop there", "that's enough", "thats enough", "enough"}:
        return ChatIntentName.STOP
    return None


def _voice_notable_rank(item: VoiceResultItem) -> int:
    if item.important_or_protected:
        return 0
    if item.final_recommendation == "review":
        return 1
    if item.final_recommendation == "trash_candidate":
        return 2
    return 3


def _pretty_label(value: str) -> str:
    return value.replace("_", " ")
