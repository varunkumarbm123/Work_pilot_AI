# WorkPilot AI progress

## Step 1 — Repository foundation, database schema, settings, state, and seed inputs
- **Status:** Complete.
- **Files created:** `.gitignore`, `.env.example`, `backend/requirements.txt`, `backend/app/{__init__.py,config.py,database.py,models.py,state.py,seed_import.py}`, `seed/{agents.json,settings.json,example-instructions.json,knowledge.md,sales.csv}`, `PROGRESS.md`.
- **Environment variables needed:** `DATABASE_URL`; model provider credentials and model names are needed for live AI; deployment origins and frontend API URL are needed when hosted. See `.env.example`.
- **Validation:** Python compilation and seed JSON validation passed.
- **Next:** Step 2 (completed below).
- **Known issues:** No deployment credentials are present in the environment. Public deployment cannot be claimed until hosting/database access is configured.

## Step 2 — LLM client, embeddings, knowledge retrieval, and sales tool
- **Status:** Complete.
- **Files created:** `backend/app/{settings_store.py,llm.py,knowledge.py,sales.py}`; model and seed settings updated.
- **Implemented:** Configurable provider calls with timeouts, jittered bounded retries, capacity error detection; real embedding and pgvector retrieval; text/PDF extraction and chunk ingestion; CSV import and database-backed numeric summary tool; seed importer reuses the same ingestion/import functions.
- **Validation:** Backend packages installed from the declared manifest; Python compilation, module imports, CSV parsing, and text extraction passed. Live model and PostgreSQL calls remain unverified because no credentials/database URL are configured.
- **Known issues:** The new SQL schema has not been applied against an actual PostgreSQL instance; provider calls require server-side API credentials and model names.
- **Next:** Step 3 (completed below).

## Step 3 — Orchestration, dynamic agent assignment, execution, and validation
- **Status:** Complete.
- **Files created:** `backend/app/{orchestrator.py,validator.py,workflow.py}`; task-step schema now stores tools and dependencies.
- **Implemented:** Runtime-generated dependency plans with profile-based agent/tool selection and schema checks; parallel independent steps; tool-guarded knowledge retrieval and database-backed sales summaries; LLM policy validation with citations and revision attempts; final human-review gate; task prompts, models, and tool activity recorded.
- **Validation:** Python compilation and imports for the orchestrator, workflow, validator, and sales tool passed. Real LLM and PostgreSQL execution cannot be verified without configured services.
- **Known issues:** PostgreSQL startup/migration and live provider execution require configured services and remain unverified.
- **Next:** Step 4 (completed below).

## Step 4 — Approvals, activity, live updates, connectors, limits, and recovery
- **Status:** Complete.
- **Files created:** `backend/app/{main.py,migrate.py,connectors.py,enquiries.py,limits.py}`, `backend/alembic.ini`, `backend/migrations/{env.py,versions/0001_initial.py}`, `seed/enquiries.json`; runtime API and persistence services updated.
- **Implemented:** FastAPI CRUD/import/task/approval/settings/activity endpoints; approve, edit-and-approve, and reject-then-regenerate flows; event-backed dashboard and activity trace; SSE updates; sample database and SMTP adapters; per-session write/rate/cost caps; auto-migrations, seed import, and restart recovery.
- **Validation:** Python compilation, OpenAPI generation, CSV parsing, and ASGI smoke tests for tool discovery, docs, and missing-session rejection passed. PostgreSQL startup/migration and provider calls are unverified: Docker isn’t running and no database/provider credentials are configured.
- **Known issues:** This public demo intentionally has no login; session caps are browser-session controls, not user authentication. SMTP side effects cannot be transactionally rolled back if the database fails after delivery.
- **Next:** Step 5 (completed below).

## Step 5 — Next.js interface and Settings
- **Status:** Complete.
- **Files created:** `frontend/{package.json,package-lock.json,next.config.mjs,postcss.config.mjs,tailwind.config.js}`, `frontend/app/{layout.jsx,page.jsx,globals.css}`, `frontend/components/workpilot.jsx`.
- **Implemented:** Responsive Control Hub and dashboard; agent CRUD; document upload/paste; CSV/row and enquiry entry; approval/edit/reject UI; SSE task cards; activity log; sent items; settings editor; health/wake-up display; anonymous browser session.
- **Validation:** `npm run build` passed on Next.js 16.4.6; `npm audit --omit=dev` reported zero production vulnerabilities. Python import/OpenAPI smoke checks still pass.
- **Known issues:** Frontend/backend full-flow verification requires a reachable PostgreSQL database and valid server-side model credentials. Cost estimates need to be configured in Settings for meaningful USD cap enforcement.
- **Next:** Step 6 (completed below).

