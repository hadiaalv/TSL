# WhatsApp

WhatsApp is a front door to the existing Chief of Staff agent. Inbound senders are explicitly bound to one active CoS username; the agent uses the same Gemini model, portfolio tools, and knowledge as web chat. Gmail inbox, Calendar, and Drive reads are read-only. Gmail draft creation requires the separately consented `gmail.compose` scope and never sends.

## Environment

Set these on the application server only. Do not put them in frontend code or commit their values.

| Variable | Required | Value |
| --- | --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | Yes | Meta permanent/system-user bearer token with WhatsApp messaging permissions |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | Cloud API phone number ID used to send messages |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | No | WhatsApp Business Account ID for administration; the send endpoint does not require it |
| `WHATSAPP_VERIFY_TOKEN` | Yes | A long random string you choose and enter in both Meta and the server |
| `WHATSAPP_APP_SECRET` | Yes | Meta app secret from App Settings > Basic, used to verify `X-Hub-Signature-256` |
| `WHATSAPP_ALLOWED_PHONE` | Yes | One authorized sender's phone in international format, e.g. `+15551234567` |
| `WHATSAPP_USERNAME` | Yes | Exact username of the active existing CoS account that owns this WhatsApp conversation |
| `WHATSAPP_API_VERSION` | No | Graph API version, defaults to `v22.0` |

### 360dialog

Set `WHATSAPP_PROVIDER=360dialog` to select 360dialog. Keep `WHATSAPP_ALLOWED_PHONE` and `WHATSAPP_USERNAME` set as above; all incoming sender binding and agent behavior are shared between providers.

| Variable | Required | Value |
| --- | --- | --- |
| `D360_API_KEY` | Yes | 360dialog API key, stored only in the server environment |
| `D360_API_BASE_URL` | Yes | Sandbox: `https://waba-sandbox.360dialog.io/v1`; production: `https://waba-v2.360dialog.io` |
| `D360_WEBHOOK_AUTH` | Yes | `basic` for production or `token` for Sandbox |
| `D360_WEBHOOK_USERNAME` | For `basic` | Username for the Basic Authorization header configured in the 360dialog Hub |
| `D360_WEBHOOK_PASSWORD` | For `basic` | Strong password for that Basic Authorization header |
| `D360_WEBHOOK_TOKEN` | For `token` | High-entropy random token included as the webhook URL's `token` query parameter |

360dialog's documented text endpoint is `POST {D360_API_BASE_URL}/messages`, authenticated with `D360-API-KEY`. The app uses the documented WhatsApp `text` payload and the incoming sender as `to`. For the Sandbox, use its `/v1` base URL and remember its API key is limited to the phone number or business-scoped user ID associated with that key.

Webhook security: the application requires either a configured Basic Authorization header (`D360_WEBHOOK_AUTH=basic`) or a high-entropy URL token (`D360_WEBHOOK_AUTH=token`). For Sandbox, register the callback using the Sandbox API's `POST https://waba-sandbox.360dialog.io/v1/configs/webhook` endpoint with the API key in the `D360-API-KEY` header and the complete callback URL (including the token query parameter) as the JSON `url`. Production Hub configurations can use a configured Basic Authorization header. Do not publish or log the configured callback URL. Both modes require HTTPS. The docs mention `x-360dialog-signature` but do not specify its signing algorithm, signed bytes, or secret source; the app does not guess at an undocumented signature algorithm. Meta callbacks continue to use `X-Hub-Signature-256` when `WHATSAPP_PROVIDER=meta`.

Use the same callback path for either provider: `https://YOUR_PUBLIC_HOST/api/whatsapp/webhook`. For Sandbox token auth configure `https://YOUR_PUBLIC_HOST/api/whatsapp/webhook?token=YOUR_RANDOM_TOKEN`. For production Basic Auth, configure the callback path and the matching Authorization header in the 360dialog Hub. The webhook returns `200` after accepting a valid 360dialog payload and schedules its existing processing flow as a FastAPI background task.

The existing `GEMINI_API_KEY` / `GEMINI_MODEL` settings are still used. For Google questions, configure Google OAuth and connect that Google account while logged into the same CoS username named by `WHATSAPP_USERNAME`. The OAuth request includes Drive read-only and Gmail draft access; existing users must reconnect and grant the requested scopes. Google may require OAuth consent-screen verification for these scopes.

The app intentionally configures one allowed phone-to-user mapping per deployment. Additional senders must not be admitted by widening the allowlist without first adding explicit per-sender account mapping and administration.

## Meta setup (fallback)

