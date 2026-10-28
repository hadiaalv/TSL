import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import attendance, db
from app.auth import hash_password
from app.config import settings
from app.main import app


class OfficeAttendanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.super_admin = db.create_user(
            "attendance-owner", hash_password("test-password"), role="super_admin"
        )
        self.admin = db.create_user(
            "attendance-admin", hash_password("test-password"), role="admin"
        )
        self.operator = db.create_user(
            "attendance-worker", hash_password("test-password")
        )
        self.other = db.create_user("other-worker", hash_password("test-password"))
        self.department = db.list_departments()[0]
        db.add_department_member(self.department["id"], self.admin["id"], self.super_admin["id"])
        db.add_department_member(self.department["id"], self.operator["id"], self.super_admin["id"])
        self.client = TestClient(app, base_url="https://testserver")
        self.start = datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc)

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def login(self, username):
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": "test-password"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def at(self, minutes):
        return self.start + timedelta(minutes=minutes)

    def test_check_in_heartbeat_gap_resume_and_check_out_only_count_confirmed_intervals(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        self.assertEqual(session["status"], "checked_in")

        with patch("app.attendance._now", return_value=self.at(2)):
            heartbeat = attendance.heartbeat(self.operator["id"], "heartbeataaaaaaaa")
        self.assertEqual(heartbeat["confirmed_seconds"], 120)

        with patch("app.attendance._now", return_value=self.at(4)):
            heartbeat = attendance.heartbeat(self.operator["id"], "heartbeatsecond01")
        self.assertEqual(heartbeat["confirmed_seconds"], 240)

        with patch("app.attendance._now", return_value=self.at(8)):
            paused = attendance.status(self.operator["id"])["session"]
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["confirmed_seconds"], 240)
        self.assertIn("unknown_tracking_gap", [event["reason"] for event in paused["events"]])

        with patch("app.attendance._now", return_value=self.at(53)):
            resumed = attendance.heartbeat(self.operator["id"], "heartbeatbbbbbbbb")
        self.assertEqual(resumed["status"], "checked_in")
        self.assertEqual(resumed["confirmed_seconds"], 240)

        with patch("app.attendance._now", return_value=self.at(55)):
            updated = attendance.heartbeat(self.operator["id"], "heartbeatcccccccc")
        self.assertEqual(updated["confirmed_seconds"], 360)

        with patch("app.attendance._now", return_value=self.at(57)):
            checked_out = attendance.check_out(self.operator["id"])
        self.assertEqual(checked_out["status"], "checked_out")
        self.assertEqual(checked_out["confirmed_seconds"], 480)
        self.assertEqual(len(checked_out["intervals"]), 2)

    def test_duplicate_check_in_is_rejected_and_heartbeat_request_is_idempotent(self):
        with patch("app.attendance._now", return_value=self.start):
            attendance.check_in(self.operator["id"])
            with self.assertRaisesRegex(ValueError, "already have an active"):
                attendance.check_in(self.operator["id"])

        request_id = "heartbeatretry0001"
        with patch("app.attendance._now", return_value=self.at(1)):
            first = attendance.heartbeat(self.operator["id"], request_id)
        with patch("app.attendance._now", return_value=self.at(2)):
            retry = attendance.heartbeat(self.operator["id"], request_id)
        self.assertEqual(first["last_heartbeat_at"], retry["last_heartbeat_at"])
        self.assertEqual(first["confirmed_seconds"], retry["confirmed_seconds"])

    def test_monthly_report_uses_policy_timezone_and_persisted_intervals(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        with patch("app.attendance._now", return_value=self.at(2)):
            attendance.heartbeat(self.operator["id"], "heartbeatmonth0001")
        with patch("app.attendance._now", return_value=self.at(5)):
            attendance.check_out(self.operator["id"])

        report = attendance.report("2026-10", [self.operator["id"]])
        employee = report["employees"][0]
        self.assertEqual(report["timezone"], "Asia/Karachi")
        self.assertEqual(employee["days_present"], 1)
        self.assertEqual(employee["confirmed_seconds"], 300)
        self.assertEqual(employee["daily"][0]["session_ids"], [session["id"]])
        self.assertEqual(employee["daily"][0]["check_in_at"], session["checked_in_at"])

    def test_status_preserves_completed_check_out_and_sums_multiple_day_sessions(self):
        with patch("app.attendance._now", return_value=self.start):
            first = attendance.check_in(self.operator["id"])
        with patch("app.attendance._now", return_value=self.at(2)):
            attendance.heartbeat(self.operator["id"], "heartbeattoday0001")
        with patch("app.attendance._now", return_value=self.at(3)):
            attendance.check_out(self.operator["id"])

        with patch("app.attendance._now", return_value=self.at(4)):
            second = attendance.check_in(self.operator["id"])
        with patch("app.attendance._now", return_value=self.at(6)):
            attendance.heartbeat(self.operator["id"], "heartbeattoday0002")
        with patch("app.attendance._now", return_value=self.at(7)):
            attendance.check_out(self.operator["id"])

        with patch("app.attendance._now", return_value=self.at(8)):
            current = attendance.status(self.operator["id"])
        self.assertEqual(current["session"]["status"], "checked_out")
        self.assertEqual(current["session"]["id"], second["id"])
        self.assertIsNotNone(current["session"]["checked_out_at"])
        self.assertEqual(current["today_confirmed_seconds"], 360)
        self.assertNotEqual(first["id"], second["id"])

    def test_missed_check_out_is_flagged_on_next_organization_day(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        with patch("app.attendance._now", return_value=self.start + timedelta(days=1)):
            paused = attendance.status(self.operator["id"])["session"]
        event_types = [event["event_type"] for event in paused["events"]]
        self.assertEqual(paused["status"], "paused")
        self.assertIn("missed_check_out", event_types)
        report = attendance.report("2026-10", [self.operator["id"]])
        self.assertEqual(report["employees"][0]["missed_check_out_days"], 1)
        self.assertEqual(session["checked_in_at"], paused["checked_in_at"])

    def test_overtime_uses_confirmed_intervals_and_configured_grace(self):
        attendance.update_policy(
            self.super_admin["id"],
            timezone_name="UTC",
            heartbeat_interval_seconds=300,
            inactivity_timeout_seconds=900,
            work_start="09:00",
            work_end="18:00",
            late_grace_minutes=15,
            overtime_enabled=True,
            overtime_grace_minutes=30,
            working_days=[0, 1, 2, 3, 4],
        )
        start = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)
        with patch("app.attendance._now", return_value=start):
            attendance.check_in(self.operator["id"])
        for minutes in range(10, 121, 10):
            with patch("app.attendance._now", return_value=start + timedelta(minutes=minutes)):
                attendance.heartbeat(self.operator["id"], f"heartbeat-overtime-{minutes}")
        with patch("app.attendance._now", return_value=start + timedelta(minutes=121)):
            attendance.check_out(self.operator["id"])

        report = attendance.report("2026-10", [self.operator["id"]])
        employee = report["employees"][0]
        self.assertEqual(employee["confirmed_seconds"], 7260)
        self.assertEqual(employee["overtime_seconds"], 1860)

    def test_api_enforces_personal_attendance_and_admin_department_scope(self):
        self.login("attendance-worker")
        self.assertEqual(self.client.get("/api/attendance/status").status_code, 200)
        forbidden = self.client.get(
            "/api/attendance/report",
            params={"month": "2026-10", "user_id": self.other["id"]},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/attendance/sessions/not-a-session/corrections",
                json={"delta_seconds": 60, "reason": "Not authorized"},
            ).status_code,
            403,
        )

        self.login("attendance-admin")
        allowed = self.client.get(
            "/api/attendance/report",
            params={
                "month": "2026-10",
                "user_id": self.operator["id"],
                "department_id": self.department["id"],
            },
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(
            self.client.get(
                "/api/attendance/report",
                params={"month": "2026-10", "user_id": self.other["id"]},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.put(
                "/api/attendance/policy",
                json={
                    "timezone": "Asia/Karachi",
                    "heartbeat_interval_seconds": 45,
                    "inactivity_timeout_seconds": 180,
                    "work_start": "09:00",
                    "work_end": "18:00",
                    "late_grace_minutes": 15,
                    "overtime_enabled": False,
                    "overtime_grace_minutes": 30,
                    "working_days": [0, 1, 2, 3, 4],
                },
            ).status_code,
            403,
        )

    def test_super_admin_correction_requires_reason_and_is_audited(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        with patch("app.attendance._now", return_value=self.at(2)):
            attendance.heartbeat(self.operator["id"], "heartbeatcorr0001")
        with patch("app.attendance._now", return_value=self.at(3)):
            attendance.check_out(self.operator["id"])

        self.login("attendance-owner")
        missing_reason = self.client.post(
            f"/api/attendance/sessions/{session['id']}/corrections",
            json={"delta_seconds": 60, "reason": ""},
        )
        self.assertEqual(missing_reason.status_code, 422)
        corrected = self.client.post(
            f"/api/attendance/sessions/{session['id']}/corrections",
            json={"delta_seconds": 60, "reason": "Verified with office sign-in log"},
        )
        self.assertEqual(corrected.status_code, 200, corrected.text)
        audit = self.client.get("/api/attendance/audit").json()
        self.assertEqual(audit[0]["action"], "attendance_corrected")

        report = self.client.get(
            "/api/attendance/report",
            params={"month": "2026-10", "user_id": self.operator["id"]},
        ).json()
        self.assertEqual(report["employees"][0]["confirmed_seconds"], 240)

    def test_policy_rejects_invalid_timeouts_and_audits_valid_changes(self):
        with self.assertRaisesRegex(ValueError, "must exceed the heartbeat"):
            attendance.update_policy(
                self.super_admin["id"],
                timezone_name="Asia/Karachi",
                heartbeat_interval_seconds=45,
                inactivity_timeout_seconds=45,
                work_start="09:00",
                work_end="18:00",
                late_grace_minutes=15,
                overtime_enabled=False,
                overtime_grace_minutes=30,
                working_days=[0, 1, 2, 3, 4],
            )
        policy = attendance.update_policy(
            self.super_admin["id"],
            timezone_name="UTC",
            heartbeat_interval_seconds=30,
            inactivity_timeout_seconds=120,
            work_start="08:30",
            work_end="17:00",
            late_grace_minutes=10,
            overtime_enabled=True,
            overtime_grace_minutes=20,
            working_days=[0, 1, 2, 3, 4, 5],
        )
        self.assertEqual(policy["timezone"], "UTC")
        self.assertTrue(policy["overtime_enabled"])
        self.assertEqual(policy["working_days"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(attendance.audit_history()[0]["action"], "policy_updated")


if __name__ == "__main__":
    unittest.main()
