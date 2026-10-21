# The Social Links (TSL)

Team workspace for shared boards, task cards, calendar events, notifications, and a Gemini-powered portfolio assistant. Existing businesses are presented as boards and their tasks as cards.

## What’s fully working after this build

| Capability | Status |
|---|---|
| Single CoS chat interface (web UI) | ✅ |
| Business-backed boards + task cards | ✅ |
| Task comments, @mentions, assignments, and recipient-scoped notifications | ✅ |
| Google Calendar upcoming events (read-only) | ✅ (needs Google credentials and calendar consent) |
| Task OS: Business / Task / Owner / Priority / Deadline / Status / Next follow-up / Notes / Source | ✅ |
| Categories: NPD, line forms, supplier/client, retailer/distributor outreach, decisions, waiting-on, delegated, etc. | ✅ |
| Attention brief: urgent, overdue, due today, follow-ups, waiting-on, decisions, P0 | ✅ |
| Approval gate for commercial actions | ✅ |
| Gemini tool-calling agent (create/update tasks, memory, brief) | ✅ |
| Demo seed data so you can try immediately | ✅ |
| Gmail + Calendar OAuth, email signals, and Gmail draft creation | ✅ (needs Google Cloud credentials and Gmail draft consent) |
| Auto-send commercial emails | ❌ by design — approvals only in MVP |
| Full autonomous “run the company” | ❌ — human-in-loop MVP |

## Quick start

Requires **Python 3.12+** (tested on 3.14). On Windows, prefer `py -3.14`.

```bash
cd ai-chief-of-staff
py -3.14 -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Or double-click `run.bat` after editing `.env`.

Edit `.env` and set:

```
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-3.8-flash
```

Run:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

Try: **“What needs my attention right now?”**

For local development only, set `AUTH_BYPASS_LOCAL=true` in `.env` to skip the login screen when the app is bound to a loopback address. This bypass is disabled by default and should remain disabled on exposed or deployed instances.

## Google (optional)

1. Google Cloud Console → OAuth client (Web)  
2. Redirect URI: `http://127.0.0.1:8000/api/google/callback`  
3. Put `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`  
4. Click **Connect Google** in the UI  
5. **Pull email signals** / **Pull calendar** — email becomes proposed actions, not an inbox dump  

The homepage Upcoming Events panel uses workspace events (brand-linked scheduled posts, shoots, meetings, deadlines, and launches). Google Calendar pull remains available as a separate sync signal.

## Team workspace scope

- Boards reuse the existing business records; cards reuse the existing task records.
- Active users can see and update the shared workspace. Board-level membership, private boards, and invitation workflows are not implemented; user accounts are managed by an administrator.
- Comments can mention active users by `@username`. Mentions and task assignments create notifications. Notification sound is off by default and its preference is saved per user.
- The existing `new_line_form` task category is available, but no external new-line-form template or submission workflow is included.
- Run the automated suite with `python -m unittest discover -s tests`.

## Brand logos

- Brand logos are stored under [data/brand_logos/](/Users/mc/Downloads/TSL/TSL/data/brand_logos) and served from `/uploads/brands/...`.
- Recommended formats: square PNG/SVG/WebP (JPEG also accepted), minimum 256×256.
- Maximum upload size is 4 MB.
- If a brand has no logo, the UI renders a colored initials fallback consistently in home tiles, sidebar, and brand header.

## Architecture

```
You  →  Web CoS UI  →  FastAPI  →  Gemini (tools)  →  SQLite OS
                                      ↓
                         businesses · tasks · notes · approvals
                                      ↓
                         Gmail / Calendar (signals in)
```

Docs: `docs/SYSTEM.md`, `docs/EXPANSION.md`, `docs/DEPLOY.md` (VPS), `docs/DEPLOY_CPANEL.md` (Hostlico), **`docs/DEPLOY_RENDER.md`** (recommended live host)

## Security note

Never commit `.env` or paste API keys into chat logs you share publicly.

## Team Chat (Slack)

Each user connects their own Slack account from **Team Chat** in the sidebar and can read and send messages without leaving the app. Tokens are encrypted at rest (same scheme as Google).

1. Create an app at <https://api.slack.com/apps> ("From scratch").
2. **OAuth & Permissions** → add the redirect URL `http://localhost:8000/api/slack/callback` (production: `https://<your-domain>/api/slack/callback`; Slack requires HTTPS for non-localhost URLs).
3. Under **User Token Scopes** add: `channels:read groups:read im:read mpim:read channels:history groups:history im:history mpim:history chat:write users:read`.
4. Put **Client ID** and **Client Secret** in `.env` as `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET`, then restart. Open the app at `http://localhost:8000` (not `127.0.0.1`) so the redirect matches.

Messages refresh every 5 seconds while the Team Chat page is open.