## Step 6 — Deployment configuration and documentation
- **Status:** Configuration complete; deployment blocked by missing credentials and repository hosting.
- **Files created:** `Dockerfile`, `render.yaml`, `frontend/vercel.json`, `README.md`; Alembic startup URL handling and root `.env` loading fixed.
- **Implemented:** Render build/start configuration runs migrations and sample import; container startup does the same; Vercel Next.js project configuration; architecture, setup, environment, demo, audit, and limitations documented.
- **Validation:** Full offline Alembic SQL rendering passed; frontend production build passed; production dependency audit has zero vulnerabilities. Deployment commands/tools and environment credential names are unavailable; no public services or URLs were created.
- **Known issues:** A PostgreSQL+pgvector URL, provider key/model configuration, Render/Vercel access, and frontend origin are required for live deployment. This workspace has no Git repository configured for hosting deployment.
- **Next:** Step 7 (completed below).

## Step 7 — Hardcoding audit and final verification
- **Status:** Source audit and local checks complete; live acceptance blocked by unavailable hosted services.
- **Audit:** Searched application source/config for literal URLs and credential patterns, filler/mock-output terms, and starter-agent names in routing. No matches were found. UI metrics/status counts derive from database events; remaining numeric literals are input/payload safety limits, polling/formatting values, and the required three-step onboarding.
- **Validation:** Five backend unit tests passed; Python compilation and OpenAPI generation passed; all seed JSON parsed; Alembic initial PostgreSQL SQL rendered offline; ASGI smoke tests passed; local production frontend returned HTTP 200 with the Control Hub; Next.js production build passed; `npm audit` reported zero vulnerabilities.
- **Initial deployment state:** No PostgreSQL server or provider credentials were configured in the implementation workspace. Deployment provisioning and current status are recorded below.
- **Next:** Complete the hosted environment configuration and run the live acceptance flow before claiming the app is fully deployed.

## Hardcoding audit continuation
- **Status:** Required runtime settings no longer silently fall back to duplicated code defaults; local validation passed.
- **Changes:** `setting_value` now raises a clear configuration error when a required database setting is missing. LLM, orchestration, validation, knowledge retrieval, and session-limit logic now read required values from the seeded Settings table. The API no longer enables wildcard CORS when `FRONTEND_ORIGINS` is empty. Added tests for required-setting errors and explicit optional defaults.
- **Files changed:** `backend/app/{settings_store.py,knowledge.py,limits.py,llm.py,orchestrator.py,workflow.py,validator.py,main.py}`, `backend/tests/test_services.py`, `README.md`, `PROGRESS.md`.
- **Environment variables needed:** Existing service values remain required; replace the revoked `OPENAI_API_KEY` directly in Render's secret settings. Do not copy the replacement key into chat or logs. Restore OpenAI API credits before attempting live model calls.
- **Validation:** Eight backend unit tests passed with a local-only `DATABASE_URL`; Python compilation and the frontend production build passed. Scans found no duplicate fallbacks for required runtime settings, no wildcard-CORS fallback, and no mock/fake/dummy/lorem/TODO markers or credential literals in application source, seed data, or deployment configuration.
- **Next:** Publish and verify the source changes after GitHub access is restored, then restore model access with a replacement key entered directly in Render and available API credits.
- **Known issues:** GitHub CLI is not authenticated in this workspace, so the tested changes are local and have not triggered a Render deployment. The exposed provider key is revoked but has not been replaced; live task execution and full approval acceptance remain blocked until a replacement key and provider credits are configured.

