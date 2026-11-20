import tempfile
import unittest

from app import db
from app.auth import hash_password
from app.config import settings


class PersonSpecificApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.creator = db.create_user(
            "approval-creator", hash_password("password"), role="admin"
        )
        self.approver = db.create_user(
            "approval-reviewer", hash_password("password"), role="operator"
        )
        self.business = db.create_business("Approval brand")
        self.task = db.create_task(
            self.business["id"],
            "Review campaign",
            actor_user_id=self.creator["id"],
        )

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def test_only_assigned_approver_can_decide(self):
        approval = db.create_approval(
            "Review campaign",
            business_id=self.business["id"],
            task_id=self.task["id"],
            approver_user_id=self.approver["id"],
            actor_user_id=self.creator["id"],
            previous_status="open",
        )
        with self.assertRaises(PermissionError):
            db.decide_approval(
                approval["id"], self.creator["id"], action="approve"
            )

        decided = db.decide_approval(
            approval["id"], self.approver["id"], action="approve"
        )
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(db.get_task(self.task["id"])["status"], "approved")

    def test_rejection_and_change_requests_require_feedback(self):
        approval = db.create_approval(
            "Review campaign",
            business_id=self.business["id"],
            task_id=self.task["id"],
            approver_user_id=self.approver["id"],
            actor_user_id=self.creator["id"],
            previous_status="open",
        )
        with self.assertRaisesRegex(ValueError, "reason is required"):
            db.decide_approval(
                approval["id"], self.approver["id"], action="request_changes"
            )

        decided = db.decide_approval(
            approval["id"],
            self.approver["id"],
            action="request_changes",
            comment="Please attach the final export.",
        )
        self.assertEqual(decided["status"], "changes_requested")
        self.assertEqual(db.get_task(self.task["id"])["status"], "changes_requested")


if __name__ == "__main__":
    unittest.main()
