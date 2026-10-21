import tempfile
import unittest

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app


class SessionAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        self.user = db.create_user(
            "session-test-user",
            hash_password("test-password"),
        )
        self.client = TestClient(app)

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def test_valid_session_persists_until_expiry_or_explicit_logout(self):
        login = self.client.post(
            "/api/auth/login",
            json={"username": "session-test-user", "password": "test-password"},
        )

        self.assertEqual(login.status_code, 200)
        max_age = max(3600, settings.auth_session_hours * 3600)
        self.assertIn(f"Max-Age={max_age}", login.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

        logout = self.client.post("/api/auth/logout", json={})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_local_auth_bypass_allows_loopback_requests_without_a_session(self):
        old_bypass = settings.auth_bypass_local
        old_username = settings.auth_username
        old_app_host = settings.app_host
        old_public_base_url = settings.public_base_url
        try:
            settings.auth_bypass_local = True
            settings.auth_username = "session-test-user"
            settings.app_host = "127.0.0.1"
            settings.public_base_url = ""
            client = TestClient(app, client=("127.0.0.1", 50000))

            response = client.get("/api/auth/me")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["username"], "session-test-user")
            self.assertEqual(client.get("/login", follow_redirects=False).status_code, 303)
        finally:
            settings.auth_bypass_local = old_bypass
            settings.auth_username = old_username
            settings.app_host = old_app_host
            settings.public_base_url = old_public_base_url

    def test_local_auth_bypass_rejects_non_loopback_requests(self):
        old_bypass = settings.auth_bypass_local
        old_username = settings.auth_username
        old_app_host = settings.app_host
        old_public_base_url = settings.public_base_url
        try:
            settings.auth_bypass_local = True
            settings.auth_username = "session-test-user"
            settings.app_host = "127.0.0.1"
            settings.public_base_url = ""
            client = TestClient(app, client=("192.0.2.1", 50000))

            response = client.get("/api/auth/me")

            self.assertEqual(response.status_code, 401)
        finally:
            settings.auth_bypass_local = old_bypass
            settings.auth_username = old_username
            settings.app_host = old_app_host
            settings.public_base_url = old_public_base_url


if __name__ == "__main__":
    unittest.main()
