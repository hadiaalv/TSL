"""SQLite persistence for businesses, tasks, notes, approvals, chat."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
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

TIMER_STATUSES = ("in_progress",)

CLOSED_STATUSES = ("done", "cancelled")

# Kanban columns and the raw task statuses each one groups.
STATUS_GROUPS = {
    "todo": ("open",),
    "in_progress": ("in_progress", "delegated"),
    "review": ("waiting_on", "blocked"),
    "completed": CLOSED_STATUSES,
}

DEFAULT_DEPARTMENTS = (
    (
        "Content Writing & Planning",
        "content-writing-planning",
        "Captions, product descriptions, content calendars, copywriting, and content strategy.",
        "#7c5cff",
        "✍️",
    ),
    (
        "Graphic Designing",
        "graphic-designing",
        "Banners, promotional graphics, social media creatives, product images, and campaign designs.",
        "#ec4899",
        "🎨",
    ),
    (
        "Social Media & Web Maintenance",
        "social-media-web-maintenance",
        "Scheduled posts, community management, SEO, website maintenance, Shopify updates, and social media operations.",
        "#0ea5e9",
        "🌐",
    ),
    (
        "Ads Management",
        "ads-management",
        "Meta Ads, Google Ads, campaign setup, optimization, performance monitoring, and advertising reports.",
        "#f59e0b",
        "📈",
    ),
    (
        "Web Development",
        "web-development",
        "Shopify theme customization, Liquid/CSS/JavaScript development, integrations, debugging, and dashboard development.",
        "#10b981",
        "💻",
    ),
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
    _backup_before_workspace_migration()
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
        _migrate_workspace(conn)
        _migrate_attendance(conn)


def _migrate_attendance(conn: sqlite3.Connection) -> None:
    """Create persistent office-attendance records independently of task timers."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS attendance_policy (
            policy_id TEXT PRIMARY KEY CHECK (policy_id = 'default'),
            timezone TEXT NOT NULL DEFAULT 'Asia/Karachi',
            heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 45,
            inactivity_timeout_seconds INTEGER NOT NULL DEFAULT 180,
            work_start TEXT NOT NULL DEFAULT '09:00',
            work_end TEXT NOT NULL DEFAULT '18:00',
            late_grace_minutes INTEGER NOT NULL DEFAULT 15,
            overtime_enabled INTEGER NOT NULL DEFAULT 0,
            overtime_grace_minutes INTEGER NOT NULL DEFAULT 30,
            working_days_json TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            status TEXT NOT NULL CHECK (status IN ('checked_in', 'paused', 'checked_out')),
            checked_in_at TEXT NOT NULL,
            checked_out_at TEXT,
            last_heartbeat_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (checked_out_at IS NULL OR checked_out_at >= checked_in_at)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_one_active_session
            ON attendance_sessions(user_id) WHERE checked_out_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_attendance_sessions_user_date
            ON attendance_sessions(user_id, checked_in_at DESC);

        CREATE TABLE IF NOT EXISTS attendance_intervals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            CHECK (ended_at IS NULL OR ended_at >= started_at)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_intervals_session
            ON attendance_intervals(session_id, started_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_one_open_interval
            ON attendance_intervals(session_id) WHERE ended_at IS NULL;

        CREATE TABLE IF NOT EXISTS attendance_heartbeats (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE(session_id, request_id)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_heartbeats_session
            ON attendance_heartbeats(session_id, received_at);

        CREATE TABLE IF NOT EXISTS attendance_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            event_key TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            details_json TEXT NOT NULL DEFAULT '{}',
            reviewed_at TEXT,
            reviewed_by TEXT REFERENCES users(id),
            resolution_note TEXT NOT NULL DEFAULT '',
            UNIQUE(session_id, event_type, event_key)
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_events_session
            ON attendance_events(session_id, occurred_at);

        CREATE TABLE IF NOT EXISTS attendance_corrections (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES attendance_sessions(id),
            actor_user_id TEXT NOT NULL REFERENCES users(id),
            delta_seconds INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_corrections_session
            ON attendance_corrections(session_id, created_at);

        CREATE TABLE IF NOT EXISTS attendance_audit (
            id TEXT PRIMARY KEY,
            actor_user_id TEXT NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            session_id TEXT REFERENCES attendance_sessions(id),
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attendance_audit_session
            ON attendance_audit(session_id, created_at);
        """
    )
    existing_policy_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(attendance_policy)").fetchall()
    }
    if "overtime_enabled" not in existing_policy_cols:
        conn.execute(
            "ALTER TABLE attendance_policy ADD COLUMN overtime_enabled INTEGER NOT NULL DEFAULT 0"
        )
    if "overtime_grace_minutes" not in existing_policy_cols:
        conn.execute(
            "ALTER TABLE attendance_policy ADD COLUMN overtime_grace_minutes INTEGER NOT NULL DEFAULT 30"
        )
    for column, default in (
        ("allowed_half_leaves", 1),
        ("allowed_days_off", 2),
        ("allowed_late_checkins", 3),
        ("allowed_early_offs", 3),
    ):
        if column not in existing_policy_cols:
            conn.execute(
                f"ALTER TABLE attendance_policy ADD COLUMN {column} INTEGER NOT NULL DEFAULT {default}"
            )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS attendance_leaves (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            leave_date TEXT NOT NULL,
            leave_type TEXT NOT NULL CHECK (leave_type IN ('half', 'full')),
            reason TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_leave_day
            ON attendance_leaves(user_id, leave_date);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO attendance_policy (policy_id, updated_at)
        VALUES ('default', ?)
        """,
        (_now(),),
    )


def _backup_before_workspace_migration() -> None:
    """Copy an existing database aside before the first department migration runs."""
    path = settings.db_path
    if not path.is_file() or path.stat().st_size == 0:
        return
    probe = sqlite3.connect(path)
    try:
        tables = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        probe.close()
    if "tasks" not in tables or "departments" in tables:
        return
    backups = path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, backups / f"cos-before-departments-{stamp}.db")


def _migrate_workspace(conn: sqlite3.Connection) -> None:
    """Departments, memberships, task assignees and notification/time-log links."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#6366f1',
            icon TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS department_members (
            department_id TEXT NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            added_by TEXT REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (department_id, user_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_department_members_user ON department_members(user_id)"
    )
    assignees_existed = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_assignees'"
        ).fetchone()
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_assignees (
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id),
            assigned_by TEXT REFERENCES users(id) ON DELETE SET NULL,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (task_id, user_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_assignees_user ON task_assignees(user_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_reads (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            last_read_at TEXT NOT NULL,
            PRIMARY KEY (user_id, task_id)
        )
        """
    )

    def add_columns(table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    add_columns(
        "tasks",
        {
            "department_id": "TEXT REFERENCES departments(id)",
            "created_by": "TEXT REFERENCES users(id)",
            "checklist_json": "TEXT NOT NULL DEFAULT '[]'",
            "links_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    )
    add_columns("task_time_entries", {"department_id": "TEXT REFERENCES departments(id)"})
    add_columns(
        "notifications",
        {
            "task_id": "TEXT REFERENCES tasks(id) ON DELETE CASCADE",
            "department_id": "TEXT REFERENCES departments(id)",
            "department_name": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_department ON tasks(department_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id, created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_time_entries_department ON task_time_entries(department_id)"
    )
    # One running timer per person per task, even under duplicate requests.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_time_entries_one_running "
            "ON task_time_entries(task_id, user_id) WHERE ended_at IS NULL"
        )
    except sqlite3.IntegrityError:
        # Pre-existing duplicate running timers: keep the data and rely on the application check.
        pass

    if conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
        now = _now()
        for order, (name, slug, description, color, icon) in enumerate(DEFAULT_DEPARTMENTS):
            conn.execute(
                """
                INSERT INTO departments
                    (id, name, slug, description, color, icon, sort_order, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (_uid(), name, slug, description, color, icon, order, now, now),
            )

    if not assignees_existed:
        # The legacy free-text owner becomes a real assignment only when it names an existing user.
        conn.execute(
            """
            INSERT OR IGNORE INTO task_assignees (task_id, user_id, assigned_by, assigned_at)
            SELECT t.id, u.id, NULL, t.updated_at
            FROM tasks t JOIN users u ON u.username = t.owner COLLATE NOCASE
            WHERE t.owner IS NOT NULL AND t.owner != '' AND t.owner != 'me'
            """
        )


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
        CREATE TABLE IF NOT EXISTS task_time_entries (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id),
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_time_entries_task ON task_time_entries(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_time_entries_user_start ON task_time_entries(user_id, started_at)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brand_sops (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            url TEXT DEFAULT '',
            created_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_brand_sops_business ON brand_sops(business_id)"
    )

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
        if updates.get("status") == "disabled":
            conn.execute(
                "UPDATE task_time_entries SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
                (_now(), user_id),
            )
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


def list_brand_sops(business_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM brand_sops WHERE business_id = ? ORDER BY created_at, id",
            (business_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_brand_sop(business_id: str, sop_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM brand_sops WHERE id = ? AND business_id = ?",
            (sop_id, business_id),
        ).fetchone()
    return row_to_dict(row)


def add_brand_sop(
    business_id: str,
    title: str,
    content: str = "",
    url: str = "",
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    now = _now()
    sop_id = _uid()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO brand_sops (id, business_id, title, content, url, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sop_id, business_id, title, content, url, created_by, now, now),
        )
    return get_brand_sop(business_id, sop_id)  # type: ignore


def update_brand_sop(business_id: str, sop_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    updates = {
        k: v for k, v in fields.items() if k in {"title", "content", "url"} and v is not None
    }
    if updates:
        updates["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in updates)
        with connect() as conn:
            conn.execute(
                f"UPDATE brand_sops SET {cols} WHERE id = ? AND business_id = ?",
                (*updates.values(), sop_id, business_id),
            )
    return get_brand_sop(business_id, sop_id)


def delete_brand_sop(business_id: str, sop_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM brand_sops WHERE id = ? AND business_id = ?", (sop_id, business_id)
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
    business_ids: Optional[list[str]] = None,
    from_time: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if business_id:
        clauses.append("e.business_id = ?")
        params.append(business_id)
    if business_ids is not None:
        if not business_ids:
            return []
        clauses.append(f"e.business_id IN ({','.join('?' * len(business_ids))})")
        params.extend(business_ids)
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


def list_recent_activity(
    limit: int = 8,
    viewer: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    vis, vis_params = _visibility_clause(viewer)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.status, t.updated_at, b.id AS business_id,
                   b.name AS business_name, b.logo_url AS business_logo_url
            FROM tasks t
            JOIN businesses b ON b.id = t.business_id
            LEFT JOIN departments d ON d.id = t.department_id
            WHERE 1 = 1
              {visibility}
            ORDER BY t.updated_at DESC
            LIMIT ?
            """.format(visibility=f"AND {vis}" if vis else ""),
            [*vis_params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


# ── Departments ─────────────────────────────────────────────


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "department"


def get_department(department_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM departments WHERE id = ?", (department_id,)).fetchone()
    return row_to_dict(row)


def list_departments(
    viewer: Optional[dict[str, Any]] = None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """Active departments for everyone (inactive ones only on request), with task counts for `viewer`."""
    clauses = []
    params: list[Any] = []
    if not include_inactive:
        clauses.append("d.status = 'active'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT d.*, (SELECT COUNT(*) FROM department_members m
                             JOIN users u ON u.id = m.user_id
                             WHERE m.department_id = d.id AND u.status = 'active') AS member_count
                FROM departments d {where} ORDER BY d.sort_order, d.name
                """,
                params,
            )
        ]
    vis, vis_params = _visibility_clause(viewer)
    today = date.today().isoformat()
    with connect() as conn:
        counts = {
            r["department_id"]: r
            for r in conn.execute(
                f"""
                SELECT t.department_id,
                  COUNT(*) AS open_count,
                  SUM(CASE WHEN t.deadline IS NOT NULL AND t.deadline != ''
                           AND substr(t.deadline, 1, 10) < ? THEN 1 ELSE 0 END) AS overdue_count,
                  SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM task_assignees ua WHERE ua.task_id = t.id)
                           THEN 1 ELSE 0 END) AS unassigned_count
                FROM tasks t LEFT JOIN departments d ON d.id = t.department_id
                WHERE t.department_id IS NOT NULL AND t.status NOT IN ('done', 'cancelled')
                {('AND ' + vis) if vis else ''}
                GROUP BY t.department_id
                """,
                [today, *vis_params],
            )
        }
    for row in rows:
        c = counts.get(row["id"])
        row["open_count"] = int(c["open_count"]) if c else 0
        row["overdue_count"] = int(c["overdue_count"] or 0) if c else 0
        row["unassigned_count"] = int(c["unassigned_count"] or 0) if c else 0
    return rows


def create_department(
    name: str, description: str = "", color: str = "#6366f1", icon: str = ""
) -> dict[str, Any]:
    name = name.strip()
    base = _slugify(name)
    slug = base
    with connect() as conn:
        if conn.execute("SELECT 1 FROM departments WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
            raise ValueError("A department with that name already exists.")
        suffix = 2
        while conn.execute("SELECT 1 FROM departments WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM departments").fetchone()[0]
        did = _uid()
        now = _now()
        conn.execute(
            """
            INSERT INTO departments
                (id, name, slug, description, color, icon, sort_order, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (did, name, slug, description.strip(), color, icon.strip(), order, now, now),
        )
    return get_department(did) or {}


def update_department(department_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"name", "description", "color", "icon", "sort_order", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "name" in updates:
        updates["name"] = str(updates["name"]).strip()
        with connect() as conn:
            clash = conn.execute(
                "SELECT 1 FROM departments WHERE name = ? COLLATE NOCASE AND id != ?",
                (updates["name"], department_id),
            ).fetchone()
        if clash:
            raise ValueError("A department with that name already exists.")
    if not updates:
        return get_department(department_id)
    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(f"UPDATE departments SET {cols} WHERE id = ?", (*updates.values(), department_id))
    return get_department(department_id)


def list_department_members(department_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, COALESCE(NULLIF(u.display_name, ''), u.username) AS display_name,
                   u.role, u.status, m.created_at AS joined_at
            FROM department_members m JOIN users u ON u.id = m.user_id
            WHERE m.department_id = ? ORDER BY display_name COLLATE NOCASE
            """,
            (department_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_department_member(department_id: str, user_id: str, added_by: Optional[str]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO department_members (department_id, user_id, added_by, created_at) "
            "VALUES (?, ?, ?, ?)",
            (department_id, user_id, added_by, _now()),
        )


def remove_department_member(department_id: str, user_id: str) -> bool:
    """Only membership is removed; assignments, comments and time logs stay as history."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM department_members WHERE department_id = ? AND user_id = ?",
            (department_id, user_id),
        )
    return cur.rowcount == 1


def user_department_ids(user_id: str) -> list[str]:
    with connect() as conn:
        return [
            r["department_id"]
            for r in conn.execute(
                "SELECT department_id FROM department_members WHERE user_id = ?", (user_id,)
            )
        ]


def users_department_map() -> dict[str, list[str]]:
    with connect() as conn:
        result: dict[str, list[str]] = {}
        for r in conn.execute("SELECT department_id, user_id FROM department_members"):
            result.setdefault(r["user_id"], []).append(r["department_id"])
    return result


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
    actor_user_id: Optional[str] = None,
    department_id: Optional[str] = None,
    assignee_ids: Optional[list[str]] = None,
    checklist: Optional[list[dict[str, Any]]] = None,
    links: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Insert a task. `owner` is a legacy username; a matching user becomes the assignee."""
    tid = _uid()
    now = _now()
    ids = list(assignee_ids or [])
    if not ids and owner and owner != "me":
        legacy_owner = get_user_by_username(owner)
        if legacy_owner:
            ids = [legacy_owner["id"]]
    ids = _active_user_ids(ids)
    owner_name = _owner_label(ids) if ids else (owner or "me")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, business_id, title, category, owner, priority, deadline, status,
                next_follow_up, waiting_on, notes, source, source_ref, created_at, updated_at,
                department_id, created_by, checklist_json, links_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                business_id,
                title,
                category,
                owner_name,
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
                department_id,
                actor_user_id,
                json.dumps(checklist or []),
                json.dumps(links or []),
            ),
        )
        for user_id in ids:
            conn.execute(
                "INSERT OR IGNORE INTO task_assignees (task_id, user_id, assigned_by, assigned_at) "
                "VALUES (?, ?, ?, ?)",
                (tid, user_id, actor_user_id, now),
            )
    task = get_task(tid)
    if task and task["status"] in TIMER_STATUSES:
        start_task_timer(task, actor_user_id)
    return get_task(tid)  # type: ignore


def _active_user_ids(user_ids: list[str]) -> list[str]:
    """Deduplicated ids of existing active users, in the order given."""
    unique = list(dict.fromkeys(str(u) for u in user_ids if u))
    if not unique:
        return []
    marks = ",".join("?" * len(unique))
    with connect() as conn:
        found = {
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM users WHERE status = 'active' AND id IN ({marks})", unique
            )
        }
    return [u for u in unique if u in found]


def _owner_label(user_ids: list[str]) -> str:
    """Legacy `owner` column: username of the first assignee."""
    if not user_ids:
        return "me"
    user = get_user(user_ids[0])
    return user["username"] if user else "me"


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


_TASK_SELECT = """
    SELECT t.*, b.name AS business_name, b.slug AS business_slug,
      d.name AS department_name, d.slug AS department_slug,
      d.color AS department_color, d.icon AS department_icon,
      d.status AS department_status,
      COALESCE(cu.display_name, cu.username) AS created_by_name,
      (SELECT COALESCE(SUM(strftime('%s', e.ended_at) - strftime('%s', e.started_at)), 0)
         FROM task_time_entries e
         WHERE e.task_id = t.id AND e.ended_at IS NOT NULL) AS tracked_seconds,
      (SELECT MIN(e.started_at) FROM task_time_entries e
         WHERE e.task_id = t.id AND e.ended_at IS NULL) AS timer_started_at
    FROM tasks t
    JOIN businesses b ON b.id = t.business_id
    LEFT JOIN departments d ON d.id = t.department_id
    LEFT JOIN users cu ON cu.id = t.created_by
"""


def _visibility_clause(_viewer: Optional[dict[str, Any]]) -> tuple[str, list[Any]]:
    """All workspace roles can read tasks; API handlers enforce role-specific writes."""
    return "", []


def _time_visibility_clause(viewer: Optional[dict[str, Any]]) -> tuple[str, list[Any]]:
    """Keep time-entry visibility scoped even though task details are workspace-wide."""
    if viewer is None or viewer.get("role") == "super_admin":
        return "", []
    uid = viewer["id"]
    return (
        "(EXISTS (SELECT 1 FROM task_assignees va WHERE va.task_id = t.id AND va.user_id = ?) "
        "OR (d.status = 'active' AND t.department_id IN "
        "(SELECT dm.department_id FROM department_members dm WHERE dm.user_id = ?)))",
        [uid, uid],
    )


def _status_clause(status: str) -> tuple[str, list[Any]]:
    values = STATUS_GROUPS.get(status, (status,))
    return f"t.status IN ({','.join('?' * len(values))})", list(values)


def _enrich_tasks(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    viewer_id: Optional[str],
) -> list[dict[str, Any]]:
    tasks = [dict(r) for r in rows]
    if not tasks:
        return tasks
    ids = [t["id"] for t in tasks]
    marks = ",".join("?" * len(ids))
    assignees: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
        f"""
        SELECT a.task_id, u.id, u.username, COALESCE(NULLIF(u.display_name, ''), u.username) AS display_name,
               u.status
        FROM task_assignees a JOIN users u ON u.id = a.user_id
        WHERE a.task_id IN ({marks}) ORDER BY a.assigned_at, u.username
        """,
        ids,
    ):
        assignees.setdefault(r["task_id"], []).append(
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "status": r["status"],
            }
        )
    comment_counts = {
        r["task_id"]: r["n"]
        for r in conn.execute(
            f"SELECT task_id, COUNT(*) AS n FROM task_comments WHERE task_id IN ({marks}) GROUP BY task_id",
            ids,
        )
    }
    unread: dict[str, int] = {}
    if viewer_id:
        unread = {
            r["task_id"]: r["n"]
            for r in conn.execute(
                f"""
                SELECT c.task_id, COUNT(*) AS n
                FROM task_comments c
                LEFT JOIN task_reads r ON r.task_id = c.task_id AND r.user_id = ?
                WHERE c.task_id IN ({marks}) AND c.author_user_id != ?
                  AND (r.last_read_at IS NULL OR c.created_at > r.last_read_at)
                GROUP BY c.task_id
                """,
                [viewer_id, *ids, viewer_id],
            )
        }
    running: dict[str, list[dict[str, str]]] = {}
    for r in conn.execute(
        f"SELECT task_id, user_id, started_at FROM task_time_entries "
        f"WHERE ended_at IS NULL AND task_id IN ({marks})",
        ids,
    ):
        running.setdefault(r["task_id"], []).append(
            {"user_id": r["user_id"], "started_at": r["started_at"]}
        )
    for task in tasks:
        task["assignees"] = assignees.get(task["id"], [])
        task["assignee_ids"] = [a["id"] for a in task["assignees"]]
        task["comment_count"] = comment_counts.get(task["id"], 0)
        task["unread_comments"] = unread.get(task["id"], 0)
        task["running_timers"] = running.get(task["id"], [])
        task["description"] = task.get("notes") or ""
        task["checklist"] = _json_list(task.pop("checklist_json", "[]"))
        task["links"] = _json_list(task.pop("links_json", "[]"))
    return tasks


def get_task(
    task_id: str, viewer: Optional[dict[str, Any]] = None
) -> Optional[dict[str, Any]]:
    """Fetch a task. With `viewer`, returns None unless that user may see it."""
    vis, vis_params = _visibility_clause(viewer)
    where = "WHERE t.id = ?" + (f" AND {vis}" if vis else "")
    with connect() as conn:
        rows = conn.execute(f"{_TASK_SELECT} {where}", [task_id, *vis_params]).fetchall()
        found = _enrich_tasks(conn, rows, viewer["id"] if viewer else None)
    return found[0] if found else None


def list_tasks(
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    open_only: bool = False,
    limit: int = 200,
    *,
    department_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: Optional[str] = None,
    view: Optional[str] = None,
    viewer: Optional[dict[str, Any]] = None,
    created_from: Optional[str] = None,
    window: Optional[tuple[str, str]] = None,
) -> list[dict[str, Any]]:
    """List tasks.

    view: mine | unassigned | completed | overdue (default: everything visible).
    department_id: a department id, or "none" for uncategorized tasks.
    window: (start, end) keeps tasks that existed during the window and were either
    still open or last changed inside it.
    """
    clauses: list[str] = []
    params: list[Any] = []
    vis, vis_params = _visibility_clause(viewer)
    if vis:
        clauses.append(vis)
        params += vis_params
    if business_id:
        clauses.append("t.business_id = ?")
        params.append(business_id)
    if status:
        clause, values = _status_clause(status)
        clauses.append(clause)
        params += values
    if open_only:
        clauses.append("t.status NOT IN ('done', 'cancelled')")
    if department_id == "none":
        clauses.append("t.department_id IS NULL")
    elif department_id:
        clauses.append("t.department_id = ?")
        params.append(department_id)
    if priority:
        clauses.append("t.priority = ?")
        params.append(priority)
    if assignee_id:
        clauses.append("EXISTS (SELECT 1 FROM task_assignees fa WHERE fa.task_id = t.id AND fa.user_id = ?)")
        params.append(assignee_id)
    if view == "mine":
        if not viewer:
            return []
        clauses.append("EXISTS (SELECT 1 FROM task_assignees ma WHERE ma.task_id = t.id AND ma.user_id = ?)")
        params.append(viewer["id"])
    elif view == "unassigned":
        clauses.append("NOT EXISTS (SELECT 1 FROM task_assignees ua WHERE ua.task_id = t.id)")
        clauses.append("t.status NOT IN ('done', 'cancelled')")
    elif view == "completed":
        clauses.append("t.status IN ('done', 'cancelled')")
    elif view == "overdue":
        clauses.append("t.deadline IS NOT NULL AND t.deadline != '' AND substr(t.deadline, 1, 10) < ?")
        params.append(date.today().isoformat())
        clauses.append("t.status NOT IN ('done', 'cancelled')")
    if created_from:
        clauses.append("t.created_at >= ?")
        params.append(created_from)
    if window:
        clauses.append(
            "t.created_at < ? AND (t.status NOT IN ('done', 'cancelled') OR t.updated_at >= ?)"
        )
        params += [window[1], window[0]]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            {_TASK_SELECT}
            {where}
            ORDER BY
              COALESCE(t.deadline, '9999'),
              t.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return _enrich_tasks(conn, rows, viewer["id"] if viewer else None)


def user_is_task_assignee(user_id: str, task_id: str) -> bool:
    with connect() as conn:
        return bool(
            conn.execute(
                "SELECT 1 FROM task_assignees WHERE task_id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
        )


def validate_task_assignees(department_id: Optional[str], user_ids: list[str]) -> list[str]:
    """Return assignee ids that are active members of the department; raise ValueError otherwise."""
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return []
    if not department_id:
        raise ValueError("Choose a department before assigning people to a task.")
    department = get_department(department_id)
    if not department or department["status"] != "active":
        raise ValueError("The selected department is not available.")
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        valid = {
            r["id"]
            for r in conn.execute(
                f"""
                SELECT u.id FROM users u
                JOIN department_members dm ON dm.user_id = u.id AND dm.department_id = ?
                WHERE u.status = 'active' AND u.id IN ({marks})
                """,
                [department_id, *ids],
            )
        }
    invalid = [u for u in ids if u not in valid]
    if invalid:
        raise ValueError("Every assignee must be an active member of the selected department.")
    return ids


def set_task_assignees(
    task_id: str, user_ids: list[str], assigned_by: Optional[str]
) -> tuple[list[str], list[str]]:
    """Replace the assignee set. Returns (added_ids, removed_ids)."""
    wanted = list(dict.fromkeys(user_ids))
    now = _now()
    with connect() as conn:
        current = [
            r["user_id"]
            for r in conn.execute("SELECT user_id FROM task_assignees WHERE task_id = ?", (task_id,))
        ]
        added = [u for u in wanted if u not in current]
        removed = [u for u in current if u not in wanted]
        for user_id in added:
            conn.execute(
                "INSERT INTO task_assignees (task_id, user_id, assigned_by, assigned_at) VALUES (?, ?, ?, ?)",
                (task_id, user_id, assigned_by, now),
            )
        for user_id in removed:
            conn.execute(
                "DELETE FROM task_assignees WHERE task_id = ? AND user_id = ?", (task_id, user_id)
            )
            conn.execute(
                "UPDATE task_time_entries SET ended_at = ? "
                "WHERE task_id = ? AND user_id = ? AND ended_at IS NULL",
                (now, task_id, user_id),
            )
        if added or removed:
            first = wanted[0] if wanted else None
            owner = "me"
            if first:
                row = conn.execute("SELECT username FROM users WHERE id = ?", (first,)).fetchone()
                owner = row["username"] if row else "me"
            conn.execute(
                "UPDATE tasks SET owner = ?, updated_at = ? WHERE id = ?", (owner, now, task_id)
            )
    return added, removed


def mark_task_read(user_id: str, task_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_reads (user_id, task_id, last_read_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id, task_id) DO UPDATE SET last_read_at = excluded.last_read_at
            """,
            (user_id, task_id, _now()),
        )


def update_task(
    task_id: str,
    *,
    actor_user_id: Optional[str] = None,
    **fields: Any,
) -> Optional[dict[str, Any]]:
    """Update a task. Authorization is the caller's job (see auth.authorize_task_update).

    `assignee_ids` replaces the assignee set; `department_id`, `deadline` and
    `next_follow_up` may be None to clear them.
    """
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
    for key in ("deadline", "next_follow_up", "department_id"):
        if key in fields:
            updates[key] = fields[key] or None
    if fields.get("description") is not None:
        updates["notes"] = fields["description"]
    if fields.get("checklist") is not None:
        updates["checklist_json"] = json.dumps(fields["checklist"])
    if fields.get("links") is not None:
        updates["links_json"] = json.dumps(fields["links"])
    assignee_ids = fields.get("assignee_ids")
    if assignee_ids is None and fields.get("owner") not in (None, "", "me"):
        legacy_owner = get_user_by_username(fields["owner"])
        if legacy_owner:
            assignee_ids = [legacy_owner["id"]]
    updates.pop("owner", None)
    previous = get_task(task_id) if "status" in updates else None
    if updates:
        updates["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in updates)
        with connect() as conn:
            conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*updates.values(), task_id))
    if assignee_ids is not None:
        set_task_assignees(task_id, _active_user_ids(assignee_ids), actor_user_id)
    elif fields.get("owner") == "me":
        set_task_assignees(task_id, [], actor_user_id)
    task = get_task(task_id)
    if previous and task:
        was_running = previous["status"] in TIMER_STATUSES
        is_running = task["status"] in TIMER_STATUSES
        if is_running and not was_running:
            start_task_timer(task, actor_user_id)
        elif was_running and not is_running:
            stop_task_timers(task_id)
    return get_task(task_id) if task else task


# ── Task time tracking ───────────────────────────────────────


def start_task_timer(task: dict[str, Any], actor_user_id: Optional[str]) -> None:
    """Start the acting user's timer, but only when that user is an assignee of the task.

    Time is never attributed to the task creator, the owner field, or whoever
    happens to drag the card — only to the assignee who actually moved it.
    """
    if not actor_user_id or not user_is_task_assignee(actor_user_id, task["id"]):
        return
    actor = get_user(actor_user_id)
    if not actor or actor.get("status") != "active":
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO task_time_entries (id, task_id, user_id, department_id, started_at)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM task_time_entries
                WHERE task_id = ? AND user_id = ? AND ended_at IS NULL
            )
            """,
            (_uid(), task["id"], actor_user_id, task.get("department_id"), _now(), task["id"], actor_user_id),
        )


def stop_task_timers(task_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE task_time_entries SET ended_at = ? WHERE task_id = ? AND ended_at IS NULL",
            (_now(), task_id),
        )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def time_report(
    start: str,
    end: str,
    user_id: Optional[str] = None,
    department_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    viewer: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Time entries overlapping [start, end), clipped to that window.

    Running timers are counted up to now. `worked_seconds` merges overlapping
    entries so working on several cards at once is not double counted.
    """
    now = _now()
    clauses = ["e.started_at < ?", "COALESCE(e.ended_at, ?) > ?"]
    params: list[Any] = [end, now, start]
    if user_id:
        clauses.append("e.user_id = ?")
        params.append(user_id)
    if department_id == "none":
        clauses.append("COALESCE(e.department_id, t.department_id) IS NULL")
    elif department_id:
        clauses.append("COALESCE(e.department_id, t.department_id) = ?")
        params.append(department_id)
    if status:
        clause, values = _status_clause(status)
        clauses.append(clause)
        params += values
    if priority:
        clauses.append("t.priority = ?")
        params.append(priority)
    vis, vis_params = _time_visibility_clause(viewer)
    if vis:
        clauses.append(vis)
        params.extend(vis_params)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT e.id, e.task_id, e.user_id, e.started_at, e.ended_at,
                   t.title AS task_title, t.status AS task_status,
                   b.name AS business_name,
                   d.id AS department_id, d.name AS department_name,
                   COALESCE(u.display_name, u.username) AS user_name
            FROM task_time_entries e
            JOIN tasks t ON t.id = e.task_id
            JOIN businesses b ON b.id = t.business_id
            JOIN users u ON u.id = e.user_id
            LEFT JOIN departments d ON d.id = COALESCE(e.department_id, t.department_id)
            WHERE {" AND ".join(clauses)}
            ORDER BY e.started_at
            """,
            params,
        ).fetchall()

    window_start, window_end, now_dt = _parse_utc(start), _parse_utc(end), _parse_utc(now)
    entries: list[dict[str, Any]] = []
    tasks: dict[str, dict[str, Any]] = {}
    intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for row in rows:
        entry_start = max(_parse_utc(row["started_at"]), window_start)
        running = row["ended_at"] is None
        entry_end = min(_parse_utc(row["ended_at"]) if not running else now_dt, window_end)
        seconds = max(0, int((entry_end - entry_start).total_seconds()))
        intervals.setdefault(row["user_id"], []).append((entry_start, entry_end))
        entries.append(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "task_title": row["task_title"],
                "business_name": row["business_name"],
                "department_id": row["department_id"],
                "department_name": row["department_name"] or "",
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "started_at": entry_start.isoformat().replace("+00:00", "Z"),
                "ended_at": None if running and entry_end >= now_dt else entry_end.isoformat().replace("+00:00", "Z"),
                "running": running and entry_end >= now_dt,
                "seconds": seconds,
            }
        )
        summary = tasks.setdefault(
            row["task_id"],
            {
                "people": [],
                "task_id": row["task_id"],
                "task_title": row["task_title"],
                "business_name": row["business_name"],
                "department_id": row["department_id"],
                "department_name": row["department_name"] or "",
                "task_status": row["task_status"],
                "seconds": 0,
                "sessions": 0,
                "running": False,
            },
        )
        if row["user_name"] not in summary["people"]:
            summary["people"].append(row["user_name"])
        summary["seconds"] += seconds
        summary["sessions"] += 1
        summary["running"] = summary["running"] or entries[-1]["running"]

    # Overlap is merged per person; different people's time is added together.
    worked = 0
    for user_intervals in intervals.values():
        cursor: Optional[datetime] = None
        for entry_start, entry_end in sorted(user_intervals):
            if cursor is None or entry_start > cursor:
                worked += int((entry_end - entry_start).total_seconds())
                cursor = entry_end
            elif entry_end > cursor:
                worked += int((entry_end - cursor).total_seconds())
                cursor = entry_end

    return {
        "start": start,
        "end": end,
        "total_seconds": sum(e["seconds"] for e in entries),
        "worked_seconds": worked,
        "tasks": sorted(tasks.values(), key=lambda item: -item["seconds"]),
        "entries": entries,
    }


def _active_comment_mentions(conn: sqlite3.Connection, user_ids: set[str]) -> set[str]:
    """Return active IDs from a candidate set."""
    if not user_ids:
        return set()
    ids = list(user_ids)
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT u.id FROM users u
        WHERE u.status = 'active' AND u.id IN ({marks})
        """,
        ids,
    ).fetchall()
    return {r["id"] for r in rows}


def _insert_notification(
    conn: sqlite3.Connection,
    *,
    recipient_id: str,
    actor_id: Optional[str],
    event_type: str,
    source_id: str,
    title: str,
    task: dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO notifications
            (id, recipient_user_id, actor_user_id, notification_type, source_id, title, body,
             target_url, created_at, task_id, department_id, department_name)
        SELECT ?, u.id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM users u WHERE u.id = ? AND u.status = 'active'
        """,
        (
            _uid(),
            actor_id,
            event_type,
            source_id,
            title,
            f"{task.get('department_name') or 'No department'} · {task['business_name']}",
            f"/?task={task['id']}",
            now,
            task["id"],
            task.get("department_id"),
            task.get("department_name") or "",
            recipient_id,
        ),
    )


def notify_super_admins(
    event_type: str,
    title: str,
    body: str,
    actor_user_id: Optional[str],
    *,
    source_id: Optional[str] = None,
    task_id: Optional[str] = None,
    target_url: str = "/admin",
) -> int:
    """Notify active super admins about approval and administration events."""
    if event_type not in ("approval", "admin"):
        raise ValueError("Unsupported super-admin notification type")
    now = _now()
    notification_source = source_id or _uid()
    recipients = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM users WHERE role = 'super_admin' AND status = 'active' AND id != ?",
            (actor_user_id,),
        ).fetchall()
        for row in rows:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (id, recipient_user_id, actor_user_id, notification_type, source_id,
                     title, body, target_url, created_at, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _uid(),
                    row["id"],
                    actor_user_id,
                    event_type,
                    notification_source,
                    title,
                    body,
                    target_url,
                    now,
                    task_id,
                ),
            )
            recipients += cursor.rowcount
    return recipients


def notify_task_event(
    task_id: str,
    event_type: str,
    recipient_ids: list[str],
    actor_user_id: Optional[str],
) -> int:
    """Notify task assignees about assignments or review requests."""
    if event_type not in ("assignment", "review"):
        return 0
    task = get_task(task_id)
    if not task:
        return 0
    actor = get_user(actor_user_id) if actor_user_id else None
    actor_name = (actor.get("display_name") or actor["username"]) if actor else "Someone"
    business_name = task.get("business_name") or "General"
    title = (
        f"{actor_name} requested your review: {task['title']} · {business_name}"
        if event_type == "review"
        else f"{actor_name} assigned you: {task['title']} · {business_name}"
    )
    now = _now()
    sent = 0
    with connect() as conn:
        for recipient_id in dict.fromkeys(recipient_ids):
            if recipient_id == actor_user_id:
                continue
            cur = conn.execute("SELECT 1 FROM users WHERE id = ? AND status = 'active'", (recipient_id,))
            if not cur.fetchone():
                continue
            _insert_notification(
                conn,
                recipient_id=recipient_id,
                actor_id=actor_user_id,
                event_type=event_type,
                source_id=_uid(),
                title=title,
                task=task,
                now=now,
            )
            sent += 1
    return sent


def create_task_comment(
    task_id: str,
    author_user_id: str,
    content: str,
    mentioned_user_ids: list[str],
) -> Optional[dict[str, Any]]:
    """Persist a comment and notify assignees and mentioned users."""
    comment_id = _uid()
    now = _now()
    task = get_task(task_id)
    if not task:
        return None
    with connect() as conn:
        author = conn.execute(
            "SELECT username, display_name FROM users WHERE id = ? AND status = 'active'",
            (author_user_id,),
        ).fetchone()
        if not author:
            return None
        active_mentions = _active_comment_mentions(conn, set(mentioned_user_ids))
        mentioned_ids = [user_id for user_id in dict.fromkeys(mentioned_user_ids) if user_id in active_mentions]
        assignee_ids = {
            row["user_id"]
            for row in conn.execute(
                """
                SELECT a.user_id FROM task_assignees a
                JOIN users u ON u.id = a.user_id
                WHERE a.task_id = ? AND u.status = 'active'
                """,
                (task_id,),
            ).fetchall()
        }
        conn.execute(
            """
            INSERT INTO task_comments
                (id, task_id, author_user_id, content, mentioned_user_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (comment_id, task_id, author_user_id, content, json.dumps(mentioned_ids), now),
        )
        conn.execute(
            """
            INSERT INTO task_reads (user_id, task_id, last_read_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id, task_id) DO UPDATE SET last_read_at = excluded.last_read_at
            """,
            (author_user_id, task_id, now),
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
        author_name = author["display_name"] or author["username"]
        business_name = task.get("business_name") or "General"
        notified_mentions = set(mentioned_ids) - {author_user_id}
        notified_assignees = assignee_ids - notified_mentions - {author_user_id}
        for recipient_id in notified_mentions:
            _insert_notification(
                conn,
                recipient_id=recipient_id,
                actor_id=author_user_id,
                event_type="mention",
                source_id=comment_id,
                title=f"{author_name} mentioned you on {task['title']} · {business_name}",
                task=task,
                now=now,
            )
        for recipient_id in notified_assignees:
            _insert_notification(
                conn,
                recipient_id=recipient_id,
                actor_id=author_user_id,
                event_type="comment",
                source_id=comment_id,
                title=f"{author_name} commented on {task['title']} · {business_name}",
                task=task,
                now=now,
            )
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
    """Legacy helper: notify a username about an assignment."""
    recipient = get_user_by_username(owner_username)
    if not recipient:
        return None
    notify_task_event(task_id, "assignment", [recipient["id"]], actor_user_id)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM notifications
            WHERE recipient_user_id = ? AND task_id = ? AND notification_type = 'assignment'
            ORDER BY created_at DESC LIMIT 1
            """,
            (recipient["id"], task_id),
        ).fetchone()
    return get_notification(row["id"], recipient["id"]) if row else None


def list_notifications(
    user_id: str,
    *,
    limit: int = 30,
    unread_only: bool = False,
    read_filter: str = "all",
) -> list[dict[str, Any]]:
    clauses = [
        "n.recipient_user_id = ?",
        "n.notification_type IN ('assignment', 'review', 'comment', 'mention', 'approval', 'admin', 'slack')",
    ]
    params: list[Any] = [user_id]
    if unread_only or read_filter == "unread":
        clauses.append("n.read_at IS NULL")
    elif read_filter == "read":
        clauses.append("n.read_at IS NOT NULL")
    params.append(max(1, min(limit, 200)))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT n.*, COALESCE(a.display_name, a.username) AS actor_name
            FROM notifications n
            LEFT JOIN users a ON a.id = n.actor_user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY n.created_at DESC, n.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def mark_notification_unread(notification_id: str, user_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE notifications SET read_at = NULL WHERE id = ? AND recipient_user_id = ?",
            (notification_id, user_id),
        )
    return cursor.rowcount == 1


def mark_all_notifications_read(user_id: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE recipient_user_id = ? AND read_at IS NULL",
            (_now(), user_id),
        )
    return cursor.rowcount


def count_unread_notifications(user_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM notifications
            WHERE recipient_user_id = ?
              AND notification_type IN ('assignment', 'review', 'comment', 'mention', 'approval', 'admin', 'slack')
              AND read_at IS NULL
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
    return bool(row["sound_enabled"]) if row else False


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


def _approval_dict(row: sqlite3.Row) -> dict[str, Any]:
    approval = dict(row)
    try:
        payload = json.loads(approval.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    approval["approver_name"] = payload.get("approver_name", "") if isinstance(payload, dict) else ""
    return approval


def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return _approval_dict(row) if row else None


def list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    return [_approval_dict(row) for row in rows]


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


def reports_overview(
    start: str,
    end: str,
    *,
    viewer: Optional[dict[str, Any]] = None,
    department_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    completion: Optional[str] = None,
) -> dict[str, Any]:
    """Report shared task data while keeping time entries scoped by role and filters."""
    is_sa = viewer is None or viewer.get("role") == "super_admin"
    is_admin = bool(viewer) and viewer.get("role") == "admin"
    if is_admin:
        user_id = None
    elif not is_sa:
        user_id = viewer["id"]
    tasks = list_tasks(
        department_id=department_id,
        assignee_id=user_id,
        status=status,
        priority=priority,
        viewer=viewer,
        window=(start, end),
        limit=5000,
    )
    if completion == "completed":
        tasks = [t for t in tasks if t["status"] in CLOSED_STATUSES]
    elif completion == "pending":
        tasks = [t for t in tasks if t["status"] not in CLOSED_STATUSES]
    today = date.today().isoformat()
    done = [t for t in tasks if t["status"] == "done"]
    open_tasks = [t for t in tasks if t["status"] not in CLOSED_STATUSES]
    overdue = [t for t in open_tasks if t.get("deadline") and t["deadline"][:10] < today]
    unassigned = [t for t in open_tasks if not t.get("assignee_ids")]

    time = time_report(
        start,
        end,
        user_id=user_id,
        department_id=department_id,
        status=status,
        priority=priority,
        viewer=viewer,
    )
    visible_ids = {t["id"] for t in tasks}
    entries = [
        e for e in time["entries"]
        if is_sa or (viewer and (e["user_id"] == viewer["id"] or e["task_id"] in visible_ids))
    ]

    by_dept: dict[str, dict[str, Any]] = {}

    def dept_bucket(dept_id: Optional[str], name: str) -> dict[str, Any]:
        key = dept_id or "none"
        return by_dept.setdefault(
            key,
            {"department_id": dept_id, "department_name": name or "Uncategorized", "total": 0,
             "completed": 0, "pending": 0, "overdue": 0, "unassigned": 0, "seconds": 0},
        )

    for t in tasks:
        b = dept_bucket(t.get("department_id"), t.get("department_name") or "")
        b["total"] += 1
        if t["status"] in CLOSED_STATUSES:
            b["completed"] += 1
        else:
            b["pending"] += 1
            if t.get("deadline") and t["deadline"][:10] < today:
                b["overdue"] += 1
            if not t.get("assignee_ids"):
                b["unassigned"] += 1
    by_emp: dict[str, dict[str, Any]] = {}
    workload: dict[str, dict[str, int]] = {}
    for e in entries:
        dept_bucket(e.get("department_id"), e.get("department_name") or "")["seconds"] += e["seconds"]
        emp = by_emp.setdefault(
            e["user_id"], {"user_id": e["user_id"], "user_name": e["user_name"], "seconds": 0, "open_tasks": 0, "completed_tasks": 0}
        )
        emp["seconds"] += e["seconds"]
    for t in tasks:
        for a in t.get("assignees", []):
            emp = by_emp.setdefault(
                a["id"], {"user_id": a["id"], "user_name": a.get("display_name") or a.get("username"), "seconds": 0, "open_tasks": 0, "completed_tasks": 0}
            )
            emp["completed_tasks" if t["status"] in CLOSED_STATUSES else "open_tasks"] += 1
            if t["status"] not in CLOSED_STATUSES:
                key = t.get("department_name") or "Uncategorized"
                workload.setdefault(a["id"], {})
                workload[a["id"]][key] = workload[a["id"]].get(key, 0) + 1
    for emp in by_emp.values():
        emp["workload"] = workload.get(emp["user_id"], {})
    departments = list(by_dept.values())
    for b in departments:
        b["completion_rate"] = round(100 * b["completed"] / b["total"]) if b["total"] else 0
    return {
        "start": start,
        "end": end,
        "totals": {
            "tasks": len(tasks),
            "completed": len(done),
            "pending": len(open_tasks),
            "overdue": len(overdue),
            "unassigned": len(unassigned),
            "completion_rate": round(100 * len([t for t in tasks if t["status"] in CLOSED_STATUSES]) / len(tasks)) if tasks else 0,
            "seconds": sum(e["seconds"] for e in entries),
        },
        "by_department": sorted(departments, key=lambda b: -b["total"]),
        "by_employee": sorted(by_emp.values(), key=lambda e: -e["seconds"]),
        "overdue": overdue[:50],
        "unassigned": unassigned[:50],
    }


def attention_brief(today: Optional[str] = None, viewer: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    today_d = date.fromisoformat(today) if today else date.today()
    today_s = today_d.isoformat()
    soon = (today_d + timedelta(days=3)).isoformat()

    tasks = list_tasks(open_only=True, limit=500, viewer=viewer)
    vis, vis_params = _visibility_clause(viewer)
    visibility = f"AND {vis}" if vis else ""
    with connect() as conn:
        metrics = dict(
            conn.execute(
                f"""
                SELECT
                  SUM(CASE WHEN t.status NOT IN ('done', 'cancelled') THEN 1 ELSE 0 END) AS open_tasks,
                  SUM(CASE WHEN t.status NOT IN ('done', 'cancelled', 'in_progress', 'delegated') THEN 1 ELSE 0 END) AS pending_tasks,
                  SUM(CASE WHEN t.status IN ('in_progress', 'delegated') THEN 1 ELSE 0 END) AS in_progress_tasks,
                  SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS completed_tasks,
                  SUM(CASE WHEN t.status NOT IN ('done', 'cancelled')
                            AND t.deadline IS NOT NULL AND t.deadline != ''
                            AND substr(t.deadline, 1, 10) < ? THEN 1 ELSE 0 END) AS overdue,
                  SUM(CASE WHEN t.status NOT IN ('done', 'cancelled')
                            AND substr(t.deadline, 1, 10) = ? THEN 1 ELSE 0 END) AS due_today,
                  SUM(CASE WHEN t.status = 'waiting_on' OR t.waiting_on != '' THEN 1 ELSE 0 END) AS waiting_on,
                  SUM(CASE WHEN t.category = 'decision' THEN 1 ELSE 0 END) AS decisions,
                  SUM(CASE WHEN t.priority = 'P0' THEN 1 ELSE 0 END) AS p0
                FROM tasks t
                LEFT JOIN departments d ON d.id = t.department_id
                WHERE 1 = 1 {visibility}
                """,
                [today_s, today_s, *vis_params],
            ).fetchone()
        )
        brand_workload = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT b.id, b.name, b.logo_url,
                       SUM(CASE WHEN t.status NOT IN ('done', 'cancelled')
                                AND t.status NOT IN ('in_progress', 'delegated') THEN 1 ELSE 0 END) AS pending_tasks,
                       SUM(CASE WHEN t.status IN ('in_progress', 'delegated') THEN 1 ELSE 0 END) AS in_progress_tasks,
                       SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS completed_tasks,
                       SUM(CASE WHEN t.status NOT IN ('done', 'cancelled')
                                 AND t.deadline IS NOT NULL AND t.deadline != ''
                                 AND substr(t.deadline, 1, 10) < ? THEN 1 ELSE 0 END) AS overdue_tasks,
                       SUM(CASE WHEN t.status NOT IN ('done', 'cancelled') AND t.priority = 'P0'
                                THEN 1 ELSE 0 END) AS high_priority_tasks
                FROM tasks t
                JOIN businesses b ON b.id = t.business_id
                LEFT JOIN departments d ON d.id = t.department_id
                WHERE 1 = 1 {visibility}
                GROUP BY b.id, b.name, b.logo_url
                ORDER BY overdue_tasks DESC,
                         pending_tasks DESC, b.name COLLATE NOCASE ASC
                """,
                [today_s, *vis_params],
            )
        ]
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
    pending_approvals = (
        list_approvals("pending")
        if viewer is None or viewer.get("role") in ("admin", "super_admin")
        else []
    )

    by_business: dict[str, int] = {}
    for t in tasks:
        name = t.get("business_name") or t["business_id"]
        by_business[name] = by_business.get(name, 0) + 1

    return {
        "as_of": today_s,
        "summary": {
            "open_tasks": metrics["open_tasks"] or 0,
            "pending_tasks": metrics["pending_tasks"] or 0,
            "in_progress_tasks": metrics["in_progress_tasks"] or 0,
            "completed_tasks": metrics["completed_tasks"] or 0,
            "overdue": metrics["overdue"] or 0,
            "due_today": metrics["due_today"] or 0,
            "due_soon": len(due_soon),
            "followups_due": len(followups_due),
            "waiting_on": metrics["waiting_on"] or 0,
            "decisions": metrics["decisions"] or 0,
            "p0": metrics["p0"] or 0,
            "pending_approvals": len(pending_approvals),
        },
        "by_business": by_business,
        "brand_workload": brand_workload,
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
