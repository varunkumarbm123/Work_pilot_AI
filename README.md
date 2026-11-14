# WorkPilot AI

WorkPilot AI is a no-login business workflow platform for customer enquiries and sales reporting. Owners submit structured task briefs—not chat messages. The orchestrator creates a dependency-ordered plan, selects stored specialist profiles and tools at runtime, and returns sourced work for validation and human approval.

## Architecture

```text
Browser (Next.js + Tailwind, Vercel)
  ├─ Control Hub: New Task form, recent work, live task cards
  ├─ Agent Builder, Knowledge Hub, sales/enquiry import
  ├─ Approval Inbox, Activity Log, Dashboard, Sent Items, Settings
  └─ anonymous browser session + SSE task updates
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
  ├─ agents, settings, tasks, steps, events, enquiries, sales rows
  └─ documents, embedded chunks, sent items
```

## Local setup

1. Provision PostgreSQL with the `vector` extension. Copy `.env.example` to `.env` and set `DATABASE_URL`, `LLM_PROVIDER`, and that provider's server-only API key, base URL, and model variables. Keep provider secrets out of frontend variables. Set `FRONTEND_ORIGINS` and `NEXT_PUBLIC_API_BASE_URL` for the local frontend/backend origins.
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

The backend runs migrations and idempotent seed import on startup when `SEED_ON_STARTUP=true`. Provider model names, system instructions, validation policy, retry limits, timeouts, retrieval settings, and cost limits are database-backed and editable in Settings. Provider credentials remain server-side.

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `LLM_PROVIDER` | Selects the configured provider |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL` | OpenAI server configuration |
| `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL` | Gemini server configuration |
| `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_MODEL` | Groq server configuration |
| `FRONTEND_ORIGINS` | Allowed browser origins for CORS |
| `NEXT_PUBLIC_API_BASE_URL` | Backend URL used by the browser |
| `CONNECTOR_ADAPTER` | Selects the sample or SMTP connector |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | Optional real email connector |
| `SEED_ON_STARTUP`, `PORT` | Startup behavior and hosting port |

## Deploy

- **Backend:** Deploy `render.yaml` as a Render Blueprint. Configure PostgreSQL, a provider/API key, provider endpoint/model values, and `FRONTEND_ORIGINS` in Render's environment settings. The service runs Alembic migrations and seed import before serving `/health` and `/api/docs`.
- **Frontend:** Deploy `frontend/` as a Next.js project on Vercel. Set `NEXT_PUBLIC_API_BASE_URL` to the backend origin and redeploy. Provider credentials must never be added to Vercel.

Public links: [WorkPilot website](https://work-pilot-ai-tawny.vercel.app) · [API docs](https://workpilot-ai-api.onrender.com/api/docs) · [API health](https://workpilot-ai-api.onrender.com/health).

## Two-minute demo

1. Open the public website; no account or setup is required for the hosted demo.
2. In **Control Hub**, submit a new brief. Optionally select an agent. The plan, assigned agents, and task cards are generated and updated from the backend.
3. Upload a policy in **Knowledge Hub** and a sales CSV in **Business Data**. Ask for a response/report in a new task brief; the specialist tools retrieve document sources and calculate figures from stored sales rows.
4. Open **Approval Inbox**. Approve, edit and approve, or reject with feedback. Rejected work returns through the workflow.
5. Inspect the event trace in **Activity Log**, task metrics in **Dashboard**, and published items in **Sent Items**.

## Hardcoding audit

No chat UI or chat endpoint is part of the task workflow. Task plans, agent selection, validation verdicts, citations, and reports are generated from live provider calls and database retrieval/tools; there are no canned AI-response fallbacks. Provider credentials are server-side environment variables. Provider/model names, prompts, validation policies, limits, and examples are stored in environment configuration or database settings. Sample agent profiles and business records are isolated under `/seed` and tagged in the UI. Business figures and dashboard counts come from PostgreSQL; sales reports use database summaries, not model-generated arithmetic. Remaining fixed values are bounded safety/input ceilings, provider protocol identifiers, and product workflow status/tool identifiers.

## Known limitations

- The public demo has no login. Browser session IDs isolate request/cost limits but are not authentication.
- SMTP is optional; the default sample connector records approved content in the database.
- Groq does not provide embeddings through the configured API, so its knowledge search uses PostgreSQL full-text retrieval; OpenAI/Gemini configurations use pgvector.
- External provider quotas and availability are controlled by the provider. On capacity errors, tasks are retained and can be retried.
