"""Google Gmail + Calendar integration (OAuth + read signals) — per-user tokens."""

from __future__ import annotations

import json
import logging
from base64 import urlsafe_b64encode
from email.message import EmailMessage

from datetime import datetime, timedelta, timezone
from time import time
from typing import Any, Optional

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from app import db
from app.config import settings


logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GMAIL_DRAFT_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


class GoogleConnectionError(RuntimeError):
    """Google credentials need user action or could not be refreshed."""

    def __init__(self, message: str, *, requires_reconnect: bool = False) -> None:
        super().__init__(message)
        self.requires_reconnect = requires_reconnect


# state -> (user_id, code_verifier, expiry)
_PENDING_OAUTH_FLOWS: dict[str, tuple[str, str, float]] = {}


def google_configured() -> bool:
    return settings.google_ready


def get_flow(
    code_verifier: Optional[str] = None,
    state: Optional[str] = None,
):
    from google_auth_oauthlib.flow import Flow

    if not settings.google_ready:
        raise RuntimeError(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env"
        )

    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [
                settings.effective_google_redirect_uri
            ],
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        code_verifier=code_verifier,
        autogenerate_code_verifier=(code_verifier is None),
        state=state,
    )

    flow.redirect_uri = settings.effective_google_redirect_uri

    return flow


def auth_url(user_id: str) -> str:
    now = time()

    # Remove expired pending OAuth flows.
    for old_state, (_, _, expires_at) in list(
        _PENDING_OAUTH_FLOWS.items()
    ):
        if expires_at <= now:
            _PENDING_OAUTH_FLOWS.pop(old_state, None)

    flow = get_flow()

    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    verifier = flow.code_verifier

    if not state or not verifier:
        raise RuntimeError(
            "Could not create Google sign-in state and verifier."
        )

    # Store user + PKCE verifier for 10 minutes.
    _PENDING_OAUTH_FLOWS[state] = (
        str(user_id),
        verifier,
        now + 600,
    )

    return url


def exchange_code(
    code: str,
    state: str,
) -> dict[str, Any]:

    pending = _PENDING_OAUTH_FLOWS.pop(state, None)

    if pending is None:
        raise RuntimeError(
            "Google sign-in state was not found. "
            "Start a fresh attempt."
        )

    user_id, code_verifier, expires_at = pending

    if expires_at <= time():
        raise RuntimeError(
            "Google sign-in expired. Start a fresh attempt."
        )

    try:
        flow = get_flow(
            code_verifier=code_verifier,
            state=state,
        )

        flow.fetch_token(code=code)

        creds = flow.credentials

        email = _fetch_account_email(creds)
        previous = db.get_google_token(user_id)
        previous_email = str((previous or {}).get("email") or "").strip()
        if previous_email and not email:
            logger.warning(
                "Google account switch could not verify account identity user_id=%s",
                user_id,
            )
            raise GoogleConnectionError(
                "Could not verify the selected Google account. The existing "
                "connection was left unchanged. Try connecting again.",
                requires_reconnect=True,
            )
        same_account = bool(
            previous_email
            and email
            and previous_email.casefold() == email.casefold()
        )
        if not creds.refresh_token and not (
            previous
            and previous.get("refresh_token")
            and same_account
        ):
            logger.warning(
                "Google account connection omitted refresh access user_id=%s",
                user_id,
            )
            raise GoogleConnectionError(
                "Google did not provide refresh access for the selected account. "
                "The existing connection was left unchanged. Reconnect and approve "
                "offline access.",
                requires_reconnect=True,
            )

        payload = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or []),
            "email": email,
            "connection_status": "connected",
            "connection_error": "",
        }

        # Save per-user Google credentials.
        db.save_google_token(
            user_id,
            payload,
        )

        return {
            "connected": True,
            "user_id": user_id,
            "email": email,
        }

    except GoogleConnectionError:
        raise
    except Exception as exc:
        logger.warning(
            "Google token exchange failed exception_type=%s",
            type(exc).__name__,
        )

        raise RuntimeError(
            "Google token exchange failed."
        ) from exc


