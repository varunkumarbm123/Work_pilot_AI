# WorkPilot

WorkPilot is a business workspace for reference documents, sales records, and customer enquiries. It has no chatbot, AI agent, model-provider, or automated publishing feature.

## Features

- Upload or paste PDF, Markdown, and text reference documents.
- Import sales data from CSV or JSON rows and view numeric summaries.
- Record and review customer enquiries.
- Configure document chunk size in workspace settings.
- Seed sample data for a local demonstration.

## Architecture

```text
Browser (Next.js)
        │ HTTPS / JSON
        ▼
FastAPI backend ── SQLAlchemy ── PostgreSQL
```

## Run locally

1. Copy `.env.example` to `.env` and set `DATABASE_URL` to a PostgreSQL connection string. Set `FRONTEND_ORIGINS` to the frontend origin if it is not running on the same origin. `NEXT_PUBLIC_API_BASE_URL` is the backend URL used by the browser.
2. Install backend dependencies and start the API:

   ```powershell
   python -m pip install -r backend\requirements.txt
   Set-Location backend
   alembic upgrade head
   python -m app.seed_import
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

3. In another shell, start the frontend:

   ```powershell
   Set-Location frontend
   npm install
   $env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000"
   npm run dev
   ```

The backend runs migrations and imports sample records when `SEED_ON_STARTUP=true`. The sample database contains one customer enquiry, four sales rows, and a reference Markdown document. API documentation is available at `/api/docs`; `/health` checks the database connection.

## Deploy

The Render Blueprint in `render.yaml` configures the API service. Provision PostgreSQL and configure `DATABASE_URL` and `FRONTEND_ORIGINS` in the hosting dashboard. Deploy the `frontend/` directory to a Next.js host and set `NEXT_PUBLIC_API_BASE_URL` to the API's public URL. The hosting accounts and credentials are not part of this repository, so deployment must be triggered by an authenticated account with access to those services.

The migration `0003_remove_assistant_tables` deletes the legacy agent, task, task-step, event, and sent-item tables. It preserves business settings, documents, enquiries, and sales rows.
