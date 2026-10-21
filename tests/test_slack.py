import tempfile
import unittest
from unittest import mock

from app import db
from app.config import settings
from app.integrations import slack


class SlackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = settings.data_dir
        settings.data_dir = self.tmp.name
        db.init_db()
        with db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, username, display_name, password_hash, role, created_at, updated_at) "
                "VALUES ('u1','u1','U1','x','member','2026-01-01','2026-01-01')"
            )

    def tearDown(self):
        settings.data_dir = self._orig
        self.tmp.cleanup()

    def test_state_roundtrip_and_tamper(self):
        state = slack._make_state("u1")
        self.assertEqual(slack._read_state(state), "u1")
        with self.assertRaises(RuntimeError):
            slack._read_state(state[:-2] + "00")

    def test_token_encrypted_and_status(self):
        self.assertFalse(slack.status("u1")["connected"])
        db.save_slack_token("u1", "xoxp-secret", "U123", "T1", "Team")
        with db.connect() as conn:
            raw = conn.execute("SELECT access_token FROM slack_tokens").fetchone()[0]
        self.assertNotIn("xoxp-secret", raw)
        self.assertEqual(db.get_slack_token("u1")["access_token"], "xoxp-secret")
        self.assertTrue(slack.status("u1")["connected"])
        with mock.patch.object(slack, "_call", return_value={"ok": True}):
            slack.disconnect("u1")
        self.assertFalse(slack.status("u1")["connected"])

    def test_messages_marked_mine(self):
        db.save_slack_token("u1", "xoxp", "U123")
        fake = {"messages": [{"ts": "1", "text": "hi", "user": "U123"}]}
        with mock.patch.object(slack, "_call", return_value=fake), mock.patch.object(
            slack, "_user_name", return_value="Me"
        ):
            msgs = slack.list_messages("u1", "C1")
        self.assertTrue(msgs[0]["mine"])


if __name__ == "__main__":
    unittest.main()