def create_gmail_draft(
    user_id: str,
    *,
    to: str,
    subject: str,
    body_text: str,
) -> dict[str, str]:
    """Create a Gmail draft without sending it."""

    from googleapiclient.discovery import build

    creds = load_credentials(user_id)
    if not creds:
        raise GoogleConnectionError(
            "Google not connected. Click Connect Google account first."
        )
    if GMAIL_DRAFT_SCOPE not in (creds.scopes or []):
        raise GoogleConnectionError(
            "Gmail draft permission is missing. Reconnect Google and approve "
            "Gmail draft access.",
            requires_reconnect=True,
        )

    message = EmailMessage()
    try:
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Email draft fields contain invalid header values.") from exc

    raw_message = urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )
    try:
        result = (
            service.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw_message}},
            )
            .execute()
        )
    except (HttpError, TransportError) as exc:
        logger.warning(
            "Gmail draft creation failed user_id=%s exception_type=%s",
            user_id,
            type(exc).__name__,
        )
        raise GoogleConnectionError(
            "Gmail could not create the draft. Check the Google connection and try again."
        ) from exc

    draft_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    if not draft_id:
        logger.warning("Gmail draft creation returned no draft id user_id=%s", user_id)
        raise GoogleConnectionError("Gmail did not confirm draft creation.")

    message_data = result.get("message") or {}
    return {
        "status": "created",
        "draft_id": draft_id,
        "thread_id": str(message_data.get("threadId") or ""),
        "to": to,
        "subject": subject,
    }


def _fetch_account_email(creds) -> str:
    """Resolve connected Google account email via Gmail profile."""

    try:
        from googleapiclient.discovery import build

        service = build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )

        profile = (
            service.users()
            .getProfile(userId="me")
            .execute()
        )

        return (
            profile.get("emailAddress") or ""
        ).strip()

    except Exception as exc:
        logger.warning(
            "Could not resolve connected Google account email exception_type=%s",
            type(exc).__name__,
        )
        return ""


def load_credentials(
    user_id: Optional[str] = None,
):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    data: Optional[dict[str, Any]] = None

    # Per-user token.
    if user_id:
        data = db.get_google_token(user_id)

    # Legacy single-file fallback.
    if not data and settings.google_token_path.exists():
        data = db.decrypt_google_token_data(
            json.loads(
                settings.google_token_path.read_text(encoding="utf-8")
            )
        )
        if user_id and data:
            db.save_google_token(
                user_id,
                data,
            )
            try:
                settings.google_token_path.unlink()
            except OSError:
                logger.warning(
                    "Migrated Google credentials but could not remove the legacy token file."
                )
        elif data:
            settings.google_token_path.write_text(
                json.dumps(db.encrypt_google_token_data(data)),
                encoding="utf-8",
            )

    if not data or not (data.get("token") or data.get("refresh_token")):
        return None

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        expiry=_parse_expiry(data.get("expiry")),
        token_uri=(
            data.get("token_uri")
            or "https://oauth2.googleapis.com/token"
        ),
        client_id=(
            data.get("client_id")
            or settings.google_client_id
        ),
        client_secret=(
            data.get("client_secret")
            or settings.google_client_secret
        ),
        scopes=data.get("scopes") or [],
    )

    needs_refresh = creds.expired or creds.expiry is None
    if needs_refresh:
        if not creds.refresh_token:
            message = "Google authorization expired. Reconnect your Google account."
            if user_id:
                db.set_google_connection_state(
                    user_id,
                    "reconnect_required",
                    message,
                )
            raise GoogleConnectionError(message, requires_reconnect=True)

        try:
            creds.refresh(Request())
        except RefreshError as exc:
            invalid_grant = "invalid_grant" in str(exc).casefold()
            if invalid_grant:
                message = (
                    "Google authorization was revoked or expired. "
                    "Reconnect your Google account."
                )
                if user_id:
                    db.set_google_connection_state(
                        user_id,
                        "reconnect_required",
                        message,
                    )
                logger.warning(
                    "Google credential refresh requires reconnection "
                    "user_id=%s exception_type=%s",
                    user_id or "legacy",
                    type(exc).__name__,
                )
                raise GoogleConnectionError(
                    message,
                    requires_reconnect=True,
                ) from exc
            logger.warning(
                "Google credential refresh failed user_id=%s exception_type=%s",
                user_id or "legacy",
                type(exc).__name__,
            )
            raise GoogleConnectionError(
                "Google credentials could not be refreshed. Try again; "
                "reconnect if the problem persists."
            ) from exc
        except TransportError as exc:
            logger.warning(
                "Google credential refresh unavailable user_id=%s exception_type=%s",
                user_id or "legacy",
                type(exc).__name__,
            )
            raise GoogleConnectionError(
                "Google could not be reached to refresh credentials. Try again shortly."
            ) from exc

        data.update(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
                "scopes": list(creds.scopes or data.get("scopes") or []),
                "connection_status": "connected",
                "connection_error": "",
            }
        )

        if user_id:
            db.save_google_token(user_id, data)
        else:
            settings.google_token_path.write_text(
                json.dumps(db.encrypt_google_token_data(data)),
                encoding="utf-8",
            )

    return creds


