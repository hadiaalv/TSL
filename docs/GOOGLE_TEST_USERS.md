# Google Connect — fix Error 403 (Testing mode)

Client error:

> Access blocked: AI INTERFACE has not completed the Google verification process  
> Error 403: access_denied  
> Only developer-approved testers can access

## Cause

OAuth consent screen is in **Testing**. Any Google account not listed under **Test users** is blocked.

## Fix (5 minutes)

1. Open [Google Cloud Console](https://console.cloud.google.com/)
2. Select the project that owns the OAuth client (**AI INTERFACE** / whatever name shows on the error)
3. **APIs & Services** → **OAuth consent screen**
4. Confirm status is **Testing** (do not need full verification for MVP)
5. Scroll to **Test users** → **+ ADD USERS**
6. Add every account that will click **Connect Google**, for example:
   - `dreamital.web@gmail.com`
   - `mitchnutrition@hotmail.com`
   - client Workspace email (if different)
7. **Save**
8. Also check **Credentials** → OAuth client → **Authorized redirect URIs** includes exactly:
   - `https://nathancoxconsulting.tech/api/google/callback`
   - (and Render URL callback if still used)
9. Client opens CoS → **Log out of other Google accounts** or use Incognito → **Connect Google account** with a **listed test user** email → Allow

## If it still fails

- Wrong Google account signed in (not a test user)
- Redirect URI mismatch (http vs https, trailing slash, wrong domain)
- Gmail API / Calendar API not enabled for the project

## Later (optional)

Publishing app to **Production** removes the test-user limit but may require Google verification for sensitive scopes (`gmail.readonly`, `gmail.compose`, `calendar.readonly`). Existing users must reconnect and consent to `gmail.compose` before Gmail drafts can be created. For a single-client MVP, **Testing + test users** is normal and fine.
