"""FastAPI app — AI Chief of Staff MVP."""

from __future__ import annotations

import hmac
import base64
import logging
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app import attendance, auth, db
from app.brand_import import merge_drive_context, normalize_import_items
from app.config import settings


STATIC = Path(__file__).parent / "static"
BRAND_UPLOADS = Path(settings.data_dir) / "brand_logos"
BRAND_UPLOADS.mkdir(parents=True, exist_ok=True)
MAX_BRAND_LOGO_BYTES = 4 * 1024 * 1024


class _WebhookTokenLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            args = list(record.args)
            if len(args) > 2 and isinstance(args[2], str):
                args[2] = re.sub(
                    r"([?&]token=)[^& ]+", r"\1[redacted]", args[2]
                )
                record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(_WebhookTokenLogFilter())

app = FastAPI(
    title="The Social Links",
    version="0.1.0",
)


def _google():
    from app.integrations import google_services as google

    return google


def _cos_chat(
    message: str,
    history: list[dict[str, str]],
    user_id: Optional[str] = None,
    persist: bool = True,
):
    from app.agent import chat as cos_chat

    return cos_chat(message, history, user_id=user_id, persist=persist)


class AuthGateMiddleware:
    """Pure ASGI gate — avoids BaseHTTPMiddleware recursion under a2wsgi/Passenger."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path

        if auth.is_public_path(path):
            await self.app(scope, receive, send)
            return

        user = auth.current_user(request)

        if not user:
            response = auth.unauthorized_response(request)
            await response(scope, receive, send)
            return

        request.state.user = user
        await self.app(scope, receive, send)


# Order: last added runs first. Session must wrap Auth so request.session exists.
app.add_middleware(AuthGateMiddleware)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.auth_secret,
    session_cookie="cos_session",
    max_age=max(
        3600,
        settings.auth_session_hours * 3600,
    ),
    same_site="lax",
    https_only=settings.auth_cookie_secure,
)


@app.on_event("startup")
def startup() -> None:
    db.init_db()

    if settings.auth_username and settings.auth_password:
        created = db.ensure_bootstrap_admin(
            settings.auth_username,
            settings.auth_password,
            auth.hash_password,
        )

        if created:
            print(
                f"[auth] Bootstrap super_admin created: "
                f"{created['username']}"
            )

    elif db.count_users() == 0:
        print(
            "[auth] WARNING: No users in DB and AUTH_PASSWORD is empty. "
            "Set AUTH_USERNAME / AUTH_PASSWORD in .env and restart."
        )

    # Ensure legacy users have a role
    for u in db.list_users(include_inactive=True):
        if not u.get("role"):
            db.update_user(
                u["id"],
                role="super_admin",
            )
            print(
                f"[auth] Promoted legacy user to super_admin: "
                f"{u['username']}"
            )

    if settings.seed_demo:
        db.seed_demo_data()

    db.seed_ncc_brand_portfolio()


# ── Schemas ─────────────────────────────────────────────────


class ChatIn(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(
        default_factory=list
    )


class BusinessIn(BaseModel):
    name: str
    description: str = ""
    context_md: str = ""
    slug: Optional[str] = None
    status: str = "active"


class BusinessPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)


class BrandStarIn(BaseModel):
    starred: bool


class AttendanceHeartbeatIn(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=16, max_length=64)


class AttendancePolicyIn(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)
    heartbeat_interval_seconds: int = Field(ge=15, le=300)
    inactivity_timeout_seconds: int = Field(ge=16, le=900)
    work_start: str = Field(pattern=r"^\d{2}:\d{2}$")
    work_end: str = Field(pattern=r"^\d{2}:\d{2}$")
    late_grace_minutes: int = Field(ge=0, le=240)
    overtime_enabled: bool
    overtime_grace_minutes: int = Field(ge=0, le=480)
    working_days: list[int] = Field(min_length=1, max_length=7)
    allowed_half_leaves: int = Field(default=1, ge=0, le=31)
    allowed_days_off: int = Field(default=2, ge=0, le=31)
    allowed_late_checkins: int = Field(default=3, ge=0, le=31)
    allowed_early_offs: int = Field(default=3, ge=0, le=31)


class AttendanceLeaveIn(BaseModel):
    user_id: str = Field(min_length=1)
    leave_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    leave_type: str = Field(pattern=r"^(half|full)$")
    reason: str = Field(default="", max_length=500)


class AttendanceCorrectionIn(BaseModel):
    delta_seconds: int = Field(ge=-86400, le=86400)
    reason: str = Field(min_length=1, max_length=500)


class AttendanceResolutionIn(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=500)


BRAND_LINK_KINDS = {"website", "instagram", "facebook", "tiktok", "youtube", "linkedin", "x", "shop", "drive", "other"}


class BrandLinkIn(BaseModel):
    kind: str = "website"
    label: str = Field(default="", max_length=80)
    url: str = Field(min_length=3, max_length=500)


class BrandSopIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=20000)
    url: str = Field(default="", max_length=500)


class BrandSopPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    content: Optional[str] = Field(default=None, max_length=20000)
    url: Optional[str] = Field(default=None, max_length=500)


class WorkspaceEventIn(BaseModel):
    business_id: str
    title: str = Field(min_length=1, max_length=180)
    event_type: str = Field(default="event", max_length=40)
    starts_at: datetime
    notes: str = Field(default="", max_length=2000)


class BrandImportIn(BaseModel):
    """Bulk-create businesses from structured items or a multi-brand markdown pack."""

    items: Optional[list[dict[str, Any]]] = None
    markdown: Optional[str] = None
    skip_existing_names: bool = True


class TaskIn(BaseModel):
    business_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=300)
    approver_name: str = Field(default="", max_length=160)
    category: str = "other"
    owner: str = "me"
    priority: str = "P2"
    deadline: Optional[str] = None
    status: str = "open"
    next_follow_up: Optional[str] = None
    waiting_on: str = ""
    notes: str = ""
    description: Optional[str] = None
    source: str = "manual"
    department_id: Optional[str] = None
    assignee_ids: list[str] = Field(default_factory=list)
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)


class TaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    category: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[str] = None
    next_follow_up: Optional[str] = None
    waiting_on: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[str] = None
    assignee_ids: Optional[list[str]] = None
    checklist: Optional[list[dict[str, Any]]] = None
    links: Optional[list[dict[str, Any]]] = None


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="", max_length=8)


class DepartmentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = None
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    icon: Optional[str] = Field(default=None, max_length=8)
    sort_order: Optional[int] = None
    status: Optional[str] = None


class DepartmentMemberIn(BaseModel):
    user_id: str


class TaskCommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class NotificationSoundIn(BaseModel):
    enabled: bool


class ApprovalDecision(BaseModel):
    approved: bool


class LoginIn(BaseModel):
    username: str
    password: str


class AdminUserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "operator"


class AdminUserPatch(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    password: Optional[str] = None


# ── Auth ────────────────────────────────────────────────────


@app.get("/login")
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse(
            "/",
            status_code=303,
        )

    return FileResponse(
        STATIC / "login.html"
    )


@app.post("/api/auth/login")
def api_login(
    request: Request,
    body: LoginIn,
) -> dict[str, Any]:
    user = db.get_user_by_username(
        body.username
    )

    if not user or user.get("status") != "active":
        raise HTTPException(
            401,
            "Invalid username or password",
        )

    if not auth.verify_password(
        body.password,
        user["password_hash"],
    ):
        raise HTTPException(
            401,
            "Invalid username or password",
        )

    # Backfill role for older rows
    if not user.get("role"):
        user = (
            db.update_user(
                user["id"],
                role="super_admin",
            )
            or user
        )

    auth.login_user(
        request,
        user,
    )

    return {
        "ok": True,
        "user": auth.public_user(user),
    }


@app.post("/api/auth/logout")
def api_logout(
    request: Request,
) -> dict[str, Any]:
    auth.logout_user(request)

    return {
        "ok": True
    }


@app.get("/api/auth/me")
def api_me(
    request: Request,
) -> dict[str, Any]:
    user = auth.require_user(request)

    return {
        "user": {
            **auth.public_user(user),
            "department_ids": db.user_department_ids(str(user["id"])),
        }
    }


# ── Admin (Phase 2) ─────────────────────────────────────────


@app.get("/admin")
def admin_page(request: Request):
    user = auth.require_user(request)

    if not auth.can_manage_users(user):
        raise HTTPException(
            403,
            "Admin access required",
        )

    return FileResponse(
        STATIC / "admin.html"
    )


def _admin_user_row(
    u: dict[str, Any],
) -> dict[str, Any]:
    tok = db.get_google_token(u["id"]) or {}

    return {
        **auth.public_user(u),
        "department_ids": db.user_department_ids(u["id"]),
        "status": u.get("status"),
        "created_at": u.get("created_at"),
        "google_connected": bool(
            tok.get("token")
        ),
        "google_email": tok.get("email") or "",
        "last_email_pull_at": tok.get(
            "last_email_pull_at"
        ),
        "last_calendar_pull_at": tok.get(
            "last_calendar_pull_at"
        ),
        "last_email_count": tok.get(
            "last_email_count"
        )
        or 0,
        "last_calendar_count": tok.get(
            "last_calendar_count"
        )
        or 0,
    }


@app.get("/api/admin/overview")
def admin_overview(
    request: Request,
) -> dict[str, Any]:
    """Portfolio snapshot for super admin tracking."""

    auth.require_super_admin(request)

    users = [
        _admin_user_row(u)
        for u in db.list_users(
            include_inactive=True
        )
    ]

    brief = db.attention_brief()
    businesses = db.list_businesses(
        active_only=False
    )
    tasks = db.list_tasks(
        open_only=True,
        limit=80,
    )
    approvals = db.list_approvals(
        "pending"
    )

    google_on = sum(
        1
        for u in users
        if u["google_connected"]
    )

    by_role: dict[str, int] = {}

    for u in users:
        r = u.get("role") or "operator"
        by_role[r] = (
            by_role.get(r, 0) + 1
        )

    return {
        "stats": {
            "users_total": len(users),
            "users_active": sum(
                1
                for u in users
                if u.get("status") == "active"
            ),
            "google_connected": google_on,
            "businesses": len(businesses),
            **brief.get("summary", {}),
            "by_role": by_role,
            "by_business": brief.get(
                "by_business",
                {},
            ),
        },
        "users": users,
        "businesses": businesses,
        "tasks": tasks,
        "approvals": approvals,
        "attention": {
            "overdue": brief.get(
                "overdue",
                [],
            )[:15],
            "due_today": brief.get(
                "due_today",
                [],
            )[:15],
            "followups_due": brief.get(
                "followups_due",
                [],
            )[:15],
            "waiting_on": brief.get(
                "waiting_on",
                [],
            )[:15],
            "decisions": brief.get(
                "decisions",
                [],
            )[:15],
        },
        "as_of": brief.get("as_of"),
    }


@app.get("/api/admin/users")
def admin_list_users(
    request: Request,
) -> dict[str, Any]:
    auth.require_super_admin(request)

    rows = db.list_users(
        include_inactive=True
    )

    return {
        "users": [
            _admin_user_row(u)
            for u in rows
        ]
    }


@app.post("/api/admin/users")
def admin_create_user(
    request: Request,
    body: AdminUserCreate,
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)

    role = body.role or "operator"
    if role not in db.USER_ROLES:
        raise HTTPException(400, "Invalid role")

    if db.get_user_by_username(
        body.username
    ):
        raise HTTPException(
            400,
            "Username already exists",
        )

    if len(body.password) < 8:
        raise HTTPException(
            400,
            "Password must be at least 8 characters",
        )

    user = db.create_user(
        username=body.username,
        password_hash=auth.hash_password(
            body.password
        ),
        display_name=(
            body.display_name
            or body.username
        ),
        role=role,
    )
    db.notify_super_admins(
        "admin",
        f"User created: {user['username']}",
        "A workspace user account was created.",
        str(actor["id"]),
        target_url="/admin",
    )

    return {
        "user": auth.public_user(user)
    }


@app.patch("/api/admin/users/{user_id}")
def admin_patch_user(
    request: Request,
    user_id: str,
    body: AdminUserPatch,
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)

    target = db.get_user(user_id)

    if not target:
        raise HTTPException(
            404,
            "User not found",
        )

    fields: dict[str, Any] = {}

    if body.display_name is not None:
        fields["display_name"] = (
            body.display_name
        )

    if body.status is not None:
        if body.status not in (
            "active",
            "disabled",
        ):
            raise HTTPException(
                400,
                "status must be active or disabled",
            )

        if (
            target["id"] == actor["id"]
            and body.status == "disabled"
        ):
            raise HTTPException(
                400,
                "Cannot disable your own account",
            )

        fields["status"] = body.status

    if body.role is not None:
        if body.role not in db.USER_ROLES:
            raise HTTPException(
                400,
                "Invalid role",
            )

        if (
            target["id"] == actor["id"]
            and body.role != target.get("role")
        ):
            raise HTTPException(
                400,
                "Cannot change your own role",
            )

        fields["role"] = body.role

    if (
        target.get("role") == "super_admin"
        and target.get("status") == "active"
        and (
            fields.get("role", "super_admin") != "super_admin"
            or fields.get("status", "active") != "active"
        )
        and sum(
            1
            for u in db.list_users(include_inactive=False)
        )
        <= 1
    ):
        raise HTTPException(
            400,
            "At least one active super admin is required",
        )

    if body.password:
        if len(body.password) < 8:
            raise HTTPException(
                400,
                "Password must be at least 8 characters",
            )

        fields["password_hash"] = (
            auth.hash_password(
                body.password
            )
        )

    updated = db.update_user(
        user_id,
        **fields,
    )
    if fields:
        db.notify_super_admins(
            "admin",
            f"User updated: {updated['username']}",
            "A workspace user account was updated.",
            str(actor["id"]),
            target_url="/admin",
        )

    return {
        "user": {
            **auth.public_user(updated),
            "status": updated.get("status"),
        }
    }  # type: ignore


# ── Core API ────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ai_configured": bool(
            settings.gemini_api_key
            and not settings.gemini_api_key.startswith(
                "your_"
            )
        ),
        "google": _google().connection_status(),
        "model": settings.gemini_model,
        "auth_users": db.count_users(),
    }


@app.get("/api/brief")
def brief(request: Request) -> dict[str, Any]:
    return db.attention_brief(viewer=auth.require_user(request))


def _visible_business_ids(_user: dict[str, Any]) -> set[str]:
    return {str(row["id"]) for row in db.list_businesses()}


def _require_business_access(user: dict[str, Any], business_id: str) -> dict[str, Any]:
    business = db.get_business(business_id)
    if not business:
        raise HTTPException(404, "Brand not found")
    if auth.is_super_admin(user) or user.get("role") == "admin":
        return business
    if business_id not in _visible_business_ids(user):
        raise HTTPException(404, "Brand not found")
    return business


@app.get("/api/businesses")
def businesses(request: Request) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    visible_businesses = _visible_business_ids(user)
    preferences = {
        item["business_id"]: item for item in db.list_brand_preferences(str(user["id"]))
    }
    return [
        {
            **business,
            "is_starred": bool(preferences.get(business["id"], {}).get("starred")),
            "last_visited_at": preferences.get(business["id"], {}).get("last_visited_at"),
        }
        for business in db.list_businesses()
        if business["id"] in visible_businesses
    ]


@app.post("/api/businesses")
def create_business(
    request: Request,
    body: BusinessIn,
) -> dict[str, Any]:
    auth.require_super_admin(request)
    return db.create_business(
        name=body.name,
        description=body.description,
        context_md=body.context_md,
        slug=body.slug,
        status=body.status,
    )


def _remove_stored_brand_logo(logo_url: Optional[str]) -> None:
    if not logo_url or not logo_url.startswith("/uploads/brands/"):
        return
    filename = Path(logo_url).name
    if filename != logo_url.removeprefix("/uploads/brands/"):
        return
    stored = BRAND_UPLOADS / filename
    if stored.is_file():
        stored.unlink()


def _validated_logo(data: bytes, content_type: str) -> tuple[bytes, str]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    extensions = {
        "image/png": ("png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
        "image/jpeg": ("jpg", lambda value: value.startswith(b"\xff\xd8\xff")),
        "image/webp": (
            "webp",
            lambda value: len(value) >= 12 and value[:4] == b"RIFF" and value[8:12] == b"WEBP",
        ),
    }
    if media_type in extensions:
        extension, valid = extensions[media_type]
        if not valid(data):
            raise HTTPException(400, "The uploaded file is not a valid image.")
        return data, extension

    if media_type != "image/svg+xml":
        raise HTTPException(415, "Upload a PNG, JPEG, WebP, or SVG image.")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise HTTPException(400, "SVG files with document types or entities are not supported.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise HTTPException(400, "The uploaded SVG could not be parsed.") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise HTTPException(400, "The uploaded file is not an SVG image.")

    allowed_tags = {
        "svg", "g", "path", "rect", "circle", "ellipse", "line",
        "polyline", "polygon", "defs", "linearGradient", "radialGradient",
        "stop", "clipPath", "mask", "title", "desc",
    }
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] not in allowed_tags:
                parent.remove(child)
        for attribute in list(parent.attrib):
            local_attribute = attribute.rsplit("}", 1)[-1].lower()
            value = parent.attrib[attribute].strip()
            if local_attribute.startswith("on"):
                del parent.attrib[attribute]
            elif local_attribute in {"href", "src"} and not value.startswith("#"):
                del parent.attrib[attribute]
            elif local_attribute == "style" and (
                "@import" in value.lower()
                or re.search(r"url\((?!\s*#)", value, flags=re.IGNORECASE)
            ):
                del parent.attrib[attribute]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), "svg"


@app.post("/api/businesses/{business_id}/logo")
async def upload_business_logo(
    request: Request,
    business_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    auth.require_super_admin(request)
    business = db.get_business(business_id)
    if not business:
        raise HTTPException(404, "Brand not found")
    data = await file.read(MAX_BRAND_LOGO_BYTES + 1)
    if not data:
        raise HTTPException(400, "Choose an image to upload.")
    if len(data) > MAX_BRAND_LOGO_BYTES:
        raise HTTPException(413, "Logo files must be 4 MB or smaller.")
    safe_data, extension = _validated_logo(data, file.content_type or "")
    filename = f"{business_id}-{uuid.uuid4().hex}.{extension}"
    BRAND_UPLOADS.mkdir(parents=True, exist_ok=True)
    (BRAND_UPLOADS / filename).write_bytes(safe_data)
    updated = db.update_business(
        business_id,
        logo_url=f"/uploads/brands/{filename}",
    )
    if not updated:
        (BRAND_UPLOADS / filename).unlink(missing_ok=True)
        raise HTTPException(404, "Brand not found")
    _remove_stored_brand_logo(business.get("logo_url"))
    return updated


@app.delete("/api/businesses/{business_id}/logo")
def remove_business_logo(request: Request, business_id: str) -> dict[str, Any]:
    auth.require_super_admin(request)
    business = db.get_business(business_id)
    if not business:
        raise HTTPException(404, "Brand not found")
    updated = db.update_business(business_id, logo_url="")
    _remove_stored_brand_logo(business.get("logo_url"))
    return updated or {}


@app.put("/api/businesses/{business_id}/star")
def set_business_star(
    request: Request,
    business_id: str,
    body: BrandStarIn,
) -> dict[str, bool]:
    user = auth.require_user(request)
    if not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    db.set_brand_starred(str(user["id"]), business_id, body.starred)
    return {"starred": body.starred}


@app.get("/api/businesses/{business_id}/links")
def list_brand_links(request: Request, business_id: str) -> list[dict[str, Any]]:
    _require_business_access(auth.require_user(request), business_id)
    return db.list_brand_links(business_id)


@app.post("/api/businesses/{business_id}/links")
def add_brand_link(request: Request, business_id: str, body: BrandLinkIn) -> dict[str, Any]:
    auth.require_super_admin(request)
    if not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    url = body.url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url, re.I):
        raise HTTPException(422, "Enter a valid link")
    kind = body.kind if body.kind in BRAND_LINK_KINDS else "other"
    return db.add_brand_link(business_id, kind, body.label.strip(), url)


@app.delete("/api/businesses/{business_id}/links/{link_id}")
def delete_brand_link(request: Request, business_id: str, link_id: str) -> dict[str, bool]:
    auth.require_super_admin(request)
    if not db.delete_brand_link(business_id, link_id):
        raise HTTPException(404, "Link not found")
    return {"ok": True}


def _normalize_sop_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url, re.I):
        raise HTTPException(422, "Enter a valid link")
    return url


@app.get("/api/businesses/{business_id}/sops")
def list_brand_sops(request: Request, business_id: str) -> list[dict[str, Any]]:
    _require_business_access(auth.require_user(request), business_id)
    return db.list_brand_sops(business_id)


@app.post("/api/businesses/{business_id}/sops")
def add_brand_sop(request: Request, business_id: str, body: BrandSopIn) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    if not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "Title is required")
    return db.add_brand_sop(
        business_id,
        title,
        body.content.strip(),
        _normalize_sop_url(body.url),
        created_by=str(actor["id"]),
    )


@app.patch("/api/businesses/{business_id}/sops/{sop_id}")
def update_brand_sop(
    request: Request, business_id: str, sop_id: str, body: BrandSopPatch
) -> dict[str, Any]:
    auth.require_super_admin(request)
    if not db.get_brand_sop(business_id, sop_id):
        raise HTTPException(404, "SOP not found")
    title = body.title.strip() if body.title is not None else None
    if title == "":
        raise HTTPException(422, "Title is required")
    return db.update_brand_sop(
        business_id,
        sop_id,
        title=title,
        content=body.content.strip() if body.content is not None else None,
        url=_normalize_sop_url(body.url) if body.url is not None else None,
    )  # type: ignore[return-value]


@app.delete("/api/businesses/{business_id}/sops/{sop_id}")
def delete_brand_sop(request: Request, business_id: str, sop_id: str) -> dict[str, bool]:
    auth.require_super_admin(request)
    if not db.delete_brand_sop(business_id, sop_id):
        raise HTTPException(404, "SOP not found")
    return {"ok": True}


@app.post("/api/businesses/{business_id}/visit")
def visit_business(request: Request, business_id: str) -> dict[str, bool]:
    user = auth.require_user(request)
    _require_business_access(user, business_id)
    db.record_brand_visit(str(user["id"]), business_id)
    return {"ok": True}


@app.get("/api/events")
def workspace_events(
    request: Request,
    business_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    if business_id:
        _require_business_access(user, business_id)
    visible_business_ids = (
        None
        if auth.is_super_admin(user) or user.get("role") == "admin"
        else sorted(_visible_business_ids(user))
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return db.list_workspace_events(
        business_id=business_id,
        business_ids=visible_business_ids,
        from_time=now,
        limit=limit,
    )


@app.post("/api/events")
def create_workspace_event(
    request: Request,
    body: WorkspaceEventIn,
) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    if not db.get_business(body.business_id):
        raise HTTPException(400, "Choose an existing brand.")
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "Event title cannot be empty.")
    starts_at = body.starts_at
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return db.create_workspace_event(
        business_id=body.business_id,
        title=title,
        event_type=body.event_type.strip() or "event",
        starts_at=starts_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        notes=body.notes.strip(),
        created_by=str(user["id"]),
    )


@app.get("/api/activity")
def recent_activity(request: Request) -> list[dict[str, Any]]:
    return db.list_recent_activity(viewer=auth.require_user(request))


@app.post("/api/businesses/import")
def import_businesses(
    request: Request,
    body: BrandImportIn,
) -> dict[str, Any]:
    """Create one business per brand brief (markdown headings or JSON items)."""

    auth.require_super_admin(request)
    payloads = normalize_import_items(
        body.items,
        body.markdown,
    )

    if not payloads:
        raise HTTPException(
            400,
            "No brands found. Paste markdown with # Brand headings, or send items[].",
        )

    existing = {
        b["name"].strip().lower()
        for b in db.list_businesses(
            active_only=False
        )
    }

    created: list[dict[str, Any]] = []
    skipped: list[str] = []

    for p in payloads:
        key = p["name"].strip().lower()

        if (
            body.skip_existing_names
            and key in existing
        ):
            skipped.append(p["name"])
            continue

        row = db.create_business(
            name=p["name"],
            description=p.get(
                "description",
                "",
            ),
            context_md=p.get(
                "context_md",
                "",
            ),
            status=p.get("status", "active"),
        )

        created.append(row)
        existing.add(key)

    return {
        "created": created,
        "created_count": len(created),
        "skipped": skipped,
        "parsed_count": len(payloads),
    }


@app.get("/api/businesses/{business_id}")
def get_business(
    request: Request,
    business_id: str,
) -> dict[str, Any]:
    user = auth.require_user(request)
    biz = _require_business_access(user, business_id)

    return {
        "business": biz,
        "notes": db.list_notes(
            business_id=business_id
        ),
        "tasks": db.list_tasks(
            business_id=business_id,
            open_only=True,
            viewer=user,
        ),
        "drive_sync": db.get_google_drive_business_sync(
            str(user["id"]),
            business_id,
        ),
    }


@app.patch("/api/businesses/{business_id}")
def patch_business(
    request: Request,
    business_id: str,
    body: BusinessPatch,
) -> dict[str, Any]:
    auth.require_super_admin(request)
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        if not fields["name"]:
            raise HTTPException(422, "Board title cannot be empty")
    updated = db.update_business(business_id, **fields)
    if not updated:
        raise HTTPException(404, "Board not found")
    return updated


TASK_VIEWS = {"mine", "unassigned", "completed", "overdue"}


def _clean_deadline(value: Optional[str], label: str) -> Optional[str]:
    if not value:
        return None
    try:
        datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(422, f"{label} must be an ISO date") from e
    return value.strip()


def _clean_checklist(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) > 100:
        raise HTTPException(422, "Checklist is limited to 100 items")
    out = []
    for item in items:
        text = str(item.get("text") or "").strip()[:300]
        if not text:
            raise HTTPException(422, "Checklist items need text")
        out.append({"text": text, "done": bool(item.get("done"))})
    return out


def _clean_links(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) > 50:
        raise HTTPException(422, "Links are limited to 50 items")
    out = []
    for item in items:
        url = str(item.get("url") or "").strip()
        if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
            raise HTTPException(422, "Links and attachments must be http(s) URLs")
        kind = "attachment" if item.get("kind") == "attachment" else "link"
        out.append({"url": url[:2000], "label": str(item.get("label") or url)[:200], "kind": kind})
    return out


def _validate_task_fields(fields: dict[str, Any]) -> None:
    if fields.get("status") is not None and fields["status"] not in db.TASK_STATUSES:
        raise HTTPException(422, "Invalid status")
    if fields.get("priority") is not None and fields["priority"] not in db.PRIORITIES:
        raise HTTPException(422, "Invalid priority")
    for key in ("deadline", "next_follow_up"):
        if key in fields:
            fields[key] = _clean_deadline(fields[key], key)
    if fields.get("checklist") is not None:
        fields["checklist"] = _clean_checklist(fields["checklist"])
    if fields.get("links") is not None:
        fields["links"] = _clean_links(fields["links"])


def _check_assignment(department_id: Optional[str], assignee_ids: list[str]) -> list[str]:
    if department_id:
        dept = db.get_department(department_id)
        if not dept or dept["status"] != "active":
            raise HTTPException(400, "Department not found or inactive")
    try:
        return db.validate_task_assignees(department_id, assignee_ids)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _general_business_id() -> str:
    biz = db.get_business_by_slug("general")
    if not biz:
        biz = db.create_business("General", description="Tasks not tied to a brand", slug="general")
    return biz["id"]


def _visible_task(user: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = db.get_task(task_id, viewer=user)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/api/tasks")
def tasks(
    request: Request,
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    open_only: bool = True,
    department_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: Optional[str] = None,
    view: Optional[str] = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    if view and view not in TASK_VIEWS:
        raise HTTPException(422, "Invalid view")
    return db.list_tasks(
        business_id=business_id,
        status=status,
        open_only=open_only and view != "completed",
        department_id=department_id,
        assignee_id=assignee_id,
        priority=priority,
        view=view,
        viewer=user,
        limit=max(1, min(limit, 1000)),
    )


@app.get("/api/tasks/{task_id}")
def get_task(request: Request, task_id: str) -> dict[str, Any]:
    user = auth.require_user(request)
    return _visible_task(user, task_id)


@app.post("/api/tasks")
def create_task(
    request: Request,
    body: TaskIn,
) -> dict[str, Any]:
    actor = auth.require_task_manager(request)
    fields = body.model_dump()
    approver_name = body.approver_name.strip()
    fields.pop("approver_name")
    _validate_task_fields(fields)
    business_id = body.business_id or _general_business_id()
    if not db.get_business(business_id):
        raise HTTPException(400, "Unknown business_id")
    if actor.get("role") == "admin" and business_id not in _visible_business_ids(actor):
        raise HTTPException(403, "You can only create tasks for brands in your work areas")
    fields["business_id"] = business_id
    if actor.get("role") == "admin":
        if not body.department_id:
            raise HTTPException(422, "Choose one of your departments for this task")
        if body.department_id not in db.user_department_ids(str(actor["id"])):
            raise HTTPException(403, "You can only create tasks in your departments")
    assignee_ids = _check_assignment(body.department_id, body.assignee_ids)
    fields["assignee_ids"] = assignee_ids
    description = fields.pop("description", None)
    if description is not None and not fields.get("notes"):
        fields["notes"] = description
    fields.pop("owner", None)
    task = db.create_task(**fields, actor_user_id=str(actor["id"]))
    if approver_name:
        approval = db.create_approval(
            summary=f"Approval requested for task: {task['title']}",
            action_type="task_approval",
            business_id=business_id,
            task_id=task["id"],
            payload={"approver_name": approver_name},
        )
        db.notify_super_admins(
            "approval",
            f"Approval needed: {approver_name}",
            f"{task['title']} · {task.get('business_name') or 'General'}",
            str(actor["id"]),
            source_id=f"{approval['id']}:requested",
            task_id=task["id"],
            target_url=f"/?task={task['id']}",
        )
    event_type = "review" if task.get("status") == "waiting_on" else "assignment"
    db.notify_task_event(task["id"], event_type, assignee_ids, str(actor["id"]))
    return db.get_task(task["id"]) or task


@app.patch("/api/tasks/{task_id}")
def patch_task(
    request: Request,
    task_id: str,
    body: TaskPatch,
) -> dict[str, Any]:
    actor = auth.require_user(request)
    previous = _visible_task(actor, task_id)
    fields = {
        k: v
        for k, v in body.model_dump().items()
        if k in body.model_fields_set and (v is not None or k in ("deadline", "next_follow_up", "department_id"))
    }
    _validate_task_fields(fields)
    if not auth.can_manage_tasks(actor):
        if not db.user_is_task_assignee(str(actor["id"]), task_id):
            raise HTTPException(403, "Only assignees can update this task")
        if set(fields) - {"status", "checklist"}:
            raise HTTPException(403, "You can only update status and checklist progress")
        if "checklist" in fields:
            old = [str(i.get("text")) for i in previous.get("checklist", [])]
            if old != [i["text"] for i in fields["checklist"]]:
                raise HTTPException(403, "You can only tick or untick checklist items")
    else:
        new_dept = fields["department_id"] if "department_id" in fields else previous.get("department_id")
        new_business_id = fields.get("business_id", previous["business_id"])
        if actor.get("role") == "admin":
            allowed_departments = set(db.user_department_ids(str(actor["id"])))
            if previous.get("department_id") not in allowed_departments:
                raise HTTPException(403, "You can only manage tasks in your departments")
            if new_dept not in allowed_departments:
                raise HTTPException(403, "You can only manage tasks in your departments")
        if (
            actor.get("role") == "admin"
            and new_business_id not in _visible_business_ids(actor)
        ):
            raise HTTPException(403, "You can only manage tasks for brands in your work areas")
        if (
            actor.get("role") == "admin"
            and new_dept
            and new_dept not in db.user_department_ids(str(actor["id"]))
        ):
            raise HTTPException(403, "You can only manage tasks in your departments")
        new_ids = fields.get("assignee_ids")
        if new_ids is None and "department_id" in fields:
            new_ids = previous.get("assignee_ids", [])
        if new_ids is not None:
            fields["assignee_ids"] = _check_assignment(new_dept, new_ids)
        fields.pop("owner", None)
    updated = db.update_task(task_id, actor_user_id=str(actor["id"]), **fields)
    if not updated:
        raise HTTPException(404, "Task not found")

    actor_id = str(actor["id"])
    before = set(previous.get("assignee_ids", []))
    after = set(updated.get("assignee_ids", []))
    newly_assigned = after - before
    requested_review = previous.get("status") != "waiting_on" and updated.get("status") == "waiting_on"
    review_recipients = after if requested_review else (
        newly_assigned if updated.get("status") == "waiting_on" else set()
    )
    db.notify_task_event(task_id, "review", sorted(review_recipients), actor_id)
    db.notify_task_event(task_id, "assignment", sorted(newly_assigned - review_recipients), actor_id)
    return updated




def _report_window(start: str, end: str) -> tuple[str, str]:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(400, "start and end must be ISO timestamps") from e
    if start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise HTTPException(400, "start and end must include a timezone")
    if end_dt <= start_dt:
        raise HTTPException(400, "end must be after start")
    if (end_dt - start_dt).days > 62:
        raise HTTPException(400, "Report range is limited to 62 days")
    return (
        start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@app.get("/api/time/report")
def time_report(
    request: Request,
    start: str,
    end: str,
    user_id: Optional[str] = None,
    department_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> dict[str, Any]:
    actor = auth.require_user(request)
    # Operators see only themselves; admins may inspect work within their departments.
    target = user_id or str(actor["id"])
    if actor.get("role") == "operator" and target != str(actor["id"]):
        raise HTTPException(403, "You can only view your own time log")
    if actor.get("role") == "admin":
        allowed_departments = set(db.user_department_ids(str(actor["id"])))
        if target == "all" or target != str(actor["id"]):
            if not department_id or department_id not in allowed_departments:
                raise HTTPException(403, "Choose one of your departments to view team time logs")
        elif department_id and department_id != "none" and department_id not in allowed_departments:
            raise HTTPException(403, "You can only view time logs in your departments")
    start_s, end_s = _report_window(start, end)
    return db.time_report(
        start_s,
        end_s,
        user_id=None if target == "all" else target,
        department_id=department_id,
        status=status,
        priority=priority,
        viewer=actor,
    )


@app.get("/api/attendance/status")
def attendance_status(request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
    return attendance.status(str(user["id"]))


@app.post("/api/attendance/check-in")
def attendance_check_in(request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
    try:
        return attendance.check_in(str(user["id"]))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/attendance/heartbeat")
def attendance_heartbeat(
    request: Request, body: AttendanceHeartbeatIn
) -> dict[str, Any]:
    user = auth.require_user(request)
    try:
        return attendance.heartbeat(str(user["id"]), body.request_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/attendance/check-out")
def attendance_check_out(request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
    try:
        return attendance.check_out(str(user["id"]))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/attendance/report")
def attendance_report(
    request: Request,
    month: str,
    user_id: Optional[str] = None,
    department_id: Optional[str] = None,
) -> dict[str, Any]:
    actor = auth.require_user(request)
    actor_id = str(actor["id"])
    role = actor.get("role")
    target = user_id or actor_id
    if role == "super_admin":
        if department_id and not db.get_department(department_id):
            raise HTTPException(404, "Department not found")
        if target == "all":
            selected_ids = [str(user["id"]) for user in db.list_users()]
        elif not db.get_user(target):
            raise HTTPException(404, "Employee not found")
        else:
            selected_ids = [target]
    elif role in ("admin", "operator"):
        # Only Super Admins may generate other people's or organisation reports.
        if target != actor_id or department_id:
            raise HTTPException(403, "You can only view your own attendance report")
        selected_ids = [actor_id]
    else:
        raise HTTPException(403, "Insufficient role permissions")
    try:
        return attendance.report(month, selected_ids, department_id=department_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/attendance/today")
def attendance_today(request: Request) -> dict[str, Any]:
    auth.require_super_admin(request)
    return attendance.today_overview()


@app.get("/api/attendance/policy")
def attendance_policy(request: Request) -> dict[str, Any]:
    auth.require_user(request)
    return attendance.get_policy()


@app.put("/api/attendance/policy")
def attendance_policy_update(
    request: Request, body: AttendancePolicyIn
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        return attendance.update_policy(
            str(actor["id"]),
            timezone_name=body.timezone,
            heartbeat_interval_seconds=body.heartbeat_interval_seconds,
            inactivity_timeout_seconds=body.inactivity_timeout_seconds,
            work_start=body.work_start,
            work_end=body.work_end,
            late_grace_minutes=body.late_grace_minutes,
            overtime_enabled=body.overtime_enabled,
            overtime_grace_minutes=body.overtime_grace_minutes,
            working_days=body.working_days,
            allowed_half_leaves=body.allowed_half_leaves,
            allowed_days_off=body.allowed_days_off,
            allowed_late_checkins=body.allowed_late_checkins,
            allowed_early_offs=body.allowed_early_offs,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/attendance/leaves")
def attendance_leave_add(request: Request, body: AttendanceLeaveIn) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        return attendance.add_leave(
            str(actor["id"]), body.user_id, body.leave_date, body.leave_type, body.reason
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/attendance/leaves/{leave_id}")
def attendance_leave_delete(request: Request, leave_id: str) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        attendance.delete_leave(str(actor["id"]), leave_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": True}


@app.post("/api/attendance/sessions/{session_id}/corrections")
def attendance_correction(
    request: Request, session_id: str, body: AttendanceCorrectionIn
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        return attendance.correct_session(
            str(actor["id"]), session_id, body.delta_seconds, body.reason
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/attendance/events/{event_id}/resolve")
def attendance_resolve_event(
    request: Request, event_id: str, body: AttendanceResolutionIn
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        return attendance.resolve_event(
            str(actor["id"]), event_id, body.resolution_note
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/attendance/audit")
def attendance_audit(
    request: Request, session_id: Optional[str] = None, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    auth.require_super_admin(request)
    return attendance.audit_history(session_id=session_id, limit=limit)


@app.get("/api/reports/overview")
def reports_overview(
    request: Request,
    start: str,
    end: str,
    department_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    completion: Optional[str] = None,
) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    if completion not in (None, "", "completed", "pending"):
        raise HTTPException(422, "completion must be completed or pending")
    start_s, end_s = _report_window(start, end)
    return db.reports_overview(
        start_s,
        end_s,
        viewer=actor,
        department_id=department_id or None,
        user_id=user_id or None,
        status=status or None,
        priority=priority or None,
        completion=completion or None,
    )


# ── Departments ─────────────────────────────────────────────


@app.get("/api/departments")
def list_departments(request: Request, include_inactive: bool = False) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    include = include_inactive and auth.is_super_admin(user)
    return db.list_departments(viewer=user, include_inactive=include)


@app.post("/api/departments")
def create_department(request: Request, body: DepartmentIn) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    try:
        department = db.create_department(body.name, body.description, body.color, body.icon)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.notify_super_admins(
        "admin", f"Department created: {department['name']}",
        "A workspace department was created.", str(actor["id"]), target_url="/?route=deptmgmt",
    )
    return department


@app.patch("/api/departments/{department_id}")
def patch_department(request: Request, department_id: str, body: DepartmentPatch) -> dict[str, Any]:
    actor = auth.require_super_admin(request)
    if body.status is not None and body.status not in ("active", "inactive"):
        raise HTTPException(422, "status must be active or inactive")
    try:
        updated = db.update_department(department_id, **body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not updated:
        raise HTTPException(404, "Department not found")
    db.notify_super_admins(
        "admin", f"Department updated: {updated['name']}",
        "A workspace department was updated.", str(actor["id"]), target_url="/?route=deptmgmt",
    )
    return updated


@app.get("/api/departments/{department_id}/members")
def department_members(request: Request, department_id: str) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    if not db.get_department(department_id):
        raise HTTPException(404, "Department not found")
    if not auth.is_super_admin(user) and department_id not in db.user_department_ids(str(user["id"])):
        raise HTTPException(403, "Not a member of this department")
    return db.list_department_members(department_id)


@app.post("/api/departments/{department_id}/members")
def add_department_member(request: Request, department_id: str, body: DepartmentMemberIn) -> list[dict[str, Any]]:
    actor = auth.require_super_admin(request)
    if not db.get_department(department_id):
        raise HTTPException(404, "Department not found")
    target = db.get_user(body.user_id)
    if not target or target.get("status") != "active":
        raise HTTPException(400, "User not found or inactive")
    department = db.get_department(department_id)
    db.add_department_member(department_id, body.user_id, str(actor["id"]))
    db.notify_super_admins(
        "admin", f"Department member added: {target.get('display_name') or target['username']}",
        f"A member was added to {department['name']}.",
        str(actor["id"]), target_url="/?route=deptmgmt",
    )
    return db.list_department_members(department_id)


@app.delete("/api/departments/{department_id}/members/{user_id}")
def remove_department_member(request: Request, department_id: str, user_id: str) -> list[dict[str, Any]]:
    actor = auth.require_super_admin(request)
    department = db.get_department(department_id)
    if not department:
        raise HTTPException(404, "Department not found")
    db.remove_department_member(department_id, user_id)
    db.notify_super_admins(
        "admin", f"Department member removed: {user_id}",
        f"A member was removed from {department['name']}.",
        str(actor["id"]), target_url="/?route=deptmgmt",
    )
    return db.list_department_members(department_id)


@app.get("/api/team/members")
def team_members(request: Request) -> list[dict[str, str]]:
    user = auth.require_user(request)
    if auth.is_super_admin(user):
        members = db.list_users(include_inactive=False)
    else:
        member_ids = {str(user["id"])}
        for department_id in db.user_department_ids(str(user["id"])):
            member_ids.update(
                str(member["id"])
                for member in db.list_department_members(department_id)
                if member.get("status") == "active"
            )
        members = [
            member for member in db.list_users(include_inactive=False)
            if str(member["id"]) in member_ids
        ]
    return [
        {
            "id": str(user["id"]),
            "username": str(user["username"]),
            "display_name": str(user.get("display_name") or user["username"]),
        }
        for user in members
    ]


@app.get("/api/tasks/{task_id}/comments")
def task_comments(request: Request, task_id: str) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    _visible_task(user, task_id)
    db.mark_task_read(str(user["id"]), task_id)
    return db.list_task_comments(task_id)


@app.post("/api/tasks/{task_id}/comments")
def add_task_comment(
    request: Request,
    task_id: str,
    body: TaskCommentIn,
) -> dict[str, Any]:
    user = auth.require_user(request)
    _visible_task(user, task_id)
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "Comment cannot be empty")
    usernames = {
        match.casefold()
        for match in re.findall(r"(?<![\w@])@([A-Za-z0-9_.-]{1,64})", content)
    }
    mentioned_ids = [
        str(member["id"])
        for member in db.list_users(include_inactive=False)
        if str(member["username"]).casefold() in usernames
    ]
    comment = db.create_task_comment(
        task_id,
        str(user["id"]),
        content,
        mentioned_ids,
    )
    if not comment:
        raise HTTPException(404, "Task not found")
    return comment


@app.get("/api/notifications")
def notifications(request: Request, filter: str = "all", limit: int = 50) -> dict[str, Any]:
    user = auth.require_user(request)
    if filter not in ("all", "read", "unread"):
        raise HTTPException(422, "filter must be all, read or unread")
    rows = db.list_notifications(str(user["id"]), limit=limit, read_filter=filter)
    return {
        "items": rows,
        "unread_count": db.count_unread_notifications(str(user["id"])),
        "sound_enabled": db.notification_sound_enabled(str(user["id"])),
    }


@app.post("/api/notifications/read-all")
def read_all_notifications(request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
    return {"ok": True, "updated": db.mark_all_notifications_read(str(user["id"]))}


@app.post("/api/notifications/{notification_id}/unread")
def unread_notification(request: Request, notification_id: str) -> dict[str, bool]:
    user = auth.require_user(request)
    if not db.mark_notification_unread(notification_id, str(user["id"])):
        raise HTTPException(404, "Notification not found")
    return {"ok": True}


@app.post("/api/notifications/{notification_id}/read")
def read_notification(
    request: Request,
    notification_id: str,
) -> dict[str, bool]:
    user = auth.require_user(request)
    if not db.get_notification(notification_id, str(user["id"])):
        raise HTTPException(404, "Notification not found")
    db.mark_notification_read(notification_id, str(user["id"]))
    return {"ok": True}


@app.put("/api/notifications/sound")
def set_notification_sound(
    request: Request,
    body: NotificationSoundIn,
) -> dict[str, bool]:
    user = auth.require_user(request)
    db.set_notification_sound_enabled(str(user["id"]), body.enabled)
    return {"sound_enabled": body.enabled}


@app.get("/api/approvals")
def approvals(
    request: Request,
    status: str = "pending",
) -> list[dict[str, Any]]:
    auth.require_task_manager(request)
    return db.list_approvals(status)


@app.post("/api/approvals/{approval_id}/decide")
def decide(
    request: Request,
    approval_id: str,
    body: ApprovalDecision,
) -> dict[str, Any]:
    actor = auth.require_task_manager(request)
    row = db.decide_approval(
        approval_id,
        body.approved,
    )

    if not row:
        raise HTTPException(
            404,
            "Approval not found",
        )
    status_text = "approved" if body.approved else "rejected"
    db.notify_super_admins(
        "approval",
        f"Approval {status_text}",
        str(row.get("summary") or "A workspace approval was decided."),
        str(actor["id"]),
        source_id=f"{approval_id}:decided",
        task_id=row.get("task_id"),
        target_url=f"/?task={row['task_id']}" if row.get("task_id") else "/admin",
    )

    return row


@app.post("/api/chat")
def chat_endpoint(
    request: Request,
    body: ChatIn,
) -> dict[str, Any]:
    try:
        return _cos_chat(
            body.message,
            body.history,
            user_id=str(auth.require_user(request)["id"]),
        )
    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).exception("Chat failed")
        raise HTTPException(502, f"Gemini error: {e}") from e


@app.get("/api/whatsapp/webhook")
def whatsapp_webhook_verify(
    mode: Optional[str] = Query(default=None, alias="hub.mode"),
    verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
) -> Any:
    if settings.whatsapp_provider.strip().lower() == "360dialog":
        return PlainTextResponse("ok")
    if not settings.whatsapp_verify_token:
        raise HTTPException(503, "WhatsApp webhook verification is not configured")
    if (
        mode != "subscribe"
        or not verify_token
        or not challenge
        or not hmac.compare_digest(verify_token, settings.whatsapp_verify_token)
    ):
        raise HTTPException(403, "Webhook verification failed")
    return PlainTextResponse(challenge)



def _valid_360dialog_webhook_auth(request: Request) -> bool:
    auth_mode = settings.d360_webhook_auth.strip().lower()

    if auth_mode == "basic":
        username = settings.d360_webhook_username
        password = settings.d360_webhook_password

        if not username or not password:
            logging.getLogger(__name__).error(
                "360dialog webhook Basic Auth is not configured"
            )
            return False

        credentials = f"{username}:{password}".encode("utf-8")
        expected = "Basic " + base64.b64encode(credentials).decode("ascii")
        received = request.headers.get("Authorization", "")
        scheme, separator, value = received.partition(" ")
        if not separator or scheme.lower() != "basic":
            logging.getLogger(__name__).warning(
                "360dialog webhook rejected: Basic authorization header missing"
            )
            return False

        valid = hmac.compare_digest(
            value.encode("utf-8"), expected.removeprefix("Basic ").encode("ascii")
        )
        if not valid:
            logging.getLogger(__name__).warning(
                "360dialog webhook rejected: invalid Basic authorization credentials"
            )
        return valid

    if auth_mode == "token":
        return bool(settings.d360_webhook_token) and hmac.compare_digest(
            request.query_params.get("token", ""),
            settings.d360_webhook_token,
        )

    logging.getLogger(__name__).error("360dialog webhook auth mode is unsupported")
    return False



@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    from app.integrations import whatsapp

    if settings.whatsapp_provider.strip().lower() == "360dialog":
        auth_valid = _valid_360dialog_webhook_auth(request)
        logging.getLogger(__name__).info(
            "[AUTH] 360dialog webhook authentication result=%s",
            "accepted" if auth_valid else "rejected",
        )
        if not auth_valid:
            raise HTTPException(403, "Invalid 360dialog webhook authorization")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "Invalid JSON webhook payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "Webhook payload must be a JSON object")
        try:
            whatsapp.validate_configuration()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        background_tasks.add_task(whatsapp.process_webhook_payload, payload)
        logging.getLogger(__name__).info("[INBOUND] 360dialog webhook payload accepted")
        return {"ok": True, "accepted": True}

    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not settings.whatsapp_app_secret:
        raise HTTPException(503, "WhatsApp webhook signature validation is not configured")
    if not whatsapp.validate_signature(raw_body, signature, settings.whatsapp_app_secret):
        raise HTTPException(403, "Invalid webhook signature")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Invalid JSON webhook payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Webhook payload must be a JSON object")
    try:
        whatsapp.validate_configuration()
        return {"ok": True, "processed": whatsapp.process_webhook_payload(payload)}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            502,
            f"Gemini error: {e}",
        ) from e


@app.get("/api/chat/history")
def chat_history(
    limit: int = 40,
) -> list[dict[str, Any]]:
    return db.recent_messages(limit)


# ── Slack ───────────────────────────────────────────────────


def _slack():
    from app.integrations import slack

    return slack


def _slack_call(fn, *args):
    try:
        return fn(*args)
    except RuntimeError as e:
        msg = str(e)
        if msg == "not_connected":
            raise HTTPException(409, "Slack is not connected.") from e
        if msg in {"invalid_auth", "token_revoked", "account_inactive", "not_authed"}:
            raise HTTPException(409, "Slack session expired. Reconnect Slack.") from e
        if msg == "missing_scope":
            raise HTTPException(
                409,
                "Slack needs more permissions for this. Add the scope in your Slack app, then Disconnect and Connect Slack again.",
            ) from e
        raise HTTPException(400, msg) from e


class SlackMessageIn(BaseModel):
    channel: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=4000)
    thread_ts: Optional[str] = Field(default=None, max_length=40)


@app.get("/api/slack/status")
def slack_status(request: Request) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    return _slack().status(str(user["id"]))


@app.get("/api/slack/auth")
def slack_auth(request: Request):
    user = auth.require_task_manager(request)
    try:
        return RedirectResponse(_slack().auth_url(str(user["id"])))
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/slack/file")
def slack_file(request: Request, url: str, download: bool = False):
    user = auth.require_task_manager(request)
    res = _slack_call(_slack().fetch_file, str(user["id"]), url)
    ctype = res.headers.get("content-type", "application/octet-stream")
    if ctype.startswith("text/html"):
        raise HTTPException(409, "Slack file access denied. Reconnect Slack to grant file access.")
    safe_inline = ctype.startswith(("image/", "video/", "audio/")) and "svg" not in ctype
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    if download or not safe_inline:
        headers["Content-Disposition"] = "attachment"
    return Response(content=res.content, media_type=ctype, headers=headers)


@app.get("/api/slack/unread")
def slack_unread(request: Request) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    uid = str(user["id"])
    items = _slack_call(_slack().unread, uid)
    for cid, info in items.items():
        where = "" if info["kind"] == "dm" else f" in {'#' if info['kind'] == 'channel' else ''}{info['name']}"
        db.create_slack_notification(
            uid,
            f"{cid}:{info['ts']}",
            f"Slack: {info['user']}{where}",
            info["text"],
        )
    return {"items": items}


@app.get("/api/slack/callback")
def slack_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    state: Optional[str] = None,
):
    from urllib.parse import quote

    if error or not code or not state:
        return RedirectResponse(f"/?slack=error&reason={quote(error or 'missing_code_or_state')}#chat")
    try:
        _slack().exchange_code(code, state)
    except RuntimeError as e:
        return RedirectResponse(f"/?slack=error&reason={quote(str(e))}#chat")
    return RedirectResponse("/?slack=connected#chat")


@app.post("/api/slack/disconnect")
def slack_disconnect(request: Request) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    _slack().disconnect(str(user["id"]))
    return {"ok": True, "connected": False}


@app.get("/api/slack/conversations")
def slack_conversations(request: Request) -> list[dict[str, Any]]:
    user = auth.require_task_manager(request)
    return _slack_call(_slack().list_conversations, str(user["id"]))


@app.get("/api/slack/messages")
def slack_messages(request: Request, channel: str) -> list[dict[str, Any]]:
    user = auth.require_task_manager(request)
    return _slack_call(_slack().list_messages, str(user["id"]), channel)


@app.get("/api/slack/replies")
def slack_replies(request: Request, channel: str, ts: str) -> list[dict[str, Any]]:
    user = auth.require_task_manager(request)
    return _slack_call(_slack().list_replies, str(user["id"]), channel, ts)


class SlackReactionIn(BaseModel):
    channel: str = Field(min_length=1, max_length=40)
    ts: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_+\-:']+$")
    remove: bool = False


@app.post("/api/slack/reactions")
def slack_reaction(body: SlackReactionIn, request: Request) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    _slack_call(_slack().set_reaction, str(user["id"]), body.channel, body.ts, body.name, body.remove)
    return {"ok": True}


SLACK_MAX_UPLOAD = 25 * 1024 * 1024


@app.post("/api/slack/upload")
async def slack_upload(
    request: Request,
    channel: str = Form(..., max_length=40),
    comment: str = Form("", max_length=4000),
    thread_ts: Optional[str] = Form(None, max_length=40),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    content = await file.read(SLACK_MAX_UPLOAD + 1)
    if not content:
        raise HTTPException(400, "The file is empty.")
    if len(content) > SLACK_MAX_UPLOAD:
        raise HTTPException(413, "File is too large (25 MB max).")
    name = (file.filename or "file").replace("\\", "/").rsplit("/", 1)[-1] or "file"
    return _slack_call(
        _slack().upload_file, str(user["id"]), channel, name, content, comment.strip(), thread_ts or None
    )


@app.post("/api/slack/messages")
def slack_send(body: SlackMessageIn, request: Request) -> dict[str, Any]:
    user = auth.require_task_manager(request)
    return _slack_call(_slack().post_message, str(user["id"]), body.channel, body.text.strip(), body.thread_ts)


# ── Google ──────────────────────────────────────────────────


@app.get("/api/google/status")
def google_status(
    request: Request,
) -> dict[str, Any]:
    user = auth.require_super_admin(request)

    status = _google().connection_status(user["id"])
    from app.integrations.google_drive import verify_drive_access

    return {**status, **verify_drive_access(str(user["id"]))}


@app.get("/api/google/auth")
def google_auth(
    request: Request,
):
    user = auth.require_super_admin(request)

    try:
        return RedirectResponse(
            _google().auth_url(
                user_id=str(user["id"])
            )
        )
    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e


@app.get("/api/google/callback")
def google_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    state: Optional[str] = None,
):
    if error:
        raise HTTPException(
            400,
            f"Google OAuth error: {error}",
        )

    if not code:
        raise HTTPException(
            400,
            "Missing code",
        )

    if not state:
        raise HTTPException(
            400,
            "Missing Google sign-in state.",
        )

    try:
        _google().exchange_code(
            code,
            state,
        )
    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e

    return RedirectResponse(
        "/?google=connected"
    )


@app.post("/api/google/disconnect")
def google_disconnect(
    request: Request,
) -> dict[str, Any]:
    user = auth.require_super_admin(request)

    _google().disconnect(
        user["id"]
    )

    return {
        "ok": True,
        "connected": False,
    }


@app.get("/api/google/emails")
def google_emails(
    request: Request,
    max_results: int = 15,
) -> dict[str, Any]:
    user = auth.require_super_admin(request)

    try:
        emails = _google().fetch_recent_emails(
            user["id"],
            max_results=max_results,
        )

        proposals = _google().propose_tasks_from_email(
            emails
        )

        return {
            "emails": emails,
            "proposed_actions": proposals,
        }

    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e
    except Exception as e:
        logging.getLogger(__name__).exception("Google pull failed")
        raise HTTPException(
            502,
            "Google could not be reached or access was refused. Reconnect Google in Integrations and try again.",
        ) from e


@app.get("/api/google/calendar")
def google_calendar(
    request: Request,
    days: int = 7,
) -> dict[str, Any]:
    user = auth.require_super_admin(request)

    try:
        return {
            "events": _google().fetch_upcoming_events(
                user["id"],
                days=days,
            )
        }

    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e
    except Exception as e:
        logging.getLogger(__name__).exception("Google pull failed")
        raise HTTPException(
            502,
            "Google could not be reached or access was refused. Reconnect Google in Integrations and try again.",
        ) from e


@app.get("/api/google/drive")
def google_drive(
    request: Request,
    max_results: int = 10,
) -> dict[str, Any]:
    user = auth.require_super_admin(request)
    from app.integrations.google_drive import fetch_recent_drive_files
    from googleapiclient.errors import HttpError
    import httplib2

    try:
        files = fetch_recent_drive_files(
            str(user["id"]),
            max_results=max(1, min(max_results, 10)),
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        status_code = getattr(exc.resp, "status", 502)
        if status_code == 403:
            detail = "Google Drive access was denied. Reconnect Google and approve the Drive read-only permission."
        else:
            detail = f"Google Drive request failed (HTTP {status_code}). Try again or reconnect Google."
        raise HTTPException(502, detail) from exc
    except (httplib2.HttpLib2Error, OSError) as exc:
        raise HTTPException(
            502,
            "Google Drive could not be reached right now. Try again shortly.",
        ) from exc

    db.record_google_pull(
        str(user["id"]),
        drive_count=len(files),
    )
    return {"files": files, "count": len(files)}


@app.post("/api/google/drive/import")
def import_google_drive_brands(request: Request) -> dict[str, Any]:
    user = auth.require_super_admin(request)
    from app.integrations.google_drive import fetch_brand_drive_files
    from googleapiclient.errors import HttpError
    import httplib2

    businesses = db.list_businesses(active_only=False)
    from app.integrations.google_drive import verify_drive_access

    access = verify_drive_access(str(user["id"]))
    if access["drive_status"] != "verified":
        db.record_google_drive_sync(
            str(user["id"]),
            [
                {
                    "business_id": business["id"],
                    "status": "failed",
                    "files": [],
                    "error": access["drive_error"],
                }
                for business in businesses
            ],
        )
        status_code = (
            403
            if access["drive_status"] == "permission_missing"
            else 400
        )
        raise HTTPException(status_code, access["drive_error"])

    try:
        drive_brands = fetch_brand_drive_files(
            str(user["id"]),
            businesses,
            portfolio_folder_id=settings.google_drive_portfolio_folder_id,
            folder_mappings=settings.google_drive_brand_folder_map,
            file_mappings=settings.google_drive_brand_file_map,
        )
    except RuntimeError as exc:
        db.record_google_drive_sync(
            str(user["id"]),
            [
                {
                    "business_id": business["id"],
                    "status": "failed",
                    "files": [],
                    "error": str(exc),
                }
                for business in businesses
            ],
        )
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        status_code = getattr(exc.resp, "status", 502)
        if status_code == 403:
            detail = "Google Drive access was denied. Reconnect Google and approve the Drive read-only permission."
        else:
            detail = f"Google Drive request failed (HTTP {status_code}). Try again or reconnect Google."
        db.record_google_drive_sync(
            str(user["id"]),
            [
                {
                    "business_id": business["id"],
                    "status": "failed",
                    "files": [],
                    "error": detail,
                }
                for business in businesses
            ],
        )
        raise HTTPException(502, detail) from exc
    except (httplib2.HttpLib2Error, OSError) as exc:
        message = "Google Drive could not be reached right now. Try again shortly."
        db.record_google_drive_sync(
            str(user["id"]),
            [
                {
                    "business_id": business["id"],
                    "status": "failed",
                    "files": [],
                    "error": message,
                }
                for business in businesses
            ],
        )
        raise HTTPException(502, message) from exc

    results = []
    sync_records = []
    total_files = 0
    updated_count = 0
    for business in businesses:
        result = drive_brands[str(business["id"])]
        status = result["status"]
        files = result.get("files", [])
        total_files += len(files)
        if status in ("matched", "incomplete") and files:
            context = merge_drive_context(
                business.get("context_md", ""),
                files,
            )
            if context != business.get("context_md", ""):
                db.update_business(business["id"], context_md=context)
                updated_count += 1
            if status != "incomplete":
                status = (
                    "imported"
                    if context != business.get("context_md", "")
                    else "unchanged"
                )
        elif status == "matched":
            status = "no_files"

        sync_records.append(
            {
                "business_id": business["id"],
                "folder_id": result.get("folder_id"),
                "status": status,
                "files": files,
                "error": result.get("error"),
            }
        )

        errors = []
        if result.get("error"):
            errors.append(
                {
                    "file_name": result.get("folder_name") or "Drive folder",
                    "reason": result["error"],
                }
            )
        if result.get("truncated"):
            errors.append(
                {
                    "file_name": result.get("folder_name") or "Drive folder",
                    "reason": (
                        "The per-brand file limit was reached; the folder is not "
                        "fully synchronized."
                    ),
                }
            )
        results.append(
            {
                "business_id": business["id"],
                "business_name": business["name"],
                "status": status,
                "file_count": len(files),
                "linked_count": len(files),
                "folder_name": result.get("folder_name"),
                "folder_link": result.get("folder_link"),
                "folder_candidates": result.get("folder_candidates", []),
                "folder_error": result.get("error"),
                "truncated": result.get("truncated", False),
                "errors": errors,
            }
        )

    db.record_google_drive_sync(str(user["id"]), sync_records)
    db.record_google_pull(str(user["id"]), drive_count=total_files)
    return {
        "sync_mode": "links_only",
        "brands": results,
        "brand_count": len(results),
        "updated_count": updated_count,
        "file_count": total_files,
    }


@app.post("/api/google/ingest-proposals")
def ingest_proposals(
    request: Request,
) -> dict[str, Any]:
    """Create draft follow-up tasks from email signals into the first business (demo hook)."""

    user = auth.require_task_manager(request)

    try:
        emails = _google().fetch_recent_emails(
            user["id"],
            max_results=20,
        )
    except RuntimeError as e:
        raise HTTPException(
            400,
            str(e),
        ) from e

    proposals = _google().propose_tasks_from_email(
        emails
    )

    visible_business_ids = _visible_business_ids(user)
    businesses = [
        business for business in db.list_businesses()
        if business["id"] in visible_business_ids
    ]

    if not businesses:
        raise HTTPException(
            400,
            "Add a business first",
        )

    default_biz = businesses[0]["id"]
    created = []

    for p in proposals[:10]:
        t = db.create_task(
            business_id=default_biz,
            title=p["title"],
            category=p.get(
                "category",
                "follow_up",
            ),
            priority=p.get(
                "priority",
                "P2",
            ),
            notes=p.get(
                "notes",
                "",
            ),
            source="gmail",
            source_ref=p.get(
                "source_ref",
                "",
            ),
            status="open",
        )

        created.append(t)

    return {
        "created": created,
        "count": len(created),
    }


# ── UI ──────────────────────────────────────────────────────


app.mount(
    "/static",
    StaticFiles(directory=str(STATIC)),
    name="static",
)
PUBLIC_DIR = Path(__file__).parent.parent / "public"
if PUBLIC_DIR.is_dir():
    app.mount("/public", StaticFiles(directory=str(PUBLIC_DIR)), name="public")
app.mount(
    "/uploads/brands",
    StaticFiles(directory=str(BRAND_UPLOADS)),
    name="brand-logos",
)


@app.get("/")
def index():
    return FileResponse(
        STATIC / "index.html"
    )