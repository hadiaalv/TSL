import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TimeTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.user = db.create_user("worker", hash_password("test-password"))
        self.business = db.create_business(name="Acme")
        self.task = db.create_task(self.business["id"], "Write report", owner="worker")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def _entries(self):
        with db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM task_time_entries")]

    def _shift_entries(self, minutes):
        with db.connect() as conn:
            conn.execute(
                "UPDATE task_time_entries SET started_at = ?, ended_at = CASE WHEN ended_at IS NULL THEN NULL ELSE ? END",
                (_iso(datetime.now(timezone.utc) - timedelta(minutes=minutes)), _iso(datetime.now(timezone.utc))),
            )

    def test_timer_is_attributed_to_assignee_actor_and_stops_when_card_leaves_in_progress(self):
        actor = db.create_user("actor", hash_password("test-password"))
        db.update_task(self.task["id"], status="in_progress", actor_user_id=actor["id"])
        self.assertEqual(self._entries(), [])
        db.update_task(self.task["id"], status="open", actor_user_id=actor["id"])

        db.update_task(self.task["id"], status="in_progress", actor_user_id=self.user["id"])
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["user_id"], self.user["id"])
        self.assertIsNone(entries[0]["ended_at"])

        listed = db.list_tasks(business_id=self.business["id"])[0]
        self.assertIsNotNone(listed["timer_started_at"])

        db.update_task(self.task["id"], status="done")
        self.assertIsNotNone(self._entries()[0]["ended_at"])
        listed = db.list_tasks(business_id=self.business["id"], open_only=False)[0]
        self.assertIsNone(listed["timer_started_at"])

    def test_repeat_in_progress_does_not_duplicate_running_timer(self):
        db.update_task(self.task["id"], status="in_progress", actor_user_id=self.user["id"])
        db.update_task(self.task["id"], status="in_progress", actor_user_id=self.user["id"])
        self.assertEqual(len(self._entries()), 1)

    def test_unassigned_card_starts_no_timer(self):
        task = db.create_task(self.business["id"], "Unassigned")
        db.update_task(task["id"], status="in_progress", actor_user_id=self.user["id"])
        self.assertEqual(self._entries(), [])

    def test_report_totals_and_merges_overlapping_work(self):
        second = db.create_task(self.business["id"], "Second", owner="worker")
        uid = self.user["id"]
        db.update_task(self.task["id"], status="in_progress", actor_user_id=uid)
        db.update_task(second["id"], status="in_progress", actor_user_id=uid)
        db.update_task(self.task["id"], status="open")
        db.update_task(second["id"], status="open")
        self._shift_entries(60)

        now = datetime.now(timezone.utc)
        report = db.time_report(_iso(now - timedelta(hours=2)), _iso(now + timedelta(hours=1)))

        self.assertEqual(len(report["tasks"]), 2)
        self.assertGreaterEqual(report["total_seconds"], 2 * 3590)
        self.assertLess(report["worked_seconds"], report["total_seconds"])
        self.assertAlmostEqual(report["worked_seconds"], 3600, delta=10)

    def test_report_clips_entries_to_window(self):
        db.update_task(self.task["id"], status="in_progress", actor_user_id=self.user["id"])
        db.update_task(self.task["id"], status="done")
        self._shift_entries(120)

        now = datetime.now(timezone.utc)
        report = db.time_report(_iso(now - timedelta(minutes=30)), _iso(now + timedelta(hours=1)))
        self.assertAlmostEqual(report["total_seconds"], 1800, delta=10)

    def test_report_endpoint_requires_login_and_valid_range(self):
        client = TestClient(app)
        now = datetime.now(timezone.utc)
        params = {"start": _iso(now - timedelta(hours=1)), "end": _iso(now)}
        self.assertEqual(client.get("/api/time/report", params=params).status_code, 401)

        client.post("/api/auth/login", json={"username": "worker", "password": "test-password"})
        self.assertEqual(client.get("/api/time/report", params=params).status_code, 200)
        self.assertEqual(
            client.get("/api/time/report", params={"start": params["end"], "end": params["start"]}).status_code,
            400,
        )

    def test_report_defaults_to_signed_in_user_and_blocks_others(self):
        other = db.create_user("other", hash_password("test-password"))
        mine = db.create_task(self.business["id"], "Mine", owner="worker")
        theirs = db.create_task(self.business["id"], "Theirs", owner="other")
        for task, actor in ((mine, self.user), (theirs, other)):
            db.update_task(task["id"], status="in_progress", actor_user_id=actor["id"])
            db.update_task(task["id"], status="done")

        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "worker", "password": "test-password"})
        now = datetime.now(timezone.utc)
        params = {"start": _iso(now - timedelta(hours=1)), "end": _iso(now + timedelta(hours=1))}

        titles = [t["task_title"] for t in client.get("/api/time/report", params=params).json()["tasks"]]
        self.assertEqual(titles, ["Mine"])
        self.assertEqual(
            client.get("/api/time/report", params={**params, "user_id": other["id"]}).status_code, 403
        )
        self.assertEqual(
            client.get("/api/time/report", params={**params, "user_id": "all"}).status_code, 403
        )

    def test_admin_can_view_everyone_or_a_specific_person(self):
        db.create_user("boss", hash_password("test-password"), role="super_admin")
        db.update_task(self.task["id"], status="in_progress", actor_user_id=self.user["id"])
        db.update_task(self.task["id"], status="done")

        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "boss", "password": "test-password"})
        now = datetime.now(timezone.utc)
        params = {"start": _iso(now - timedelta(hours=1)), "end": _iso(now + timedelta(hours=1))}

        self.assertEqual(client.get("/api/time/report", params=params).json()["tasks"], [])
        everyone = client.get("/api/time/report", params={**params, "user_id": "all"}).json()
        self.assertEqual(len(everyone["tasks"]), 1)
        worker = client.get("/api/time/report", params={**params, "user_id": self.user["id"]}).json()
        self.assertEqual(len(worker["tasks"]), 1)

    def test_moving_card_via_api_records_time_for_acting_user(self):
        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "worker", "password": "test-password"})
        res = client.patch(f"/api/tasks/{self.task['id']}", json={"status": "in_progress"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self._entries()), 1)
        tasks = client.get("/api/tasks").json()
        self.assertIsNotNone(tasks[0]["timer_started_at"])


if __name__ == "__main__":
    unittest.main()