1. In Meta for Developers, create or select a Business app, add the WhatsApp product, and complete the WhatsApp Business account and phone-number setup.
2. Create a production system-user access token with the required WhatsApp Business messaging permissions. Set it as `WHATSAPP_ACCESS_TOKEN`; use the displayed Phone Number ID for `WHATSAPP_PHONE_NUMBER_ID` and optionally record the WABA ID in `WHATSAPP_BUSINESS_ACCOUNT_ID`.
3. Set the app's Basic Settings secret as `WHATSAPP_APP_SECRET`. Choose a separate unpredictable `WHATSAPP_VERIFY_TOKEN` and set it in both Meta and the application environment.
4. Set `WHATSAPP_ALLOWED_PHONE` to the operator's sender number and `WHATSAPP_USERNAME` to their existing active CoS login.
5. Deploy with a public HTTPS application URL. Set Meta's callback URL to `https://YOUR_PUBLIC_HOST/api/whatsapp/webhook`, enter the same verify token, and subscribe the app to the `messages` webhook field.
6. Keep `WHATSAPP_PROVIDER=meta` and restart/redeploy after adding secrets. Meta's verification GET is served without login; message POSTs require a valid Meta HMAC signature and an allowlisted sender.

## 360dialog setup

1. For a first Sandbox test, follow the [360dialog Sandbox guide](https://docs.360dialog.com/docs/get-started/sandbox): message `START` to the documented Sandbox number and save the API key privately. Configure `WHATSAPP_PROVIDER=360dialog`, `D360_API_KEY`, `D360_API_BASE_URL=https://waba-sandbox.360dialog.io/v1`, `D360_WEBHOOK_AUTH=token`, and a high-entropy `D360_WEBHOOK_TOKEN`.
2. Deploy to an HTTPS host and register `https://YOUR_PUBLIC_HOST/api/whatsapp/webhook?token=YOUR_RANDOM_TOKEN` by POSTing `{"url":"https://YOUR_PUBLIC_HOST/api/whatsapp/webhook?token=YOUR_RANDOM_TOKEN"}` to `https://waba-sandbox.360dialog.io/v1/configs/webhook` with `D360-API-KEY: YOUR_API_KEY`. The app endpoint must be reachable publicly; use an HTTPS tunnel for local testing.
3. Set `WHATSAPP_ALLOWED_PHONE` to the exact Sandbox sender phone in international format (digits with optional leading `+`) and `WHATSAPP_USERNAME` to the active CoS username. Ensure the Sandbox key's fixed recipient matches the sender, as Sandbox sending is restricted to that recipient.
4. For a production 360dialog number, use `D360_API_BASE_URL=https://waba-v2.360dialog.io`, choose `D360_WEBHOOK_AUTH=basic`, and configure matching Basic Authorization credentials in the number's 360dialog Hub webhook settings. Restart/redeploy after changing server environment variables.

For Render, the production callback is `https://<your-render-service>.onrender.com/api/whatsapp/webhook` after the service's assigned hostname is known. For another host, substitute its HTTPS origin. `PUBLIC_BASE_URL` remains the public app origin; the webhook path itself is fixed.

## Behavior and checks

- Incoming text messages, sender/user binding, and successfully sent replies are stored in `whatsapp_messages`, separate from browser chat history. Duplicate provider message IDs are ignored by the existing database uniqueness constraint.
- The shared agent's existing task, business, approval, and attention tools are available. Gmail inbox, Calendar, and Drive reads use the bound user's connected Google account. The agent can create Gmail drafts only when the user has granted the `gmail.compose` scope; it calls the draft-creation API and never sends. Google grants sending capability at the OAuth-scope level, but the app exposes no send tool or send call. Existing Google users must reconnect and approve that scope before drafting works. Drive text retrieval is limited to Docs, Sheets exports, and plain-text files; other file types return metadata only.
- WhatsApp and browser chat use separate conversation histories. WhatsApp messages are stored in `whatsapp_messages`; browser chat uses `chat_messages`. They share the agent and portfolio data, but WhatsApp history is not shown in the web chat.
- There is no new-line-form template or form-completion integration in this project. The agent can track a new-line-form task, but needs the actual form and required workflow before it can complete one.
- The integration has no WhatsApp-specific email-send, price-change, calendar-write, invoice, or commercial-commitment capability. Existing approval requests remain available through the shared agent.
- Meta webhook signatures are validated against the exact raw request body. 360dialog callbacks require the selected Basic Auth or secret URL token. Logs do not include credentials or message text.
- Local automated coverage: `c:/python312/python.exe -m unittest discover -s tests`.
- Live acceptance: send a normal question, then a follow-up; ask for a named business and urgent tasks; ask for upcoming meetings, recent Gmail, and a known Drive document after connecting Google; send from a different phone and confirm it is ignored; temporarily use an invalid provider key to confirm the server records delivery failure without claiming success.