def _parse_expiry(value: Any) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def disconnect(user_id: str) -> None:
    db.delete_google_token(user_id)


def connection_status(
    user_id: Optional[str] = None,
) -> dict[str, Any]:

    row = (
        db.get_google_token(user_id)
        if user_id
        else None
    )

    credential_error = ""
    credential_requires_reconnect = False
    try:
        creds = load_credentials(user_id) if user_id else None
    except GoogleConnectionError as exc:
        creds = None
        credential_error = str(exc)
        credential_requires_reconnect = exc.requires_reconnect

    token_present = bool(row and (row.get("token") or row.get("refresh_token")))

    if not token_present and not user_id:
        token_present = (
            settings.google_token_path.exists()
        )
        creds = load_credentials(None)

    if user_id:
        row = db.get_google_token(user_id)
        token_present = bool(row and (row.get("token") or row.get("refresh_token")))

    email = (
        (row or {}).get("email")
        or ""
    )

    if creds and user_id and not email:
        email = _fetch_account_email(creds)

        if email:
            db.save_google_token(
                user_id,
                {
                    **(row or {}),
                    "email": email,
                    "token": creds.token,
                },
            )

    return {
        "oauth_configured": settings.google_ready,
        "token_present": token_present,
        "connected": creds is not None,
        "connection_status": (
            (db.get_google_token(user_id) or {}).get("connection_status")
            if user_id
            else ("connected" if creds else "not_connected")
        ),
        "connection_error": (
            credential_error
            or ((db.get_google_token(user_id) or {}).get("connection_error") or "")
            if user_id
            else ""
        ),
        "requires_reconnect": credential_requires_reconnect
        or ((row or {}).get("connection_status") == "reconnect_required"),
        "scopes": (row or {}).get("scopes") or [],
        "user_id": user_id,
        "email": email,
        "last_email_pull_at": (
            (row or {}).get(
                "last_email_pull_at"
            )
        ),
        "last_calendar_pull_at": (
            (row or {}).get(
                "last_calendar_pull_at"
            )
        ),
        "last_email_count": (
            (row or {}).get(
                "last_email_count"
            )
            or 0
        ),
        "last_calendar_count": (
            (row or {}).get(
                "last_calendar_count"
            )
            or 0
        ),
        "last_drive_pull_at": (
            (row or {}).get("last_drive_pull_at")
        ),
        "last_drive_count": (
            (row or {}).get("last_drive_count")
            or 0
        ),
    }


