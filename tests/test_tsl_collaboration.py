import tempfile
import unittest

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app


class TSLTaskCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.author = db.create_user(
            "board-author",
            hash_password("test-password"),
            display_name="Board Author",
            role="super_admin",
        )
        self.recipient = db.create_user(
            "board-recipient",
            hash_password("test-password"),
            display_name="Board Recipient",
        )
        self.department = db.list_departments()[0]
        db.add_department_member(self.department["id"], self.recipient["id"], self.author["id"])
        self.business = db.create_business("Launch board")
        self.task = db.create_task(
            business_id=self.business["id"],
            title="Prepare launch",
            department_id=self.department["id"],
        )
        self.author_client = TestClient(app)
        self.recipient_client = TestClient(app)
        self._login(self.author_client, "board-author")
        self._login(self.recipient_client, "board-recipient")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    @staticmethod
    def _login(client, username):
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": "test-password"},
        )
        if response.status_code != 200:
            raise AssertionError(f"Could not log in test user: {response.text}")

    def test_mentions_are_recorded_without_notifications(self):
        response = self.author_client.post(
            f"/api/tasks/{self.task['id']}/comments",
            json={"content": "Please review @BOARD-RECIPIENT and @board-recipient"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(db.list_task_comments(self.task["id"])), 1)
        self.assertEqual(
            self.author_client.get("/api/notifications").json()["unread_count"],
            0,
        )
        self.assertIn(self.business["name"], recipient_notifications["items"][0]["title"])

        self.assertEqual(
            self.recipient_client.get("/api/notifications").json()["unread_count"],
            0,
        )

    def test_assigning_task_notifies_assignee_and_board_endpoints_work(self):
        response = self.author_client.patch(
            f"/api/tasks/{self.task['id']}",
            json={"assignee_ids": [self.recipient["id"]]},
        )

        self.assertEqual(response.status_code, 200)
        recipient_notifications = self.recipient_client.get("/api/notifications").json()
        self.assertEqual(recipient_notifications["unread_count"], 1)
        self.assertEqual(
            self.author_client.get("/api/notifications").json()["unread_count"],
            0,
        )

        updated_board = self.author_client.patch(
            f"/api/businesses/{self.business['id']}",
            json={"name": "Updated launch board", "description": "Shared launch work"},
        )
        self.assertEqual(updated_board.status_code, 200)
        self.assertEqual(updated_board.json()["name"], "Updated launch board")

        created_task = self.author_client.post(
            "/api/tasks",
            json={
                "business_id": self.business["id"],
                "title": "Review launch assets",
                "assignee_ids": [self.recipient["id"]],
                "department_id": self.department["id"],
                "priority": "P1",
                "status": "open",
                "source": "board",
            },
        )
        self.assertEqual(created_task.status_code, 200)
        self.assertEqual(created_task.json()["business_id"], self.business["id"])
        self.assertEqual(created_task.json()["priority"], "P1")
        self.assertEqual(
            self.recipient_client.get("/api/notifications").json()["unread_count"],
            2,
        )
        self.assertIn(
            "board-recipient",
            {member["username"] for member in self.author_client.get("/api/team/members").json()},
        )

    def test_moving_assigned_task_to_review_notifies_reviewer_who_can_complete_it(self):
        assigned = self.author_client.patch(
            f"/api/tasks/{self.task['id']}",
            json={"assignee_ids": [self.recipient["id"]]},
        )
        self.assertEqual(assigned.status_code, 200)

        review = self.author_client.patch(
            f"/api/tasks/{self.task['id']}",
            json={"status": "waiting_on"},
        )
        self.assertEqual(review.status_code, 200)
        notifications = self.recipient_client.get("/api/notifications").json()
        self.assertEqual(notifications["unread_count"], 2)
        review_notification = next(
            item for item in notifications["items"] if item["notification_type"] == "review"
        )
        self.assertIn("requested your review", review_notification["title"])
        self.assertEqual(review_notification["task_id"], self.task["id"])

        completed = self.recipient_client.patch(
            f"/api/tasks/{self.task['id']}",
            json={"status": "done"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "done")

    def test_assigning_reviewer_to_task_already_in_review_sends_review_notification(self):
        db.update_task(self.task["id"], status="waiting_on")

        assigned = self.author_client.patch(
            f"/api/tasks/{self.task['id']}",
            json={"assignee_ids": [self.recipient["id"]]},
        )
        self.assertEqual(assigned.status_code, 200)
        notifications = self.recipient_client.get("/api/notifications").json()
        self.assertEqual(notifications["unread_count"], 1)
        self.assertEqual(notifications["items"][0]["notification_type"], "review")

    def test_creating_task_in_review_notifies_assigned_reviewer(self):
        created = self.author_client.post(
            "/api/tasks",
            json={
                "business_id": self.business["id"],
                "title": "Check final artwork",
                "assignee_ids": [self.recipient["id"]],
                "department_id": self.department["id"],
                "status": "waiting_on",
            },
        )
        self.assertEqual(created.status_code, 200)
        notifications = self.recipient_client.get("/api/notifications").json()
        self.assertEqual(notifications["unread_count"], 1)
        self.assertEqual(notifications["items"][0]["notification_type"], "review")

    def test_sound_preference_persists_per_user(self):
        enabled = self.recipient_client.put(
            "/api/notifications/sound",
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.json()["sound_enabled"])
        self.assertTrue(self.recipient_client.get("/api/notifications").json()["sound_enabled"])
        self.assertFalse(self.author_client.get("/api/notifications").json()["sound_enabled"])

    def test_brand_links_add_list_delete(self):
        base = f"/api/businesses/{self.business['id']}/links"
        added = self.author_client.post(
            base, json={"kind": "instagram", "url": "instagram.com/launch"}
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.json()["url"], "https://instagram.com/launch")
        self.assertEqual(len(self.author_client.get(base).json()), 1)
        bad = self.author_client.post(base, json={"kind": "website", "url": "nonsense"})
        self.assertEqual(bad.status_code, 422)
        deleted = self.author_client.delete(f"{base}/{added.json()['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.author_client.get(base).json(), [])


if __name__ == "__main__":
    unittest.main()
