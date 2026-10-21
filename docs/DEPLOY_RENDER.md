# Deploy on Render

Hostlico/cPanel se better option for this FastAPI + SQLite CoS app.

## What you need

- GitHub/GitLab account (repo push)
- [Render](https://render.com) account
- Your `.env` values: `GEMINI_API_KEY`, auth password, Google (optional)

---

## A) Push code to GitHub

Local PowerShell:

```powershell
cd "D:\New folder (2)\ai-chief-of-staff"
git init
git add app docs requirements.txt Dockerfile render.yaml README.md .env.example .gitignore passenger_wsgi.py cos_wsgi.py
git commit -m "Prepare Render deploy"
```

GitHub pe naya repo banao, phir:

```powershell
git remote add origin https://github.com/YOUR_USER/ai-chief-of-staff.git
git branch -M main
git push -u origin main
```

**`.env` mat push karo** (gitignore me hai).

---

## B) Create Web Service on Render

1. https://dashboard.render.com → **New** → **Web Service**
2. Connect GitHub repo `ai-chief-of-staff`
3. Settings:
   - **Runtime:** Docker
   - **Instance:** Free (demo) or Starter (production)
   - **Health Check Path:** `/api/health`
4. **Create Web Service**

After first deploy you get a URL like:

`https://ai-chief-of-staff-xxxx.onrender.com`

---

## C) Environment variables (Render → Environment)

| Key | Value |
|-----|--------|
| `GEMINI_API_KEY` | `your-gemini-key` |
| `GEMINI_MODEL` | `gemini-3.8-flash` |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `DATA_DIR` | `/app/data` |
| `SEED_DEMO` | `false` |
| `PUBLIC_BASE_URL` | `https://ai-chief-of-staff-xxxx.onrender.com` |
| `AUTH_COOKIE_SECURE` | `true` |
| `AUTH_SECRET` | long random (or Generate) |
| `AUTH_USERNAME` | `admin` |
| `AUTH_PASSWORD` | strong password |
| `AUTH_SESSION_HOURS` | `168` |
| `GOOGLE_CLIENT_ID` | (optional) |
| `GOOGLE_CLIENT_SECRET` | (optional) |
| `GOOGLE_REDIRECT_URI` | `https://ai-chief-of-staff-xxxx.onrender.com/api/google/callback` |

**Save** → service auto-redeploys.

---

## D) Google Console (if using Gmail/Calendar)

Authorized redirect URI add:

`https://YOUR-RENDER-URL/api/google/callback`

---

## E) Open the app

1. `https://YOUR-RENDER-URL` → login page  
2. Sign in with `AUTH_USERNAME` / `AUTH_PASSWORD`  
3. Try: *What needs my attention?*

---

## Free vs Paid

| | Free | Paid (Starter+) |
|--|------|-----------------|
| Always on | Spins down after idle (~cold start) | Always on |
| SQLite persist | May reset on redeploy | Add **Disk** mount `/app/data` |
| Client demo | OK | Better |

**Add disk (paid):** Render service → Disks → mount path `/app/data`, size 1 GB.

---

## Alternate: Blueprint

Repo me `render.yaml` hai.

Dashboard → **New** → **Blueprint** → select repo → fill secret env vars → Apply.

---

## Logs / debug

Render → service → **Logs**

Health check fail ho to usually env missing (`AUTH_PASSWORD` / bad Docker build).

---

## Custom domain (optional)

Render → Settings → Custom Domains → `cos.yourdomain.com`  
Phir `PUBLIC_BASE_URL` aur Google redirect us domain pe update karo.