def fetch_recent_emails(
    user_id: str,
    max_results: int = 15,
) -> list[dict[str, Any]]:
    """Read recent inbox messages as signals."""

    from googleapiclient.discovery import build

    creds = load_credentials(user_id)

    if not creds:
        raise RuntimeError(
            "Google not connected. "
            "Click Connect Google account first."
        )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    listed = (
        service.users()
        .messages()
        .list(
            userId="me",
            maxResults=max_results,
            q="in:inbox newer_than:7d",
        )
        .execute()
    )

    out: list[dict[str, Any]] = []

    for item in listed.get("messages", []):

        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=[
                    "From",
                    "Subject",
                    "Date",
                ],
            )
            .execute()
        )

        headers = {
            h["name"]: h["value"]
            for h in msg.get(
                "payload",
                {},
            ).get(
                "headers",
                [],
            )
        }

        out.append(
            {
                "gmail_id": msg["id"],
                "thread_id": msg.get(
                    "threadId"
                ),
                "subject": headers.get(
                    "Subject",
                    "",
                ),
                "from_addr": headers.get(
                    "From",
                    "",
                ),
                "date": headers.get(
                    "Date",
                    "",
                ),
                "snippet": msg.get(
                    "snippet",
                    "",
                ),
            }
        )

    db.record_google_pull(
        user_id,
        email_count=len(out),
    )

    row = db.get_google_token(user_id) or {}

    if not row.get("email"):
        email = _fetch_account_email(creds)

        if email:
            db.save_google_token(
                user_id,
                {
                    **row,
                    "email": email,
                    "token": creds.token,
                },
            )

    return out


def fetch_upcoming_events(
    user_id: str,
    days: int = 7,
) -> list[dict[str, Any]]:

    from googleapiclient.discovery import build

    creds = load_credentials(user_id)

    if not creds:
        raise RuntimeError(
            "Google not connected. "
            "Click Connect Google account first."
        )

    service = build(
        "calendar",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )

    now = datetime.utcnow()
    end = now + timedelta(days=days)

    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=30,
        )
        .execute()
    )

    out = []

    for e in events.get("items", []):

        start = (
            e.get("start", {}).get(
                "dateTime"
            )
            or e.get("start", {}).get(
                "date"
            )
        )

        out.append(
            {
                "id": e.get("id"),
                "summary": e.get(
                    "summary",
                    "(no title)",
                ),
                "start": start,
                "htmlLink": e.get(
                    "htmlLink"
                ),
                "description": e.get("description", ""),
                "organizer": (e.get("organizer") or {}).get("displayName")
                or (e.get("organizer") or {}).get("email", ""),
                "attendees": [
                    attendee.get("displayName") or attendee.get("email", "")
                    for attendee in e.get("attendees", [])
                    if attendee.get("displayName") or attendee.get("email")
                ],
                "status": e.get("status", ""),
                "hangoutLink": e.get("hangoutLink", ""),
            }
        )

    db.record_google_pull(
        user_id,
        calendar_count=len(out),
    )

    return out


def propose_tasks_from_email(
    emails: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Heuristic proposals."""

    proposals = []

    keywords = (
        "quote",
        "proposal",
        "follow up",
        "follow-up",
        "deadline",
        "invoice",
        "meeting",
        "action",
        "urgent",
        "asap",
        "MOQ",
        "order",
        "buyer",
        "distributor",
        "retailer",
    )

    for em in emails:

        blob = (
            f"{em.get('subject', '')} "
            f"{em.get('snippet', '')}"
        ).lower()

        if any(
            k.lower() in blob
            for k in keywords
        ):
            proposals.append(
                {
                    "title": (
                        "Follow up email: "
                        f"{em.get('subject') or '(no subject)'}"
                    ),
                    "category": "follow_up",
                    "priority": "P2",
                    "source": "gmail",
                    "source_ref": em.get(
                        "gmail_id"
                    ),
                    "notes": (
                        f"From: {em.get('from_addr')} | "
                        f"{em.get('snippet', '')[:240]}"
                    ),
                    "status_hint": (
                        "proposed — confirm business "
                        "& create via CoS"
                    ),
                }
            )

    return proposals