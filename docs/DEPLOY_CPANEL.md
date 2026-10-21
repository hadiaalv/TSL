# Deploy on Hostlico cPanel (Setup Python App + Passenger)

Target example: **https://demo.summitstaffing.com.au**

Yeh Docker/VPS guide nahi hai — cPanel Python App ke liye hai.

---

## cPanel settings (exact)

**Software → Setup Python App**

| Field | Value |
|---|---|
| Python version | 3.12.x |
| Application root | `demo.summitstaffing.com.au` |
| Application URL | `demo.summitstaffing.com.au` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Phir **Run Pip Install** → `requirements.txt`  
Phir **RESTART**

---

## Files that must exist in Application Root

```
demo.summitstaffing.com.au/
  passenger_wsgi.py      ← required
  requirements.txt
  .env                   ← production values
  app/
  data/                  ← writable (0755 or 0775)
  tmp/                   ← create empty; touch tmp/restart.txt to reload
  docs/
```

`run.bat` / `.venv` server pe zaroori nahi.

---

## Server `.env` (production)

Use `deploy/env.cpanel.example` as template. Critical lines:

```env
PUBLIC_BASE_URL=https://demo.summitstaffing.com.au
AUTH_COOKIE_SECURE=true
AUTH_SECRET=long-random-string
AUTH_USERNAME=admin
AUTH_PASSWORD=strong-password
GOOGLE_REDIRECT_URI=https://demo.summitstaffing.com.au/api/google/callback
GEMINI_API_KEY=your-gemini-key
```

---

## Upload / update code from Windows

File Manager zip upload **or** from PowerShell (if SSH enabled):

```powershell
# zip project without .venv
cd "D:\New folder (2)\ai-chief-of-staff"
```

File Manager me:
1. Upload zip into `demo.summitstaffing.com.au`
2. Extract (overwrite `app/`, `passenger_wsgi.py`, `requirements.txt`)
3. **Do not delete** server `.env` / `data/` unless intentional
4. Setup Python App → **Run Pip Install** → **RESTART**
5. Or File Manager: create/edit `tmp/restart.txt` (empty) to force Passenger reload

---

## Google Console

Add redirect URI:

`https://demo.summitstaffing.com.au/api/google/callback`

---

## Verify

1. Open https://demo.summitstaffing.com.au → should show **Sign in**
2. Login with `AUTH_USERNAME` / `AUTH_PASSWORD`
3. If error page: File Manager → `stderr.log` (bottom of file = latest)

---

## Common Hostlico/cPanel failures

| Problem | Fix |
|---|---|
| ModuleNotFoundError / a2wsgi | Run Pip Install on `requirements.txt`, Restart |
| 500 after upload | Check `stderr.log`; ensure `passenger_wsgi.py` Entry point = `application` |
| Login cookie lost | `AUTH_COOKIE_SECURE=true` + site must be **HTTPS** |
| Google redirect mismatch | Live callback URL in Google Console + `.env` |
| Permission denied on DB | `data/` folder writable by app user |
| Old code still running | RESTART app or `touch tmp/restart.txt` |
| Blank / wrong site | Confirm Application URL domain matches subdomain |

---

## After auth / Google changes

Har baar code update ke baad:

1. Upload new files  
2. Run Pip Install (if requirements changed)  
3. **RESTART** Python App  
