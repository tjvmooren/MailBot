from __future__ import annotations

import json

from mailbot.models import MessageClassification, ParsedMessage

from .base import LLMProvider, LLMProviderError

SYSTEM_PROMPT = """
You classify Gmail messages for a cautious local inbox cleanup tool.

Return a structured result using these exact enums:
- category: promotional, newsletter, social, transactional, personal, job_employer, school_professor, bank_finance, government_legal, security_account, spam, other
- sender_type: human_individual, organization, automated_system, unknown
- importance: high, medium, low
- trash_recommendation: keep, review, trash_candidate

Be conservative.
- If the message looks important, personal, financial, legal, educational, employment-related, security-related, or time-sensitive, do not recommend trash.
- Distinguish real-risk action from marketing urgency.
- Real-risk signals include interviews, job applications, offer letters, recruiters, professors, assignments, due dates, invoices, bills, payment warnings, account locks, password resets, login attempts, account verification, tax/legal/government/insurance notices, and document-signature requests.
- Marketing urgency by itself does not make a message important. Phrases such as "limited time offer", "sale ends soon", "act now", "last chance", "deal expires", "save today", "exclusive offer", "order now", and "shop now" should still allow `trash_candidate` when the message is clearly promotional or newsletter content with no protected signals.
- Prefer review when uncertain.
- summary should be a short sentence.
- rationale should be a short sentence.
- Set the booleans true for real deadlines, interviews, applications, offers, or real requests for user action.
- Do not set those booleans for marketing urgency alone when the email is clearly promotional.
- Return JSON-compatible values only.
""".strip()


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise LLMProviderError("OPENAI_API_KEY is missing.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMProviderError(
                "The `openai` package is not installed. Run `python -m pip install -e .`."
            ) from exc

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def classify_message(self, message: ParsedMessage) -> MessageClassification:
        try:
            return self._classify_with_structured_outputs(message)
        except Exception:
            try:
                return self._classify_with_json_mode(message)
            except Exception as exc:
                raise LLMProviderError(f"OpenAI classification failed: {exc}") from exc

    def _classify_with_structured_outputs(
        self, message: ParsedMessage
    ) -> MessageClassification:
        if not hasattr(self.client.responses, "parse"):
            raise RuntimeError("Structured output parsing is unavailable in this SDK version.")

        response = self.client.responses.parse(
            model=self.model,
            input=self._build_messages(message),
            text_format=MessageClassification,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ValueError("OpenAI did not return a structured classification.")
        if isinstance(parsed, MessageClassification):
            return parsed
        return MessageClassification.model_validate(parsed)

    def _classify_with_json_mode(self, message: ParsedMessage) -> MessageClassification:
        response = self.client.responses.create(
            model=self.model,
            input=self._build_messages(message),
            text={"format": {"type": "json_object"}},
            max_output_tokens=400,
        )
        if not getattr(response, "output_text", ""):
            raise ValueError("OpenAI returned an empty response.")
        payload = json.loads(response.output_text)
        return MessageClassification.model_validate(payload)

    def _build_messages(self, message: ParsedMessage) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classify this email as JSON.\n\n"
                    f"From: {message.sender} <{message.sender_email}>\n"
                    f"Subject: {message.subject}\n"
                    f"Received: {message.received_at.isoformat()}\n"
                    f"Labels: {', '.join(message.label_ids) or 'none'}\n"
                    f"Has attachments: {message.has_attachments}\n"
                    f"Attachment names: {', '.join(message.attachment_names) or 'none'}\n"
                    f"Snippet: {message.snippet or '(empty)'}\n"
                    f"Body preview: {message.body_preview or '(empty)'}"
                ),
            },
        ]
