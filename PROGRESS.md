# WorkPilot AI build status

## Step 1 — Repository foundation, schema, state, and seeds
- **Status:** Complete.
- **Files restored:** `backend/app/models.py`, `state.py`, `config.py`, `seed_import.py`; provider dependency manifest; `.env.example`; `seed/agents.json`, `seed/settings.json`, and `seed/example-instructions.json`.
- **Migrations:** Added `0004_restore_workforce_schema.py` after the business-only deployment's `0003` migration. It restores agent/task/event tables and the pgvector embedding column; `0001` enables the vector extension for fresh databases. `0005_configurable_cost_units.py` migrates legacy cost fields/settings to currency-aware names.
- **Validation:** Alembic offline SQL rendered; JSON seed files parsed.
- **Environment needed:** PostgreSQL `DATABASE_URL`; an LLM provider key on the server; provider/model configuration; frontend API URL and CORS origin.
- **Known issues:** No local database or provider credentials are configured. The live Render environment must be checked after deployment. Restored workflow tables start empty; startup seed import repopulates sample agents and business data.
- **Next:** Step 2 — LLM client, retry/backoff, embeddings, RAG, and sales tool.

## Step 2 — LLM client, RAG, and sales tool
- **Status:** Complete; external calls remain unverified without provider credentials.
- **Files restored:** `backend/app/llm.py`, `knowledge.py`, `sales.py`, `settings_store.py`, and `limits.py`; requirements include HTTP and pgvector clients.
- **Implemented:** Configurable OpenAI/Gemini/Groq request adapters; settings-driven model names, token limits, retries, timeouts, currency-aware cost caps/prices, chunk size/overlap, and top-k; bounded exponential backoff with jitter; vector retrieval when embeddings are supported, otherwise PostgreSQL full-text retrieval; CSV ingestion and numeric sales summaries.
- **Validation:** Python compilation and focused import/request/parser checks passed; seed CSV parsed as four rows; RAG query sanitization passed.
- **Known issues:** No local provider credentials or PostgreSQL URL; no live embedding/model call yet.
- **Next:** Step 3 — orchestrator, dynamic agent routing, execution, and validator.

## Step 3 — Orchestrator, agent execution, and validator
- **Status:** Complete; full workflow has not yet been exercised against a live provider/database.
- **Files restored:** `backend/app/orchestrator.py`, `workflow.py`, and `validator.py`.
- **Implemented:** LLM-generated dependency plans validated against database-backed agent profiles and allowed tools; persisted task-step execution; shared context; source retrieval; structured validation with policy context. Backend status enums and transition rules are centralized; health exposes status values for the frontend.
- **Validation:** Backend modules compiled; dynamic tool-name normalization and persisted state-machine statuses were checked.
- **Known issues:** No live provider/database credentials; HTTP/API and workflow integration tests run in Step 7.
- **Next:** Step 4 — approvals, activity events, live updates, connectors, request limits, and restart recovery.

## Step 4 — Approvals, activity, connectors, limits, and recovery
- **Status:** Complete; persistence/restart behavior is implemented, but not yet verified against the deployed PostgreSQL service.
- **Files restored:** `backend/app/main.py`, `connectors.py`, `enquiries.py`, and `backend/tests/test_services.py`.
- **Implemented:** Task/agent/settings APIs; approval, edit, rejection/regeneration routes; event timeline; SSE task updates; sample and SMTP connectors; request/rate/cost caps; startup task recovery.
- **Validation:** 23 backend unit tests pass; tests verify provider contracts, transition rejection, event scoping, task planning constraints, configured chunk overlap, and billing-currency validation. No chat route is exposed.
- **Known issues:** Tests use no live PostgreSQL or external LLM credentials; a deployed task flow remains unverified.
- **Next:** Step 5 — restore the structured-task frontend and configurable settings UI, then build it.

## Step 5 — Frontend pages and Settings
- **Status:** Complete; frontend build and backend suite pass.
- **Files restored:** `frontend/components/workpilot.jsx`, `frontend/app/globals.css`, and `frontend/app/layout.jsx`.
- **Implemented:** Structured New Task form, database-loaded example briefs, optional agent filter, live task cards, Dashboard, Agent Builder, Knowledge Hub, Business Data, Approval Inbox, Activity Log, Sent Items, and Settings. Status values are loaded from the backend health response. No chat UI or chat endpoint is restored.
- **Validation:** Next.js production build passed. All 20 backend unit tests pass, including selected-agent plan scoping.
- **Known issues:** Frontend page build does not verify external provider/database connectivity. No local database/provider credentials are available.
- **Next:** Step 6 — deployment configuration, deploy, and live verification.

## Step 6 — Deployment and live verification
- **Status:** Public Vercel/Render deployment is live. Two complete real-provider acceptance tasks were verified: knowledge-backed reply approved/published via the sample connector, and sales report totals checked against database rows, edited, approved, and published.
- **Environment:** Live health reports PostgreSQL connected, `ai_configured=true`, and Groq configured. Local deploy credentials are not present; deployment is triggered through the configured Git remote.
- **Known issue:** A live rejection exposed that synchronous approval/retry handlers called `asyncio.create_task` without a running event loop. The rejection persisted, but regeneration did not start and the response surfaced as a CORS fetch error. Both handlers are now async; a regression test verifies rejection schedules the workflow on the active loop. The rejected task remains recoverable through startup recovery after deployment.
- **Next:** Deploy this fix, verify server recovery regenerates the rejected draft from reviewer feedback, and confirm the public health endpoint.

## Step 7 — Acceptance checks and zero-hardcoding audit
- **Status:** In progress.
- **Validation:** Backend suite (25 tests), frontend production build, and Alembic SQL rendering pass. Audit found only documented protocol/test/legacy-migration URL and USD references; no credential-shaped values were found. Runtime status names are centralized and API-driven in the UI. No chat UI or chat endpoint is present.
- **Live checks:** Fresh LLM plans, differing support/sales plans, knowledge citations, read-only sales totals (34 units and revenue 835 across 2026-11-12 to 2026-11-13), approve/publish, edit/approve/publish, and rejection feedback persistence were exercised on the public site. The rejection regeneration is pending deployment of the event-loop fix.
- **Known limitations:** Live `cost_tracking_configured=false` because token price settings are zero; request/rate caps remain active. Policy upload, new-agent routing, restart recovery, and post-rejection regeneration still need live verification. The deployed sample knowledge document predates the source seed wording until a fresh database is initialized.
- **Next:** Deploy and verify recovery/regeneration; then report the tested acceptance items and remaining limitations without repeated provider retries.
