# WorkPilot AI build status

## Step 1 — Repository foundation, schema, state, and seeds
- **Status:** Complete.
- **Files restored:** `backend/app/models.py`, `state.py`, `config.py`, `seed_import.py`; provider dependency manifest; `.env.example`; `seed/agents.json`, `seed/settings.json`, and `seed/example-instructions.json`.
- **Migration:** Added `0004_restore_workforce_schema.py` after the business-only deployment's `0003` migration. It restores agent/task/event tables and the pgvector embedding column; `0001` enables the vector extension for fresh databases.
- **Validation:** Alembic offline SQL rendered; JSON seed files parsed.
- **Environment needed:** PostgreSQL `DATABASE_URL`; an LLM provider key on the server; provider/model configuration; frontend API URL and CORS origin.
- **Known issues:** No local database or provider credentials are configured. The live Render environment must be checked after deployment. Restored workflow tables start empty; startup seed import repopulates sample agents and business data.
- **Next:** Step 2 — LLM client, retry/backoff, embeddings, RAG, and sales tool.

## Step 2 — LLM client, RAG, and sales tool
- **Status:** Complete; external calls remain unverified without provider credentials.
- **Files restored:** `backend/app/llm.py`, `knowledge.py`, `sales.py`, `settings_store.py`, and `limits.py`; requirements include HTTP and pgvector clients.
- **Implemented:** Configurable OpenAI/Gemini/Groq request adapters; settings-driven model names, token limits, retries, timeouts, and cost caps; bounded exponential backoff with jitter; vector retrieval when embeddings are supported, otherwise PostgreSQL full-text retrieval; CSV ingestion and numeric sales summaries.
- **Validation:** Python compilation and focused import/request/parser checks passed; seed CSV parsed as four rows; RAG query sanitization passed.
- **Known issues:** No local provider credentials or PostgreSQL URL; no live embedding/model call yet.
- **Next:** Step 3 — orchestrator, dynamic agent routing, execution, and validator.

## Step 3 — Orchestrator, agent execution, and validator
- **Status:** Complete; full workflow has not yet been exercised against a live provider/database.
- **Files restored:** `backend/app/orchestrator.py`, `workflow.py`, and `validator.py`.
- **Implemented:** LLM-generated dependency plans validated against database-backed agent profiles and allowed tools; persisted task-step execution; shared context; source retrieval; structured validation with policy context.
- **Validation:** Backend modules compiled; dynamic tool-name normalization and persisted state-machine statuses were checked.
- **Known issues:** No live provider/database credentials; HTTP/API and workflow integration tests run in Step 7.
- **Next:** Step 4 — approvals, activity events, live updates, connectors, request limits, and restart recovery.

## Step 4 — Approvals, activity, connectors, limits, and recovery
- **Status:** Complete; persistence/restart behavior is implemented, but not yet verified against the deployed PostgreSQL service.
- **Files restored:** `backend/app/main.py`, `connectors.py`, `enquiries.py`, and `backend/tests/test_services.py`.
- **Implemented:** Task/agent/settings APIs; approval, edit, rejection/regeneration routes; event timeline; SSE task updates; sample and SMTP connectors; request/rate/cost caps; startup task recovery.
- **Validation:** All 19 restored backend unit tests pass; tests verify provider contracts, transition rejection, event scoping, and task planning constraints. No chat route is exposed.
- **Known issues:** Tests use no live PostgreSQL or external LLM credentials; a deployed task flow remains unverified.
- **Next:** Step 5 — restore the structured-task frontend and configurable settings UI, then build it.

## Step 5 — Frontend pages and Settings
- **Status:** Complete; frontend build and backend suite pass.
- **Files restored:** `frontend/components/workpilot.jsx`, `frontend/app/globals.css`, and `frontend/app/layout.jsx`.
- **Implemented:** Structured New Task form, database-loaded example briefs, optional agent filter, live task cards, Dashboard, Agent Builder, Knowledge Hub, Business Data, Approval Inbox, Activity Log, Sent Items, and Settings. No chat UI or chat endpoint is restored.
- **Validation:** Next.js production build passed. All 20 backend unit tests pass, including selected-agent plan scoping.
- **Known issues:** Frontend page build does not verify external provider/database connectivity. No local database/provider credentials are available.
- **Next:** Step 6 — deployment configuration, deploy, and live verification.

## Planned remaining build order
6. Deploy and verify a live end-to-end task.
7. Acceptance checks and zero-hardcoding audit.
