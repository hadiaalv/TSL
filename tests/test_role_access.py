import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app

PASSWORD = "test-password"


class RoleAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.super_admin = db.create_user(
            "portfolio-owner", hash_password(PASSWORD), role="super_admin"
        )
        self.admin = db.create_user("operations-admin", hash_password(PASSWORD), role="admin")
        self.operator = db.create_user("content-operator", hash_password(PASSWORD))
        departments = db.list_departments()
        self.department = departments[0]
        self.other_department = departments[1]
        db.add_department_member(self.department["id"], self.admin["id"], self.super_admin["id"])
        db.add_department_member(self.department["id"], self.operator["id"], self.super_admin["id"])
        self.business = db.create_business("Authorized Brand")
        self.unrelated_business = db.create_business("Unrelated Brand")
        self.admin_client = self._client("operations-admin")
        self.operator_client = self._client("content-operator")
        self.sa_client = self._client("portfolio-owner")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    @staticmethod
    def _client(username):
        client = TestClient(app)
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD},
        )
        assert response.status_code == 200, response.text
        return client

    def test_admin_can_manage_tasks_only_in_authorized_departments(self):
        created = self.admin_client.post(
            "/api/tasks",
            json={
                "business_id": self.business["id"],
                "title": "Plan the launch",
                "department_id": self.department["id"],
                "assignee_ids": [self.operator["id"]],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["id"]
        self.assertEqual(
            self.admin_client.patch(
                f"/api/tasks/{task_id}",
                json={"priority": "P0", "assignee_ids": [self.operator["id"]]},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.admin_client.post(
                "/api/tasks",
                json={
                    "business_id": self.business["id"],
                    "title": "Outside the team",
                    "department_id": self.other_department["id"],
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            self.admin_client.patch(
                f"/api/tasks/{task_id}",
                json={"department_id": self.other_department["id"]},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.admin_client.patch(
                f"/api/tasks/{task_id}",
                json={"department_id": None},
            ).status_code,
            403,
        )

        outside_task = db.create_task(
            business_id=self.business["id"],
            title="Assigned from another department",
            department_id=self.other_department["id"],
            assignee_ids=[self.admin["id"]],
        )
        self.assertEqual(
            self.admin_client.patch(
                f"/api/tasks/{outside_task['id']}",
                json={"priority": "P0"},
            ).status_code,
            403,
        )

    def test_operator_cannot_create_tasks_or_edit_management_fields(self):
        task = db.create_task(
            business_id=self.business["id"],
            title="Assigned work",
            department_id=self.department["id"],
            assignee_ids=[self.operator["id"]],
        )
        self.assertEqual(
            self.operator_client.post(
                "/api/tasks",
                json={"business_id": self.business["id"], "title": "Unauthorized"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.operator_client.patch(
                f"/api/tasks/{task['id']}", json={"priority": "P0"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.operator_client.patch(
                f"/api/tasks/{task['id']}", json={"status": "in_progress"}
            ).status_code,
            200,
        )

    def test_role_boundaries_apply_to_brand_and_administration_apis(self):
        task = db.create_task(
            business_id=self.business["id"],
            title="Visible brand work",
            department_id=self.department["id"],
            assignee_ids=[self.operator["id"]],
        )
        unrelated_task = db.create_task(
            business_id=self.unrelated_business["id"],
            title="Workspace-wide task",
            department_id=self.other_department["id"],
        )
        operator_brands = {
            brand["id"] for brand in self.operator_client.get("/api/businesses").json()
        }
        self.assertEqual(
            operator_brands,
            {self.business["id"], self.unrelated_business["id"]},
        )
        self.assertEqual(
            self.operator_client.get(f"/api/businesses/{self.unrelated_business['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.operator_client.get(f"/api/tasks/{unrelated_task['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.operator_client.patch(
                f"/api/businesses/{self.business['id']}", json={"name": "Changed"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.operator_client.get("/api/admin/users").status_code,
            403,
        )
        self.assertEqual(
            self.admin_client.post("/api/departments", json={"name": "Unauthorized"}).status_code,
            403,
        )
        self.assertEqual(self.operator_client.get("/api/approvals").status_code, 403)
        self.assertEqual(self.admin_client.get("/api/approvals").status_code, 200)
        self.assertEqual(
            self.operator_client.get(f"/api/tasks/{task['id']}").status_code,
            200,
        )

    def test_integrations_are_limited_to_their_workspace_roles(self):
        for path in ("/api/google/status", "/api/google/auth"):
            self.assertEqual(self.operator_client.get(path).status_code, 403)
            self.assertEqual(self.admin_client.get(path).status_code, 403)
        for path in ("/api/slack/status", "/api/slack/auth"):
            self.assertEqual(self.operator_client.get(path).status_code, 403)

    def test_overview_metrics_and_brand_counts_use_visible_database_work(self):
        past_due = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        for title, status, deadline in (
            ("Pending task", "open", None),
            ("Overdue task", "open", past_due),
            ("Active task", "in_progress", None),
            ("Completed task", "done", None),
        ):
            db.create_task(
                business_id=self.business["id"],
                title=title,
                status=status,
                deadline=deadline,
                department_id=self.department["id"],
                assignee_ids=[self.operator["id"]],
            )

        brief = self.admin_client.get("/api/brief").json()
        self.assertEqual(brief["summary"]["pending_tasks"], 2)
        self.assertEqual(brief["summary"]["in_progress_tasks"], 1)
        self.assertEqual(brief["summary"]["completed_tasks"], 1)
        self.assertEqual(brief["summary"]["overdue"], 1)
        self.assertEqual(
            brief["brand_workload"][0]["id"],
            self.business["id"],
        )
        self.assertEqual(brief["brand_workload"][0]["overdue_tasks"], 1)
        self.assertEqual(brief["brand_workload"][0]["in_progress_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
