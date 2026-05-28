from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

from .models import ParsedMessage

_WHITESPACE_RE = re.compile(r"\s+")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def parse_message(raw_message: dict[str, Any], body_preview_chars: int) -> ParsedMessage:
    payload = raw_message.get("payload", {}) or {}
    headers = {
        str(header.get("name", "")).lower(): str(header.get("value", "")).strip()
        for header in payload.get("headers", []) or []
        if header.get("name")
    }

    sender_header = headers.get("from", "Unknown sender")
    sender_name, sender_email = parseaddr(sender_header)
    sender_display = sender_name or sender_email or sender_header
    received_at = _parse_datetime(
        headers.get("date", ""), raw_message.get("internalDate", "")
    )

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachment_names: list[str] = []
    _walk_parts(payload, plain_parts, html_parts, attachment_names)

    body_text = "\n".join(part for part in plain_parts if part.strip())
    if not body_text:
        body_text = "\n".join(part for part in html_parts if part.strip())

    snippet = _normalize_text(str(raw_message.get("snippet", "")))
    preview_source = _normalize_text(body_text) or snippet
    body_preview = _truncate(preview_source, body_preview_chars)

    return ParsedMessage(
        message_id=str(raw_message.get("id", "")),
        thread_id=str(raw_message.get("threadId", "")),
        sender=sender_display,
        sender_email=sender_email.lower(),
        subject=headers.get("subject", "(no subject)"),
        received_at=received_at,
        snippet=snippet,
        body_preview=body_preview,
        label_ids=list(raw_message.get("labelIds", []) or []),
        has_attachments=bool(attachment_names),
        attachment_names=_unique(attachment_names),
        headers=headers,
    )


def _walk_parts(
    part: dict[str, Any],
    plain_parts: list[str],
    html_parts: list[str],
    attachment_names: list[str],
) -> None:
    mime_type = str(part.get("mimeType", "")).lower()
    filename = str(part.get("filename", "")).strip()
    body = part.get("body", {}) or {}

    if body.get("attachmentId"):
        attachment_names.append(filename or "(unnamed attachment)")

    body_data = _decode_body(body.get("data"))
    if mime_type == "text/plain" and body_data:
        plain_parts.append(body_data)
    elif mime_type == "text/html" and body_data:
        html_parts.append(_strip_html(body_data))

    for child in part.get("parts", []) or []:
        _walk_parts(child, plain_parts, html_parts, attachment_names)


def _decode_body(raw_data: Any) -> str:
    if not raw_data:
        return ""
    data = str(raw_data)
    padded = data + ("=" * (-len(data) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
    except (ValueError, TypeError):
        return ""
    return decoded.decode("utf-8", errors="replace")


def _strip_html(raw_html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(raw_html)
    parser.close()
    return _normalize_text(unescape(parser.get_text()))


def _parse_datetime(date_header: str, internal_date: str) -> datetime:
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (TypeError, ValueError, IndexError, OverflowError):
            pass

    if internal_date:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    return datetime.now(tz=timezone.utc)


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 3)].rstrip() + "..."


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
