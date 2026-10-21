# Expansion roadmap

Built for growth without rewriting the core OS.

## Near-term (next builds)

1. **Per-business email routing** — map senders/domains → business_id before task create  
2. **Meeting → actions** — after calendar event, CoS prompts for action capture  
3. **Scheduled routines** — cron/APScheduler: daily brief Slack/WhatsApp push  
4. **Approved send** — after approval, optional Gmail send of draft (still gated)  
5. **Notion / Sheets sync** — mirror tasks for team visibility  
6. **Multi-user owners** — replace free-text `owner` with people table  

## Integration pattern

1. Ingest signal (email, form, webhook, spreadsheet)  
2. Normalize to task/approval proposal  
3. CoS or human assigns business + priority  
4. Persist in SQLite (or later Postgres)  
5. Surface via attention brief  

Never short-circuit to “inbox summary bot” — always land in the OS.

## Suggested stack upgrades

- SQLite → Postgres when multi-device  
- Add Redis queue for ingest workers  
- Add audit log table for every tool write  
- Swap UI to React only if you need richer boards; current SPA is enough for operator use  

## Gemini Bot / agent notes

This MVP uses the **Google Gemini API** as the reasoning + tool layer (same pattern as production agent systems: model + tools + durable state).

If you also use Gemini inside X/Twitter “Gemini bots”, treat those as optional front-doors that POST into `/api/chat` — keep this repo as the system of record.
