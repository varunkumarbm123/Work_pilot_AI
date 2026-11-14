# WorkPilot AI

WorkPilot turns a plain-language business instruction into an LLM-generated, dependency-ordered plan. Stored agent profiles and allowed tools drive agent assignment at runtime. Agents retrieve relevant company knowledge from PostgreSQL/pgvector or PostgreSQL full-text search, depending on provider embedding support, and read sales summaries from imported database rows. An LLM validator checks drafts against retrieved policy chunks. A person approves, edits, or rejects every final draft before the active connector publishes it.

## Architecture

```text
Browser (Next.js + Tailwind, Vercel)
  ├─ Control Hub, Agent Builder, Knowledge Hub, data import
  ├─ Approval Inbox, Activity Log, Dashboard, Sent Items, Settings
  └─ anonymous browser session + SSE task updates
            │ HTTPS / JSON / SSE
            ▼
FastAPI (Render)
  ├─ LLM orchestrator ── runtime profile/tool matching ── specialist steps
  ├─ evidence retrieval + policy validator + approval/retry state machine
  ├─ bounded provider retries, session rate/request/cost guards, recovery
  └─ sample-database or configured SMTP connector
            │ SQLAlchemy / psycopg
            ▼
PostgreSQL + pgvector (Neon or Supabase)
  ├─ agents, settings, tasks, steps, events, enquiries, sales rows
  └─ documents, indexed chunks, sent items
```

## Run locally

1. Create a PostgreSQL database with the `vector` extension available. Copy `.env.example` to `.env` and set `DATABASE_URL` plus one model provider's server-only credentials. Google Gemini uses `LLM_PROVIDER=gemini` and the `GEMINI_*` variables; GroqCloud uses `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_BASE_URL`, and `GROQ_MODEL`; OpenAI remains available through its `OPENAI_*` variables. Groq chat uses PostgreSQL full-text retrieval for knowledge because Groq does not expose embeddings; Gemini and OpenAI use pgvector embeddings.
2. Set `FRONTEND_ORIGINS` to the frontend origin and set `PORT` in the backend shell. Configure the browser-visible `NEXT_PUBLIC_API_BASE_URL` to the backend origin in the frontend shell.
3. In PowerShell, install and start the backend:

   ```powershell
   python -m pip install -r backend\requirements.txt
   Set-Location backend
   alembic upgrade head
   python -m app.seed_import
   uvicorn app.main:app --host 0.0.0.0 --port $env:PORT
   ```

4. In another shell:

   ```powershell
   Set-Location frontend
   npm install
   npm run dev
   ```

The backend runs migrations and idempotent sample import on startup. Sample records are kept in `/seed` and tagged as sample in the interface. When the embedding provider is configured, seed import embeds the sample policy through the same provider path used by document uploads. Without provider configuration, the service stays available and records that sample indexing is pending; tasks report provider errors rather than returning generated-looking defaults.

Settings are stored in the database and editable in the Settings page. Enter provider-specific input, output, and embedding prices there to make the USD cost cap meaningful. The request cap and per-minute limiter remain active independently. Gemini and Groq free-tier access is subject to provider-enforced quotas that can change and does not guarantee uninterrupted capacity; avoid submitting sensitive customer data unless the provider's current data-use terms meet your requirements. Configure the SMTP environment variables and `CONNECTOR_ADAPTER=smtp` only when approved output should be delivered to a real recipient; the default connector stores approved items in the database.

## Deploy

1. Provision PostgreSQL with pgvector on Neon or Supabase.
2. Deploy the repository's `render.yaml` as a Render Blueprint, then provide its required database, model, and frontend-origin environment values. Render applies Alembic migrations and imports seed data before serving. `/health` is the service health check and `/api/docs` is the API reference.
3. Import `frontend/` as a Next.js project on Vercel. Set `NEXT_PUBLIC_API_BASE_URL` to the Render service origin and redeploy. Set the Render `FRONTEND_ORIGINS` value to the deployed frontend origin.
4. Configure model cost prices in Settings. For Gemini, create an API key in Google AI Studio and add it as `GEMINI_API_KEY` in Render. For Groq, create an API key at [GroqCloud](https://console.groq.com/keys), set `LLM_PROVIDER=groq`, and add it as `GROQ_API_KEY` in Render. Set the Settings-page `model_name` to match `GROQ_MODEL`. Keep API keys and SMTP credentials in backend environment settings only.

The public frontend is [work-pilot-ai-tawny.vercel.app](https://work-pilot-ai-tawny.vercel.app). The Render API is [workpilot-ai-api.onrender.com](https://workpilot-ai-api.onrender.com), with interactive docs at [/api/docs](https://workpilot-ai-api.onrender.com/api/docs); Neon project `workpilot-ai` is in Sydney. The Neon database is connected and migrations have run. The exposed OpenAI key was revoked and removed from Render. The backend must have a valid server-side provider key before AI requests can run. Never put provider keys in frontend variables, source control, or chat. Gemini and Groq free-tier limits are imposed by their providers, may change, and can cause capacity errors. When switching providers, update the Settings-page `model_name` to match the selected provider. Switching from Gemini/OpenAI to Groq needs no embedding migration; knowledge retrieval uses PostgreSQL full-text matching instead of vector similarity.

## Two-minute demo

1. Open the Vercel URL; wait for the health indicator to show a connected backend.
2. Open **Business data** to inspect the clearly labelled sample enquiry and sample sales dataset. Add a new policy in **Knowledge Hub**.
3. In **Control Hub**, type a new instruction asking for a response to an enquiry using the newly uploaded policy, or request a sales summary. The instruction is sent to the live LLM orchestrator; the generated plan and agent assignments appear on the task card.
4. Open **Approval Inbox**. Inspect the validator reasons and source chunks, edit the draft or reject it with feedback, then approve a result. The sample connector records it in **Sent Items**.
5. Open **Activity Log** to inspect model prompts, tool calls, retrieved chunks, validation decisions, and reviewer actions.

## Hardcoding audit

Audit scope: application source and deployment configuration, excluding dependency lockfiles and generated build output. Provider credentials and service origins come from server-side environment variables; prompts, model names, retry limits, timeout, chunk size, retrieval count, task/request limits, and cost caps come from database-backed Settings seeded from `/seed/settings.json`. Missing required settings now fail explicitly instead of silently substituting code defaults. CORS no longer falls back to a wildcard origin when `FRONTEND_ORIGINS` is unset. The activity API scopes event history to the requesting browser session's tasks and session-tagged events. Sample agent profiles, documents, enquiries, sales CSV, and example instructions exist only under `/seed`; the UI labels sample records. Product-defined status/tool identifiers, bounded safety ceilings, input/file/row limits, and UI polling/formatting intervals remain explicit constraints. No canned model responses or credentials are present in application source; displayed operational metrics are calculated from stored events. The production dependency audit (`npm audit --omit=dev`) reported **zero vulnerabilities** after updating Next.js.

## Known limitations

- The public no-login demo isolates limits by browser-generated session ID; it is not user authentication or a security boundary.
- A provider's actual price is model-specific; set the three token-price settings for accurate USD cost accounting. Request/rate limits still apply if price settings are unset.
- The hosted PostgreSQL/pgvector connection and migrations are verified. The exposed OpenAI key has been revoked, and the replacement key must be entered directly in Render; real task execution also requires API credits. The full approval flow remains unverified until provider access is restored.
- SMTP delivery occurs before its database record commits; a database failure after a successful send can leave delivery and the audit record temporarily inconsistent.
- Plans are bounded by the configured step maximum, and imported sales analysis is capped by the tool's row safety limit.
