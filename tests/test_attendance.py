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

    def test_sleep_or_closed_browser_never_pauses_and_duration_uses_server_timestamps(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        self.assertEqual(session["status"], "checked_in")

        with patch("app.attendance._now", return_value=self.at(4)):
            attendance.heartbeat(self.operator["id"], "heartbeatsecond01")

        # No heartbeats for hours (sleep / shutdown): the session is still running.
        with patch("app.attendance._now", return_value=self.at(180)):
            active = attendance.status(self.operator["id"])["session"]
        self.assertEqual(active["status"], "checked_in")
        self.assertEqual(active["confirmed_seconds"], 180 * 60)

        with patch("app.attendance._now", return_value=self.at(480)):
            checked_out = attendance.check_out(self.operator["id"])
        self.assertEqual(checked_out["status"], "checked_out")
        self.assertEqual(checked_out["confirmed_seconds"], 8 * 3600)
        self.assertEqual(checked_out["checked_out_at"], attendance._stamp(self.at(480)))

        with patch("app.attendance._now", return_value=self.at(481)):
            with self.assertRaisesRegex(ValueError, "no active attendance session"):
                attendance.check_out(self.operator["id"])

    def test_sessions_paused_by_the_old_gap_logic_are_resumed(self):
        with patch("app.attendance._now", return_value=self.start):
            session = attendance.check_in(self.operator["id"])
        with db.connect() as conn:
            conn.execute("UPDATE attendance_sessions SET status = 'paused' WHERE id = ?", (session["id"],))
            conn.execute(
                "UPDATE attendance_intervals SET ended_at = ? WHERE session_id = ?",
                (attendance._stamp(self.at(5)), session["id"]),
            )
        with patch("app.attendance._now", return_value=self.at(60)):
            current = attendance.status(self.operator["id"])["session"]
        self.assertEqual(current["status"], "checked_in")
        self.assertEqual(current["confirmed_seconds"], 3600)

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
        self.assertEqual(paused["status"], "checked_in")
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
        self.assertEqual(allowed.status_code, 403, allowed.text)
        self.assertEqual(
            self.client.get(
                "/api/attendance/report",
                params={"month": "2026-10", "user_id": self.operator["id"]},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/attendance/report", params={"month": "2026-10"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/attendance/leaves",
                json={"user_id": self.operator["id"], "leave_date": "2026-10-05", "leave_type": "half"},
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

    def test_today_overview_is_super_admin_only(self):
        for username in ("attendance-admin", "attendance-worker"):
            self.client.cookies.clear()
            self.login(username)
            self.assertEqual(self.client.get("/api/attendance/today").status_code, 403)
        self.client.cookies.clear()
        self.login("attendance-owner")
        response = self.client.get("/api/attendance/today")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["counts"]["checked_in"], 0)
        self.assertGreaterEqual(len(body["employees"]), 4)

    def test_leave_allowances_are_counted_per_employee(self):
        self.login("attendance-owner")
        for day, kind in (("2026-10-05", "half"), ("2026-10-06", "half"), ("2026-10-07", "full")):
            response = self.client.post(
                "/api/attendance/leaves",
                json={"user_id": self.operator["id"], "leave_date": day, "leave_type": kind},
            )
            self.assertEqual(response.status_code, 200, response.text)
        duplicate = self.client.post(
            "/api/attendance/leaves",
            json={"user_id": self.operator["id"], "leave_date": "2026-10-05", "leave_type": "full"},
        )
        self.assertEqual(duplicate.status_code, 422)
        report = self.client.get(
            "/api/attendance/report",
            params={"month": "2026-10", "user_id": self.operator["id"]},
        ).json()
        allowances = report["employees"][0]["allowances"]
        self.assertEqual(allowances["half_leaves"]["taken"], 2)
        self.assertTrue(allowances["half_leaves"]["exceeded"])
        self.assertEqual(allowances["half_leaves"]["allowed"], 1)
        self.assertEqual(allowances["days_off"]["taken"], 1)
        other = self.client.get(
            "/api/attendance/report",
            params={"month": "2026-10", "user_id": self.admin["id"]},
        ).json()
        self.assertEqual(other["employees"][0]["allowances"]["half_leaves"]["taken"], 0)

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


class DepartmentLeadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.sa = db.create_user("lead-owner", hash_password("test-password"), role="super_admin")
        self.lead = db.create_user("lead-one", hash_password("test-password"))
        self.lead2 = db.create_user("lead-two", hash_password("test-password"))
        self.member = db.create_user("lead-member", hash_password("test-password"))
        self.outsider = db.create_user("lead-outsider", hash_password("test-password"))
        self.dept, self.other_dept = db.list_departments()[:2]
        for user in (self.lead, self.lead2, self.member):
            db.add_department_member(self.dept["id"], user["id"], self.sa["id"])
        db.add_department_member(self.other_dept["id"], self.outsider["id"], self.sa["id"])
        self.client = TestClient(app, base_url="https://testserver")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def login(self, username):
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": "test-password"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_only_super_admin_assigns_and_only_department_members_qualify(self):
        path = f"/api/departments/{self.dept['id']}/lead"
        self.login("lead-one")
        self.assertEqual(self.client.put(path, json={"user_id": self.lead["id"]}).status_code, 403)

        self.login("lead-owner")
        self.assertEqual(self.client.put(path, json={"user_id": self.outsider["id"]}).status_code, 400)
        ok = self.client.put(path, json={"user_id": self.lead["id"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        listed = {d["id"]: d for d in self.client.get("/api/departments").json()}
        self.assertEqual(listed[self.dept["id"]]["lead_user_id"], self.lead["id"])
        self.assertTrue(listed[self.dept["id"]]["lead_name"])

    def test_lead_sees_own_department_only_and_changing_lead_moves_access(self):
        db.set_department_lead(self.dept["id"], self.lead["id"])
        with patch("app.attendance._now", return_value=datetime.now(timezone.utc) - timedelta(hours=2)):
            attendance.check_in(self.member["id"])

        self.login("lead-one")
        me = self.client.get("/api/auth/me").json()["user"]
        self.assertEqual(me["led_department_ids"], [self.dept["id"]])
        overview = self.client.get("/api/attendance/overview")
        self.assertEqual(overview.status_code, 200, overview.text)
        body = overview.json()
        self.assertEqual([d["id"] for d in body["departments"]], [self.dept["id"]])
        states = {m["id"]: m["state"] for m in body["departments"][0]["members"]}
        self.assertEqual(states[self.member["id"]], "checked_in")
        self.assertNotIn(self.outsider["id"], states)

        self.assertEqual(
            self.client.get("/api/attendance/overview", params={"department_id": self.other_dept["id"]}).status_code, 403
        )
        report = self.client.get(
            "/api/attendance/report",
            params={"month": datetime.now(timezone.utc).strftime("%Y-%m"), "user_id": "all", "department_id": self.dept["id"]},
        )
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(
            self.client.get(
                "/api/attendance/report",
                params={"month": "2026-10", "user_id": self.outsider["id"], "department_id": self.dept["id"]},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/attendance/report",
                params={"month": "2026-10", "user_id": "all", "department_id": self.other_dept["id"]},
            ).status_code,
            403,
        )
        window = {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}
        self.assertEqual(
            self.client.get("/api/time/report", params={**window, "user_id": self.member["id"], "department_id": self.dept["id"]}).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/time/report", params={**window, "user_id": self.outsider["id"], "department_id": self.dept["id"]}).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/time/report", params={**window, "user_id": self.member["id"]}).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(
            self.client.put(f"/api/departments/{self.dept['id']}/lead", json={"user_id": None}).status_code, 403
        )
        self.assertEqual(
            self.client.post("/api/attendance/sessions/x/corrections", json={"delta_seconds": 60, "reason": "no"}).status_code,
            403,
        )

        db.set_department_lead(self.dept["id"], self.lead2["id"])
        self.assertEqual(self.client.get("/api/attendance/overview").status_code, 403)
        self.login("lead-two")
        self.assertEqual(self.client.get("/api/attendance/overview").status_code, 200)

    def test_removing_the_lead_or_their_membership_clears_access(self):
        db.set_department_lead(self.dept["id"], self.lead["id"])
        db.remove_department_member(self.dept["id"], self.lead["id"])
        self.assertEqual(db.led_department_ids(self.lead["id"]), [])
        db.set_department_lead(self.dept["id"], self.lead2["id"])
        db.set_department_lead(self.dept["id"], None)
        self.assertEqual(db.led_department_ids(self.lead2["id"]), [])

    def test_regular_employee_and_super_admin_overview(self):
        self.login("lead-member")
        self.assertEqual(self.client.get("/api/attendance/overview").status_code, 403)
        self.login("lead-owner")
        body = self.client.get("/api/attendance/overview").json()
        self.assertGreaterEqual(len(body["departments"]), 2)

