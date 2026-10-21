"""Session auth — gate the CoS UI and APIs behind login."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from typing import Any, Optional

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import db
from app.config import settings

SESSION_USER_KEY = "user_id"

# Paths anyone can hit without a session
PUBLIC_EXACT = {
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/health",
    "/api/whatsapp/webhook",
    "/api/slack/callback",
    "/favicon.ico",
}
PUBLIC_PREFIXES = (
    "/static/",
    "/public/",
)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    rounds = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
        )
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def current_user(request: Request) -> Optional[dict[str, Any]]:
    uid = request.session.get(SESSION_USER_KEY)
    user = db.get_user(uid) if uid else None
    if (
        not user or user.get("status") != "active"
    ) and _local_auth_bypass_allowed(request):
        user = db.get_user_by_username(settings.auth_username)
    if not user or user.get("status") != "active":
        return None
    return user


def _local_auth_bypass_allowed(request: Request) -> bool:
    if not settings.auth_bypass_local or settings.public_base_url:
        return False
    try:
        app_host_is_loopback = ipaddress.ip_address(settings.app_host).is_loopback
        client_is_loopback = ipaddress.ip_address(
            request.client.host if request.client else ""
        ).is_loopback
    except ValueError:
        return False
    return app_host_is_loopback and client_is_loopback


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user


def require_roles(request: Request, *roles: str) -> dict[str, Any]:
    user = require_user(request)
    role = (user.get("role") or "operator").strip()
    if role not in roles:
        raise HTTPException(403, "Forbidden — admin access required")
    return user


def require_super_admin(request: Request) -> dict[str, Any]:
    return require_roles(request, "super_admin")


def can_manage_users(user: dict[str, Any]) -> bool:
    return (user.get("role") or "") in ("super_admin", "admin")


def login_user(request: Request, user: dict[str, Any]) -> None:
    request.session.clear()
    request.session[SESSION_USER_KEY] = user["id"]


def logout_user(request: Request) -> None:
    request.session.clear()


def is_public_path(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def unauthorized_response(request: Request) -> Response:
    accept = request.headers.get("accept", "")
    wants_html = "text/html" in accept or not request.url.path.startswith("/api/")
    if wants_html and not request.url.path.startswith("/api/"):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Unauthorized — please log in"}, status_code=401)


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if is_public_path(path):
        return await call_next(request)

    user = current_user(request)
    if not user:
        return unauthorized_response(request)

    request.state.user = user
    return await call_next(request)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    role = user.get("role") or "operator"
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": role,
        "can_manage_users": role in ("super_admin", "admin"),
    }