## Activity feed session scoping
- **Status:** Implemented, pushed, and live.
- **Changes:** The Activity Log API now requires the browser session ID and only returns task-linked or explicitly session-tagged events for that session. The UI labels the feed as session-scoped, clarifies its empty state, adds a task-ID placeholder, and only says “prompt” for events that include one.
- **Files changed:** `backend/app/main.py`, `backend/tests/test_services.py`, `frontend/components/workpilot.jsx`, `README.md`, `PROGRESS.md`.
- **Validation:** Ten backend unit tests passed, including session-filter SQL and required-session checks; Python compilation and API import passed; the Next.js production build passed. Live event isolation is not yet deployed.
- **Deployment change:** Commit `fa7dff9` was pushed to `main`. Removed the revoked OpenAI key from the Render environment group.
- **Live verification:** Render `/health` reports database connected and `ai_configured:false`; `/api/events` rejects calls without a session ID. The deployed frontend displays “EVENTS · THIS SESSION,” a task-ID filter placeholder, and “Inspect event details” for events without prompts.
- **Next:** Add a fresh OpenAI API key directly in Render and ensure the provider account has credits, then re-run a task end to end.
- **Known issues:** AI tasks are intentionally unavailable until a valid provider key and credits are configured. Seed/sample records remain in the database as sample data per product requirements; the Activity Log no longer mixes unrelated browser-session events into its feed.

## Gemini free-tier provider
- **Status:** Gemini provider support implemented and validated locally; deployment and provider-key verification pending.
- **Changes:** Added provider selection and server-only Gemini API settings. Chat uses Google's OpenAI-compatible Gemini endpoint; embeddings use Gemini's native `embedContent` API. Existing retry/capacity logging, timeout, model pricing accounting, Settings-based model names, and embedding reindex path are preserved. Render config now selects Gemini and includes the API base and model identifiers; no key is committed.
- **Files changed:** `.env.example`, `render.yaml`, `backend/app/{config.py,llm.py,main.py,seed_import.py}`, `backend/tests/test_services.py`, `README.md`, `PROGRESS.md`.
- **Models:** `gemini-4-hydrogen` for chat and `gemini-embedding-001` for embeddings, initially configured via Render environment variables; chat and embedding model names are also stored in database Settings.
- **Validation:** 14 backend tests passed, including Gemini chat/embedding request and response mapping; Python compilation and frontend production build passed.
- **Next:** Deploy source and set `LLM_PROVIDER`, Gemini base/model settings in Render; obtain a Gemini API key in AI Studio and store it only in Render `GEMINI_API_KEY`. Update DB chat model and embedding model settings, causing stored document embeddings to be reindexed, then verify live AI task and approval flow.
- **Known issues:** AI Studio is currently at its onboarding page (no signed-in account); the provider secret cannot be created until the user signs into Google AI Studio. Free-tier quotas are provider-controlled and may change.

## Deployment continuation
- **Status:** Frontend and database-backed API deployed publicly; OpenAI is configured but account billing blocks model execution.
- **Completed:** Confirmed the repository is on the GitHub `main` branch; connected Render Blueprint `Zen_AI` to the repository; created Render web service `workpilot-ai-api` with public origin `https://workpilot-ai-api.onrender.com`; created Neon project `workpilot-ai` in Sydney; created Vercel project `work-pilot-ai` from the `frontend/` root and set `NEXT_PUBLIC_API_BASE_URL` to the Render service origin.
- **Frontend URL:** `https://work-pilot-ai-tawny.vercel.app` returned HTTP 200 and its response contains the WorkPilot interface.
- **Backend status:** The original deployment `dep-dbs4d2ei0phs73c38rl0` failed because required `DATABASE_URL` was missing. Created Render environment group `workpilot-ai-backend` (`evg-dbs51sl6e07s738rj5s0`), securely copied Neon’s pooled connection string into its `DATABASE_URL`, and linked the group to the Render service. Deployment `dep-dbs52d6k1f9s7389qi10` reached `Deploy succeeded|Live`. Added `FRONTEND_ORIGINS=https://work-pilot-ai-tawny.vercel.app` to the linked group; the next deployment completed sufficiently for the public UI to report the backend connected.
- **Live verification:** `GET /health` and `GET /api/docs` return HTTP 200. After setting model identifiers in the database, `/health` reports `ai_configured:true`. A real task reached the OpenAI API, retried HTTP 429 responses per policy, then failed with `credit_balance_exhausted`: the OpenAI account has no remaining API credits. The task/error trace is stored in the database.
- **Provider configuration:** Render environment group includes `OPENAI_BASE_URL=https://api.openai.com/v1`, `OPENAI_MODEL=gpt-4o-mini`, `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`, and `OPENAI_API_KEY` stored as a Render secret. Database Settings have matching model names. The key was created with a 30-day expiration.
- **Remaining:** OpenAI account owner must add API billing/credits; this requires the account holder's payment authorization. After credits are available, retry a live task, verify an approval flow, and continue the acceptance tests. No credentials are recorded in this file or source control.
- **Next:** Add OpenAI API credits in the account billing console; then retry a live task and complete end-to-end verification.

