# WorkPilot AI

WorkPilot turns a plain-language business instruction into an LLM-generated, dependency-ordered plan. Stored agent profiles and allowed tools drive agent assignment at runtime. Agents retrieve relevant company knowledge from PostgreSQL/pgvector and read sales summaries from imported database rows. An LLM validator checks drafts against retrieved policy chunks. A person approves, edits, or rejects every final draft before the active connector publishes it.

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
  └─ documents, embedded chunks, sent items
```

## Run locally

1. Create a PostgreSQL database with the `vector` extension available. Copy `.env.example` to `.env` and set `DATABASE_URL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_EMBEDDING_MODEL`. Keep provider and SMTP credentials server-side.
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

Settings are stored in the database and editable in the Settings page. Enter provider-specific input, output, and embedding prices there to make the USD cost cap meaningful. The request cap and per-minute limiter remain active independently. Configure the SMTP environment variables and `CONNECTOR_ADAPTER=smtp` only when approved output should be delivered to a real recipient; the default connector stores approved items in the database.

## Deploy

1. Provision PostgreSQL with pgvector on Neon or Supabase.
2. Deploy the repository's `render.yaml` as a Render Blueprint, then provide its required database, model, and frontend-origin environment values. Render applies Alembic migrations and imports seed data before serving. `/health` is the service health check and `/api/docs` is the API reference.
3. Import `frontend/` as a Next.js project on Vercel. Set `NEXT_PUBLIC_API_BASE_URL` to the Render service origin and redeploy. Set the Render `FRONTEND_ORIGINS` value to the deployed frontend origin.
4. Configure model cost prices in Settings. Keep API keys and SMTP credentials in backend environment settings only.

The repository is deployment-ready but **has not been deployed** from this workspace: no Vercel, Render, Neon/Supabase, or model-provider credentials are available here. Therefore no public URL or live end-to-end verification is claimed.

## Two-minute demo

1. Open the Vercel URL; wait for the health indicator to show a connected backend.
2. Open **Business data** to inspect the clearly labelled sample enquiry and sample sales dataset. Add a new policy in **Knowledge Hub**.
3. In **Control Hub**, type a new instruction asking for a response to an enquiry using the newly uploaded policy, or request a sales summary. The instruction is sent to the live LLM orchestrator; the generated plan and agent assignments appear on the task card.
4. Open **Approval Inbox**. Inspect the validator reasons and source chunks, edit the draft or reject it with feedback, then approve a result. The sample connector records it in **Sent Items**.
5. Open **Activity Log** to inspect model prompts, tool calls, retrieved chunks, validation decisions, and reviewer actions.

## Hardcoding audit

Audit scope: application source and deployment configuration, excluding dependency lockfiles and generated build output. Repository searches found no literal service URLs, credential values, fixed provider response samples, canned AI answers, or starter-agent IDs/names in routing logic; the filler-content scan returned no source matches. API origins, provider credentials/models, database access, SMTP settings, prompts, validation guidance, retries, chunking, retrieval count, request caps, and cost caps come from deployment environment or database-backed Settings. Starter agent profiles, sample documents, enquiries, sales CSV, and example instructions exist only under `/seed`; the UI labels sample records. Product-defined status/tool identifiers and input/file/row safety checks remain explicit code-level contract constraints; displayed operational metrics are calculated from stored events. The production dependency audit (`npm audit --omit=dev`) reported **zero vulnerabilities** after updating Next.js.

## Known limitations

- The public no-login demo isolates limits by browser-generated session ID; it is not user authentication or a security boundary.
- A provider's actual price is model-specific; set the three token-price settings for accurate USD cost accounting. Request/rate limits still apply if price settings are unset.
- PostgreSQL/pgvector and real provider execution were not available in the implementation workspace, so migrations, live RAG, and the complete acceptance flow still require deployment verification.
- SMTP delivery occurs before its database record commits; a database failure after a successful send can leave delivery and the audit record temporarily inconsistent.
- Plans are bounded by the configured step maximum, and imported sales analysis is capped by the tool's row safety limit.
