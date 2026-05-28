from __future__ import annotations

import argparse
from datetime import datetime

from .config import AppConfig, load_config
from .exceptions import MailBotError
from .models import AnalyzedMessage
from .service import MailBotService, ScanOutput, TrashPreview


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        service = MailBotService(config)

        if args.command == "auth":
            email_address = service.authenticate()
            print(f"Authenticated Gmail account: {email_address}")
            return 0

        if args.command == "unread":
            _print_scan(service.unread(limit=_validated_limit(args.limit)))
            return 0

        if args.command == "important":
            _print_scan(service.important(limit=_validated_limit(args.limit)))
            return 0

        if args.command == "cleanup":
            _print_scan(service.cleanup(limit=_validated_limit(args.limit)))
            return 0

        if args.command == "search":
            _print_scan(
                service.search(query=args.query, limit=_validated_limit(args.limit))
            )
            return 0

        if args.command == "trash":
            preview = service.prepare_trash(_parse_id_list(args.ids))
            _print_trash_preview(preview)
            if not preview.eligible:
                return 0
            if not args.yes:
                confirmation = input(
                    "Type TRASH to confirm moving the eligible messages to Gmail trash: "
                ).strip()
                if confirmation != "TRASH":
                    print("Trash operation cancelled.")
                    return 0
            moved = service.move_to_trash(preview)
            print(f"Moved {len(moved)} message(s) to Gmail trash.")
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except MailBotError as exc:
        print(f"Error: {exc}")
        return 1
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailbot",
        description="Safely review and clean up Gmail messages with an LLM provider.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the MailBot config file. Defaults to config.yaml.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("auth", help="Authenticate MailBot with Gmail OAuth.")

    unread_parser = subparsers.add_parser(
        "unread", help="Review recent unread Gmail messages."
    )
    unread_parser.add_argument("--limit", type=int, default=None)

    important_parser = subparsers.add_parser(
        "important", help="Review recent Gmail messages marked important."
    )
    important_parser.add_argument("--limit", type=int, default=None)

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Find likely cleanup candidates among recent Gmail messages.",
    )
    cleanup_parser.add_argument("--limit", type=int, default=None)

    trash_parser = subparsers.add_parser(
        "trash",
        help="Move safe candidates from the latest cleanup/search session to trash.",
    )
    trash_parser.add_argument("--ids", required=True, help="Comma-separated display IDs.")
    trash_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt and trash eligible messages immediately.",
    )

    search_parser = subparsers.add_parser(
        "search", help="Run a Gmail search query and classify the results."
    )
    search_parser.add_argument("query", help="A Gmail search query.")
    search_parser.add_argument("--limit", type=int, default=None)

    return parser


def _print_scan(output: ScanOutput) -> None:
    if output.provider_warning:
        print(f"Warning: {output.provider_warning}")
        print("")

    if not output.messages:
        print("No Gmail messages matched this command.")
        return

    trash_candidates = sum(1 for message in output.messages if message.safe_to_trash)
    protected = sum(1 for message in output.messages if message.protection.blocked)

    print(
        f"{len(output.messages)} message(s) from `{output.command}`"
        f" using query `{output.query}`."
    )
    if output.session_id is not None:
        print(
            f"Saved actionable session {output.session_id}. "
            "Use `mailbot trash --ids ...` with these display IDs."
        )
    print(
        f"Summary: {trash_candidates} trash candidate(s), "
        f"{protected} protected message(s), "
        f"{len(output.messages) - trash_candidates} keep/review item(s)."
    )
    print("")

    for display_id, message in enumerate(output.messages, start=1):
        _print_message(display_id, message)


def _print_message(display_id: int, message: AnalyzedMessage) -> None:
    local_time = message.parsed.received_at.astimezone()
    protection = ", ".join(message.protection.reasons) if message.protection.reasons else "none"
    attachments = (
        ", ".join(message.parsed.attachment_names)
        if message.parsed.attachment_names
        else "none"
    )

    print(
        f"[{display_id}] {message.final_recommendation.value.upper()} | "
        f"category={message.classification.category.value} | "
        f"importance={message.classification.importance.value}"
    )
    print(f"From: {message.parsed.sender}")
    print(f"Subject: {message.parsed.subject}")
    print(f"Date: {local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Snippet: {message.parsed.snippet or '(empty)'}")
    print(f"Body preview: {message.parsed.body_preview or '(empty)'}")
    print(f"Attachments: {attachments}")
    print(f"Summary: {message.classification.summary}")
    print(f"Rationale: {message.classification.rationale}")
    print(f"Protected reasons: {protection}")
    print("")


def _print_trash_preview(preview: TrashPreview) -> None:
    created_at = _format_timestamp(preview.session.created_at)
    print(
        f"Latest actionable session: {preview.session.command} "
        f"at {created_at} with query `{preview.session.query}`."
    )

    if preview.missing_ids:
        print(f"Unknown IDs: {', '.join(str(item) for item in preview.missing_ids)}")

    if preview.blocked:
        print("Blocked items:")
        for item in preview.blocked:
            reasons = ", ".join(item.protected_reasons) or item.final_recommendation
            print(f"  [{item.display_id}] {item.subject} ({reasons})")

    if preview.already_trashed:
        print("Already trashed:")
        for item in preview.already_trashed:
            print(f"  [{item.display_id}] {item.subject}")

    if preview.eligible:
        print("Eligible to trash:")
        for item in preview.eligible:
            print(f"  [{item.display_id}] {item.subject} - {item.summary}")
    else:
        print("No eligible messages to trash from the requested IDs.")


def _parse_id_list(raw_ids: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw_ids.split(","):
        value = chunk.strip()
        if not value:
            continue
        numeric = int(value)
        if numeric <= 0:
            raise ValueError("Display IDs must be positive integers.")
        if numeric not in ids:
            ids.append(numeric)
    if not ids:
        raise ValueError("Provide at least one display ID.")
    return ids


def _validated_limit(raw_limit: int | None) -> int | None:
    if raw_limit is not None and raw_limit <= 0:
        raise ValueError("--limit must be a positive integer.")
    return raw_limit


def _format_timestamp(raw_timestamp: str) -> str:
    try:
        return datetime.fromisoformat(raw_timestamp).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    except ValueError:
        return raw_timestamp