## Gemini credential setup continuation
- **Status:** Production remains reachable, but Gemini is not configured and no live AI workflow can run yet.
- **Completed:** Created the `WorkPilot AI` Google Cloud project (`workpilot-ai-514713`), enabled the Gemini API, imported only this project into AI Studio, and created the `WorkPilot Gemini Runtime` service account. The database `model_name` setting is `gemini-4-hydrogen`. Backend unit tests pass (14 tests), including provider configuration, Gemini request formats, and Bearer authentication for Gemini's OpenAI-compatible chat endpoint.
- **Environment:** Render has Gemini provider/model/base URL variables, but no `GEMINI_API_KEY`. Live `/health` reports `database=connected`, `ai_provider=gemini`, and `ai_configured=false`. The Render environment-group editor was opened but canceled without saving any changes.
- **Credential safety:** A previously-created key was exposed in a browser tool snapshot. The Google Cloud credentials page currently lists no API keys. Do not reuse the exposed key. An attempt to create a Gemini-restricted replacement bound to the existing service account did not complete; no replacement was generated or saved.
- **Permissions:** The user confirmed that API-key management, service-account-use, and service-account-viewer permissions were granted. After waiting and refreshing the Google Cloud page, its API-key form still reports that the signed-in account cannot view service accounts to bind; the picker does not enable account selection. No replacement key was created.
- **Required next steps:** Resolve the Google Cloud permission discrepancy for the exact signed-in principal and WorkPilot AI project until the Gemini API-key form allows selecting `WorkPilot Gemini Runtime`. Then create a replacement key restricted to Gemini API, add it as Render's masked `GEMINI_API_KEY`, and redeploy. After activation, update the database `embedding_model_name` to `gemini-embedding-001` through Settings and allow knowledge reindexing to finish. Verify `/health` reports `ai_configured:true`, then run a live task and verify the approval flow.
- **Known issues:** Google AI Studio key setup has not completed; Google Cloud's service-account-binding form remains blocked despite the reported role grants. Live LLM requests, embedding migration, and end-to-end approval remain unverified. No credentials were put in source control or progress notes.

Groq provider addition
- **Status:** Groq is active in production. `/health` reports `ai_configured=true`, `ai_provider=groq`, and `database=connected`.
- **Completed:** Added Groq server configuration, OpenAI-compatible chat request handling, Render Blueprint variables, `.env.example` entries, and README setup instructions. Groq-specific knowledge ingest/retrieval stores document chunks and ranks matches with PostgreSQL full-text search because Groq does not expose embeddings. The health endpoint does not require an embedding model for Groq. Added tests for Groq config, request URL/headers/payload, and full-text query sanitation.
- **Live configuration:** Render has `LLM_PROVIDER=groq` and a masked `GROQ_API_KEY` with a 90-day expiration. The Settings-page `model_name` is `openai/gpt-oss-120b`, matching `GROQ_MODEL`. Never put the key in source control or progress notes.
- **Live validation:** Production health reports `ai_configured=true`, `ai_provider=groq`, and `database=connected`. A live task generated a dependency-ordered two-step plan; both agents ran Groq model calls and validation, and the task reached human approval.
- **Issue found and fix implemented:** An earlier plan omitted the sales-data tool. The planner prompt now says to select every tool required for correctness; a regression test covers this instruction. Backend tests pass (18 tests) with an in-memory SQLite test URL; `git diff --check` passes. Code commit `feb3069` is deployed.
- **Live retest:** A new Groq task was accepted and completed its two-step plan, agent calls, and validator calls, reaching human approval. Its plan still selected no tools. The event's agent profile listed no allowed tools for the Sales Analyst, while the current Agent Builder page shows the sample Sales Analyst allowed `Sales Query`. Therefore evidence does not establish a successful live sales-data tool call, and the difference between the plan-time and current agent profiles is unresolved. The draft is awaiting review; do not approve it.
- **Other known issues:** The production UI reports `cost_tracking_configured=false` because token-price settings are unset. A browser console reported an HTTP 429 for a resource during the retest; the affected request and source were not identified. Do not claim all instructions or the sales workflow are verified.
- **Next:** Investigate why task-time agent profiles do not match the current Agent Builder records; then run one sales task only after confirming the assigned agent has `sales_summary`, and verify its `tool_call` event contains actual database rows/aggregates before presenting the workflow as verified.
