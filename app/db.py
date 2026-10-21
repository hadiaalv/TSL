"""SQLite persistence for businesses, tasks, notes, approvals, chat."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

TASK_STATUSES = (
    "open",
    "in_progress",
    "waiting_on",
    "delegated",
    "blocked",
    "done",
    "cancelled",
)

PRIORITIES = ("P0", "P1", "P2", "P3")

CATEGORIES = (
    "npd",
    "new_line_form",
    "supplier",
    "client",
    "retailer_outreach",
    "distributor_outreach",
    "follow_up",
    "meeting_action",
    "decision",
    "waiting_on",
    "delegated",
    "opportunity",
    "other",
)

APPROVAL_TYPES = (
    "commercial_outreach",
    "pricing",
    "commitment",
    "external_email",
    "other",
)

_GOOGLE_SECRET_PREFIX = "enc:v1:"
_GOOGLE_SECRET_FIELDS = ("token", "refresh_token", "client_secret")


def _google_token_cipher() -> Fernet:
    if not settings.auth_secret:
        raise RuntimeError(
            "AUTH_SECRET must be configured to protect stored Google credentials."
        )
    key = base64.urlsafe_b64encode(
        hashlib.sha256(
            ("google-token-storage:" + settings.auth_secret).encode("utf-8")
        ).digest()
    )
    return Fernet(key)


def _encrypt_google_secret(value: Optional[str]) -> Optional[str]:
    if not value or value.startswith(_GOOGLE_SECRET_PREFIX):
        return value
    encrypted = _google_token_cipher().encrypt(value.encode("utf-8")).decode("ascii")
    return _GOOGLE_SECRET_PREFIX + encrypted


def _decrypt_google_secret(value: Optional[str]) -> Optional[str]:
    if not value or not value.startswith(_GOOGLE_SECRET_PREFIX):
        return value
    try:
        decrypted = _google_token_cipher().decrypt(
            value[len(_GOOGLE_SECRET_PREFIX) :].encode("ascii")
        )
    except InvalidToken as exc:
        raise RuntimeError(
            "Stored Google credentials cannot be decrypted. Restore the original "
            "AUTH_SECRET before reconnecting Google."
        ) from exc
    return decrypted.decode("utf-8")


def encrypt_google_token_data(data: dict[str, Any]) -> dict[str, Any]:
    encrypted = dict(data)
    for field in _GOOGLE_SECRET_FIELDS:
        value = encrypted.get(field)
        if value is not None:
            encrypted[field] = _encrypt_google_secret(str(value))
    return encrypted


def decrypt_google_token_data(data: dict[str, Any]) -> dict[str, Any]:
    decrypted = dict(data)
    for field in _GOOGLE_SECRET_FIELDS:
        value = decrypted.get(field)
        if value is not None:
            decrypted[field] = _decrypt_google_secret(str(value))
    return decrypted


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _uid() -> str:
    return uuid4().hex[:12]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS businesses (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                context_md TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                logo_url TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS brand_links (
                id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL REFERENCES businesses(id),
                kind TEXT DEFAULT 'other',
                label TEXT DEFAULT '',
                url TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                business_id TEXT NOT NULL REFERENCES businesses(id),
                title TEXT NOT NULL,
                category TEXT DEFAULT 'other',
                owner TEXT DEFAULT 'me',
                priority TEXT DEFAULT 'P2',
                deadline TEXT,
                status TEXT DEFAULT 'open',
                next_follow_up TEXT,
                waiting_on TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                source_ref TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_notes (
                id TEXT PRIMARY KEY,
                business_id TEXT REFERENCES businesses(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                business_id TEXT REFERENCES businesses(id),
                task_id TEXT REFERENCES tasks(id),
                action_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                meta_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id TEXT PRIMARY KEY,
                provider_message_id TEXT UNIQUE,
                user_id TEXT NOT NULL REFERENCES users(id),
                sender_phone TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_signals (
                id TEXT PRIMARY KEY,
                gmail_id TEXT UNIQUE,
                thread_id TEXT,
                subject TEXT,
                from_addr TEXT,
                snippet TEXT,
                received_at TEXT,
                business_id TEXT REFERENCES businesses(id),
                proposed_task_json TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                role TEXT DEFAULT 'operator',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_comments (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                author_user_id TEXT NOT NULL REFERENCES users(id),
                content TEXT NOT NULL,
                mentioned_user_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                recipient_user_id TEXT NOT NULL REFERENCES users(id),
                actor_user_id TEXT REFERENCES users(id),
                notification_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                target_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                UNIQUE (recipient_user_id, notification_type, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_recipient_created
                ON notifications (recipient_user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id TEXT PRIMARY KEY REFERENCES users(id),
                sound_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS google_tokens (
                user_id TEXT PRIMARY KEY REFERENCES users(id),
                token TEXT,
                refresh_token TEXT,
                token_uri TEXT,
                client_id TEXT,
                client_secret TEXT,
                scopes TEXT DEFAULT '[]',
                email TEXT DEFAULT '',
                last_email_pull_at TEXT,
                last_calendar_pull_at TEXT,
                last_email_count INTEGER DEFAULT 0,
                last_calendar_count INTEGER DEFAULT 0,
                last_drive_pull_at TEXT,
                last_drive_count INTEGER DEFAULT 0,
                expiry TEXT,
                connection_status TEXT DEFAULT 'unknown',
                connection_error TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS google_drive_brand_sync (
                user_id TEXT NOT NULL REFERENCES users(id),
                business_id TEXT NOT NULL REFERENCES businesses(id),
                folder_id TEXT,
                status TEXT NOT NULL,
                files_seen INTEGER DEFAULT 0,
                files_read INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                last_successful_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, business_id)
            );

            CREATE TABLE IF NOT EXISTS google_drive_sources (
                user_id TEXT NOT NULL REFERENCES users(id),
                business_id TEXT NOT NULL REFERENCES businesses(id),
                file_id TEXT NOT NULL,
                name TEXT NOT NULL,
                mime_type TEXT DEFAULT '',
                modified_time TEXT,
                parent_id TEXT,
                web_view_link TEXT,
                content_status TEXT NOT NULL,
                content_error TEXT DEFAULT '',
                content TEXT DEFAULT '',
                content_truncated INTEGER DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (user_id, file_id)
            );
            """
        )
        _migrate_schema(conn)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Idempotent column/table upgrades for existing DBs."""
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'operator'")
        # Existing single-tenant installs become super_admins
        conn.execute("UPDATE users SET role = 'super_admin'")

    business_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(businesses)").fetchall()
    }
    if "logo_url" not in business_cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN logo_url TEXT DEFAULT ''")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS slack_tokens (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            access_token TEXT NOT NULL,
            slack_user_id TEXT NOT NULL,
            team_id TEXT DEFAULT '',
            team_name TEXT DEFAULT '',
            scopes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brand_preferences (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            starred INTEGER NOT NULL DEFAULT 0,
            last_visited_at TEXT,
            PRIMARY KEY (user_id, business_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_events (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'event',
            starts_at TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workspace_events_start "
        "ON workspace_events (starts_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workspace_events_business_start "
        "ON workspace_events (business_id, starts_at)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_tokens (
            user_id TEXT PRIMARY KEY REFERENCES users(id),
            token TEXT,
            refresh_token TEXT,
            token_uri TEXT,
            client_id TEXT,
            client_secret TEXT,
            scopes TEXT DEFAULT '[]',
            email TEXT DEFAULT '',
            last_email_pull_at TEXT,
            last_calendar_pull_at TEXT,
            last_email_count INTEGER DEFAULT 0,
            last_calendar_count INTEGER DEFAULT 0,
            last_drive_pull_at TEXT,
            last_drive_count INTEGER DEFAULT 0,
            expiry TEXT,
            connection_status TEXT DEFAULT 'unknown',
            connection_error TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    gt_cols = {row[1] for row in conn.execute("PRAGMA table_info(google_tokens)").fetchall()}
    for col, decl in (
        ("email", "TEXT DEFAULT ''"),
        ("last_email_pull_at", "TEXT"),
        ("last_calendar_pull_at", "TEXT"),
        ("last_email_count", "INTEGER DEFAULT 0"),
        ("last_calendar_count", "INTEGER DEFAULT 0"),
        ("last_drive_pull_at", "TEXT"),
        ("last_drive_count", "INTEGER DEFAULT 0"),
        ("expiry", "TEXT"),
        ("connection_status", "TEXT DEFAULT 'unknown'"),
        ("connection_error", "TEXT DEFAULT ''"),
    ):
        if col not in gt_cols:
            conn.execute(f"ALTER TABLE google_tokens ADD COLUMN {col} {decl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_drive_brand_sync (
            user_id TEXT NOT NULL REFERENCES users(id),
            business_id TEXT NOT NULL REFERENCES businesses(id),
            folder_id TEXT,
            status TEXT NOT NULL,
            files_seen INTEGER DEFAULT 0,
            files_read INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            last_successful_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, business_id)
        )
        """
    )
    sync_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(google_drive_brand_sync)"
        ).fetchall()
    }
    if "error" not in sync_cols:
        conn.execute(
            "ALTER TABLE google_drive_brand_sync ADD COLUMN error TEXT DEFAULT ''"
        )
    if "last_successful_at" not in sync_cols:
        conn.execute(
            "ALTER TABLE google_drive_brand_sync ADD COLUMN last_successful_at TEXT"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_drive_sources (
            user_id TEXT NOT NULL REFERENCES users(id),
            business_id TEXT NOT NULL REFERENCES businesses(id),
            file_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT DEFAULT '',
            modified_time TEXT,
            parent_id TEXT,
            web_view_link TEXT,
            content_status TEXT NOT NULL,
            content_error TEXT DEFAULT '',
            content TEXT DEFAULT '',
            content_truncated INTEGER DEFAULT 0,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (user_id, file_id)
        )
        """
    )
    source_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(google_drive_sources)"
        ).fetchall()
    }
    if "content" not in source_cols:
        conn.execute(
            "ALTER TABLE google_drive_sources ADD COLUMN content TEXT DEFAULT ''"
        )
    if "content_truncated" not in source_cols:
        conn.execute(
            "ALTER TABLE google_drive_sources ADD COLUMN content_truncated INTEGER DEFAULT 0"
        )


def row_to_dict(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


# ── Users / auth ─────────────────────────────────────────────

USER_ROLES = ("super_admin", "admin", "operator")


def count_users() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] if row else 0)


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


def get_user_by_username(username: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    return row_to_dict(row)


def list_users(include_inactive: bool = True) -> list[dict[str, Any]]:
    q = "SELECT * FROM users"
    if not include_inactive:
        q += " WHERE status = 'active'"
    q += " ORDER BY role, username"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def create_user(
    username: str,
    password_hash: str,
    display_name: str = "",
    role: str = "operator",
) -> dict[str, Any]:
    if role not in USER_ROLES:
        role = "operator"
    uid = _uid()
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users (id, username, password_hash, display_name, role, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (uid, username.strip(), password_hash, display_name or username.strip(), role, now, now),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return row_to_dict(row) or {}


def update_user(user_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"display_name", "role", "status", "password_hash"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "role" in updates and updates["role"] not in USER_ROLES:
        raise ValueError("Invalid role")
    if not updates:
        return get_user(user_id)
    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [user_id]
    with connect() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE id = ?", vals)
    return get_user(user_id)


def ensure_bootstrap_admin(username: str, password: str, password_hasher) -> Optional[dict[str, Any]]:
    """Create the first super_admin from .env when the users table is empty."""
    if not username or not password:
        return None
    if count_users() > 0:
        return None
    return create_user(
        username=username,
        password_hash=password_hasher(password),
        display_name=username,
        role="super_admin",
    )


def save_google_token(user_id: str, data: dict[str, Any]) -> None:
    now = _now()
    scopes = data.get("scopes") or []
    scopes_json = json.dumps(list(scopes))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO google_tokens (
                user_id, token, refresh_token, token_uri, client_id, client_secret, scopes,
                email, last_email_pull_at, last_calendar_pull_at, last_email_count, last_calendar_count,
                last_drive_pull_at, last_drive_count, expiry, connection_status, connection_error,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                token = excluded.token,
                refresh_token = COALESCE(NULLIF(excluded.refresh_token, ''), google_tokens.refresh_token),
                token_uri = excluded.token_uri,
                client_id = excluded.client_id,
                client_secret = excluded.client_secret,
                scopes = excluded.scopes,
                expiry = COALESCE(excluded.expiry, google_tokens.expiry),
                connection_status = CASE
                    WHEN excluded.connection_status != 'unknown' THEN excluded.connection_status
                    ELSE google_tokens.connection_status
                END,
                connection_error = CASE
                    WHEN excluded.connection_status != 'unknown' THEN excluded.connection_error
                    ELSE google_tokens.connection_error
                END,
                email = CASE
                    WHEN excluded.email IS NOT NULL AND excluded.email != '' THEN excluded.email
                    ELSE google_tokens.email
                END,
                last_email_pull_at = COALESCE(excluded.last_email_pull_at, google_tokens.last_email_pull_at),
                last_calendar_pull_at = COALESCE(excluded.last_calendar_pull_at, google_tokens.last_calendar_pull_at),
                last_email_count = COALESCE(excluded.last_email_count, google_tokens.last_email_count),
                last_calendar_count = COALESCE(excluded.last_calendar_count, google_tokens.last_calendar_count),
                last_drive_pull_at = COALESCE(excluded.last_drive_pull_at, google_tokens.last_drive_pull_at),
                last_drive_count = COALESCE(excluded.last_drive_count, google_tokens.last_drive_count),
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                _encrypt_google_secret(data.get("token")),
                _encrypt_google_secret(data.get("refresh_token")),
                data.get("token_uri"),
                data.get("client_id"),
                _encrypt_google_secret(data.get("client_secret")),
                scopes_json,
                data.get("email") or "",
                data.get("last_email_pull_at"),
                data.get("last_calendar_pull_at"),
                data.get("last_email_count"),
                data.get("last_calendar_count"),
                data.get("last_drive_pull_at"),
                data.get("last_drive_count"),
                data.get("expiry"),
                data.get("connection_status") or "unknown",
                data.get("connection_error") or "",
                now,
            ),
        )


def set_google_connection_state(
    user_id: str,
    status: str,
    error: str = "",
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE google_tokens
            SET connection_status = ?, connection_error = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (status, error, now, user_id),
        )


def record_google_pull(
    user_id: str,
    *,
    email_count: Optional[int] = None,
    calendar_count: Optional[int] = None,
    drive_count: Optional[int] = None,
) -> None:
    now = _now()
    with connect() as conn:
        if email_count is not None:
            conn.execute(
                """
                UPDATE google_tokens
                SET last_email_pull_at = ?, last_email_count = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (now, int(email_count), now, user_id),
            )
        if calendar_count is not None:
            conn.execute(
                """
                UPDATE google_tokens
                SET last_calendar_pull_at = ?, last_calendar_count = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (now, int(calendar_count), now, user_id),
            )
        if drive_count is not None:
            conn.execute(
                """
                UPDATE google_tokens
                SET last_drive_pull_at = ?, last_drive_count = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (now, int(drive_count), now, user_id),
            )


