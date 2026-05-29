from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import AnalyzedMessage


@dataclass(slots=True)
class StoredSession:
    session_id: int
    command: str
    query: str
    created_at: str
    actionable: bool


@dataclass(slots=True)
class StoredSessionResult:
    session_id: int
    display_id: int
    gmail_message_id: str
    sender_email: str
    sender: str
    subject: str
    received_at: str
    has_attachments: bool
    attachment_names: list[str]
    category: str
    sender_type: str
    importance: str
    model_recommendation: str
    summary: str
    rationale: str
    final_recommendation: str
    protected_reasons: list[str]
    allow_trash: bool
    trashed_at: str | None


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_session(
        self,
        command: str,
        query: str,
        results: list[AnalyzedMessage],
        actionable: bool,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_sessions (command, query, created_at, actionable)
                VALUES (?, ?, ?, ?)
                """,
                (command, query, created_at, int(actionable)),
            )
            session_id = int(cursor.lastrowid)

            for display_id, result in enumerate(results, start=1):
                connection.execute(
                    """
                    INSERT INTO scan_results (
                        session_id,
                        display_id,
                        gmail_message_id,
                        thread_id,
                        sender,
                        sender_email,
                        subject,
                        received_at,
                        snippet,
                        body_preview,
                        label_ids,
                        has_attachments,
                        attachment_names,
                        category,
                        sender_type,
                        importance,
                        model_recommendation,
                        final_recommendation,
                        protected_reasons,
                        summary,
                        rationale,
                        classification_json,
                        allow_trash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        display_id,
                        result.parsed.message_id,
                        result.parsed.thread_id,
                        result.parsed.sender,
                        result.parsed.sender_email,
                        result.parsed.subject,
                        result.parsed.received_at.isoformat(),
                        result.parsed.snippet,
                        result.parsed.body_preview,
                        json.dumps(result.parsed.label_ids),
                        int(result.parsed.has_attachments),
                        json.dumps(result.parsed.attachment_names),
                        result.classification.category.value,
                        result.classification.sender_type.value,
                        result.classification.importance.value,
                        result.classification.trash_recommendation.value,
                        result.final_recommendation.value,
                        json.dumps(result.protection.reasons),
                        result.classification.summary,
                        result.classification.rationale,
                        result.classification.model_dump_json(),
                        int(result.safe_to_trash),
                    ),
                )

            return session_id

    def get_latest_actionable_session(self) -> StoredSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, command, query, created_at, actionable
                FROM scan_sessions
                WHERE actionable = 1
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return StoredSession(
            session_id=int(row["id"]),
            command=str(row["command"]),
            query=str(row["query"]),
            created_at=str(row["created_at"]),
            actionable=bool(row["actionable"]),
        )

    def get_session_results(
        self, session_id: int, display_ids: list[int] | None = None
    ) -> list[StoredSessionResult]:
        query = """
            SELECT
                session_id,
                display_id,
                gmail_message_id,
                sender_email,
                sender,
                subject,
                received_at,
                has_attachments,
                attachment_names,
                category,
                sender_type,
                importance,
                model_recommendation,
                summary,
                rationale,
                final_recommendation,
                protected_reasons,
                allow_trash,
                trashed_at
            FROM scan_results
            WHERE session_id = ?
        """
        parameters: list[int] = [session_id]
        if display_ids:
            placeholders = ", ".join("?" for _ in display_ids)
            query += f" AND display_id IN ({placeholders})"
            parameters.extend(display_ids)
        query += " ORDER BY display_id ASC"

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [
            StoredSessionResult(
                session_id=int(row["session_id"]),
                display_id=int(row["display_id"]),
                gmail_message_id=str(row["gmail_message_id"]),
                sender_email=str(row["sender_email"]),
                sender=str(row["sender"]),
                subject=str(row["subject"]),
                received_at=str(row["received_at"]),
                has_attachments=bool(row["has_attachments"]),
                attachment_names=json.loads(str(row["attachment_names"])),
                category=str(row["category"]),
                sender_type=str(row["sender_type"]),
                importance=str(row["importance"]),
                model_recommendation=str(row["model_recommendation"]),
                summary=str(row["summary"]),
                rationale=str(row["rationale"]),
                final_recommendation=str(row["final_recommendation"]),
                protected_reasons=json.loads(str(row["protected_reasons"])),
                allow_trash=bool(row["allow_trash"]),
                trashed_at=str(row["trashed_at"]) if row["trashed_at"] else None,
            )
            for row in rows
        ]

    def mark_trashed(self, session_id: int, display_ids: list[int]) -> None:
        if not display_ids:
            return
        trashed_at = datetime.now(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in display_ids)
        parameters = [trashed_at, session_id, *display_ids]
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE scan_results
                SET trashed_at = ?
                WHERE session_id = ? AND display_id IN ({placeholders})
                """,
                parameters,
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    query TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actionable INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS scan_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    display_id INTEGER NOT NULL,
                    gmail_message_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    body_preview TEXT NOT NULL,
                    label_ids TEXT NOT NULL,
                    has_attachments INTEGER NOT NULL DEFAULT 0,
                    attachment_names TEXT NOT NULL,
                    category TEXT NOT NULL,
                    sender_type TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    model_recommendation TEXT NOT NULL,
                    final_recommendation TEXT NOT NULL,
                    protected_reasons TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    classification_json TEXT NOT NULL,
                    allow_trash INTEGER NOT NULL DEFAULT 0,
                    trashed_at TEXT,
                    UNIQUE(session_id, display_id),
                    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
