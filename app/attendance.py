"""Persistent office attendance, separate from task time tracking."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Attendance timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _policy(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM attendance_policy WHERE policy_id = 'default'").fetchone()
    if not row:
        raise RuntimeError("Attendance policy is not initialized")
    result = dict(row)
    result["working_days"] = json.loads(result.pop("working_days_json"))
    result["overtime_enabled"] = bool(result["overtime_enabled"])
    return result


def get_policy() -> dict[str, Any]:
    with db.connect() as conn:
        return _policy(conn)


def update_policy(
    actor_user_id: str,
    *,
    timezone_name: str,
    heartbeat_interval_seconds: int,
    inactivity_timeout_seconds: int,
    work_start: str,
    work_end: str,
    late_grace_minutes: int,
    overtime_enabled: bool,
    overtime_grace_minutes: int,
    working_days: list[int],
) -> dict[str, Any]:
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Choose a valid IANA timezone") from exc
    for value in (work_start, work_end):
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Working hours must use HH:MM") from exc
    if not 15 <= heartbeat_interval_seconds <= 300:
        raise ValueError("Heartbeat interval must be between 15 and 300 seconds")
    if not heartbeat_interval_seconds < inactivity_timeout_seconds <= 900:
        raise ValueError("Inactivity timeout must exceed the heartbeat interval and be at most 900 seconds")
    if not 0 <= late_grace_minutes <= 240:
        raise ValueError("Late-arrival grace must be between 0 and 240 minutes")
    if not 0 <= overtime_grace_minutes <= 480:
        raise ValueError("Overtime grace must be between 0 and 480 minutes")
    if not working_days or any(day not in range(7) for day in working_days):
        raise ValueError("Select at least one valid working day")

    now = _stamp(_now())
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE attendance_policy
            SET timezone = ?, heartbeat_interval_seconds = ?,
                inactivity_timeout_seconds = ?, work_start = ?, work_end = ?,
                late_grace_minutes = ?, overtime_enabled = ?,
                overtime_grace_minutes = ?, working_days_json = ?, updated_at = ?
            WHERE policy_id = 'default'
            """,
            (
                timezone_name,
                heartbeat_interval_seconds,
                inactivity_timeout_seconds,
                work_start,
                work_end,
                late_grace_minutes,
                int(overtime_enabled),
                overtime_grace_minutes,
                json.dumps(sorted(set(working_days))),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO attendance_audit
                (id, actor_user_id, action, details_json, created_at)
            VALUES (?, ?, 'policy_updated', ?, ?)
            """,
            (
                uuid4().hex,
                actor_user_id,
                json.dumps(
                    {
                        "timezone": timezone_name,
                        "heartbeat_interval_seconds": heartbeat_interval_seconds,
                        "inactivity_timeout_seconds": inactivity_timeout_seconds,
                        "work_start": work_start,
                        "work_end": work_end,
                        "late_grace_minutes": late_grace_minutes,
                        "overtime_enabled": overtime_enabled,
                        "overtime_grace_minutes": overtime_grace_minutes,
                        "working_days": sorted(set(working_days)),
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )
        return _policy(conn)


def _add_event(
    conn: sqlite3.Connection,
    session_id: str,
    event_type: str,
    event_key: str,
    occurred_at: str,
    reason: str = "",
    details: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    event_id = uuid4().hex
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO attendance_events
            (id, session_id, event_type, event_key, occurred_at, reason, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            session_id,
            event_type,
            event_key,
            occurred_at,
            reason,
            json.dumps(details or {}, sort_keys=True),
        ),
    )
    if cursor.rowcount != 1:
        return None
    if event_type in ("interruption", "missed_check_out"):
        owner = conn.execute(
            "SELECT user_id FROM attendance_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if owner:
            title = (
                "Attendance tracking gap detected"
                if event_type == "interruption"
                else "Attendance check-out may be missing"
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (id, recipient_user_id, notification_type, source_id, title, body,
                     target_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, '/#attendance', ?)
                """,
                (
                    uuid4().hex,
                    owner["user_id"],
                    f"attendance_{event_type}",
                    f"{session_id}:{event_key}",
                    title,
                    "Review your attendance timeline and confirm any needed correction.",
                    occurred_at,
                ),
            )
    return event_id