def get_google_token(user_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM google_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        values_to_migrate: dict[str, str] = {}
        for field in _GOOGLE_SECRET_FIELDS:
            value = data.get(field)
            data[field] = _decrypt_google_secret(value)
            if value and not value.startswith(_GOOGLE_SECRET_PREFIX):
                values_to_migrate[field] = _encrypt_google_secret(value) or ""
        if values_to_migrate:
            assignments = ", ".join(
                f"{field} = ?" for field in values_to_migrate
            )
            conn.execute(
                f"UPDATE google_tokens SET {assignments} WHERE user_id = ?",
                (*values_to_migrate.values(), user_id),
            )
    try:
        data["scopes"] = json.loads(data.get("scopes") or "[]")
    except json.JSONDecodeError:
        data["scopes"] = []
    return data


def record_google_drive_sync(
    user_id: str,
    results: list[dict[str, Any]],
) -> None:
    now = _now()
    with connect() as conn:
        for result in results:
            business_id = str(result["business_id"])
            files = result.get("files") or []
            files_read = sum(
                file.get("content_status") in ("read", "cached")
                for file in files
            )
            error_count = sum(
                file.get("content_status") in ("error", "unsupported", "metadata_only")
                for file in files
            )
            conn.execute(
                """
                INSERT INTO google_drive_brand_sync (
                    user_id, business_id, folder_id, status, files_seen,
                    files_read, error_count, error, last_successful_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, business_id) DO UPDATE SET
                    folder_id = excluded.folder_id,
                    status = excluded.status,
                    files_seen = excluded.files_seen,
                    files_read = excluded.files_read,
                    error_count = excluded.error_count,
                    error = excluded.error,
                    last_successful_at = CASE
                        WHEN excluded.status IN ('imported', 'unchanged', 'no_files')
                        THEN excluded.updated_at
                        ELSE google_drive_brand_sync.last_successful_at
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    business_id,
                    result.get("folder_id"),
                    result["status"],
                    len(files),
                    files_read,
                    error_count,
                    result.get("error")
                    or "; ".join(
                        f"{file.get('name') or '(untitled)'}: "
                        f"{file.get('content_error')}"
                        for file in files
                        if file.get("content_status")
                        in ("error", "unsupported", "metadata_only")
                    )[:1000],
                    now if result["status"] in ("imported", "unchanged", "no_files") else None,
                    now,
                ),
            )
            for file in files:
                if not file.get("id"):
                    continue
                conn.execute(
                    """
                    INSERT INTO google_drive_sources (
                        user_id, business_id, file_id, name, mime_type,
                        modified_time, parent_id, web_view_link, content_status,
                        content_error, content, content_truncated, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, file_id) DO UPDATE SET
                        business_id = excluded.business_id,
                        name = excluded.name,
                        mime_type = excluded.mime_type,
                        modified_time = excluded.modified_time,
                        parent_id = excluded.parent_id,
                        web_view_link = excluded.web_view_link,
                        content_status = excluded.content_status,
                        content_error = excluded.content_error,
                        content = CASE
                            WHEN excluded.content_status IN ('read', 'cached')
                            THEN excluded.content
                            ELSE google_drive_sources.content
                        END,
                        content_truncated = CASE
                            WHEN excluded.content_status IN ('read', 'cached')
                            THEN excluded.content_truncated
                            ELSE google_drive_sources.content_truncated
                        END,
                        synced_at = excluded.synced_at
                    """,
                    (
                        user_id,
                        business_id,
                        str(file["id"]),
                        file.get("name") or "(untitled)",
                        file.get("mime_type") or "",
                        file.get("modified_time"),
                        (file.get("parent_ids") or [None])[0],
                        file.get("web_view_link"),
                        file.get("content_status") or "unknown",
                        file.get("content_error") or "",
                        file.get("content") or "",
                        bool(file.get("content_truncated")),
                        now,
                    ),
                )


def get_google_drive_business_sync(
    user_id: str,
    business_id: str,
) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT status, folder_id, files_seen, files_read, error_count,
                   error, last_successful_at, updated_at
            FROM google_drive_brand_sync
            WHERE user_id = ? AND business_id = ?
            """,
            (user_id, business_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sources"] = [
            dict(source)
            for source in conn.execute(
                """
                SELECT file_id, name, mime_type, modified_time, parent_id,
                       web_view_link, content_status, content_error,
                       content_truncated, synced_at
                FROM google_drive_sources
                WHERE user_id = ? AND business_id = ?
                ORDER BY name COLLATE NOCASE, file_id
                """,
                (user_id, business_id),
            ).fetchall()
        ]
    return result


def get_google_drive_file_cache(
    user_id: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT business_id, file_id, modified_time, content, content_truncated
            FROM google_drive_sources
            WHERE user_id = ?
              AND content_status IN ('read', 'cached')
              AND content IS NOT NULL
              AND content != ''
            """,
            (user_id,),
        ).fetchall()
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        cache.setdefault(row["business_id"], {})[row["file_id"]] = {
            "modified_time": row["modified_time"],
            "content": row["content"],
            "content_truncated": bool(row["content_truncated"]),
        }
    return cache


def delete_google_token(user_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (user_id,))


def google_token_user_ids() -> list[str]:
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT user_id FROM google_tokens").fetchall()]


# ── Businesses ──────────────────────────────────────────────


def create_business(
    name: str,
    description: str = "",
    context_md: str = "",
    slug: Optional[str] = None,
    status: str = "active",
    logo_url: str = "",
) -> dict[str, Any]:
    bid = _uid()
    now = _now()
    slug = slug or name.lower().replace(" ", "-").replace("/", "-")[:40]
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO businesses (id, name, slug, description, context_md, status, logo_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bid, name, slug, description, context_md, status, logo_url, now, now),
        )
    return get_business(bid)  # type: ignore


def list_businesses(active_only: bool = True) -> list[dict[str, Any]]:
    q = "SELECT * FROM businesses"
    if active_only:
        q += " WHERE status NOT IN ('archived', 'inactive')"
    q += " ORDER BY name"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_business(business_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
        )


def get_business_by_slug(slug: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM businesses WHERE slug = ?", (slug,)).fetchone()
        )


def list_brand_links(business_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM brand_links WHERE business_id = ? ORDER BY created_at, id",
            (business_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_brand_link(business_id: str, kind: str, label: str, url: str) -> dict[str, Any]:
    link = {
        "id": _uid(),
        "business_id": business_id,
        "kind": kind,
        "label": label,
        "url": url,
        "created_at": _now(),
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO brand_links (id, business_id, kind, label, url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(link.values()),
        )
    return link


def delete_brand_link(business_id: str, link_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM brand_links WHERE id = ? AND business_id = ?", (link_id, business_id)
        )
    return cur.rowcount > 0


def update_business(business_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"name", "description", "context_md", "status", "slug", "logo_url"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_business(business_id)
    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(
            f"UPDATE businesses SET {cols} WHERE id = ?",
            (*updates.values(), business_id),
        )
    return get_business(business_id)


def set_brand_starred(user_id: str, business_id: str, starred: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO brand_preferences (user_id, business_id, starred)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, business_id)
            DO UPDATE SET starred = excluded.starred
            """,
            (user_id, business_id, int(starred)),
        )


def record_brand_visit(user_id: str, business_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO brand_preferences (user_id, business_id, last_visited_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, business_id)
            DO UPDATE SET last_visited_at = excluded.last_visited_at
            """,
            (user_id, business_id, _now()),
        )


def list_brand_preferences(user_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT business_id, starred, last_visited_at
            FROM brand_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_workspace_event(
    business_id: str,
    title: str,
    event_type: str,
    starts_at: str,
    notes: str = "",
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    event_id = _uid()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO workspace_events
                (id, business_id, title, event_type, starts_at, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, business_id, title, event_type, starts_at, notes, created_by, _now()),
        )
    return get_workspace_event(event_id) or {}


def get_workspace_event(event_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT e.*, b.name AS business_name, b.logo_url AS business_logo_url
            FROM workspace_events e
            JOIN businesses b ON b.id = e.business_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
    return row_to_dict(row)


def list_workspace_events(
    *,
    business_id: Optional[str] = None,
    from_time: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if business_id:
        clauses.append("e.business_id = ?")
        params.append(business_id)
    if from_time:
        clauses.append("e.starts_at >= ?")
        params.append(from_time)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT e.*, b.name AS business_name, b.logo_url AS business_logo_url
            FROM workspace_events e
            JOIN businesses b ON b.id = e.business_id
            {where}
            ORDER BY e.starts_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def list_recent_activity(limit: int = 8) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.status, t.updated_at, b.id AS business_id,
                   b.name AS business_name, b.logo_url AS business_logo_url
            FROM tasks t
            JOIN businesses b ON b.id = t.business_id
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# ── Tasks ───────────────────────────────────────────────────


def create_task(
    business_id: str,
    title: str,
    category: str = "other",
    owner: str = "me",
    priority: str = "P2",
    deadline: Optional[str] = None,
    status: str = "open",
    next_follow_up: Optional[str] = None,
    waiting_on: str = "",
    notes: str = "",
    source: str = "manual",
    source_ref: str = "",
) -> dict[str, Any]:
    tid = _uid()
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, business_id, title, category, owner, priority, deadline, status,
                next_follow_up, waiting_on, notes, source, source_ref, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                business_id,
                title,
                category,
                owner,
                priority,
                deadline,
                status,
                next_follow_up,
                waiting_on,
                notes,
                source,
                source_ref,
                now,
                now,
            ),
        )
    return get_task(tid)  # type: ignore


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT t.*, b.name AS business_name, b.slug AS business_slug
            FROM tasks t JOIN businesses b ON b.id = t.business_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        return row_to_dict(row)


def list_tasks(
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    open_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if business_id:
        clauses.append("t.business_id = ?")
        params.append(business_id)
    if status:
        clauses.append("t.status = ?")
        params.append(status)
    if open_only:
        clauses.append("t.status NOT IN ('done', 'cancelled')")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, b.name AS business_name, b.slug AS business_slug
            FROM tasks t JOIN businesses b ON b.id = t.business_id
            {where}
            ORDER BY
              CASE t.priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
              COALESCE(t.deadline, '9999'),
              t.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def update_task(task_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {
        "title",
        "category",
        "owner",
        "priority",
        "deadline",
        "status",
        "next_follow_up",
        "waiting_on",
        "notes",
        "source",
        "source_ref",
        "business_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_task(task_id)
    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*updates.values(), task_id))
    return get_task(task_id)


def create_task_comment(
    task_id: str,
    author_user_id: str,
    content: str,
    mentioned_user_ids: list[str],
) -> Optional[dict[str, Any]]:
    comment_id = _uid()
    now = _now()
    mentioned_ids = list(dict.fromkeys(mentioned_user_ids))
    with connect() as conn:
        task = conn.execute(
            """
            SELECT t.id, t.title, b.name AS business_name
            FROM tasks t JOIN businesses b ON b.id = t.business_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        author = conn.execute(
            "SELECT username, display_name FROM users WHERE id = ? AND status = 'active'",
            (author_user_id,),
        ).fetchone()
        if not task or not author:
            return None
        conn.execute(
            """
            INSERT INTO task_comments
                (id, task_id, author_user_id, content, mentioned_user_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                comment_id,
                task_id,
                author_user_id,
                content,
                json.dumps(mentioned_ids),
                now,
            ),
        )
        for mentioned_id in mentioned_ids:
            if mentioned_id == author_user_id:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (id, recipient_user_id, actor_user_id, notification_type,
                     source_id, title, body, target_url, created_at)
                SELECT ?, u.id, ?, 'mention', ?, ?, ?, ?, ?
                FROM users u
                WHERE u.id = ? AND u.status = 'active'
                """,
                (
                    _uid(),
                    author_user_id,
                    comment_id,
                    f"{author['display_name'] or author['username']} mentioned you",
                    f"{task['title']} · {task['business_name']}",
                    f"/?task={task_id}",
                    now,
                    mentioned_id,
                ),
            )
        # Notify earlier commenters on this task; if nobody commented before, notify everyone.
        prior = {
            r["author_user_id"]
            for r in conn.execute(
                "SELECT DISTINCT author_user_id FROM task_comments WHERE task_id = ? AND id != ?",
                (task_id, comment_id),
            ).fetchall()
        }
        if prior:
            recipients = prior
        else:
            recipients = {
                r["id"]
                for r in conn.execute("SELECT id FROM users WHERE status = 'active'").fetchall()
            }
        for recipient_id in recipients - {author_user_id} - set(mentioned_ids):
            conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (id, recipient_user_id, actor_user_id, notification_type,
                     source_id, title, body, target_url, created_at)
                SELECT ?, u.id, ?, 'comment', ?, ?, ?, ?, ?
                FROM users u
                WHERE u.id = ? AND u.status = 'active'
                """,
                (
                    _uid(),
                    author_user_id,
                    comment_id,
                    f"{author['display_name'] or author['username']} commented",
                    f"{task['title']} · {task['business_name']}",
                    f"/?task={task_id}",
                    now,
                    recipient_id,
                ),
            )
        row = conn.execute(
            """
            SELECT c.*, u.username AS author_username,
                   u.display_name AS author_display_name
            FROM task_comments c JOIN users u ON u.id = c.author_user_id
            WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
    return row_to_dict(row)


def list_task_comments(task_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username AS author_username,
                   u.display_name AS author_display_name
            FROM task_comments c JOIN users u ON u.id = c.author_user_id
            WHERE c.task_id = ?
            ORDER BY c.created_at, c.id
            """,
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_slack_notification(
    user_id: str, source_id: str, title: str, body: str
) -> Optional[dict[str, Any]]:
    notification_id = _uid()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO notifications
                (id, recipient_user_id, actor_user_id, notification_type,
                 source_id, title, body, target_url, created_at)
            VALUES (?, ?, NULL, 'slack', ?, ?, ?, '/#chat', ?)
            """,
            (notification_id, user_id, source_id, title, body, _now()),
        )
        if not cur.rowcount:
            return None
    return get_notification(notification_id, user_id)


def create_task_assignment_notification(
    task_id: str,
    owner_username: str,
    actor_user_id: str,
) -> Optional[dict[str, Any]]:
    recipient = get_user_by_username(owner_username)
    task = get_task(task_id)
    if (
        not recipient
        or recipient.get("status") != "active"
        or not task
        or recipient["id"] == actor_user_id
    ):
        return None
    notification_id = _uid()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO notifications
                (id, recipient_user_id, actor_user_id, notification_type,
                 source_id, title, body, target_url, created_at)
            VALUES (?, ?, ?, 'assignment', ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                recipient["id"],
                actor_user_id,
                notification_id,
                f"Task assigned to you: {task['title']}",
                task["business_name"],
                f"/?task={task_id}",
                _now(),
            ),
        )
    return get_notification(notification_id, recipient["id"])


def list_notifications(
    user_id: str,
    *,
    limit: int = 30,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["recipient_user_id = ?"]
    params: list[Any] = [user_id]
    if unread_only:
        clauses.append("read_at IS NULL")
    params.append(max(1, min(limit, 100)))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM notifications
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def count_unread_notifications(user_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM notifications
            WHERE recipient_user_id = ? AND read_at IS NULL
            """,
            (user_id,),
        ).fetchone()
    return int(row["total"] if row else 0)


def get_notification(notification_id: str, user_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM notifications
            WHERE id = ? AND recipient_user_id = ?
            """,
            (notification_id, user_id),
        ).fetchone()
    return row_to_dict(row)


def mark_notification_read(notification_id: str, user_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE notifications SET read_at = ?
            WHERE id = ? AND recipient_user_id = ? AND read_at IS NULL
            """,
            (_now(), notification_id, user_id),
        )
    return cursor.rowcount == 1


def notification_sound_enabled(user_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT sound_enabled FROM notification_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return bool(row["sound_enabled"]) if row else True


def set_notification_sound_enabled(user_id: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO notification_preferences (user_id, sound_enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                sound_enabled = excluded.sound_enabled,
                updated_at = excluded.updated_at
            """,
            (user_id, int(enabled), _now()),
        )


# ── Knowledge ───────────────────────────────────────────────


def add_note(
    title: str,
    body: str,
    business_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    nid = _uid()
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_notes (id, business_id, title, body, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (nid, business_id, title, body, json.dumps(tags or []), now, now),
        )
        row = conn.execute("SELECT * FROM knowledge_notes WHERE id = ?", (nid,)).fetchone()
        return dict(row)


def list_notes(business_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        if business_id:
            rows = conn.execute(
                "SELECT * FROM knowledge_notes WHERE business_id = ? OR business_id IS NULL ORDER BY updated_at DESC LIMIT ?",
                (business_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_notes ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Approvals ───────────────────────────────────────────────


def create_approval(
    summary: str,
    action_type: str = "other",
    business_id: Optional[str] = None,
    task_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict[str, Any]:
    aid = _uid()
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO approvals (id, business_id, task_id, action_type, summary, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (aid, business_id, task_id, action_type, summary, json.dumps(payload or {}), now),
        )
    return get_approval(aid)  # type: ignore


def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        )


def list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        ]


def decide_approval(approval_id: str, approved: bool) -> Optional[dict[str, Any]]:
    status = "approved" if approved else "rejected"
    with connect() as conn:
        conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
            (status, _now(), approval_id),
        )
    return get_approval(approval_id)


# ── Chat ────────────────────────────────────────────────────


def save_message(role: str, content: str, meta: Optional[dict] = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (_uid(), role, content, json.dumps(meta or {}), _now()),
        )


def recent_messages(limit: int = 40) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))


def add_whatsapp_message(
    user_id: str,
    sender_phone: str,
    role: str,
    content: str,
    provider_message_id: Optional[str] = None,
    status: str = "received",
    error: str = "",
) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO whatsapp_messages
                (id, provider_message_id, user_id, sender_phone, role, content, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _uid(),
                provider_message_id,
                user_id,
                sender_phone,
                role,
                content,
                status,
                error,
                _now(),
            ),
        )
        return cursor.rowcount == 1


def update_whatsapp_message_status(
    provider_message_id: str,
    status: str,
    error: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE whatsapp_messages SET status = ?, error = ? WHERE provider_message_id = ?",
            (status, error, provider_message_id),
        )


def whatsapp_history(
    user_id: str,
    sender_phone: str,
    limit: int = 32,
) -> list[dict[str, str]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM whatsapp_messages
            WHERE user_id = ? AND sender_phone = ? AND role IN ('user', 'assistant')
            ORDER BY rowid DESC LIMIT ?
            """,
            (user_id, sender_phone, limit),
        ).fetchall()
    return [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(rows)
    ]


# ── Attention brief ─────────────────────────────────────────


def attention_brief(today: Optional[str] = None) -> dict[str, Any]:
    today_d = date.fromisoformat(today) if today else date.today()
    today_s = today_d.isoformat()
    soon = (today_d + timedelta(days=3)).isoformat()

    tasks = list_tasks(open_only=True, limit=500)
    overdue = [t for t in tasks if t.get("deadline") and t["deadline"][:10] < today_s]
    due_today = [t for t in tasks if t.get("deadline") and t["deadline"][:10] == today_s]
    due_soon = [
        t
        for t in tasks
        if t.get("deadline") and today_s < t["deadline"][:10] <= soon
    ]
    followups_due = [
        t
        for t in tasks
        if t.get("next_follow_up") and t["next_follow_up"][:10] <= today_s
    ]
    waiting = [t for t in tasks if t["status"] == "waiting_on" or t.get("waiting_on")]
    decisions = [t for t in tasks if t["category"] == "decision"]
    p0 = [t for t in tasks if t["priority"] == "P0"]
    pending_approvals = list_approvals("pending")

    by_business: dict[str, int] = {}
    for t in tasks:
        name = t.get("business_name") or t["business_id"]
        by_business[name] = by_business.get(name, 0) + 1

    return {
        "as_of": today_s,
        "summary": {
            "open_tasks": len(tasks),
            "overdue": len(overdue),
            "due_today": len(due_today),
            "due_soon": len(due_soon),
            "followups_due": len(followups_due),
            "waiting_on": len(waiting),
            "decisions": len(decisions),
            "p0": len(p0),
            "pending_approvals": len(pending_approvals),
        },
        "by_business": by_business,
        "overdue": overdue[:20],
        "due_today": due_today[:20],
        "due_soon": due_soon[:20],
        "followups_due": followups_due[:20],
        "waiting_on": waiting[:20],
        "decisions": decisions[:20],
        "p0": p0[:20],
        "pending_approvals": pending_approvals,
    }


def seed_ncc_brand_portfolio() -> dict[str, Any]:
    """Ensure NCC businesses exist and refresh only the managed pack snapshot block."""
    from app.ncc_portfolio import NCC_BRAND_DEFINITIONS, NCC_BRAND_SNAPSHOTS

    start_marker = "<!-- NCC_PACK_SNAPSHOT_START -->"
    end_marker = "<!-- NCC_PACK_SNAPSHOT_END -->"
    existing = {b["slug"]: b for b in list_businesses(active_only=False)}
    created_slugs: list[str] = []
    updated_slugs: list[str] = []

    for brand in NCC_BRAND_DEFINITIONS:
        slug = brand["slug"]
        snapshot = NCC_BRAND_SNAPSHOTS.get(slug, "").strip()
        managed_block = f"{start_marker}\n{snapshot}\n{end_marker}"
        current = existing.get(slug)

        if current is None:
            create_business(
                name=brand["name"],
                description=brand["description"],
                context_md=f"{brand['context_md'].rstrip()}\n\n{managed_block}",
                slug=slug,
                status=brand["status"],
            )
            created_slugs.append(slug)
            continue

        context = current.get("context_md") or ""
        if start_marker in context and end_marker in context:
            before, remainder = context.split(start_marker, 1)
            _, after = remainder.split(end_marker, 1)
            refreshed = f"{before.rstrip()}\n\n{managed_block}{after}"
        else:
            refreshed = f"{context.rstrip()}\n\n{managed_block}" if context else managed_block

        if refreshed != context:
            update_business(current["id"], context_md=refreshed)
            updated_slugs.append(slug)

    if created_slugs or updated_slugs:
        return {
            "seeded": bool(created_slugs),
            "created_count": len(created_slugs),
            "created_slugs": created_slugs,
            "updated_count": len(updated_slugs),
            "updated_slugs": updated_slugs,
            "status": "ok",
        }
    return {
        "seeded": False,
        "created_count": 0,
        "created_slugs": [],
        "updated_count": 0,
        "updated_slugs": [],
        "status": "already_seeded",
    }


def seed_demo_data() -> dict[str, Any]:
    """Idempotent-ish seed: only if no businesses exist."""
    if list_businesses(active_only=False):
        return {"seeded": False, "reason": "already has businesses"}

    alpha = create_business(
        "Alpha Consulting",
        description="Strategy & go-to-market consulting practice.",
        context_md=(
            "# Alpha Consulting\n"
            "Focus: mid-market CPG brands entering retail.\n"
            "Key clients: Northwind Foods, Harbor Retail Group.\n"
            "Owner preference: weekly decision batches on Fridays.\n"
        ),
        slug="alpha-consulting",
    )
    beta = create_business(
        "Beta Portfolio Brand",
        description="Own-brand product line / NPD pipeline.",
        context_md=(
            "# Beta Portfolio Brand\n"
            "Category: specialty beverages.\n"
            "Active NPD: citrus adaptogen SKU, glass bottle redesign.\n"
            "Key suppliers: GlassCo, FlavorWorks.\n"
        ),
        slug="beta-brand",
    )
    gamma = create_business(
        "Gamma Distribution Ops",
        description="Distributor & retailer outreach tracker.",
        context_md=(
            "# Gamma Distribution Ops\n"
            "Target: regional specialty distributors in UK/EU.\n"
            "Do not send commercial outreach without approval.\n"
        ),
        slug="gamma-dist",
    )

    today = date.today()
    create_task(
        alpha["id"],
        "Send revised proposal to Northwind Foods",
        category="client",
        priority="P0",
        deadline=(today - timedelta(days=1)).isoformat(),
        status="open",
        next_follow_up=today.isoformat(),
        notes="Waiting on final pricing confirmation from us.",
        source="manual",
    )
    create_task(
        alpha["id"],
        "Decide: retain Harbor retainer at current rate?",
        category="decision",
        priority="P1",
        deadline=(today + timedelta(days=2)).isoformat(),
        status="open",
        notes="Finance model in notes; needs owner call.",
        source="meeting",
    )
    create_task(
        beta["id"],
        "Complete new line form for citrus adaptogen SKU",
        category="new_line_form",
        priority="P1",
        deadline=(today + timedelta(days=5)).isoformat(),
        status="in_progress",
        next_follow_up=(today + timedelta(days=2)).isoformat(),
        notes="Need nutrition panel from lab.",
        source="manual",
    )
    create_task(
        beta["id"],
        "Follow up GlassCo on MOQ quote",
        category="supplier",
        priority="P2",
        deadline=(today + timedelta(days=1)).isoformat(),
        status="waiting_on",
        waiting_on="GlassCo — procurement lead",
        next_follow_up=today.isoformat(),
        source="email",
    )
    create_task(
        gamma["id"],
        "Outreach: Midlands Specialty Distributors intro",
        category="distributor_outreach",
        priority="P1",
        deadline=(today + timedelta(days=3)).isoformat(),
        status="open",
        notes="Draft ready — requires commercial approval before send.",
        source="manual",
    )
    create_task(
        gamma["id"],
        "Retailer call actions: list 3 pilot stores",
        category="meeting_action",
        priority="P2",
        deadline=(today + timedelta(days=4)).isoformat(),
        status="delegated",
        owner="ops_assistant",
        waiting_on="ops_assistant",
        source="meeting",
    )

    add_note(
        "Portfolio operating rules",
        "Commercial outreach and pricing commitments require approval.\n"
        "P0 = same-day attention. Decisions batch Fridays unless blocking.\n"
        "Email is an input signal — convert to tasks, do not live in inbox.",
        tags=["portfolio", "rules"],
    )
    create_approval(
        summary="Send intro email to Midlands Specialty Distributors with pricing range",
        action_type="commercial_outreach",
        business_id=gamma["id"],
        payload={"to": "buyers@midlands-specialty.example", "draft": "Intro + range"},
    )

    return {
        "seeded": True,
        "businesses": [alpha["slug"], beta["slug"], gamma["slug"]],
    }


def save_slack_token(
    user_id: str,
    access_token: str,
    slack_user_id: str,
    team_id: str = "",
    team_name: str = "",
    scopes: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO slack_tokens
                (user_id, access_token, slack_user_id, team_id, team_name, scopes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token = excluded.access_token,
                slack_user_id = excluded.slack_user_id,
                team_id = excluded.team_id,
                team_name = excluded.team_name,
                scopes = excluded.scopes,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                _encrypt_google_secret(access_token),
                slack_user_id,
                team_id,
                team_name,
                scopes,
                _now(),
            ),
        )


def get_slack_token(user_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM slack_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["access_token"] = _decrypt_google_secret(data["access_token"])
    return data


def delete_slack_token(user_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM slack_tokens WHERE user_id = ?", (user_id,))
