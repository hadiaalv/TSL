"""Per-user Slack integration (OAuth v2 user tokens + read/post messages)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app import db
from app.config import settings

API = "https://slack.com/api"
USER_SCOPES = [
    "channels:read",
    "groups:read",
    "im:read",
    "mpim:read",
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "chat:write",
    "files:read",
    "files:write",
    "reactions:read",
    "reactions:write",
    "users:read",
]
STATE_TTL_SECONDS = 600

_user_cache: dict[str, dict[str, str]] = {}


def _sign(payload: str) -> str:
    return hmac.new(
        ("slack-oauth-state:" + settings.auth_secret).encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def _make_state(user_id: str) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps({"u": user_id, "e": int(time.time()) + STATE_TTL_SECONDS}).encode()
    ).decode()
    return f"{body}.{_sign(body)}"


def _read_state(state: str) -> str:
    try:
        body, sig = state.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        if int(data["e"]) < time.time():
            raise ValueError
        return str(data["u"])
    except Exception as exc:
        raise RuntimeError("Slack sign-in state is invalid or expired. Try again.") from exc


def auth_url(user_id: str) -> str:
    if not settings.slack_ready:
        raise RuntimeError(
            "Slack is not configured. Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env."
        )
    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "user_scope": ",".join(USER_SCOPES),
            "redirect_uri": settings.effective_slack_redirect_uri,
            "state": _make_state(user_id),
        }
    )
    return f"https://slack.com/oauth/v2/authorize?{query}"


def _call(method: str, token: str, *, post: bool = False, **params: Any) -> dict[str, Any]:
    try:
        if post:
            res = httpx.post(
                f"{API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=params,
                timeout=15,
            )
        else:
            res = httpx.get(
                f"{API}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
        data = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "slack_error"))
    return data


def exchange_code(code: str, state: str) -> str:
    user_id = _read_state(state)
    try:
        res = httpx.post(
            f"{API}/oauth.v2.access",
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": settings.effective_slack_redirect_uri,
            },
            timeout=15,
        )
        data = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Slack sign-in failed: {data.get('error', 'unknown')}")
    authed = data.get("authed_user") or {}
    token = authed.get("access_token")
    if not token:
        raise RuntimeError("Slack did not return a user token.")
    db.save_slack_token(
        user_id,
        token,
        authed.get("id", ""),
        (data.get("team") or {}).get("id", ""),
        (data.get("team") or {}).get("name", ""),
        authed.get("scope", ""),
    )
    return user_id


def _token(user_id: str) -> dict[str, Any]:
    row = db.get_slack_token(user_id)
    if not row:
        raise RuntimeError("not_connected")
    return row


def status(user_id: str) -> dict[str, Any]:
    row = db.get_slack_token(user_id)
    return {
        "configured": settings.slack_ready,
        "connected": bool(row),
        "team_name": (row or {}).get("team_name", ""),
        "slack_user_id": (row or {}).get("slack_user_id", ""),
    }


def disconnect(user_id: str) -> None:
    row = db.get_slack_token(user_id)
    if row:
        try:
            _call("auth.revoke", row["access_token"], post=True)
        except RuntimeError:
            pass
    db.delete_slack_token(user_id)


_users_loaded_at: dict[str, float] = {}
USERS_TTL_SECONDS = 600


def _display(info: dict[str, Any]) -> str:
    profile = info.get("profile", {}) or {}
    return (
        profile.get("display_name")
        or profile.get("real_name")
        or info.get("real_name")
        or info.get("name")
        or info.get("id", "")
    )


def _store_user(info: dict[str, Any]) -> None:
    profile = info.get("profile", {}) or {}
    _user_cache[info["id"]] = {
        "name": _display(info),
        "avatar": profile.get("image_48") or profile.get("image_72") or "",
    }


def _load_users(token: str, user_id: str) -> None:
    if time.time() - _users_loaded_at.get(user_id, 0) < USERS_TTL_SECONDS:
        return
    cursor = ""
    try:
        for _ in range(15):
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = _call("users.list", token, **params)
            for member in data.get("members", []):
                _store_user(member)
            cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break
    except RuntimeError:
        return
    _users_loaded_at[user_id] = time.time()


def _user_info(token: str, slack_id: str) -> dict[str, str]:
    if not slack_id:
        return {"name": "Unknown", "avatar": ""}
    cached = _user_cache.get(slack_id)
    if cached:
        return cached
    try:
        info = _call("users.info", token, user=slack_id)["user"]
        _store_user(info)
        return _user_cache[slack_id]
    except (RuntimeError, KeyError):
        return {"name": slack_id, "avatar": ""}


def _user_name(token: str, slack_id: str) -> str:
    return _user_info(token, slack_id)["name"]


def list_conversations(user_id: str) -> list[dict[str, Any]]:
    row = _token(user_id)
    token = row["access_token"]
    _load_users(token, user_id)
    channels: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(20):
        params: dict[str, Any] = {
            "types": "public_channel,private_channel,mpim,im",
            "exclude_archived": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = _call("users.conversations", token, **params)
        channels.extend(data.get("channels", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break
    items = []
    for conv in channels:
        if conv.get("is_im"):
            info = _user_info(token, conv.get("user", ""))
            kind, name = "dm", info["name"]
        elif conv.get("is_mpim"):
            kind = "group"
            name = ", ".join(
                n for n in conv.get("name", "group").replace("mpdm-", "").rsplit("-", 1)[0].split("--")
            )
        else:
            kind = "private" if conv.get("is_private") else "channel"
            name = "#" + conv.get("name", "")
        items.append({"id": conv["id"], "name": name, "kind": kind})
    order = {"channel": 0, "private": 1, "group": 2, "dm": 3}
    return sorted(items, key=lambda c: (order[c["kind"]], c["name"].lower()))


_MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_read_marks: dict[tuple[str, str], str] = {}
_baseline: dict[str, str] = {}
_unread_cache: dict[str, tuple[float, dict[str, Any]]] = {}
UNREAD_TTL_SECONDS = 12
UNREAD_MAX_CONVERSATIONS = 40


def _resolve_mentions(token: str, text: str) -> str:
    return _MENTION.sub(lambda m: "@" + _user_name(token, m.group(1)), text)


def mark_read(user_id: str, channel: str, ts: str) -> None:
    key = (user_id, channel)
    if ts and ts > _read_marks.get(key, ""):
        _read_marks[key] = ts
    _unread_cache.pop(user_id, None)


def unread(user_id: str) -> dict[str, Any]:
    """Incoming messages from others since each conversation was last opened.

    Conversations start "read" at the moment the server first checks for the
    user, so only messages that arrive after connecting are counted.
    """
    now = time.time()
    cached = _unread_cache.get(user_id)
    if cached and now - cached[0] < UNREAD_TTL_SECONDS:
        return cached[1]
    row = _token(user_id)
    token = row["access_token"]
    baseline = _baseline.setdefault(user_id, f"{now:.6f}")
    try:
        data = _call(
            "users.conversations",
            token,
            types="public_channel,private_channel,mpim,im",
            exclude_archived="true",
            limit=UNREAD_MAX_CONVERSATIONS,
        )
    except RuntimeError:
        return cached[1] if cached else {}
    result: dict[str, Any] = {}
    for conv in data.get("channels", [])[:UNREAD_MAX_CONVERSATIONS]:
        cid = conv["id"]
        oldest = _read_marks.get((user_id, cid), baseline)
        try:
            hist = _call("conversations.history", token, channel=cid, oldest=oldest, limit=15)
        except RuntimeError as exc:
            if str(exc) == "ratelimited":
                break
            continue
        incoming = [
            m
            for m in hist.get("messages", [])
            if m.get("user") != row["slack_user_id"]
            and m.get("subtype") not in {"channel_join", "channel_leave"}
        ]
        if not incoming:
            continue
        latest = incoming[0]
        sender = latest.get("user", "")
        files = latest.get("files") or []
        text = _resolve_mentions(token, latest.get("text", "")) or (
            "📎 " + ", ".join(f.get("name", "file") for f in files) if files else ""
        )
        result[cid] = {
            "count": len(incoming),
            "ts": latest.get("ts"),
            "user": _user_name(token, sender) if sender else latest.get("username", "Bot"),
            "text": text[:200],
            "kind": "dm" if conv.get("is_im") else "group" if conv.get("is_mpim") else "channel",
            "name": conv.get("name", ""),
        }
    _unread_cache[user_id] = (now, result)
    return result


def _files(msg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in msg.get("files", []) or []:
        url = f.get("url_private") or ""
        if not url:
            continue
        thumb = f.get("thumb_720") or f.get("thumb_480") or f.get("thumb_360") or ""
        out.append(
            {
                "name": f.get("name") or f.get("title") or "file",
                "mimetype": f.get("mimetype", ""),
                "size": f.get("size", 0),
                "url": url,
                "thumb": thumb,
                "permalink": f.get("permalink", ""),
            }
        )
    return out


def fetch_file(user_id: str, url: str) -> httpx.Response:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "slack.com" or host.endswith(".slack.com")):
        raise RuntimeError("Invalid file URL.")
    token = _token(user_id)["access_token"]
    try:
        res = httpx.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc
    if res.status_code != 200:
        raise RuntimeError("Could not download file from Slack.")
    return res


def _shape(token: str, row: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any]:
    sender = msg.get("user", "")
    if sender:
        info = _user_info(token, sender)
    else:
        bot = msg.get("bot_profile") or {}
        info = {
            "name": msg.get("username") or bot.get("name") or "App",
            "avatar": (bot.get("icons") or {}).get("image_48", ""),
        }
    text = _resolve_mentions(token, msg.get("text", ""))
    if not text and msg.get("attachments"):
        text = "\n".join(
            a.get("fallback") or a.get("text") or a.get("title") or "" for a in msg["attachments"]
        ).strip()
    return {
        "ts": msg.get("ts"),
        "text": text,
        "user": info["name"],
        "avatar": info["avatar"],
        "mine": sender == row["slack_user_id"],
        "files": _files(msg),
        "thread_ts": msg.get("thread_ts") if msg.get("thread_ts") != msg.get("ts") else None,
        "reply_count": msg.get("reply_count", 0),
        "edited": bool(msg.get("edited")),
        "reactions": [
            {
                "name": r.get("name", ""),
                "count": r.get("count", 0),
                "me": row["slack_user_id"] in (r.get("users") or []),
            }
            for r in msg.get("reactions", []) or []
        ],
    }


_HIDDEN_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave"}


def list_messages(user_id: str, channel: str, limit: int = 100) -> list[dict[str, Any]]:
    row = _token(user_id)
    token = row["access_token"]
    _load_users(token, user_id)
    data = _call("conversations.history", token, channel=channel, limit=limit)
    messages = [
        _shape(token, row, msg)
        for msg in reversed(data.get("messages", []))
        if msg.get("subtype") not in _HIDDEN_SUBTYPES
    ]
    if messages:
        mark_read(user_id, channel, messages[-1]["ts"] or "")
    return messages


def list_replies(user_id: str, channel: str, thread_ts: str) -> list[dict[str, Any]]:
    row = _token(user_id)
    token = row["access_token"]
    data = _call("conversations.replies", token, channel=channel, ts=thread_ts, limit=200)
    return [_shape(token, row, msg) for msg in data.get("messages", [])]


def post_message(
    user_id: str, channel: str, text: str, thread_ts: str | None = None
) -> dict[str, Any]:
    token = _token(user_id)["access_token"]
    params: dict[str, Any] = {"channel": channel, "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts
    data = _call("chat.postMessage", token, post=True, **params)
    return {"ts": data.get("ts")}


def set_reaction(user_id: str, channel: str, ts: str, name: str, remove: bool) -> None:
    token = _token(user_id)["access_token"]
    try:
        _call("reactions.remove" if remove else "reactions.add", token, post=True, channel=channel, timestamp=ts, name=name)
    except RuntimeError as exc:
        if str(exc) not in {"already_reacted", "no_reaction"}:
            raise


def upload_file(
    user_id: str,
    channel: str,
    filename: str,
    content: bytes,
    comment: str = "",
    thread_ts: str | None = None,
) -> dict[str, Any]:
    token = _token(user_id)["access_token"]
    slot = _call("files.getUploadURLExternal", token, filename=filename, length=len(content))
    try:
        res = httpx.post(slot["upload_url"], content=content, timeout=120)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc
    if res.status_code != 200:
        raise RuntimeError("Slack rejected the file upload.")
    params: dict[str, Any] = {
        "files": [{"id": slot["file_id"], "title": filename}],
        "channel_id": channel,
    }
    if comment:
        params["initial_comment"] = comment
    if thread_ts:
        params["thread_ts"] = thread_ts
    _call("files.completeUploadExternal", token, post=True, **params)
    return {"ok": True}
