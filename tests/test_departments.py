import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app

PASSWORD = "test-password"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DepartmentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.admin = db.create_user("boss", hash_password(PASSWORD), "Boss", role="super_admin")
        self.ann = db.create_user("ann", hash_password(PASSWORD), "Ann")
        self.bob = db.create_user("bob", hash_password(PASSWORD), "Bob")
        self.cy = db.create_user("cy", hash_password(PASSWORD), "Cy")
        self.depts = {d["name"]: d for d in db.list_departments()}
        self.writing = self.depts["Content Writing & Planning"]
        self.design = self.depts["Graphic Designing"]
        db.add_department_member(self.writing["id"], self.ann["id"], self.admin["id"])
        db.add_department_member(self.writing["id"], self.bob["id"], self.admin["id"])
        db.add_department_member(self.design["id"], self.ann["id"], self.admin["id"])
        self.business = db.create_business("Acme")
        self.sa = self._client("boss")
        self.ann_c = self._client("ann")
        self.bob_c = self._client("bob")
        self.cy_c = self._client("cy")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    @staticmethod
    def _client(username):
        client = TestClient(app)
        res = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert res.status_code == 200, res.text
        return client

    def _create(self, **extra):
        body = {
            "business_id": self.business["id"],
            "title": "Write captions",
            "department_id": self.writing["id"],
            "assignee_ids": [self.ann["id"]],
            **extra,
        }
        res = self.sa.post("/api/tasks", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_create_with_description_checklist_and_links(self):
        task = self._create(
            description="post",
            checklist=[{"text": "post", "done": False}],
            links=[{"url": "https://araish.com/", "label": "", "kind": "link"}],
            assignee_ids=[],
        )
        self.assertEqual(task["description"], "post")
        self.assertEqual(len(task["checklist"]), 1)
        self.assertEqual(len(task["links"]), 1)

    def test_every_user_sees_all_active_departments_and_tasks(self):
        res = self.cy_c.get("/api/departments")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 5)
        task = self._create()
        tasks = self.cy_c.get("/api/tasks", params={"department_id": self.writing["id"]}).json()
        self.assertEqual([item["id"] for item in tasks], [task["id"]])

    def test_five_departments_seeded_and_idempotent(self):
        db.init_db()
        names = [d["name"] for d in db.list_departments()]
        self.assertEqual(len(names), 5)
        self.assertIn("Ads Management", names)

    def test_only_super_admin_manages_departments_and_users(self):
        self.assertEqual(self.ann_c.post("/api/departments", json={"name": "Ops"}).status_code, 403)
        self.assertEqual(
            self.ann_c.patch(f"/api/departments/{self.writing['id']}", json={"name": "X"}).status_code, 403
        )
        self.assertEqual(
            self.ann_c.post(
                f"/api/departments/{self.writing['id']}/members", json={"user_id": self.cy["id"]}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.ann_c.post("/api/admin/users", json={"username": "evil", "password": "longenough1"}).status_code,
            403,
        )
        self.assertEqual(
            self.ann_c.patch(f"/api/admin/users/{self.ann['id']}", json={"role": "super_admin"}).status_code, 403
        )

        created = self.sa.post("/api/departments", json={"name": "Ops", "color": "#112233"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.sa.post("/api/departments", json={"name": "ops"}).status_code, 400)
        renamed = self.sa.patch(f"/api/departments/{created.json()['id']}", json={"name": "Operations"})
        self.assertEqual(renamed.json()["name"], "Operations")
        off = self.sa.patch(f"/api/departments/{created.json()['id']}", json={"status": "inactive"})
        self.assertEqual(off.json()["status"], "inactive")

    def test_last_super_admin_cannot_be_demoted_or_disabled(self):
        self.assertEqual(self.sa.patch(f"/api/admin/users/{self.admin['id']}", json={"role": "operator"}).status_code, 400)
        self.assertEqual(
            self.sa.patch(f"/api/admin/users/{self.admin['id']}", json={"status": "disabled"}).status_code, 400
        )

    def test_operator_cannot_create_or_manage_tasks(self):
        body = {"business_id": self.business["id"], "title": "Nope"}
        self.assertEqual(self.ann_c.post("/api/tasks", json=body).status_code, 403)
        task = self._create()
        res = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"assignee_ids": [self.bob["id"]]})
        self.assertEqual(res.status_code, 403)
        res = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"department_id": self.design["id"]})
        self.assertEqual(res.status_code, 403)
        res = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"status": "review_nope"})
        self.assertEqual(res.status_code, 422)
        res = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"status": "waiting_on"})
        self.assertEqual(res.status_code, 200)

    def test_assignees_must_belong_to_department_and_are_explicit(self):
        res = self.sa.post(
            "/api/tasks",
            json={
                "business_id": self.business["id"],
                "title": "Bad",
                "department_id": self.design["id"],
                "assignee_ids": [self.bob["id"]],
            },
        )
        self.assertEqual(res.status_code, 400)
        no_dept = self.sa.post(
            "/api/tasks",
            json={"business_id": self.business["id"], "title": "Bad", "assignee_ids": [self.ann["id"]]},
        )
        self.assertEqual(no_dept.status_code, 400)

        task = self._create()
        self.assertEqual(task["assignee_ids"], [self.ann["id"]])
        # Bob can see the task but cannot change it unless assigned.
        self.assertEqual(self.bob_c.get(f"/api/tasks/{task['id']}").status_code, 200)
        self.assertEqual(self.bob_c.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code, 403)
        # All signed-in users can see all tasks and their conversations.
        self.assertEqual(self.cy_c.get(f"/api/tasks/{task['id']}").status_code, 200)
        self.assertEqual(self.cy_c.get(f"/api/tasks/{task['id']}/comments").status_code, 200)
        self.assertEqual(
            self.cy_c.post(f"/api/tasks/{task['id']}/comments", json={"content": "hi"}).status_code, 200
        )

    def test_views_my_unassigned_overdue_completed(self):
        mine = self._create(title="Mine")
        unassigned = self._create(title="Loose", assignee_ids=[])
        past = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        overdue = self._create(title="Late", deadline=past)
        done = self._create(title="Finished")
        self.sa.patch(f"/api/tasks/{done['id']}", json={"status": "done"})

        def titles(client, **params):
            return {t["title"] for t in client.get("/api/tasks", params=params).json()}

        self.assertEqual(titles(self.ann_c, view="mine"), {"Mine", "Late"})
        self.assertIn("Loose", titles(self.sa, view="unassigned"))
        self.assertNotIn("Mine", titles(self.sa, view="unassigned"))
        self.assertEqual(titles(self.sa, view="overdue"), {"Late"})
        self.assertEqual(titles(self.sa, view="completed"), {"Finished"})
        self.assertEqual(titles(self.sa, department_id=self.writing["id"], open_only="false"), {
            "Mine", "Loose", "Late", "Finished"})
        self.assertEqual(
            {task["id"] for task in self.cy_c.get("/api/tasks").json()},
            {mine["id"], unassigned["id"], overdue["id"]},
        )
        self.assertTrue({mine["id"], unassigned["id"], overdue["id"]})

    def test_assignment_notification_includes_department_and_actor(self):
        task = self._create()
        items = self.ann_c.get("/api/notifications").json()["items"]
        self.assertEqual(len(items), 1)
        n = items[0]
        self.assertEqual(n["notification_type"], "assignment")
        self.assertEqual(n["department_name"], "Content Writing & Planning")
        self.assertEqual(n["actor_name"], "Boss")
        self.assertEqual(n["task_id"], task["id"])
        self.assertEqual(n["target_url"], f"/?task={task['id']}")
        self.assertEqual(self.bob_c.get("/api/notifications").json()["items"], [])
        self.assertEqual(self.sa.get("/api/notifications").json()["items"], [])

        self.sa.patch(f"/api/tasks/{task['id']}", json={"assignee_ids": [self.bob["id"]]})
        self.assertEqual(self.bob_c.get("/api/notifications").json()["unread_count"], 1)
        types = {i["notification_type"] for i in self.ann_c.get("/api/notifications").json()["items"]}
        self.assertEqual(types, {"assignment"})

    def test_reassigning_department_requires_members_without_notifying(self):
        task = self._create()
        res = self.sa.patch(f"/api/tasks/{task['id']}", json={"department_id": self.depts["Ads Management"]["id"]})
        self.assertEqual(res.status_code, 400)
        res = self.sa.patch(f"/api/tasks/{task['id']}", json={"department_id": self.design["id"]})
        self.assertEqual(res.status_code, 200)
        types = [i["notification_type"] for i in self.ann_c.get("/api/notifications").json()["items"]]
        self.assertEqual(types, ["assignment"])

    def test_notification_filters_read_unread_and_mark_all(self):
        self._create(title="One")
        self._create(title="Two")
        data = self.ann_c.get("/api/notifications").json()
        self.assertEqual(data["unread_count"], 2)
        first = data["items"][0]["id"]
        self.ann_c.post(f"/api/notifications/{first}/read")
        self.assertEqual(len(self.ann_c.get("/api/notifications", params={"filter": "read"}).json()["items"]), 1)
        self.assertEqual(len(self.ann_c.get("/api/notifications", params={"filter": "unread"}).json()["items"]), 1)
        self.ann_c.post(f"/api/notifications/{first}/unread")
        self.assertEqual(self.ann_c.get("/api/notifications").json()["unread_count"], 2)
        self.assertEqual(self.bob_c.post(f"/api/notifications/{first}/unread").status_code, 404)
        self.ann_c.post("/api/notifications/read-all")
        self.assertEqual(self.ann_c.get("/api/notifications").json()["unread_count"], 0)
        self.assertEqual(self.ann_c.get("/api/notifications", params={"filter": "bogus"}).status_code, 422)

    def test_comments_notify_assignees_and_still_track_unread(self):
        task = self._create(assignee_ids=[self.ann["id"], self.bob["id"]])
        self.ann_c.post("/api/notifications/read-all")
        self.bob_c.post("/api/notifications/read-all")
        res = self.ann_c.post(f"/api/tasks/{task['id']}/comments", json={"content": "On it"})
        self.assertEqual(res.status_code, 200)
        bob_items = self.bob_c.get("/api/notifications", params={"filter": "unread"}).json()["items"]
        self.assertEqual(len(bob_items), 1)
        self.assertEqual(bob_items[0]["notification_type"], "comment")
        self.assertIn("commented on", bob_items[0]["title"])
        self.assertEqual(self.ann_c.get("/api/notifications", params={"filter": "unread"}).json()["items"], [])

        card = next(t for t in self.bob_c.get("/api/tasks").json() if t["id"] == task["id"])
        self.assertEqual(card["comment_count"], 1)
        self.assertEqual(card["unread_comments"], 1)
        self.bob_c.get(f"/api/tasks/{task['id']}/comments")
        card = next(t for t in self.bob_c.get("/api/tasks").json() if t["id"] == task["id"])
        self.assertEqual(card["unread_comments"], 0)

    def test_mentions_are_recorded_and_notify_mentioned_users(self):
        task = self._create()
        comment = self.ann_c.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "cc @cy and @bob"},
        ).json()
        self.assertEqual(
            set(json.loads(comment["mentioned_user_ids"])),
            {self.cy["id"], self.bob["id"]},
        )
        for client in (self.cy_c, self.bob_c):
            notifications = client.get("/api/notifications").json()
            self.assertEqual(notifications["unread_count"], 1)
            self.assertEqual(notifications["items"][0]["notification_type"], "mention")

    def test_time_logs_belong_to_assignee_actor_with_department(self):
        task = self._create()
        # Super admin (creator, not assignee) moving the card logs no time.
        self.sa.patch(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_time_entries").fetchone()[0], 0)
        self.sa.patch(f"/api/tasks/{task['id']}", json={"status": "open"})
        self.ann_c.patch(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
        with db.connect() as conn:
            row = conn.execute("SELECT user_id, department_id FROM task_time_entries").fetchone()
        self.assertEqual(row["user_id"], self.ann["id"])
        self.assertEqual(row["department_id"], self.writing["id"])

        now = datetime.now(timezone.utc)
        params = {"start": _iso(now - timedelta(hours=1)), "end": _iso(now + timedelta(hours=1))}
        by_dept = self.sa.get(
            "/api/time/report", params={**params, "user_id": "all", "department_id": self.writing["id"]}
        ).json()
        self.assertEqual(len(by_dept["entries"]), 1)
        self.assertEqual(by_dept["entries"][0]["department_name"], "Content Writing & Planning")
        other = self.sa.get(
            "/api/time/report", params={**params, "user_id": "all", "department_id": self.design["id"]}
        ).json()
        self.assertEqual(other["entries"], [])

    def test_losing_membership_keeps_history(self):
        task = self._create()
        self.ann_c.patch(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
        self.ann_c.post(f"/api/tasks/{task['id']}/comments", json={"content": "history"})
        res = self.sa.delete(f"/api/departments/{self.writing['id']}/members/{self.ann['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.ann_c.get(f"/api/tasks/{task['id']}").status_code, 200)
        self.assertEqual(len(db.list_task_comments(task["id"])), 1)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_time_entries").fetchone()[0], 1)

    def test_reports_overview_scopes_by_role(self):
        self._create(title="A")
        self._create(title="B", assignee_ids=[])
        now = datetime.now(timezone.utc)
        params = {"start": _iso(now - timedelta(days=1)), "end": _iso(now + timedelta(days=1))}
        full = self.sa.get("/api/reports/overview", params=params).json()
        self.assertEqual(full["totals"]["tasks"], 2)
        self.assertEqual(full["totals"]["unassigned"], 1)
        row = next(d for d in full["by_department"] if d["department_name"] == "Content Writing & Planning")
        self.assertEqual(row["total"], 2)
        scoped = self.sa.get(
            "/api/reports/overview", params={**params, "department_id": self.design["id"]}
        ).json()
        self.assertEqual(scoped["totals"]["tasks"], 0)
        self.assertEqual(self.cy_c.get("/api/reports/overview", params=params).json()["totals"]["tasks"], 0)

    def test_links_must_be_http_and_checklist_progress_only_for_assignees(self):
        bad = self.sa.post(
            "/api/tasks",
            json={"business_id": self.business["id"], "title": "x", "links": [{"url": "javascript:alert(1)"}]},
        )
        self.assertEqual(bad.status_code, 422)
        task = self._create(checklist=[{"text": "Draft", "done": False}], links=[{"url": "https://example.com"}])
        ok = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"checklist": [{"text": "Draft", "done": True}]})
        self.assertEqual(ok.status_code, 200)
        edit = self.ann_c.patch(f"/api/tasks/{task['id']}", json={"checklist": [{"text": "Other", "done": True}]})
        self.assertEqual(edit.status_code, 403)

    def test_migration_preserves_legacy_tasks(self):
        path = Path(self.temp_dir.name) / "legacy"
        path.mkdir()
        conn = sqlite3.connect(path / "cos.db")
        conn.executescript(
            """
            CREATE TABLE businesses (id TEXT PRIMARY KEY, name TEXT, slug TEXT, status TEXT);
            CREATE TABLE tasks (id TEXT PRIMARY KEY, business_id TEXT, title TEXT, status TEXT DEFAULT 'open',
                owner TEXT DEFAULT 'me', created_at TEXT, updated_at TEXT, deadline TEXT,
                priority TEXT DEFAULT 'P2', category TEXT DEFAULT 'other', next_follow_up TEXT,
                waiting_on TEXT DEFAULT '', notes TEXT DEFAULT '', source TEXT DEFAULT 'manual',
                source_ref TEXT DEFAULT '');
            INSERT INTO businesses VALUES ('b1', 'Old', 'old', 'active');
            INSERT INTO tasks (id, business_id, title, created_at, updated_at)
                VALUES ('t1', 'b1', 'Legacy task', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
            """
        )
        conn.commit()
        conn.close()
        settings.data_dir = str(path)
        try:
            db.init_db()
            self.assertEqual(db.get_task("t1")["title"], "Legacy task")
            self.assertEqual(len(db.list_departments()), 5)
            self.assertTrue(list((path / "backups").glob("cos-before-departments-*.db")))
        finally:
            settings.data_dir = self.temp_dir.name


if __name__ == "__main__":
    unittest.main()
