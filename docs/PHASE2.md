# Phase 2 — Production users + Google

## What’s new

- **Roles:** `super_admin`, `admin`, `operator`
- **Admin panel:** `/admin` — create users, set role/status
- **Per-user Google:** each login connects their own Gmail/Calendar
- Existing accounts are promoted to **super_admin** on migrate

## Google OAuth → Production (so more than test users can connect)

1. [Google Cloud Console](https://console.cloud.google.com/) → project with **AI INTERFACE**
2. **Google Auth platform** → **Audience**
3. Click **Publish app** (Testing → In production)
4. For Gmail/Calendar sensitive scopes, Google may require **Verification**  
   - Until verified, Google may show warnings or limit unverified apps  
   - For a closed consulting team, keep **Testing** + add each staff email as Test user (up to 100) is often enough
5. Confirm redirect URI: `https://nathancoxconsulting.tech/api/google/callback`

### Recommendation

| Team size | Approach |
|-----------|----------|
| ≤ 100 staff | Stay on **Testing** + Test users list (simplest) |
| Public / many users | **Publish** + verification if Google requires it |

## How operators use it

1. Super admin opens `/admin` → **Create user**
2. New user logs into CoS with their username/password
3. User clicks **Connect Google account** (their own Gmail)
4. **Pull email / Pull calendar** uses that user’s token

## Out of this Phase 2 slice

- WhatsApp integration (separate quote)
- Fine-grained permissions per business
- Audit log UI
