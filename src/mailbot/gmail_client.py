from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import GmailSettings
from .exceptions import AuthRequiredError, ConfigError, MailBotError
from .models import ParsedMessage
from .parser import parse_message


class GmailClient:
    def __init__(self, settings: GmailSettings) -> None:
        self.settings = settings

    def authenticate(self) -> str:
        service = self._build_service(interactive=True)
        profile = service.users().getProfile(userId="me").execute()
        return str(profile.get("emailAddress", "unknown"))

    def fetch_messages(
        self, query: str, limit: int, body_preview_chars: int
    ) -> list[ParsedMessage]:
        service = self._build_service(interactive=False)
        try:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=limit,
                    includeSpamTrash=False,
                )
                .execute()
            )
        except HttpError as exc:
            raise MailBotError(f"Unable to list Gmail messages: {exc}") from exc

        results: list[ParsedMessage] = []
        for message_stub in response.get("messages", []) or []:
            try:
                raw_message = (
                    service.users()
                    .messages()
                    .get(userId="me", id=message_stub["id"], format="full")
                    .execute()
                )
            except HttpError as exc:
                raise MailBotError(
                    f"Unable to read Gmail message {message_stub['id']}: {exc}"
                ) from exc
            results.append(parse_message(raw_message, body_preview_chars))

        return results

    def trash_message(self, message_id: str) -> None:
        service = self._build_service(interactive=False)
        try:
            service.users().messages().trash(userId="me", id=message_id).execute()
        except HttpError as exc:
            raise MailBotError(f"Unable to move Gmail message to trash: {exc}") from exc

    def message_has_trash_label(self, message_id: str) -> bool:
        service = self._build_service(interactive=False)
        try:
            response = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="minimal")
                .execute()
            )
        except HttpError as exc:
            raise MailBotError(
                f"Unable to verify Gmail message {message_id} after trash move: {exc}"
            ) from exc
        label_ids = set(response.get("labelIds", []) or [])
        return "TRASH" in label_ids

    def _build_service(self, interactive: bool) -> Any:
        credentials = self._load_credentials(interactive=interactive)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _load_credentials(self, interactive: bool) -> Credentials:
        token_path = self.settings.token_path
        credentials_path = self.settings.credentials_path
        scopes = self.settings.scopes
        credentials: Credentials | None = None

        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
            if not set(scopes).issubset(set(credentials.scopes or [])):
                credentials = None

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self._save_credentials(credentials, token_path)
            return credentials

        if not interactive:
            raise AuthRequiredError(
                "Gmail credentials are not ready. Run `mailbot auth` first."
            )

        if not credentials_path.exists():
            raise ConfigError(
                f"Gmail desktop OAuth credentials not found: {credentials_path}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
        credentials = flow.run_local_server(port=0)
        self._save_credentials(credentials, token_path)
        return credentials

    def _save_credentials(self, credentials: Credentials, token_path: Path) -> None:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
