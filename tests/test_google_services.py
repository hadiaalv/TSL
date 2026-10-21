from base64 import urlsafe_b64decode
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
from urllib.parse import parse_qs, urlparse
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from app import db
from app.config import settings
from app.integrations.google_drive import verify_drive_access
from app.integrations import google_services
from app.integrations.google_services import (
    DRIVE_READ_SCOPE,
    GMAIL_DRAFT_SCOPE,
    GoogleConnectionError,
    SCOPES,
    load_credentials,
)


class GoogleCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        self.old_google_client_id = settings.google_client_id
        self.old_google_client_secret = settings.google_client_secret
        self.old_public_base_url = settings.public_base_url
        settings.data_dir = self.temp_dir.name
        settings.google_client_id = "test-client-id"
        settings.google_client_secret = "test-client-secret"
        settings.public_base_url = ""
        google_services._PENDING_OAUTH_FLOWS.clear()
        db.init_db()
        self.user = db.create_user("google-test-user", "not-used")

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        settings.google_client_id = self.old_google_client_id
        settings.google_client_secret = self.old_google_client_secret
        settings.public_base_url = self.old_public_base_url
        google_services._PENDING_OAUTH_FLOWS.clear()
        self.temp_dir.cleanup()

    def _save_token(self, **overrides):
        data = {
            "token": "stale-access-token",
            "refresh_token": "stored-refresh-token",
            "expiry": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "scopes": SCOPES,
            "client_id": "client-id",
            "client_secret": "client-secret",
        }
        data.update(overrides)
        db.save_google_token(self.user["id"], data)

    def test_expired_access_token_refreshes_and_keeps_existing_refresh_token(self):
        self._save_token()
        refreshed_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        def refresh(credentials, _request):
            credentials.token = "fresh-access-token"
            credentials._refresh_token = None
            credentials.expiry = refreshed_expiry

        with patch.object(Credentials, "refresh", autospec=True, side_effect=refresh):
            credentials = load_credentials(self.user["id"])

        row = db.get_google_token(self.user["id"])
        self.assertEqual(credentials.token, "fresh-access-token")
        self.assertEqual(row["token"], "fresh-access-token")
        self.assertEqual(row["refresh_token"], "stored-refresh-token")
        self.assertEqual(row["connection_status"], "connected")
        self.assertIsNotNone(row["expiry"])

    def test_invalid_grant_marks_connection_for_reauthentication(self):
        self._save_token()

        with patch.object(
            Credentials,
            "refresh",
            autospec=True,
            side_effect=RefreshError("invalid_grant: credentials revoked"),
        ):
            with self.assertRaisesRegex(
                GoogleConnectionError,
                "Reconnect your Google account",
            ):
                load_credentials(self.user["id"])

        row = db.get_google_token(self.user["id"])
        self.assertEqual(row["connection_status"], "reconnect_required")
        self.assertIn("revoked or expired", row["connection_error"])
        self.assertNotIn("stored-refresh-token", row["connection_error"])

    def test_expired_token_without_refresh_token_requires_reconnection(self):
        self._save_token(refresh_token=None)

        with self.assertRaisesRegex(
            GoogleConnectionError,
            "Reconnect your Google account",
        ):
            load_credentials(self.user["id"])

        self.assertEqual(
            db.get_google_token(self.user["id"])["connection_status"],
            "reconnect_required",
        )

    def test_current_credentials_are_not_refreshed(self):
        self._save_token(
            token="current-access-token",
            expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

        with patch.object(Credentials, "refresh", autospec=True) as refresh:
            credentials = load_credentials(self.user["id"])

        self.assertEqual(credentials.token, "current-access-token")
        refresh.assert_not_called()

    def test_account_switch_without_new_refresh_token_keeps_existing_connection(self):
        self._save_token(
            token="personal-access-token",
            refresh_token="personal-refresh-token",
            email="personal@example.test",
        )
        flow = MagicMock()
        flow.credentials = SimpleNamespace(
            token="business-access-token",
            refresh_token=None,
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=SCOPES,
        )
        google_services._PENDING_OAUTH_FLOWS["switch-state"] = (
            self.user["id"],
            "verifier",
            datetime.now().timestamp() + 600,
        )

        with patch.object(google_services, "get_flow", return_value=flow), patch.object(
            google_services,
            "_fetch_account_email",
            return_value="business@example.test",
        ):
            with self.assertRaisesRegex(
                GoogleConnectionError,
                "existing connection was left unchanged",
            ):
                google_services.exchange_code("oauth-code", "switch-state")

        row = db.get_google_token(self.user["id"])
        self.assertEqual(row["email"], "personal@example.test")
        self.assertEqual(row["token"], "personal-access-token")
        self.assertEqual(row["refresh_token"], "personal-refresh-token")

    def test_unverified_account_identity_does_not_replace_existing_connection(self):
        self._save_token(
            token="personal-access-token",
            refresh_token="personal-refresh-token",
            email="personal@example.test",
        )
        flow = MagicMock()
        flow.credentials = SimpleNamespace(
            token="possibly-different-access-token",
            refresh_token="possibly-different-refresh-token",
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=SCOPES,
        )
        google_services._PENDING_OAUTH_FLOWS["unknown-account-state"] = (
            self.user["id"],
            "verifier",
            datetime.now().timestamp() + 600,
        )

        with patch.object(google_services, "get_flow", return_value=flow), patch.object(
            google_services,
            "_fetch_account_email",
            return_value="",
        ):
            with self.assertRaisesRegex(
                GoogleConnectionError,
                "Could not verify the selected Google account",
            ):
                google_services.exchange_code("oauth-code", "unknown-account-state")

        row = db.get_google_token(self.user["id"])
        self.assertEqual(row["email"], "personal@example.test")
        self.assertEqual(row["token"], "personal-access-token")
        self.assertEqual(row["refresh_token"], "personal-refresh-token")

    def test_create_gmail_draft_uses_compose_scope_and_never_sends(self):
        self._save_token(
            token="current-access-token",
            expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        service = MagicMock()
        service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
            "id": "draft-id",
            "message": {"threadId": "thread-id"},
        }

        with patch(
            "googleapiclient.discovery.build",
            return_value=service,
        ) as build:
            result = google_services.create_gmail_draft(
                self.user["id"],
                to="buyer@example.test",
                subject="Product introduction",
                body_text="Hello,\nHere are the product details.",
            )

        self.assertEqual(
            result,
            {
                "status": "created",
                "draft_id": "draft-id",
                "thread_id": "thread-id",
                "to": "buyer@example.test",
                "subject": "Product introduction",
            },
        )
        build.assert_called_once_with(
            "gmail",
            "v1",
            credentials=unittest.mock.ANY,
            cache_discovery=False,
        )
        create_call = service.users.return_value.drafts.return_value.create
        create_call.assert_called_once()
        encoded = create_call.call_args.kwargs["body"]["message"]["raw"]
        raw_message = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
        self.assertEqual(parsed["To"], "buyer@example.test")
        self.assertEqual(parsed["Subject"], "Product introduction")
        self.assertIn("Here are the product details.", parsed.get_content())
        service.users.return_value.messages.return_value.send.assert_not_called()

    def test_create_gmail_draft_requires_compose_permission(self):
        read_only_scopes = [scope for scope in SCOPES if scope != GMAIL_DRAFT_SCOPE]
        self._save_token(
            token="current-access-token",
            expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            scopes=read_only_scopes,
        )

        with patch("googleapiclient.discovery.build") as build:
            with self.assertRaisesRegex(
                GoogleConnectionError,
                "Reconnect Google and approve Gmail draft access",
            ):
                google_services.create_gmail_draft(
                    self.user["id"],
                    to="buyer@example.test",
                    subject="Product introduction",
                    body_text="Hello.",
                )

        build.assert_not_called()

    def test_google_credentials_are_encrypted_at_rest_and_legacy_rows_migrate(self):
        self._save_token()

        with db.connect() as conn:
            raw_token = conn.execute(
                "SELECT token FROM google_tokens WHERE user_id = ?",
                (self.user["id"],),
            ).fetchone()[0]
        self.assertNotEqual(raw_token, "stale-access-token")
        self.assertTrue(raw_token.startswith("enc:v1:"))
        self.assertEqual(
            db.get_google_token(self.user["id"])["token"],
            "stale-access-token",
        )

        with db.connect() as conn:
            conn.execute(
                "UPDATE google_tokens SET token = ? WHERE user_id = ?",
                ("legacy-plain-token", self.user["id"]),
            )
        migrated = db.get_google_token(self.user["id"])
        with db.connect() as conn:
            raw_token = conn.execute(
                "SELECT token FROM google_tokens WHERE user_id = ?",
                (self.user["id"],),
            ).fetchone()[0]
        self.assertEqual(migrated["token"], "legacy-plain-token")
        self.assertTrue(raw_token.startswith("enc:v1:"))

    def test_legacy_token_file_is_encrypted_when_migrated_to_database(self):
        settings.google_token_path.write_text(
            json.dumps(
                {
                    "token": "legacy-file-token",
                    "refresh_token": "legacy-file-refresh-token",
                    "client_secret": "legacy-file-client-secret",
                    "expiry": (
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                    "scopes": SCOPES,
                }
            ),
            encoding="utf-8",
        )

        credentials = load_credentials(self.user["id"])

        self.assertEqual(credentials.token, "legacy-file-token")
        self.assertFalse(settings.google_token_path.exists())
        with db.connect() as conn:
            raw_token = conn.execute(
                "SELECT token FROM google_tokens WHERE user_id = ?",
                (self.user["id"],),
            ).fetchone()[0]
        self.assertTrue(raw_token.startswith("enc:v1:"))

    def test_drive_scope_is_required_before_reporting_drive_verified(self):
        self._save_token(scopes=[scope for scope in SCOPES if scope != DRIVE_READ_SCOPE])

        with patch("googleapiclient.discovery.build") as build:
            result = verify_drive_access(self.user["id"])

        self.assertEqual(result["drive_status"], "permission_missing")
        self.assertIn("read-only permission is missing", result["drive_error"])
        build.assert_not_called()

    def test_drive_status_requires_successful_api_request(self):
        self._save_token(
            token="valid-access-token",
            expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        service = MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "drive-file-id"}]
        }

        with patch(
            "googleapiclient.discovery.build",
            return_value=service,
        ) as build:
            result = verify_drive_access(self.user["id"])

        self.assertEqual(result, {"drive_status": "verified", "drive_error": ""})
        build.assert_called_once_with(
            "drive",
            "v3",
            credentials=unittest.mock.ANY,
            cache_discovery=False,
        )
        service.files.return_value.list.assert_called_once()
        self.assertEqual(
            db.get_google_token(self.user["id"])["connection_status"],
            "connected",
        )

    def test_oauth_requests_offline_scopes_and_uses_public_callback_url(self):
        settings.public_base_url = "https://cos.example.test/"

        authorization_url = google_services.auth_url(self.user["id"])
        query = parse_qs(urlparse(authorization_url).query)

        self.assertEqual(
            query["redirect_uri"][0],
            "https://cos.example.test/api/google/callback",
        )
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(
            set(query["scope"][0].split()),
            set(SCOPES),
        )


if __name__ == "__main__":
    unittest.main()
