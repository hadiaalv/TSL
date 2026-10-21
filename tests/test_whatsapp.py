import hashlib
import hmac
import json
import logging
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.agent import TOOLS, _run_tool, chat
from app.config import settings
from app.integrations import whatsapp
from app.main import app


class WhatsAppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_values = {
            name: getattr(settings, name)
            for name in (
                "data_dir",
                "whatsapp_access_token",
                "whatsapp_phone_number_id",
                "whatsapp_verify_token",
                "whatsapp_app_secret",
                "whatsapp_allowed_phone",
                "whatsapp_username",
                "whatsapp_provider",
                "d360_api_key",
                "d360_api_base_url",
                "d360_webhook_auth",
                "d360_webhook_username",
                "d360_webhook_password",
                "d360_webhook_token",
            )
        }
        settings.data_dir = self.temp_dir.name
        settings.whatsapp_access_token = "test-access-token"
        settings.whatsapp_phone_number_id = "test-phone-id"
        settings.whatsapp_verify_token = "test-verify-token"
        settings.whatsapp_app_secret = "test-app-secret"
        settings.whatsapp_allowed_phone = "+15551234567"
        settings.whatsapp_username = "owner"
        settings.whatsapp_provider = "meta"
        settings.d360_api_key = "test-d360-key"
        settings.d360_api_base_url = "https://waba-sandbox.360dialog.io/v1"
        settings.d360_webhook_auth = "token"
        settings.d360_webhook_username = ""
        settings.d360_webhook_password = ""
        settings.d360_webhook_token = "test-webhook-token"
        db.init_db()
        self.user = db.create_user("owner", "not-used")
        self.client = TestClient(app)

    def tearDown(self):
        for name, value in self.old_values.items():
            setattr(settings, name, value)
        self.temp_dir.cleanup()

    @staticmethod
    def payload(message_id="wamid.in.1", sender="15551234567", text="What is urgent?"):
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": sender,
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

    def signed_post(self, payload):
        body = json.dumps(payload).encode()
        digest = hmac.new(
            settings.whatsapp_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )

    def d360_post(self, payload, token="test-webhook-token"):
        return self.client.post(
            "/api/whatsapp/webhook",
            params={"token": token},
            json=payload,
        )

    def test_meta_verification_returns_plain_text_challenge(self):
        response = self.client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "challenge-123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "challenge-123")
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))

    def test_360dialog_webhook_get_returns_readiness_response(self):
        settings.whatsapp_provider = "360dialog"
        response = self.client.get("/api/whatsapp/webhook")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_invalid_signature_is_rejected(self):
        response = self.client.post(
            "/api/whatsapp/webhook",
            content=b"{}",
            headers={"X-Hub-Signature-256": "sha256=bad"},
        )
        self.assertEqual(response.status_code, 403)

    def test_non_object_payload_is_rejected(self):
        body = b"[]"
        digest = hmac.new(
            settings.whatsapp_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        response = self.client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )
        self.assertEqual(response.status_code, 400)

    def test_malformed_nested_payload_is_ignored(self):
        response = self.signed_post({"entry": "unexpected"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 0)

    def test_sender_binding_history_and_deduplication(self):
        agent_turns = []

        def fake_chat(message, history, user_id=None, persist=True):
            agent_turns.append((message, history, user_id, persist))
            return {"reply": f"Answer: {message}"}

        with patch("app.agent.chat", side_effect=fake_chat) as agent, patch(
            "app.integrations.whatsapp._send_text",
            side_effect=["wamid.out.1", "wamid.out.2"],
        ) as send:
            first = self.signed_post(self.payload())
            duplicate = self.signed_post(self.payload())
            second = self.signed_post(
                self.payload("wamid.in.2", text="And what about the top priority?")
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["processed"], 1)
        self.assertEqual(duplicate.json()["processed"], 0)
        self.assertEqual(second.json()["processed"], 1)
        self.assertEqual(agent.call_count, 2)
        self.assertEqual(agent_turns[0][2:], (self.user["id"], False))
        self.assertEqual(
            agent_turns[1][1],
            [
                {"role": "user", "content": "What is urgent?"},
                {"role": "assistant", "content": "Answer: What is urgent?"},
            ],
        )
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            db.whatsapp_history(self.user["id"], "15551234567"),
            [
                {"role": "user", "content": "What is urgent?"},
                {"role": "assistant", "content": "Answer: What is urgent?"},
                {"role": "user", "content": "And what about the top priority?"},
                {
                    "role": "assistant",
                    "content": "Answer: And what about the top priority?",
                },
            ],
        )

    def test_unallowlisted_sender_never_reaches_agent(self):
        with patch("app.agent.chat") as agent:
            response = self.signed_post(self.payload(sender="15550000000"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 0)
        agent.assert_not_called()

    def test_google_tools_use_the_bound_account(self):
        with patch(
            "app.integrations.google_services.fetch_recent_emails",
            return_value=[{"subject": "Actual inbox signal"}],
        ) as fetch_emails:
            result = _run_tool(
                "get_recent_gmail", {"max_results": 4}, user_id=self.user["id"]
            )
        self.assertEqual(result, [{"subject": "Actual inbox signal"}])
        fetch_emails.assert_called_once_with(self.user["id"], max_results=4)

        with patch(
            "app.integrations.google_drive.fetch_recent_drive_files",
            return_value=[{"name": "Actual business brief"}],
        ) as fetch_drive:
            drive_result = _run_tool(
                "search_google_drive",
                {"search_text": "business brief", "max_results": 3},
                user_id=self.user["id"],
            )
        self.assertEqual(drive_result, [{"name": "Actual business brief"}])
        fetch_drive.assert_called_once_with(
            self.user["id"], search_text="business brief", max_results=3
        )

    def test_gmail_draft_tool_routes_to_google_and_requires_complete_details(self):
        draft_tool = next(
            tool["function"]
            for tool in TOOLS
            if tool["function"]["name"] == "create_gmail_draft"
        )
        self.assertEqual(
            draft_tool["parameters"]["required"],
            ["to", "subject", "body_text"],
        )
        with patch(
            "app.integrations.google_services.create_gmail_draft",
            return_value={"status": "created", "draft_id": "draft-1"},
        ) as create_draft:
            result = _run_tool(
                "create_gmail_draft",
                {
                    "to": "buyer@example.test",
                    "subject": "Product introduction",
                    "body_text": "Hello.",
                },
                user_id=self.user["id"],
            )
            missing = _run_tool(
                "create_gmail_draft",
                {"to": "", "subject": "Product introduction"},
                user_id=self.user["id"],
            )

        self.assertEqual(result, {"status": "created", "draft_id": "draft-1"})
        create_draft.assert_called_once_with(
            self.user["id"],
            to="buyer@example.test",
            subject="Product introduction",
            body_text="Hello.",
        )
        self.assertIn("Missing required email draft details", missing["error"])
        self.assertIn("body_text", missing["error"])

    def test_agent_does_not_claim_a_failed_gmail_draft_was_created(self):
        tool_call = SimpleNamespace(
            id="draft-call",
            function=SimpleNamespace(
                name="create_gmail_draft",
                arguments=json.dumps(
                    {
                        "to": "buyer@example.test",
                        "subject": "Product introduction",
                        "body_text": "Hello.",
                    }
                ),
            ),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=unittest.mock.Mock(
                        side_effect=[
                            SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(
                                            content=None,
                                            tool_calls=[tool_call],
                                        )
                                    )
                                ]
                            ),
                            SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(
                                            content="I created and sent it.",
                                            tool_calls=None,
                                        )
                                    )
                                ]
                            ),
                        ]
                    )
                )
            )
        )

        with patch("app.agent._client", return_value=client), patch(
            "app.integrations.google_services.create_gmail_draft",
            side_effect=RuntimeError("Gmail draft permission is missing."),
        ) as create_draft:
            result = chat(
                "Draft an email to the buyer.",
                user_id=self.user["id"],
                persist=False,
            )

        self.assertEqual(
            result["reply"],
            "I couldn't create the Gmail draft: Gmail draft permission is missing.",
        )
        create_draft.assert_called_once_with(
            self.user["id"],
            to="buyer@example.test",
            subject="Product introduction",
            body_text="Hello.",
        )
        self.assertEqual(
            result["tools_used"][0]["result"],
            {"error": "Gmail draft permission is missing."},
        )

    def test_agent_tools_read_actual_business_and_task_records(self):
        business = db.create_business("Test portfolio business", "", "Real context")
        task = db.create_task(
            business_id=business["id"],
            title="Actual urgent task",
            priority="P0",
        )
        context = _run_tool(
            "get_business_context", {"business_id": business["id"]}
        )
        tasks = _run_tool("list_tasks", {"business_id": business["id"]})
        self.assertEqual(context["business"]["name"], "Test portfolio business")
        self.assertEqual(context["open_tasks"][0]["id"], task["id"])
        self.assertEqual(tasks[0]["title"], "Actual urgent task")

    def test_meta_send_failure_raises_without_leaking_token(self):
        class FailedResponse:
            is_success = False
            status_code = 401

        with patch("app.integrations.whatsapp.httpx.post", return_value=FailedResponse()) as post:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                whatsapp._send_text("15551234567", "test")
        self.assertNotIn(settings.whatsapp_access_token, str(raised.exception))
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            f"Bearer {settings.whatsapp_access_token}",
        )

    def test_delivery_failure_is_recorded_and_not_added_to_history(self):
        with patch("app.agent.chat", return_value={"reply": "Real AI answer"}), patch(
            "app.integrations.whatsapp._send_text", side_effect=RuntimeError("offline")
        ):
            response = self.signed_post(self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 0)
        self.assertEqual(
            db.whatsapp_history(self.user["id"], "15551234567"),
            [{"role": "user", "content": "What is urgent?"}],
        )

    def test_360dialog_webhook_processes_text_and_deduplicates_provider_id(self):
        settings.whatsapp_provider = "360dialog"
        agent_turns = []

        def fake_chat(message, history, user_id=None, persist=True):
            agent_turns.append((message, history, user_id, persist))
            return {"reply": f"Answer: {message}"}

        with patch("app.agent.chat", side_effect=fake_chat) as agent, patch(
            "app.integrations.whatsapp._send_text",
            side_effect=["wamid.out.360-1"],
        ) as send:
            first = self.d360_post(self.payload("wamid.360.in.1"))
            duplicate = self.d360_post(self.payload("wamid.360.in.1"))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"ok": True, "accepted": True})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(agent.call_count, 1)
        self.assertEqual(agent_turns[0][0], "What is urgent?")
        self.assertEqual(agent_turns[0][2:], (self.user["id"], False))
        send.assert_called_once_with("15551234567", "Answer: What is urgent?")
        self.assertEqual(
            db.whatsapp_history(self.user["id"], "15551234567"),
            [
                {"role": "user", "content": "What is urgent?"},
                {"role": "assistant", "content": "Answer: What is urgent?"},
            ],
        )

    def test_360dialog_rejects_sender_outside_existing_mapping(self):
        settings.whatsapp_provider = "360dialog"
        with patch("app.agent.chat") as agent, patch(
            "app.integrations.whatsapp._send_text"
        ) as send:
            response = self.d360_post(
                self.payload("wamid.360.in.2", sender="15550000000")
            )
        self.assertEqual(response.status_code, 200)
        agent.assert_not_called()
        send.assert_not_called()

    def test_360dialog_ignores_status_and_unsupported_events(self):
        settings.whatsapp_provider = "360dialog"
        status_payload = {
            "entry": [
                {
                    "changes": [
                        {"value": {"statuses": [{"id": "wamid.out.1", "status": "read"}]}}
                    ]
                }
            ]
        }
        image_payload = self.payload("wamid.360.image.1")
        image_payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
        with patch("app.agent.chat") as agent:
            status_response = self.d360_post(status_payload)
            image_response = self.d360_post(image_payload)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(image_response.status_code, 200)
        agent.assert_not_called()
        self.assertEqual(db.whatsapp_history(self.user["id"], "15551234567"), [])

    def test_360dialog_webhook_requires_configured_secret(self):
        settings.whatsapp_provider = "360dialog"
        response = self.d360_post(self.payload(), token="wrong-token")
        self.assertEqual(response.status_code, 403)

    def test_360dialog_url_token_is_redacted_from_access_logs(self):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            "%s %s %s",
            ("127.0.0.1", "POST", "/api/whatsapp/webhook?token=private-value"),
            None,
        )
        from app.main import _WebhookTokenLogFilter

        self.assertTrue(_WebhookTokenLogFilter().filter(record))
        self.assertNotIn("private-value", str(record.args))
        self.assertIn("[redacted]", str(record.args))

    def test_360dialog_basic_auth_matches_documented_webhook_header(self):
        settings.whatsapp_provider = "360dialog"
        settings.d360_webhook_auth = "basic"
        settings.d360_webhook_username = "webhook-user"
        settings.d360_webhook_password = "webhook-pass"
        import base64

        credentials = base64.b64encode(b"webhook-user:webhook-pass").decode("ascii")
        with patch("app.agent.chat", return_value={"reply": "ok"}), patch(
            "app.integrations.whatsapp._send_text", return_value="wamid.out.basic"
        ):
            response = self.client.post(
                "/api/whatsapp/webhook",
                json=self.payload("wamid.360.basic.1"),
                headers={"Authorization": f"Basic {credentials}"},
            )
        self.assertEqual(response.status_code, 200)

    def test_360dialog_basic_auth_accepts_case_insensitive_scheme_without_stdout(self):
        import base64
        from contextlib import redirect_stdout
        from io import StringIO

        settings.whatsapp_provider = "360dialog"
        settings.d360_webhook_auth = "basic"
        settings.d360_webhook_username = "webhook-user"
        settings.d360_webhook_password = "webhook-pass"
        credentials = base64.b64encode(b"webhook-user:webhook-pass").decode("ascii")
        output = StringIO()

        with redirect_stdout(output), patch(
            "app.agent.chat", return_value={"reply": "ok"}
        ), patch(
            "app.integrations.whatsapp._send_text", return_value="wamid.out.basic-lower"
        ):
            response = self.client.post(
                "/api/whatsapp/webhook",
                json=self.payload("wamid.360.basic.lower"),
                headers={"Authorization": f"basic {credentials}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(output.getvalue(), "")

    def test_360dialog_basic_auth_rejects_invalid_credentials_without_logging_them(self):
        settings.whatsapp_provider = "360dialog"
        settings.d360_webhook_auth = "basic"
        settings.d360_webhook_username = "webhook-user"
        settings.d360_webhook_password = "webhook-pass"

        with self.assertLogs("app.main", level="WARNING") as captured:
            response = self.client.post(
                "/api/whatsapp/webhook",
                json=self.payload("wamid.360.basic.invalid"),
                headers={"Authorization": "Basic invalid-credential-value"},
            )

        self.assertEqual(response.status_code, 403)
        logs = "\n".join(captured.output)
        self.assertNotIn("webhook-pass", logs)
        self.assertNotIn("invalid-credential-value", logs)

    def test_360dialog_send_uses_sandbox_endpoint_header_and_payload(self):
        class SuccessfulResponse:
            is_success = True

            @staticmethod
            def json():
                return {"messages": [{"id": "wamid.out.sandbox"}]}

        settings.whatsapp_provider = "360dialog"
        with patch(
            "app.integrations.whatsapp.httpx.post", return_value=SuccessfulResponse()
        ) as post:
            message_id = whatsapp._send_text("15551234567", "A short reply")

        self.assertEqual(message_id, "wamid.out.sandbox")
        self.assertEqual(
            post.call_args.args[0],
            "https://waba-sandbox.360dialog.io/v1/messages",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"], {"D360-API-KEY": "test-d360-key"}
        )
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": "15551234567",
                "type": "text",
                "text": {"preview_url": False, "body": "A short reply"},
            },
        )

    def test_360dialog_send_uses_production_endpoint(self):
        class SuccessfulResponse:
            is_success = True

            @staticmethod
            def json():
                return {"messages": [{"id": "wamid.out.production"}]}

        settings.whatsapp_provider = "360dialog"
        settings.d360_api_base_url = "https://waba-v2.360dialog.io"
        with patch(
            "app.integrations.whatsapp.httpx.post", return_value=SuccessfulResponse()
        ) as post:
            whatsapp._send_text("15551234567", "Production reply")

        self.assertEqual(
            post.call_args.args[0],
            "https://waba-v2.360dialog.io/messages",
        )

    def test_360dialog_send_errors_do_not_expose_api_key(self):
        class FailedResponse:
            is_success = False
            status_code = 401

        settings.whatsapp_provider = "360dialog"
        with patch("app.integrations.whatsapp.httpx.post", return_value=FailedResponse()):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                whatsapp._send_text("15551234567", "test")
        self.assertNotIn(settings.d360_api_key, str(raised.exception))


if __name__ == "__main__":
    unittest.main()