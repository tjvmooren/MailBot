from __future__ import annotations

import json

from mailbot.chat_intents import ChatIntent

from .base import ChatIntentParser, ChatIntentParserError

SYSTEM_PROMPT = """
You route user requests for MailBot, a safe Gmail assistant.

Return a structured result using one of these exact intents:
- auth
- unread
- important
- cleanup
- search
- review
- trash
- help
- exit
- unsupported

Rules:
- Only route to existing MailBot capabilities.
- Never invent send, draft, reply, archive, or permanent delete capabilities.
- For trash, extract display IDs from the latest actionable session mapping.
- If a trash request does not include IDs, ask a short clarification question.
- For review filters, use one of: trash_candidates, protected, review, keep.
- For search, convert the user's request into a Gmail query string when possible.
- If the request is unsupported, set intent=unsupported and explain the limitation briefly.
- Keep clarification questions short.
- Return JSON-compatible values only.
""".strip()


class OpenAIChatIntentParser(ChatIntentParser):
    description = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ChatIntentParserError("OPENAI_API_KEY is missing.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ChatIntentParserError(
                "The `openai` package is not installed. Run `python -m pip install -e .`."
            ) from exc

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def parse_intent(self, user_message: str) -> ChatIntent:
        try:
            return self._parse_with_structured_outputs(user_message)
        except Exception:
            try:
                return self._parse_with_json_mode(user_message)
            except Exception as exc:
                raise ChatIntentParserError(f"OpenAI intent parsing failed: {exc}") from exc

    def _parse_with_structured_outputs(self, user_message: str) -> ChatIntent:
        if not hasattr(self.client.responses, "parse"):
            raise RuntimeError("Structured output parsing is unavailable in this SDK version.")

        response = self.client.responses.parse(
            model=self.model,
            input=self._build_messages(user_message),
            text_format=ChatIntent,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ValueError("OpenAI did not return a structured chat intent.")
        if isinstance(parsed, ChatIntent):
            return parsed
        return ChatIntent.model_validate(parsed)

    def _parse_with_json_mode(self, user_message: str) -> ChatIntent:
        response = self.client.responses.create(
            model=self.model,
            input=self._build_messages(user_message),
            text={"format": {"type": "json_object"}},
            max_output_tokens=400,
        )
        if not getattr(response, "output_text", ""):
            raise ValueError("OpenAI returned an empty intent response.")
        payload = json.loads(response.output_text)
        return ChatIntent.model_validate(payload)

    def _build_messages(self, user_message: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message.strip()},
        ]
