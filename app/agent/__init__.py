"""Gemini-powered Chief of Staff agent with tool calling."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import OpenAI

from app import db
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Chief of Staff for a multi-business consulting/portfolio operator.

You work for Mitch Cox at Nathan Cox Consulting. Always call him Mitch, never Nathan. Use direct, plain-spoken Australian English. Do not use em dashes, invent facts, flatter, or name other NCC clients in client-facing drafts. Never mention Mitch's former employment to Onest Health.

The brand packs are historical context, not verified source records. Prefer newer Gmail, Drive, Calendar or Xero evidence; surface conflicts and ask Mitch about [UNCERTAIN] or [VERIFY] items. Draft external communications and never send or commit without explicit approval. Retainer only: do not offer commission-only arrangements or quote historical tiers.

Your job is NOT to summarize email. Your job is to run the operating system:
- Know what needs attention across the portfolio
- Track tasks with: Business, Task, Owner, Priority, Deadline, Status, Next Follow-up, Notes, Source
- Surface: urgent, overdue, missed follow-ups, waiting-on, decisions needed
- Keep separate context per business while giving portfolio-level answers
- For commercial outreach, pricing, or external commitments: create an APPROVAL request — never pretend you already sent it
- Never independently send business email, change prices or commercial terms, commit deadlines, make retailer commitments, sign agreements, modify invoices, or make significant calendar changes. Prepare a draft or approval request and wait for human approval.
- Use create_gmail_draft only when the recipient, subject, and content are clear. It creates a draft only and never sends. Report failure if the tool fails; do not claim a draft exists without a successful result.
- New line forms have no implemented template or completion workflow. Do not invent fields or claim to complete a form; ask for the actual template/process or offer to track the task.

When the user asks "what needs my attention?" use get_attention_brief and answer crisply with priorities.

When a question asks about a Google Drive file, document, spreadsheet, or information likely recorded in Drive, use search_google_drive with concise identifying terms. Base answers on returned file content and cite the file name and link when available. A result with content_status "folder" is a folder, not a document-read failure; identify it as a folder and do not say its content could not be read. Say when a document match is metadata-only or could not be read.

When creating/updating work, use tools. Prefer structured updates over chat-only advice.

Be concise, decisive, and operational. Date context is provided in the user message / brief.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_attention_brief",
            "description": "Portfolio attention brief: overdue, due today, follow-ups, waiting-on, decisions, P0, approvals.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_businesses",
            "description": "List all active businesses in the portfolio.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_business_context",
            "description": "Get full context/knowledge for one business by id or slug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_id": {"type": "string"},
                    "slug": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_business",
            "description": "Add a new business to the portfolio without rebuilding the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "context_md": {"type": "string"},
                    "slug": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_business_context",
            "description": "Update a business description or context memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_id": {"type": "string"},
                    "description": {"type": "string"},
                    "context_md": {"type": "string"},
                },
                "required": ["business_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks, optionally filtered by business or status. open_only defaults true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_id": {"type": "string"},
                    "status": {"type": "string"},
                    "open_only": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a tracked action/task in the OS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_id": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string", "enum": list(db.CATEGORIES)},
                    "owner": {"type": "string"},
                    "priority": {"type": "string", "enum": list(db.PRIORITIES)},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD"},
                    "status": {"type": "string", "enum": list(db.TASK_STATUSES)},
                    "next_follow_up": {"type": "string"},
                    "waiting_on": {"type": "string"},
                    "notes": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["business_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Update fields on an existing task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "owner": {"type": "string"},
                    "priority": {"type": "string"},
                    "deadline": {"type": "string"},
                    "status": {"type": "string"},
                    "next_follow_up": {"type": "string"},
                    "waiting_on": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_knowledge_note",
            "description": "Store a durable note in business or portfolio memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "business_id": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_approval",
            "description": "Queue an important commercial action for human approval before execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "action_type": {"type": "string", "enum": list(db.APPROVAL_TYPES)},
                    "business_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_approvals",
            "description": "List actions waiting for owner approval.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_gmail",
            "description": "Read recent inbox email signals from the authenticated user's connected Gmail account.",
            "parameters": {
                "type": "object",
                "properties": {"max_results": {"type": "integer", "minimum": 1, "maximum": 15}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_gmail_draft",
            "description": "Create a Gmail draft only. This does not send the email. Use only after recipient, subject, and body are known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body_text": {"type": "string"},
                },
                "required": ["to", "subject", "body_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_calendar_events",
            "description": "Read upcoming events from the authenticated user's connected Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 30}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_google_drive",
            "description": "Search the authenticated user's Google Drive for files matching one or more terms and read Google Docs, Google Sheets, or plain-text files. Returns file metadata and actual text content when supported. Folders are returned with content_status 'folder' and are not document-read failures; other unsupported types are metadata-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_text": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
            },
        },
    },
]


def _client() -> OpenAI:
    if not settings.gemini_api_key or settings.gemini_api_key.startswith("your_"):
        raise RuntimeError(
            "GEMINI_API_KEY missing. Copy .env.example to .env and set your Google Gemini key."
        )
    return OpenAI(api_key=settings.gemini_api_key, base_url=settings.gemini_base_url)


def _get_business_context(args: dict[str, Any]) -> Any:
    biz = None
    if args.get("business_id"):
        biz = db.get_business(args["business_id"])
    elif args.get("slug"):
        biz = db.get_business_by_slug(args["slug"])
    if not biz:
        return {"error": "business not found"}
    notes = db.list_notes(business_id=biz["id"], limit=20)
    tasks = db.list_tasks(business_id=biz["id"], open_only=True, limit=50)
    return {"business": biz, "notes": notes, "open_tasks": tasks}


def _run_tool(
    name: str,
    args: dict[str, Any],
    user_id: Optional[str] = None,
) -> Any:
    try:
        if name == "get_attention_brief":
            return db.attention_brief()
        if name == "list_businesses":
            return db.list_businesses()
        if name == "get_business_context":
            return _get_business_context(args)
        if name == "add_business":
            return db.create_business(
                name=args["name"],
                description=args.get("description", ""),
                context_md=args.get("context_md", ""),
                slug=args.get("slug"),
            )
        if name == "update_business_context":
            return db.update_business(
                args["business_id"],
                description=args.get("description"),
                context_md=args.get("context_md"),
            )
        if name == "list_tasks":
            return db.list_tasks(
                business_id=args.get("business_id"),
                status=args.get("status"),
                open_only=args.get("open_only", True),
            )
        if name == "create_task":
            return db.create_task(
                business_id=args["business_id"],
                title=args["title"],
                category=args.get("category", "other"),
                owner=args.get("owner", "me"),
                priority=args.get("priority", "P2"),
                deadline=args.get("deadline"),
                status=args.get("status", "open"),
                next_follow_up=args.get("next_follow_up"),
                waiting_on=args.get("waiting_on", ""),
                notes=args.get("notes", ""),
                source=args.get("source", "cos_agent"),
            )
        if name == "update_task":
            return db.update_task(
                args["task_id"],
                **{k: v for k, v in args.items() if k != "task_id"},
            )
        if name == "add_knowledge_note":
            return db.add_note(
                title=args["title"],
                body=args["body"],
                business_id=args.get("business_id"),
                tags=args.get("tags"),
            )
        if name == "request_approval":
            return db.create_approval(
                summary=args["summary"],
                action_type=args.get("action_type", "other"),
                business_id=args.get("business_id"),
                task_id=args.get("task_id"),
                payload=args.get("payload"),
            )
        if name == "list_pending_approvals":
            return db.list_approvals("pending")
        if name == "create_gmail_draft":
            if not user_id:
                return {"error": "No authenticated Google account is associated with this conversation."}
            missing = [
                field
                for field in ("to", "subject", "body_text")
                if not isinstance(args.get(field), str) or not args[field].strip()
            ]
            if missing:
                return {
                    "error": "Missing required email draft details: "
                    + ", ".join(missing)
                    + "."
                }
            from app.integrations import google_services

            return google_services.create_gmail_draft(
                user_id,
                to=args["to"].strip(),
                subject=args["subject"].strip(),
                body_text=args["body_text"].strip(),
            )
        if name in ("get_recent_gmail", "get_upcoming_calendar_events"):
            if not user_id:
                return {"error": "No authenticated Google account is associated with this conversation."}
            from app.integrations import google_services

            if name == "get_recent_gmail":
                return google_services.fetch_recent_emails(
                    user_id,
                    max_results=max(1, min(int(args.get("max_results", 10)), 15)),
                )
            return google_services.fetch_upcoming_events(
                user_id,
                days=max(1, min(int(args.get("days", 7)), 30)),
            )
        if name == "search_google_drive":
            if not user_id:
                return {"error": "No authenticated Google account is associated with this conversation."}
            from app.integrations.google_drive import fetch_recent_drive_files

            return fetch_recent_drive_files(
                user_id,
                search_text=str(args.get("search_text", "")),
                max_results=max(1, min(int(args.get("max_results", 5)), 10)),
            )
        return {"error": f"Unknown tool: {name}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def chat(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    user_id: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run a Chief of Staff turn with tool loop. Returns assistant text + tool trace."""
    client = _client()
    brief = db.attention_brief()
    businesses = db.list_businesses()
    logger.info(
        "[CONTEXT] Chief of Staff portfolio context loaded businesses=%s",
        len(businesses),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Portfolio snapshot (JSON):\n"
                + json.dumps(
                    {
                        "businesses": [
                            {"id": b["id"], "name": b["name"], "slug": b["slug"]}
                            for b in businesses
                        ],
                        "attention_summary": brief["summary"],
                    },
                    ensure_ascii=False,
                )
            ),
        },
    ]
    if history:
        for m in history[-16:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_message})

    tool_trace: list[dict[str, Any]] = []
    final_text = ""

    for _ in range(8):
        logger.info("[AI] Gemini request started model=%s", settings.gemini_model)
        resp = client.chat.completions.create(
            model=settings.gemini_model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
        )
        logger.info("[AI] Gemini request completed")
        choice = resp.choices[0]
        msg = choice.message

        if msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                            # Gemini requires its thought signature echoed back with tool calls.
                            **(
                                {"extra_content": tc.model_extra["extra_content"]}
                                if (tc.model_extra or {}).get("extra_content")
                                else {}
                            ),
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _run_tool(tc.function.name, args, user_id=user_id)
                tool_trace.append({"tool": tc.function.name, "args": args, "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:12000],
                    }
                )
            continue

        final_text = msg.content or ""
        break

    draft_errors = [
        trace["result"]["error"]
        for trace in tool_trace
        if trace["tool"] == "create_gmail_draft"
        and isinstance(trace.get("result"), dict)
        and trace["result"].get("error")
    ]
    if draft_errors:
        final_text = f"I couldn't create the Gmail draft: {draft_errors[-1]}"

    if persist:
        db.save_message("user", user_message)
        db.save_message("assistant", final_text, meta={"tools": [t["tool"] for t in tool_trace]})
    return {"reply": final_text, "tools_used": tool_trace}
