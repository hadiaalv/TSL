# System documentation — AI Chief of Staff MVP

## Purpose

One primary AI interface that understands the portfolio and manages ongoing workload across businesses.

Email is an **input signal**, not the product. The product is the operating system: attention, tasks, decisions, waiting-on, approvals.

## Components

### 1. Web UI (`app/static`)

- Left rail: attention stats, businesses, pending approvals, Google hooks  
- Center: CoS chat (Gemini)  
- Right: open work board  

### 2. API (`app/main.py`)

| Endpoint | Role |
|---|---|
| `POST /api/chat` | Chief of Staff turn (Gemini + tools) |
| `GET /api/brief` | Attention brief |
| `GET/POST /api/businesses` | Portfolio businesses |
| `GET/POST/PATCH /api/tasks` | Work items |
| `GET /api/approvals` + decide | Commercial approval gate |
| `GET /api/google/*` | OAuth, email signals, calendar, and Drive file/content pull |

### 3. Agent (`app/agent`)

Gemini via its OpenAI-compatible API. Tools:

- `get_attention_brief`
- `list_businesses` / `get_business_context` / `add_business` / `update_business_context`
- `list_tasks` / `create_task` / `update_task`
- `add_knowledge_note`
- `request_approval` / `list_pending_approvals`
- `get_recent_gmail` / `create_gmail_draft` / `get_upcoming_calendar_events` / `search_google_drive`

### 4. Data (`app/db.py` → SQLite `data/cos.db`)

**Business:** name, slug, description, `context_md` (per-business memory)

**Task fields:** business, title, category, owner, priority, deadline, status, next_follow_up, waiting_on, notes, source, source_ref

**Statuses:** open, in_progress, waiting_on, delegated, blocked, done, cancelled  

**Priorities:** P0–P3  

**Approvals:** pending → approved/rejected (commercial outreach, pricing, commitment, external email)

## Routines (MVP)

1. **Morning attention** — ask CoS for brief (or hit `GET /api/brief`)  
2. **Email signal pull** — Gmail → proposed follow-ups → human/CoS confirms business + creates tasks  
3. **Calendar pull** — upcoming meetings as context for meeting actions  
4. **Drive pull/search** — the Google panel's Pull Drive action fetches the 10 most recently modified files and supported content; the CoS agent can search Drive for relevant Docs, Sheets, and plain-text file contents; unsupported file types return metadata only  
5. **Approval queue** — commercial actions stay blocked until you approve in UI

Gmail inbox reads use `gmail.readonly`; draft creation uses `gmail.compose` and only calls Gmail's draft-creation API, never its send API. Google grants sending capability at the OAuth-scope level with `gmail.compose`, but the app exposes no send tool or send call. Calendar and Drive remain read-only. Existing Google users must reconnect and consent to Gmail draft access before the draft tool can work. Drive uses `drive.readonly`; no Drive-specific environment variables are required.

Each CoS user has one connected Google account. Connecting a different account replaces that user's current connection; simultaneous personal and business Google accounts are not supported.

WhatsApp and browser messages use separate history tables and conversation contexts. WhatsApp calls the same CoS agent and portfolio tools, but its messages do not appear in browser chat history.

## Demo seed

On first boot, three businesses and sample overdue / decision / supplier / outreach / approval rows are created so the brief is immediately useful.

## Config

See `.env.example`. Required for chat: `GEMINI_API_KEY`. Google fields optional.
