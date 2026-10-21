"""WhatsApp webhook processing with Meta and 360dialog providers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

from app import db
from app.config import settings

logger = logging.getLogger(__name__)


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def validate_signature(raw_body: bytes, signature: str, app_secret: str) -> bool:
    if not signature.startswith("sha256=") or not app_secret:
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _send_meta_text(recipient: str, text: str) -> str:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise RuntimeError("WhatsApp sending is not configured")
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        },
        timeout=20,
    )
    if not response.is_success:
        logger.warning("WhatsApp send failed with HTTP %s", response.status_code)
        raise RuntimeError(f"WhatsApp API returned HTTP {response.status_code}")
    try:
        messages = response.json().get("messages", [])
        return str(messages[0].get("id", "")) if messages else ""
    except (ValueError, AttributeError, TypeError, IndexError):
        return ""


def _send_360dialog_text(recipient: str, text: str) -> str:
    if not settings.d360_api_key:
        raise RuntimeError("360dialog sending is not configured")
    logger.info("[OUTBOUND] 360dialog send request started")
    response = httpx.post(
        f"{settings.d360_api_base_url.rstrip('/')}/messages",
        headers={"D360-API-KEY": settings.d360_api_key},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text[:4096]},
        },
        timeout=20,
    )
    if not response.is_success:
        logger.warning("360dialog send failed with HTTP %s", response.status_code)
        raise RuntimeError(f"360dialog API returned HTTP {response.status_code}")
    logger.info("[OUTBOUND] 360dialog send request completed")
    try:
        messages = response.json().get("messages", [])
        return str(messages[0].get("id", "")) if messages else ""
    except (ValueError, AttributeError, TypeError, IndexError):
        return ""


def _send_text(recipient: str, text: str) -> str:
    provider = settings.whatsapp_provider.strip().lower()
    if provider == "360dialog":
        return _send_360dialog_text(recipient, text)
    if provider == "meta":
        return _send_meta_text(recipient, text)
    raise RuntimeError("WhatsApp provider is not supported")


def validate_configuration() -> None:
    provider = settings.whatsapp_provider.strip().lower()
    if provider == "360dialog":
        if not settings.d360_api_key:
            raise RuntimeError("360dialog API key is not configured")
    elif provider == "meta":
        if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
            raise RuntimeError("WhatsApp Cloud API credentials are not configured")
    else:
        raise RuntimeError("WhatsApp provider is not supported")
    if not settings.whatsapp_allowed_phone or not settings.whatsapp_username:
        raise RuntimeError("WhatsApp sender and CoS user mapping are not configured")


def _configured_user() -> dict[str, Any] | None:
    if not settings.whatsapp_username:
        return None
    user = db.get_user_by_username(settings.whatsapp_username)
    if not user or user.get("status") != "active":
        return None
    return user


def _process_text_message(message: dict[str, Any]) -> bool:
    logger.info("[INBOUND] WhatsApp message received")
    sender = normalize_phone(str(message.get("from", "")))
    allowed = normalize_phone(settings.whatsapp_allowed_phone)
    user = _configured_user()
    message_id = str(message.get("id", ""))
    text_data = message.get("text")
    if not isinstance(text_data, dict):
        return False
    text = str(text_data.get("body", "")).strip()
    if not sender or not allowed or not hmac.compare_digest(sender, allowed):
        logger.warning("[USER] Phone-to-CoS mapping rejected")
        logger.warning("Rejected WhatsApp message from an unconfigured sender")
        return False
    if not user:
        logger.error("[USER] Phone-to-CoS mapping has no active user")
        logger.error("WhatsApp is not mapped to an active CoS user")
        return False
    logger.info("[USER] Phone-to-CoS mapping resolved to an active account")
    if not message_id or not text:
        return False

    history = db.whatsapp_history(str(user["id"]), sender)
    if not db.add_whatsapp_message(
        str(user["id"]), sender, "user", text, provider_message_id=message_id
    ):
        return False

    try:
        from app.agent import chat

        logger.info("[CONTEXT] Calling the shared Chief of Staff for the mapped account")
        result = chat(text, history, user_id=str(user["id"]), persist=False)
        reply = str(result.get("reply") or "I couldn't prepare a response just now.")
        logger.info("[AI] Chief of Staff request completed")
    except Exception as exc:  # noqa: BLE001
        logger.error("[AI] Chief of Staff request failed (%s)", type(exc).__name__)
        logger.error("WhatsApp CoS processing failed (%s)", type(exc).__name__)
        reply = "I couldn't process that message just now. Please try again shortly."

    try:
        outgoing_id = _send_text(sender, reply)
    except Exception as exc:  # noqa: BLE001
        db.update_whatsapp_message_status(
            message_id, "delivery_failed", type(exc).__name__
        )
        logger.error("WhatsApp reply delivery failed (%s)", type(exc).__name__)
        return False

    db.add_whatsapp_message(
        str(user["id"]),
        sender,
        "assistant",
        reply[:4096],
        provider_message_id=outgoing_id or None,
        status="sent",
    )
    db.update_whatsapp_message_status(message_id, "processed")
    return True


def process_webhook_payload(payload: dict[str, Any]) -> int:
    validate_configuration()

    processed = 0
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return processed
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            messages = value.get("messages", [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "text" and _process_text_message(message):
                    processed += 1
    return processed