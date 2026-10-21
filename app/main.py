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

from app import auth, db
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


BRAND_LINK_KINDS = {"website", "instagram", "facebook", "tiktok", "youtube", "linkedin", "x", "shop", "drive", "other"}


class BrandLinkIn(BaseModel):
    kind: str = "website"
    label: str = Field(default="", max_length=80)
    url: str = Field(min_length=3, max_length=500)


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
    business_id: str
    title: str
    category: str = "other"
    owner: str = "me"
    priority: str = "P2"
    deadline: Optional[str] = None
    status: str = "open"
    next_follow_up: Optional[str] = None
    waiting_on: str = ""
    notes: str = ""
    source: str = "manual"


class TaskPatch(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[str] = None
    next_follow_up: Optional[str] = None
    waiting_on: Optional[str] = None
    notes: Optional[str] = None


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
        "user": auth.public_user(user)
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
    """Portfolio snapshot for super admin / admin tracking."""

    auth.require_roles(
        request,
        "super_admin",
        "admin",
    )

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
    auth.require_roles(
        request,
        "super_admin",
        "admin",
    )

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
    actor = auth.require_roles(
        request,
        "super_admin",
        "admin",
    )

    role = (
        body.role
        if body.role in db.USER_ROLES
        else "operator"
    )

    if (
        role == "super_admin"
        and actor.get("role") != "super_admin"
    ):
        raise HTTPException(
            403,
            "Only super_admin can create super_admin users",
        )

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

    return {
        "user": auth.public_user(user)
    }


@app.patch("/api/admin/users/{user_id}")
def admin_patch_user(
    request: Request,
    user_id: str,
    body: AdminUserPatch,
) -> dict[str, Any]:
    actor = auth.require_roles(
        request,
        "super_admin",
        "admin",
    )

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
            body.role == "super_admin"
            and actor.get("role") != "super_admin"
        ):
            raise HTTPException(
                403,
                "Only super_admin can assign super_admin",
            )

        fields["role"] = body.role

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
def brief() -> dict[str, Any]:
    return db.attention_brief()


@app.get("/api/businesses")
def businesses(request: Request) -> list[dict[str, Any]]:
    user = auth.require_user(request)
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
    ]


@app.post("/api/businesses")
def create_business(
    request: Request,
    body: BusinessIn,
) -> dict[str, Any]:
    auth.require_user(request)
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
    auth.require_user(request)
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
    auth.require_user(request)
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
    auth.require_user(request)
    if not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    return db.list_brand_links(business_id)


@app.post("/api/businesses/{business_id}/links")
def add_brand_link(request: Request, business_id: str, body: BrandLinkIn) -> dict[str, Any]:
    auth.require_user(request)
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
    auth.require_user(request)
    if not db.delete_brand_link(business_id, link_id):
        raise HTTPException(404, "Link not found")
    return {"ok": True}


@app.post("/api/businesses/{business_id}/visit")
def visit_business(request: Request, business_id: str) -> dict[str, bool]:
    user = auth.require_user(request)
    if not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    db.record_brand_visit(str(user["id"]), business_id)
    return {"ok": True}


