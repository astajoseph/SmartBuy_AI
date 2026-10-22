# Accounts, admin console and saved assistant conversations

Added 2026-10-23. Sign-in uses **Supabase Auth** (e-mail + password). Everything a signed-in user does in
the assistant, the purchase advisor and the notification / price-alert part is saved to their account and
shown in their history. Admins can see every user's activity and conversations and manage accounts.

## What users get

| Page | What it does |
|---|---|
| `/login` | Sign in, create an account (8+ character password, Supabase sends a confirmation e-mail), or request a password-reset link |
| `/reset-password` | Opened from the reset e-mail; choose a new password |
| `/advisor` | Requires sign-in. Recommendations and assistant questions are saved. Left column lists the user's conversations; the last one reopens automatically |
| `/settings` | Requires sign-in. Target prices are saved to the account (used for alerts); viewing notifications is recorded |
| `/account` | Conversations (continue / delete), full history with filter and "load more", price alerts, profile (display name) |

Dashboard, product, vendor, review and sale pages stay public.

## What admins get (`/admin`, admin accounts only)

* **Overview**: account count, admins, active users (24 h / 7 days), questions in the last 7 days, disabled
  accounts, failed answers, activity by type.
* **Users**: search, role and status, last seen, conversation and activity counts. *Make admin / Remove admin*,
  *Disable / Enable*. Opening a user shows their conversations (read the full transcript), price alerts and
  history. Admins have their own history and conversations like any user.
* **All activity**: every user's activity, newest first, filterable by type, with links to the conversations.
* **Operations**: the existing source health, manual collection, URL test, vendor cache and job log tools.

Safeguards: an admin cannot disable or demote themselves, and the last active admin cannot be removed.
Every role / status change is recorded as an `admin_action` in the acting admin's history (with the
target account). A disabled user can still sign in to Supabase, but every SmartBuy API request is refused (403).

### Making the first admin

Put the e-mail in `ADMIN_EMAILS` (comma-separated) in `.env`. The account becomes an admin the next time it
signs in. Further admins can be promoted from the Users tab. `ADMIN_API_TOKEN` still works for scripts
and automation, and the Admin page falls back to the token box when Supabase Auth is not configured.

## Interrupted connections: how a conversation continues

1. The browser gives each question a random `request_id` and puts it in an **outbox** in `localStorage`
   before sending it. The draft you are typing is saved too.
2. The server stores the question and a *pending* answer row (`chat_messages`), then generates the answer in a
   task that keeps running even if the browser disconnects, so the answer is saved in any case.
3. If the request fails (offline, server unreachable, 5xx, 429), the page shows *"Your question is saved and
   will be sent automatically…"* and retries with backoff (2 s doubling to 30 s), immediately when the browser
   comes back online or the tab becomes visible again, and when the page is reopened.
4. A retried `request_id` is never answered twice: the server returns the saved answer, waits for the one still
   being generated, or re-generates it if the earlier attempt failed or stopped for more than 3 minutes (for
   example after a server restart). A failed answer shows a **Retry** button.
5. **Context**: each answer is generated with the earlier completed messages of the same conversation, loaded
   from the database, and the conversation remembers its product. A resumed, reloaded or reopened
   conversation therefore continues with the context of the previous questions.

## Data (migration `20261023000001_accounts.sql`, schema `smartbuy`)

| Table | Content |
|---|---|
| `profiles` | one row per Supabase user: e-mail, display name, role (`user`/`admin`), `is_disabled`, created / last seen |
| `activity_log` | `sign_in`, `assistant_chat`, `recommendation`, `notifications_viewed` (at most every 30 min), `target_saved`, `admin_action` with a short summary and JSON details |
| `chat_sessions` | conversations (title from the first question, product, soft delete) |
| `chat_messages` | questions and answers; `unique (session_id, request_id, role)` makes retries idempotent; answer status `pending` / `complete` / `failed` |

RLS is enabled on all four tables; through Supabase's own APIs users could only read their own rows. The
backend connects with the privileged role and checks ownership itself.

## API

| Method & path | Who |
|---|---|
| `GET/POST /api/me`, `POST /api/me/sign-in`, `GET /api/me/activity`, `GET /api/me/targets` | signed-in user |
| `GET/POST /api/assistant/sessions`, `GET /api/assistant/sessions/{id}`, `POST …/{id}/rename`, `DELETE …/{id}` | signed-in user (own conversations only) |
| `POST /api/assistant/sessions/{id}/messages` `{request_id, message, product_id?, preferences?}` | signed-in user; safe to retry |
| `GET /api/admin/stats`, `/api/admin/users`, `/api/admin/users/{id}`, `/api/admin/activity`, `/api/admin/chats/{id}`, `/api/admin/whoami` | admin |
| `POST /api/admin/users/{id}/role`, `POST /api/admin/users/{id}/disabled` | admin |

Sign-in tokens are verified in `backend/app/security/permissions.py` with the project's public signing keys
(`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, ES256 for this project), with issuer and audience checks.
Projects still on the legacy shared secret can set `SUPABASE_JWT_SECRET` (HS256) instead.

## Configuration

| Variable | Where | Value |
|---|---|---|
| `SUPABASE_URL` | `.env` (backend, and docker compose for the frontend build) | `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | `.env` (docker compose passes it to the frontend build) | the public anon / publishable key |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `frontend/.env.local` for `npm run dev` | same two values |
| `ADMIN_EMAILS` | `.env` | e-mails that become admins on sign-in |

In the Supabase dashboard (Authentication → URL Configuration) set the **Site URL** to where the frontend runs
(e.g. `http://localhost:3100`) and add `…/login` and `…/reset-password` to the redirect URLs, so the
confirmation and reset e-mails link back to the app. Never put the service-role key in the frontend.

## Tests

`backend/tests/test_accounts.py` (8 tests): sign-in creates the profile and refuses expired / wrongly signed
tokens; ES256 tokens are verified with the signing keys and the issuer; admin access by role and by
`ADMIN_EMAILS`; disable / promote and the last-admin protection; conversations are saved, a retried
`request_id` is answered once, follow-up questions get the earlier turns as context; failed or abandoned
answers are re-claimed; an answer is still saved when the request is cancelled mid-way (dropped connection);
advisor, notification and target activity is written to the history.
