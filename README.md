# WorkPilot AI

WorkPilot AI is an email-and-password business workflow platform for customer enquiries and sales reporting. Each account gets a private workspace. Owners submit structured task briefs—not chat messages. The orchestrator creates a dependency-ordered plan, selects stored specialist profiles and tools at runtime, and returns sourced work for validation and human approval.

## Architecture

```text
Browser (Next.js + Tailwind, Vercel)
  ├─ Control Hub: New Task form, recent work, live task cards
  ├─ Agent Builder, Knowledge Hub, sales/enquiry import
  ├─ Approval Inbox, Activity Log, Dashboard, Sent Items, Settings
  └─ email/password sign-in + HttpOnly session cookie + same-origin API/SSE proxy
            │ HTTPS / JSON / SSE
            ▼
FastAPI (Render)
  ├─ LLM orchestrator ── database agent profiles ── dependency plan
  ├─ specialist tools ── policy retrieval ── LLM validator
  ├─ persisted task state machine, review gate, recovery, event log
  └─ database sample connector or configured SMTP adapter
            │ SQLAlchemy / psycopg
            ▼
PostgreSQL + pgvector
  ├─ users, revocable hashed sessions, private workspaces/settings
  ├─ agents, tasks, steps, events, enquiries, sales rows
  └─ documents, embedded chunks, sent items
```

## Local setup

1. Provision PostgreSQL with the `vector` extension. Copy `.env.example` to `.env` and set `DATABASE_URL`, `LLM_PROVIDER`, and that provider's server-only API key, base URL, and model variables. Keep provider secrets out of frontend variables. Set `FRONTEND_ORIGINS=http://localhost:3000` and `NEXT_PUBLIC_API_BASE_URL` for local development. For local HTTP, use `AUTH_COOKIE_SECURE=false` and `AUTH_COOKIE_SAMESITE=lax`; production uses HTTPS with `AUTH_COOKIE_SECURE=true` and `AUTH_COOKIE_SAMESITE=lax`.
2. Install and start the backend:

   ```powershell
   python -m pip install -r backend\requirements.txt
   Set-Location backend
   alembic upgrade head
   python -m app.seed_import
   uvicorn app.main:app --host 0.0.0.0 --port $env:PORT
   ```

3. In another shell:

   ```powershell
   Set-Location frontend
   npm install
   npm run dev
   ```

The backend runs migrations and idempotent seed import on startup when `SEED_ON_STARTUP=true`. Provider model names, system instructions, validation policy, retry limits, timeouts, retrieval settings (including chunk overlap), billing currency, token prices, and cost limits are database-backed and editable in Settings. Provider credentials remain server-side.

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `LLM_PROVIDER` | Selects the configured provider |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL` | OpenAI server configuration |
| `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL` | Gemini server configuration |
| `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_MODEL` | Groq server configuration |
| `FRONTEND_ORIGINS` | Allowed browser origins for CORS |
| `AUTH_COOKIE_NAME` | HttpOnly authentication cookie name |
| `AUTH_COOKIE_SECURE` | Require HTTPS for the authentication cookie |
| `AUTH_COOKIE_SAMESITE` | Cookie policy (`none` for cross-origin HTTPS, `lax` for local HTTP) |
| `AUTH_SESSION_DAYS` | Session lifetime, bounded by the backend |
| `NEXT_PUBLIC_API_BASE_URL` | Backend origin targeted by the Next.js same-origin proxy |
| `CONNECTOR_ADAPTER` | Selects the sample or SMTP connector |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | Optional real email connector |
| `SEED_ON_STARTUP`, `PORT` | Startup behavior and hosting port |

## Deploy

- **Backend:** Deploy `render.yaml` as a Render Blueprint. Configure PostgreSQL, a provider/API key, provider endpoint/model values, and the exact Vercel origin in `FRONTEND_ORIGINS`. The service runs Alembic migrations and seed import before serving `/health` and `/api/docs`. Health and API docs are public; business APIs require an authenticated cookie.
- **Frontend:** Deploy `frontend/` as a Next.js project on Vercel. Set `NEXT_PUBLIC_API_BASE_URL` to the backend origin and redeploy. Provider credentials must never be added to Vercel.

Public links: [WorkPilot website](https://work-pilot-ai-tawny.vercel.app) · [API docs](https://workpilot-ai-api.onrender.com/api/docs) · [API health](https://workpilot-ai-api.onrender.com/health).

## Two-minute demo

1. Open the public website and create an account with an email and a password of at least 12 characters. A private workspace is initialized with sample records.
2. In **Control Hub**, submit a new brief. Optionally select an agent. The plan, assigned agents, and task cards are generated and updated from the backend.
3. Upload a policy in **Knowledge Hub** and a sales CSV in **Business Data**. Ask for a response/report in a new task brief; the specialist tools retrieve document sources and calculate figures from stored sales rows.
4. Open **Approval Inbox**. Approve, edit and approve, or reject with feedback. Rejected work returns through the workflow.
5. Inspect the event trace in **Activity Log**, task metrics in **Dashboard**, and published items in **Sent Items**. Signing in with a second account shows an independent workspace.

## Hardcoding audit

The source audit found no credentials or provider model-name defaults. Provider endpoints and keys are supplied by server environment variables; frontend API origin is supplied by `NEXT_PUBLIC_API_BASE_URL`. Prompts, models, retry/time limits, retrieval settings (chunk size, overlap, top-k), rate/cost caps, billing currency, token prices, and example briefs are database settings. Task and step state names are centralized in the backend status enums and returned to the UI by `/health`; transition rules are centralized with the task enum. Product API paths, provider protocol identifiers, bounded input/safety ceilings, and CSS state selectors remain fixed implementation identifiers.

There is no chat UI, conversational route, or chat history. The provider adapters use completion protocol paths (including paths named `chat/completions`) to call LLM APIs; this is transport protocol only. Application-source absolute URLs are limited to public deployment links in this README and non-routable provider fixtures in unit tests; the frontend dependency lockfile contains package registry and project metadata links. Historical migrations refer to obsolete USD setting keys only to translate prior settings and event amounts while retaining their legacy unit. Test-only session stubs and asynchronous call patches are not runtime AI fallbacks. Sample records are isolated under `/seed`, stored through the normal importer, and marked as sample in the UI; no customer-company name is embedded in runtime or sample business content. Dashboard counts and sales figures are derived from persisted events and database summaries.

Passwords are PBKDF2-HMAC-SHA256 hashes. Session tokens are random, opaque, stored only as hashes in PostgreSQL, sent in HttpOnly cookies, and revoked on logout. Business rows and editable settings are scoped to the authenticated workspace; each new account receives copies of sample records rather than another account's data.

## Known limitations

- Account email verification and password reset are not implemented; users can create an account and sign in directly.
- SMTP is optional; the default sample connector records approved content in the database.
- Groq does not provide embeddings through the configured API, so its knowledge search uses PostgreSQL full-text retrieval; OpenAI/Gemini configurations use pgvector.
- The hosted provider token prices are currently unset (`cost_tracking_configured=false`), so estimated-spend caps are inactive; request and rate caps remain enabled. Configure token prices in Settings to enable spend tracking.
- Live checks verified task submission, runtime planning, knowledge citations, sales summaries, approve/edit/reject-regenerate, and recovery of persisted rejected work after a backend restart. Policy upload and routing to a newly created agent have not been verified against the hosted services.
- The hosted database contains an older sample policy document. Seed import does not overwrite existing sample content; a fresh database receives the current `/seed` version.
- External provider quotas and availability are controlled by the provider. On capacity errors, tasks are retained and can be retried.