@app.get("/api/events")
def workspace_events(
    request: Request,
    business_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    auth.require_user(request)
    if business_id and not db.get_business(business_id):
        raise HTTPException(404, "Brand not found")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return db.list_workspace_events(
        business_id=business_id,
        from_time=now,
        limit=limit,
    )


@app.post("/api/events")
def create_workspace_event(
    request: Request,
    body: WorkspaceEventIn,
) -> dict[str, Any]:
    user = auth.require_user(request)
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
    auth.require_user(request)
    return db.list_recent_activity()


@app.post("/api/businesses/import")
def import_businesses(
    body: BrandImportIn,
) -> dict[str, Any]:
    """Create one business per brand brief (markdown headings or JSON items)."""

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
    biz = db.get_business(business_id)

    if not biz:
        raise HTTPException(
            404,
            "Business not found",
        )

    return {
        "business": biz,
        "notes": db.list_notes(
            business_id=business_id
        ),
        "tasks": db.list_tasks(
            business_id=business_id,
            open_only=True,
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
    auth.require_user(request)
    fields = body.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        if not fields["name"]:
            raise HTTPException(422, "Board title cannot be empty")
    updated = db.update_business(business_id, **fields)
    if not updated:
        raise HTTPException(404, "Board not found")
    return updated


@app.get("/api/tasks")
def tasks(
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    open_only: bool = True,
) -> list[dict[str, Any]]:
    return db.list_tasks(
        business_id=business_id,
        status=status,
        open_only=open_only,
    )


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    row = db.get_task(task_id)
    if not row:
        raise HTTPException(404, "Task not found")
    return row


@app.post("/api/tasks")
def create_task(
    request: Request,
    body: TaskIn,
) -> dict[str, Any]:
    actor = auth.require_user(request)
    if not db.get_business(
        body.business_id
    ):
        raise HTTPException(
            400,
            "Unknown business_id",
        )

    task = db.create_task(
        **body.model_dump()
    )
    if body.owner.strip() != "me":
        db.create_task_assignment_notification(
            task["id"],
            body.owner.strip(),
            str(actor["id"]),
        )
    return task


@app.patch("/api/tasks/{task_id}")
def patch_task(
    request: Request,
    task_id: str,
    body: TaskPatch,
) -> dict[str, Any]:
    actor = auth.require_user(request)
    previous = db.get_task(task_id)
    updated = db.update_task(
        task_id,
        **body.model_dump(
            exclude_none=True
        ),
    )

    if not updated:
        raise HTTPException(
            404,
            "Task not found",
        )

    if (
        body.owner is not None
        and previous
        and body.owner.strip() != (previous.get("owner") or "").strip()
    ):
        db.create_task_assignment_notification(
            task_id,
            body.owner.strip(),
            str(actor["id"]),
        )

    return updated


@app.get("/api/team/members")
def team_members(request: Request) -> list[dict[str, str]]:
    auth.require_user(request)
    return [
        {
            "id": str(user["id"]),
            "username": str(user["username"]),
            "display_name": str(user.get("display_name") or user["username"]),
        }
        for user in db.list_users(include_inactive=False)
    ]


@app.get("/api/tasks/{task_id}/comments")
def task_comments(request: Request, task_id: str) -> list[dict[str, Any]]:
    auth.require_user(request)
    if not db.get_task(task_id):
        raise HTTPException(404, "Task not found")
    return db.list_task_comments(task_id)


@app.post("/api/tasks/{task_id}/comments")
def add_task_comment(
    request: Request,
    task_id: str,
    body: TaskCommentIn,
) -> dict[str, Any]:
    user = auth.require_user(request)
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
def notifications(request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
    rows = db.list_notifications(str(user["id"]))
    return {
        "items": rows,
        "unread_count": db.count_unread_notifications(str(user["id"])),
        "sound_enabled": db.notification_sound_enabled(str(user["id"])),
    }


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
    status: str = "pending",
) -> list[dict[str, Any]]:
    return db.list_approvals(status)


@app.post("/api/approvals/{approval_id}/decide")
def decide(
    approval_id: str,
    body: ApprovalDecision,
) -> dict[str, Any]:
    row = db.decide_approval(
        approval_id,
        body.approved,
    )

    if not row:
        raise HTTPException(
            404,
            "Approval not found",
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
    user = auth.require_user(request)
    return _slack().status(str(user["id"]))


@app.get("/api/slack/auth")
def slack_auth(request: Request):
    user = auth.require_user(request)
    try:
        return RedirectResponse(_slack().auth_url(str(user["id"])))
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/slack/file")
def slack_file(request: Request, url: str, download: bool = False):
    user = auth.require_user(request)
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
    user = auth.require_user(request)
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
    user = auth.require_user(request)
    _slack().disconnect(str(user["id"]))
    return {"ok": True, "connected": False}


@app.get("/api/slack/conversations")
def slack_conversations(request: Request) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    return _slack_call(_slack().list_conversations, str(user["id"]))


@app.get("/api/slack/messages")
def slack_messages(request: Request, channel: str) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    return _slack_call(_slack().list_messages, str(user["id"]), channel)


@app.get("/api/slack/replies")
def slack_replies(request: Request, channel: str, ts: str) -> list[dict[str, Any]]:
    user = auth.require_user(request)
    return _slack_call(_slack().list_replies, str(user["id"]), channel, ts)


class SlackReactionIn(BaseModel):
    channel: str = Field(min_length=1, max_length=40)
    ts: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_+\-:']+$")
    remove: bool = False


@app.post("/api/slack/reactions")
def slack_reaction(body: SlackReactionIn, request: Request) -> dict[str, Any]:
    user = auth.require_user(request)
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
    user = auth.require_user(request)
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
    user = auth.require_user(request)
    return _slack_call(_slack().post_message, str(user["id"]), body.channel, body.text.strip(), body.thread_ts)


# ── Google ──────────────────────────────────────────────────


@app.get("/api/google/status")
def google_status(
    request: Request,
) -> dict[str, Any]:
    user = auth.require_user(request)

    status = _google().connection_status(user["id"])
    from app.integrations.google_drive import verify_drive_access

    return {**status, **verify_drive_access(str(user["id"]))}


@app.get("/api/google/auth")
def google_auth(
    request: Request,
):
    user = auth.require_user(request)

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
    user = auth.require_user(request)

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
    user = auth.require_user(request)

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
    user = auth.require_user(request)

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
    user = auth.require_user(request)
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
    user = auth.require_user(request)
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

    user = auth.require_user(request)

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

    businesses = db.list_businesses()

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