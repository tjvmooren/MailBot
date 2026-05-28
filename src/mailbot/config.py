from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .exceptions import ConfigError

DEFAULT_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


@dataclass(slots=True)
class GmailSettings:
    credentials_path: Path
    token_path: Path
    scopes: list[str]


@dataclass(slots=True)
class QuerySettings:
    unread: str
    important: str
    cleanup: str


@dataclass(slots=True)
class LimitSettings:
    default_list_limit: int
    body_preview_chars: int


@dataclass(slots=True)
class OpenAISettings:
    model: str


@dataclass(slots=True)
class AppConfig:
    provider: str
    database_path: Path
    gmail: GmailSettings
    queries: QuerySettings
    limits: LimitSettings
    openai: OpenAISettings


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    load_dotenv()
    resolved_config_path = Path(config_path).expanduser().resolve()
    if not resolved_config_path.exists():
        raise ConfigError(f"Config file not found: {resolved_config_path}")

    with resolved_config_path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle) or {}

    config_dir = resolved_config_path.parent
    provider = str(raw_config.get("provider", "openai")).strip().lower()
    database_path = _resolve_path(
        config_dir, raw_config.get("database_path", "data/mailbot.db")
    )

    gmail_raw = raw_config.get("gmail", {})
    gmail_settings = GmailSettings(
        credentials_path=_resolve_path(
            config_dir, gmail_raw.get("credentials_path", "credentials.json")
        ),
        token_path=_resolve_path(config_dir, gmail_raw.get("token_path", "token.json")),
        scopes=list(gmail_raw.get("scopes", [DEFAULT_GMAIL_SCOPE])),
    )

    query_raw = raw_config.get("queries", {})
    query_settings = QuerySettings(
        unread=str(query_raw.get("unread", "is:unread -in:trash")),
        important=str(query_raw.get("important", "is:important -in:trash")),
        cleanup=str(query_raw.get("cleanup", "is:unread -in:trash")),
    )

    limit_raw = raw_config.get("limits", {})
    limit_settings = LimitSettings(
        default_list_limit=int(limit_raw.get("default_list_limit", 10)),
        body_preview_chars=int(limit_raw.get("body_preview_chars", 600)),
    )

    openai_raw = _providers_block(raw_config).get("openai", {})
    openai_settings = OpenAISettings(model=str(openai_raw.get("model", "gpt-5-mini")))

    return AppConfig(
        provider=provider,
        database_path=database_path,
        gmail=gmail_settings,
        queries=query_settings,
        limits=limit_settings,
        openai=openai_settings,
    )


def _providers_block(raw_config: dict[str, Any]) -> dict[str, Any]:
    providers = raw_config.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("The `providers` section in config.yaml must be a mapping.")
    return providers


def _resolve_path(base_dir: Path, raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate
