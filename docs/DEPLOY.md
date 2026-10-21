# Deploy — AI Chief of Staff (live server)

Pehle local pe `127.0.0.1` se chalna theek hai. Live pe yeh commonly toot-ta hai:

1. App sirf `127.0.0.1` pe bind → bahar se open nahi hoti
2. Process SSH band hote hi mar jati hai (no systemd/docker)
3. Google redirect abhi bhi `localhost` pe hai
4. HTTPS ke bina secure cookies / OAuth mess
5. Firewall port 80/443 band

Yeh guide **Ubuntu VPS + Docker + Nginx + HTTPS** assume karti hai (recommended).

---

## 0) Aapke paas kya hona chahiye

- VPS (Ubuntu 22.04/24.04) + SSH access
- Domain (e.g. `cos.yourdomain.com`) → A record to server IP
- `.env` values: `GEMINI_API_KEY`, `AUTH_*`, Google (optional)

---

## 1) Server pe project

```bash
ssh root@YOUR_SERVER_IP
apt update && apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx git
mkdir -p /opt/ai-chief-of-staff
# upload code (git clone OR scp from your PC)
cd /opt/ai-chief-of-staff
```

Windows se upload (PowerShell):

```powershell
scp -r "D:\New folder (2)\ai-chief-of-staff\*" root@YOUR_SERVER_IP:/opt/ai-chief-of-staff/
```

`.venv` aur local `data/` mat bhejo agar possible ho — server pe fresh better.

---

## 2) Production `.env` on server

```bash
cd /opt/ai-chief-of-staff
nano .env
```

Minimum:

```env
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-3.8-flash
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

APP_HOST=0.0.0.0
APP_PORT=8000
DATA_DIR=/app/data
SEED_DEMO=false

PUBLIC_BASE_URL=https://cos.yourdomain.com
AUTH_COOKIE_SECURE=true
AUTH_SECRET=long-random-string
AUTH_USERNAME=admin
AUTH_PASSWORD=strong-password-here
AUTH_SESSION_HOURS=168

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://cos.yourdomain.com/api/google/callback
GOOGLE_DRIVE_PORTFOLIO_FOLDER_ID=verified_drive_folder_id
GOOGLE_DRIVE_BRAND_FOLDER_MAP={}
```

`PUBLIC_BASE_URL` set ho to Google callback auto usi domain pe use hota hai.

---

## 3) Docker se app chalao

```bash
cd /opt/ai-chief-of-staff
docker compose up -d --build
docker compose ps
curl -I http://127.0.0.1:8000/login
```

Data persist: Docker volume `cos_data`.

---

## 4) Nginx + HTTPS

```bash
cp deploy/nginx.conf.example /etc/nginx/sites-available/ai-chief-of-staff
# edit domain name inside the file
nano /etc/nginx/sites-available/ai-chief-of-staff
ln -sf /etc/nginx/sites-available/ai-chief-of-staff /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d cos.yourdomain.com
```

Open firewall if needed:

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

---

## 5) Google Console (live)

OAuth client me **Authorized redirect URI** add karo:

`https://cos.yourdomain.com/api/google/callback`

Localhost wali URI rakh sakte ho testing ke liye; live alag URI chahiye.

Enable **Google Drive API** in the same Google Cloud project as the OAuth client.
The app requests Gmail read-only and Gmail draft (`gmail.compose`) scopes,
Calendar read-only, and Drive read-only scopes. Draft creation never sends email.
After reconnecting, the app checks Drive with a real `files.list` request; a token
record alone is not treated as verified Drive access. Existing users should use
**Reconnect Google account** and approve the requested permissions, including
Gmail draft access if drafting is needed.

`PUBLIC_BASE_URL` takes precedence when building the callback:
`<PUBLIC_BASE_URL>/api/google/callback`. Keep that exact HTTPS URL in both the
production environment and the OAuth client's authorized redirect URIs.
`GOOGLE_REDIRECT_URI` is the fallback for local development.
Set `GOOGLE_DRIVE_PORTFOLIO_FOLDER_ID` to the ID from the verified portfolio
folder URL in the connected Google account. Brand matching is constrained to
folders beneath that root; when no root ID is configured the import reports
`portfolio_not_configured` and does not match folders elsewhere in Drive.

Brand Drive sync is link-only: it records each file's name, type, modified date,
and clickable Drive URL in that brand without downloading or parsing content.
This supports Office documents, PDFs, images, and other formats without
format-specific extraction. Opening a link still requires the Google account
to have access. Folders are paginated recursively without a per-brand
100-file cutoff.

When a brand folder name is missing or duplicated, the import reports candidate
folder IDs and links and does not guess. Set `GOOGLE_DRIVE_BRAND_FOLDER_MAP` to
a JSON object mapping exact brand names (or business IDs) to folder IDs beneath
the portfolio root, for example:

```env
GOOGLE_DRIVE_BRAND_FOLDER_MAP='{"Example Brand":"drive_folder_id"}'
```

Sync metadata and source file IDs are stored in the existing SQLite data
directory; schema upgrades run automatically at startup.
Per-user access tokens, refresh tokens, and the stored OAuth client secret are
encrypted in SQLite using a key derived from `AUTH_SECRET`. Keep `AUTH_SECRET`
stable across restarts and deployments; changing it prevents decryption of
already stored credentials. Existing plaintext token rows are encrypted when
read, and a legacy token file is migrated to the per-user database when that
account reconnects.

---

## 6) Verify

1. https://cos.yourdomain.com → login page
2. Sign in with `AUTH_USERNAME` / `AUTH_PASSWORD`
3. Dashboard loads
4. Connect or reconnect Google and approve the requested scopes (including Gmail draft access if needed)
5. Confirm the Google panel says **Google + Drive verified**, then run **Load Drive into brands**

Logs:

```bash
docker compose logs -f --tail=100
```

Restart:

```bash
docker compose restart
```

---

## Alternate: systemd (no Docker)

```bash
cd /opt/ai-chief-of-staff
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# .env with DATA_DIR=/opt/ai-chief-of-staff/data and APP_HOST=0.0.0.0
cp deploy/cos.service.example /etc/systemd/system/cos.service
systemctl daemon-reload
systemctl enable --now cos
```

Phir same Nginx + certbot steps.

---

## Checklist agar pehle fail hua

| Symptom | Fix |
|---|---|
| Site open nahi / timeout | `APP_HOST=0.0.0.0`, firewall 80/443, nginx up |
| SSH band → site dead | Docker `restart: unless-stopped` or systemd |
| Login cookie nahi rehti | `AUTH_COOKIE_SECURE=true` + real HTTPS |
| Google redirect_uri_mismatch | Console + `.env` me live callback URL |
| 502 Bad Gateway | App not on 8000: `docker compose ps` / `systemctl status cos` |
| Blank / Failed to fetch | Browser HTTPS page calling HTTP API — use one domain behind nginx |