def _record_missed_checkout(
    conn: sqlite3.Connection, session_id: str, now: datetime, policy: dict[str, Any]
) -> None:
    session = conn.execute(
        "SELECT last_heartbeat_at FROM attendance_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        return
    local_now = now.astimezone(ZoneInfo(policy["timezone"])).date()
    local_last = _parse(session["last_heartbeat_at"]).astimezone(
        ZoneInfo(policy["timezone"])
    ).date()
    if local_now > local_last:
        _add_event(
            conn,
            session_id,
            "missed_check_out",
            "missed-checkout",
            _stamp(now),
            "No explicit check-out was recorded before the next local day.",
        )


def _reconcile(conn: sqlite3.Connection, session: sqlite3.Row, now: datetime) -> sqlite3.Row:
    policy = _policy(conn)
    if session["status"] == "checked_in":
        last_heartbeat = _parse(session["last_heartbeat_at"])
        if (now - last_heartbeat).total_seconds() > policy["inactivity_timeout_seconds"]:
            boundary = session["last_heartbeat_at"]
            conn.execute(
                """
                UPDATE attendance_intervals SET ended_at = ?
                WHERE session_id = ? AND ended_at IS NULL
                """,
                (boundary, session["id"]),
            )
            conn.execute(
                """
                UPDATE attendance_sessions
                SET status = 'paused', updated_at = ?
                WHERE id = ? AND status = 'checked_in'
                """,
                (_stamp(now), session["id"]),
            )
            _add_event(
                conn,
                session["id"],
                "interruption",
                f"gap:{boundary}",
                boundary,
                "unknown_tracking_gap",
                {"last_confirmed_at": boundary, "observed_again_at": _stamp(now)},
            )
    _record_missed_checkout(conn, session["id"], now, policy)
    return conn.execute(
        "SELECT * FROM attendance_sessions WHERE id = ?", (session["id"],)
    ).fetchone()


def _duration_seconds(conn: sqlite3.Connection, session_id: str) -> int:
    session = conn.execute(
        "SELECT last_heartbeat_at FROM attendance_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        return 0
    intervals = conn.execute(
        """
        SELECT started_at, COALESCE(ended_at, ?) AS confirmed_until
        FROM attendance_intervals WHERE session_id = ?
        """,
        (session["last_heartbeat_at"], session_id),
    ).fetchall()
    return sum(
        max(0, int((_parse(row["confirmed_until"]) - _parse(row["started_at"])).total_seconds()))
        for row in intervals
    )


def _session_payload(conn: sqlite3.Connection, session: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if not session:
        return None
    payload = dict(session)
    correction = conn.execute(
        "SELECT COALESCE(SUM(delta_seconds), 0) FROM attendance_corrections WHERE session_id = ?",
        (session["id"],),
    ).fetchone()[0]
    payload["confirmed_seconds"] = max(
        0, _duration_seconds(conn, session["id"]) + int(correction or 0)
    )
    payload["intervals"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT started_at, ended_at FROM attendance_intervals
            WHERE session_id = ? ORDER BY started_at
            """,
            (session["id"],),
        )
    ]
    payload["events"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_type, occurred_at, reason, details_json, reviewed_at,
                   resolution_note
            FROM attendance_events WHERE session_id = ?
            ORDER BY occurred_at DESC LIMIT 50
            """,
            (session["id"],),
        )
    ]
    for event in payload["events"]:
        event["details"] = json.loads(event.pop("details_json"))
        event["review_status"] = "resolved" if event["reviewed_at"] else "needs_review"
    return payload


def status(user_id: str) -> dict[str, Any]:
    now = _now()
    with db.connect() as conn:
        policy = _policy(conn)
        session = conn.execute(
            """
            SELECT * FROM attendance_sessions
            WHERE user_id = ? AND checked_out_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if session:
            session = _reconcile(conn, session, now)
        else:
            zone = ZoneInfo(policy["timezone"])
            local_day = now.astimezone(zone).date()
            day_start = _stamp(datetime.combine(local_day, time.min, tzinfo=zone))
            day_end = _stamp(datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone))
            session = conn.execute(
                """
                SELECT * FROM attendance_sessions
                WHERE user_id = ? AND checked_in_at < ?
                  AND COALESCE(checked_out_at, '9999-12-31T23:59:59.999999Z') >= ?
                ORDER BY COALESCE(checked_out_at, checked_in_at) DESC LIMIT 1
                """,
                (user_id, day_end, day_start),
            ).fetchone()
        payload = _session_payload(conn, session)
    zone = ZoneInfo(policy["timezone"])
    local_now = now.astimezone(zone)
    month = local_now.strftime("%Y-%m")
    daily = report(month, [user_id])["employees"]
    today_key = local_now.date().isoformat()
    today_row = next(
        (day for employee in daily for day in employee["daily"] if day["date"] == today_key),
        None,
    )
    return {
        "session": payload,
        "today_confirmed_seconds": today_row["confirmed_seconds"] if today_row else 0,
        "policy": policy,
        "server_time": _stamp(now),
        "tracking_mode": "browser_heartbeat",
        "tracking_limit": (
            "Browser heartbeats detect tracking gaps but cannot reliably confirm "
            "operating-system sleep or physical office presence."
        ),
    }


def check_in(user_id: str) -> dict[str, Any]:
    now = _stamp(_now())
    session_id = uuid4().hex
    with db.connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO attendance_sessions
                    (id, user_id, status, checked_in_at, last_heartbeat_at,
                     created_at, updated_at)
                VALUES (?, ?, 'checked_in', ?, ?, ?, ?)
                """,
                (session_id, user_id, now, now, now, now),
            )
            conn.execute(
                """
                INSERT INTO attendance_intervals (id, session_id, started_at)
                VALUES (?, ?, ?)
                """,
                (uuid4().hex, session_id, now),
            )
            conn.execute(
                """
                INSERT INTO attendance_heartbeats (id, session_id, request_id, received_at)
                VALUES (?, ?, ?, ?)
                """,
                (uuid4().hex, session_id, "check-in", now),
            )
            _add_event(conn, session_id, "checked_in", "check-in", now)
            conn.execute(
                """
                INSERT INTO notifications
                    (id, recipient_user_id, notification_type, source_id, title, body,
                     target_url, created_at)
                VALUES (?, ?, 'attendance_check_in', ?, 'Office check-in recorded',
                        'Your attendance session has started.', '/#attendance', ?)
                """,
                (uuid4().hex, user_id, session_id, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("You already have an active attendance session.") from exc
        session = conn.execute(
            "SELECT * FROM attendance_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _session_payload(conn, session) or {}


def heartbeat(user_id: str, request_id: str) -> dict[str, Any]:
    now_dt = _now()
    now = _stamp(now_dt)
    with db.connect() as conn:
        session = conn.execute(
            """
            SELECT * FROM attendance_sessions
            WHERE user_id = ? AND checked_out_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if not session:
            raise ValueError("Check in before sending attendance heartbeats.")
        session = _reconcile(conn, session, now_dt)
        if conn.execute(
            """
            SELECT 1 FROM attendance_heartbeats
            WHERE session_id = ? AND request_id = ?
            """,
            (session["id"], request_id),
        ).fetchone():
            return _session_payload(conn, session) or {}

        if session["status"] == "paused":
            conn.execute(
                """
                INSERT INTO attendance_intervals (id, session_id, started_at)
                VALUES (?, ?, ?)
                """,
                (uuid4().hex, session["id"], now),
            )
            _add_event(
                conn,
                session["id"],
                "resumed",
                f"resume:{request_id}",
                now,
                "tracking_active_again",
            )
        conn.execute(
            """
            INSERT INTO attendance_heartbeats (id, session_id, request_id, received_at)
            VALUES (?, ?, ?, ?)
            """,
            (uuid4().hex, session["id"], request_id, now),
        )
        conn.execute(
            """
            UPDATE attendance_sessions
            SET status = 'checked_in', last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, session["id"]),
        )
        session = conn.execute(
            "SELECT * FROM attendance_sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        return _session_payload(conn, session) or {}


def check_out(user_id: str) -> dict[str, Any]:
    now_dt = _now()
    now = _stamp(now_dt)
    with db.connect() as conn:
        session = conn.execute(
            """
            SELECT * FROM attendance_sessions
            WHERE user_id = ? AND checked_out_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if not session:
            raise ValueError("There is no active attendance session to check out.")
        session = _reconcile(conn, session, now_dt)
        if session["status"] == "checked_in":
            conn.execute(
                """
                UPDATE attendance_intervals SET ended_at = ?
                WHERE session_id = ? AND ended_at IS NULL
                """,
                (now, session["id"]),
            )
        conn.execute(
            """
            UPDATE attendance_sessions
            SET status = 'checked_out', checked_out_at = ?, updated_at = ?
            WHERE id = ? AND checked_out_at IS NULL
            """,
            (now, now, session["id"]),
        )
        _add_event(conn, session["id"], "checked_out", "check-out", now)
        conn.execute(
            """
            INSERT INTO notifications
                (id, recipient_user_id, notification_type, source_id, title, body,
                 target_url, created_at)
            VALUES (?, ?, 'attendance_check_out', ?, 'Office check-out recorded',
                    'Your attendance session has ended.', '/#attendance', ?)
            """,
            (uuid4().hex, user_id, session["id"], now),
        )
        session = conn.execute(
            "SELECT * FROM attendance_sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        return _session_payload(conn, session) or {}


def report(
    month: str,
    user_ids: list[str],
    *,
    department_id: Optional[str] = None,
) -> dict[str, Any]:
    try:
        month_start = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM format") from exc
    if len(month) != 7:
        raise ValueError("month must use YYYY-MM format")
    next_month = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)

    with db.connect() as conn:
        policy = _policy(conn)
        zone = ZoneInfo(policy["timezone"])
        users: dict[str, dict[str, Any]] = {}
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            for active in conn.execute(
                f"""
                SELECT * FROM attendance_sessions
                WHERE checked_out_at IS NULL AND user_id IN ({placeholders})
                """,
                user_ids,
            ).fetchall():
                _reconcile(conn, active, _now())
            for row in conn.execute(
                f"""
                SELECT DISTINCT u.id, u.username, u.display_name
                FROM users u
                LEFT JOIN department_members dm ON dm.user_id = u.id
                WHERE u.id IN ({placeholders})
                  AND (? IS NULL OR dm.department_id = ?)
                ORDER BY u.display_name, u.username
                """,
                (*user_ids, department_id, department_id),
            ):
                users[row["id"]] = dict(row)
        output: list[dict[str, Any]] = []
        month_start_dt = datetime.combine(month_start, time.min, tzinfo=zone).astimezone(timezone.utc)
        month_end_dt = datetime.combine(next_month, time.min, tzinfo=zone).astimezone(timezone.utc)
        for user_id, user in users.items():
            sessions = conn.execute(
                """
                SELECT * FROM attendance_sessions
                WHERE user_id = ? AND checked_in_at < ?
                  AND COALESCE(checked_out_at, '9999-12-31T23:59:59.999999Z') > ?
                ORDER BY checked_in_at
                """,
                (user_id, _stamp(month_end_dt), _stamp(month_start_dt)),
            ).fetchall()
            daily: dict[str, dict[str, Any]] = {}

            def day_row(day: date) -> dict[str, Any]:
                key = day.isoformat()
                return daily.setdefault(
                    key,
                    {
                        "date": key,
                        "confirmed_seconds": 0,
                        "correction_seconds": 0,
                        "overtime_seconds": 0,
                        "check_ins": [],
                        "check_outs": [],
                        "session_ids": [],
                        "late_arrival": False,
                        "early_departure": False,
                        "missed_check_out": False,
                        "interrupted": False,
                        "exceptions": [],
                    },
                )

            session_ids: list[str] = []
            for session in sessions:
                session_ids.append(session["id"])
                checked_in = _parse(session["checked_in_at"]).astimezone(zone)
                checked_out = _parse(session["checked_out_at"]).astimezone(zone) if session["checked_out_at"] else None
                if month_start <= checked_in.date() < next_month:
                    row = day_row(checked_in.date())
                    row["check_ins"].append(session["checked_in_at"])
                    row["session_ids"].append(session["id"])
                    if checked_in.weekday() in policy["working_days"]:
                        start_clock = time.fromisoformat(policy["work_start"])
                        scheduled = datetime.combine(checked_in.date(), start_clock, tzinfo=zone)
                        late_by = (checked_in - scheduled).total_seconds() / 60
                        row["late_arrival"] = row["late_arrival"] or late_by > policy["late_grace_minutes"]
                if checked_out and month_start <= checked_out.date() < next_month:
                    row = day_row(checked_out.date())
                    row["check_outs"].append(session["checked_out_at"])
                    if session["id"] not in row["session_ids"]:
                        row["session_ids"].append(session["id"])
                    if checked_out.weekday() in policy["working_days"]:
                        scheduled_end = datetime.combine(
                            checked_out.date(), time.fromisoformat(policy["work_end"]), tzinfo=zone
                        )
                        row["early_departure"] = row["early_departure"] or checked_out < scheduled_end

                intervals = conn.execute(
                    """
                    SELECT started_at, ended_at FROM attendance_intervals
                    WHERE session_id = ? ORDER BY started_at
                    """,
                    (session["id"],),
                ).fetchall()
                for interval in intervals:
                    start = max(_parse(interval["started_at"]), month_start_dt)
                    end = min(
                        _parse(interval["ended_at"]) if interval["ended_at"] else _parse(session["last_heartbeat_at"]),
                        month_end_dt,
                    )
                    if end <= start:
                        continue
                    cursor = start
                    while cursor < end:
                        local_cursor = cursor.astimezone(zone)
                        next_local_midnight = datetime.combine(
                            local_cursor.date() + timedelta(days=1), time.min, tzinfo=zone
                        ).astimezone(timezone.utc)
                        segment_end = min(end, next_local_midnight)
                        day = local_cursor.date()
                        if month_start <= day < next_month:
                            day_row(day)["confirmed_seconds"] += int((segment_end - cursor).total_seconds())
                            if policy["overtime_enabled"] and day.weekday() in policy["working_days"]:
                                overtime_boundary = datetime.combine(
                                    day,
                                    time.fromisoformat(policy["work_end"]),
                                    tzinfo=zone,
                                ) + timedelta(minutes=policy["overtime_grace_minutes"])
                                overtime_start = max(cursor, overtime_boundary.astimezone(timezone.utc))
                                if segment_end > overtime_start:
                                    day_row(day)["overtime_seconds"] += int(
                                        (segment_end - overtime_start).total_seconds()
                                    )
                        cursor = segment_end
            if session_ids:
                ids = ",".join("?" for _ in session_ids)
                for event in conn.execute(
                    f"""
                    SELECT e.*, s.checked_in_at FROM attendance_events e
                    JOIN attendance_sessions s ON s.id = e.session_id
                    WHERE e.session_id IN ({ids}) AND e.event_type IN
                        ('interruption', 'missed_check_out')
                    """,
                    session_ids,
                ):
                    occurred = _parse(event["occurred_at"]).astimezone(zone).date()
                    if month_start <= occurred < next_month:
                        row = day_row(occurred)
                        row["exceptions"].append(
                            {
                                "id": event["id"],
                                "event_type": event["event_type"],
                                "reason": event["reason"],
                                "occurred_at": event["occurred_at"],
                                "review_status": "resolved" if event["reviewed_at"] else "needs_review",
                            }
                        )
                        if event["event_type"] == "interruption":
                            row["interrupted"] = True
                        if event["event_type"] == "missed_check_out":
                            row["missed_check_out"] = True
                for correction in conn.execute(
                    f"""
                    SELECT c.delta_seconds, s.checked_in_at
                    FROM attendance_corrections c
                    JOIN attendance_sessions s ON s.id = c.session_id
                    WHERE c.session_id IN ({ids})
                    """,
                    session_ids,
                ):
                    correction_day = _parse(correction["checked_in_at"]).astimezone(zone).date()
                    if month_start <= correction_day < next_month:
                        day_row(correction_day)["correction_seconds"] += correction["delta_seconds"]

            for row in daily.values():
                row["confirmed_seconds"] += row["correction_seconds"]
                row["check_in_at"] = min(row["check_ins"]) if row["check_ins"] else None
                row["check_out_at"] = max(row["check_outs"]) if row["check_outs"] else None
                row["check_ins"].sort()
                row["check_outs"].sort()
            daily_rows = sorted(daily.values(), key=lambda row: row["date"])
            local_today = _now().astimezone(zone).date()
            scheduled_until = min(next_month, local_today + timedelta(days=1))
            scheduled_days = sum(
                1
                for offset in range(max(0, (scheduled_until - month_start).days))
                if (month_start + timedelta(days=offset)).weekday() in policy["working_days"]
            )
            present_days = sum(bool(day["check_ins"] or day["confirmed_seconds"]) for day in daily_rows)
            total_seconds = sum(day["confirmed_seconds"] for day in daily_rows)
            output.append(
                {
                    "user_id": user_id,
                    "username": user["username"],
                    "employee_name": user["display_name"] or user["username"],
                    "month": month,
                    "scheduled_working_days": scheduled_days,
                    "days_present": present_days,
                    "days_absent": max(0, scheduled_days - present_days),
                    "late_arrivals": sum(day["late_arrival"] for day in daily_rows),
                    "early_departures": sum(day["early_departure"] for day in daily_rows),
                    "missed_check_out_days": sum(day["missed_check_out"] for day in daily_rows),
                    "interrupted_days": sum(day["interrupted"] for day in daily_rows),
                    "confirmed_seconds": total_seconds,
                    "average_seconds_per_present_day": int(total_seconds / present_days) if present_days else 0,
                    "approved_break_seconds": 0,
                    "overtime_seconds": sum(day["overtime_seconds"] for day in daily_rows),
                    "leave_and_holiday_data_available": False,
                    "daily": daily_rows,
                }
            )
        return {
            "month": month,
            "timezone": policy["timezone"],
            "policy": policy,
            "employees": output,
        }


def correct_session(
    actor_user_id: str, session_id: str, delta_seconds: int, reason: str
) -> dict[str, Any]:
    if delta_seconds == 0 or abs(delta_seconds) > 86400:
        raise ValueError("Correction must add or subtract between 1 second and 24 hours.")
    reason = reason.strip()
    if not reason:
        raise ValueError("A reason is required for attendance corrections.")
    if len(reason) > 500:
        raise ValueError("Correction reason must be 500 characters or fewer.")
    now = _stamp(_now())
    with db.connect() as conn:
        session = conn.execute(
            "SELECT * FROM attendance_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise LookupError("Attendance session not found.")
        current_seconds = _duration_seconds(conn, session_id)
        previous_corrections = conn.execute(
            "SELECT COALESCE(SUM(delta_seconds), 0) FROM attendance_corrections WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        if not 0 <= current_seconds + previous_corrections + delta_seconds <= 86400:
            raise ValueError("The corrected attendance duration must be between 0 and 24 hours.")
        conn.execute(
            """
            INSERT INTO attendance_corrections
                (id, session_id, actor_user_id, delta_seconds, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex, session_id, actor_user_id, delta_seconds, reason, now),
        )
        conn.execute(
            """
            INSERT INTO attendance_audit
                (id, actor_user_id, action, session_id, details_json, created_at)
            VALUES (?, ?, 'attendance_corrected', ?, ?, ?)
            """,
            (
                uuid4().hex,
                actor_user_id,
                session_id,
                json.dumps({"delta_seconds": delta_seconds, "reason": reason}, sort_keys=True),
                now,
            ),
        )
        return _session_payload(conn, session) or {}


def resolve_event(
    actor_user_id: str, event_id: str, resolution_note: str
) -> dict[str, Any]:
    resolution_note = resolution_note.strip()
    if not resolution_note:
        raise ValueError("A resolution note is required.")
    if len(resolution_note) > 500:
        raise ValueError("Resolution note must be 500 characters or fewer.")
    now = _stamp(_now())
    with db.connect() as conn:
        event = conn.execute(
            "SELECT * FROM attendance_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not event:
            raise LookupError("Attendance exception not found.")
        if event["reviewed_at"]:
            raise ValueError("This attendance exception has already been resolved.")
        conn.execute(
            """
            UPDATE attendance_events
            SET reviewed_at = ?, reviewed_by = ?, resolution_note = ?
            WHERE id = ? AND reviewed_at IS NULL
            """,
            (now, actor_user_id, resolution_note, event_id),
        )
        conn.execute(
            """
            INSERT INTO attendance_audit
                (id, actor_user_id, action, session_id, details_json, created_at)
            VALUES (?, ?, 'attendance_exception_resolved', ?, ?, ?)
            """,
            (
                uuid4().hex,
                actor_user_id,
                event["session_id"],
                json.dumps({"event_id": event_id, "resolution_note": resolution_note}),
                now,
            ),
        )
        return {"ok": True, "event_id": event_id, "reviewed_at": now}


def audit_history(session_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    with db.connect() as conn:
        if session_id:
            rows = conn.execute(
                """
                SELECT a.*, u.display_name, u.username
                FROM attendance_audit a JOIN users u ON u.id = a.actor_user_id
                WHERE a.session_id = ? ORDER BY a.created_at DESC LIMIT ?
                """,
                (session_id, limit),
            )
        else:
            rows = conn.execute(
                """
                SELECT a.*, u.display_name, u.username
                FROM attendance_audit a JOIN users u ON u.id = a.actor_user_id
                ORDER BY a.created_at DESC LIMIT ?
                """,
                (limit,),
            )
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result
