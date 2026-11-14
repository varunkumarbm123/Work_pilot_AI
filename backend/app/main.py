import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pypdf.errors import PdfReadError
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_config
from app.connectors import get_connector
from app.database import SessionLocal, engine
from app.enquiries import create_enquiry
from app.knowledge import extract_text, ingest_document
from app.limits import reserve_session_request
from app.llm import ModelRequestError, embed
from app.migrate import run_migrations
from app.models import Agent, Document, Enquiry, Event, KnowledgeChunk, SentItem, Setting, Task, TaskStep
from app.orchestrator import AVAILABLE_TOOLS
from app.sales import import_sales_rows, parse_sales_csv, sales_summary
from app.seed_import import import_seed_data
from app.settings_store import setting_value, settings_map
from app.state import InvalidTransition, transition_task
from app.workflow import execute_task


class TaskInput(BaseModel):
    instruction: str = Field(min_length=1, max_length=10000)


class AgentInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=4000)
    instructions: str = Field(min_length=1, max_length=8000)
    allowed_tools: list[str] = Field(default_factory=list)
    knowledge_access: bool = True


class SettingInput(BaseModel):
    value: Any


class EnquiryInput(BaseModel):
    customer_name: str = Field(default="", max_length=255)
    customer_email: str = Field(default="", max_length=255)
    subject: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=12000)


class SalesRowsInput(BaseModel):
    dataset: str = Field(min_length=1, max_length=255)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class ApprovalInput(BaseModel):
    action: Literal["approve", "edit_approve", "reject"]
    content: str | None = Field(default=None, max_length=20000)
    reason: str | None = Field(default=None, max_length=4000)
    recipient: str | None = Field(default=None, max_length=320)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_config().seed_on_startup:
        await import_seed_data()
    else:
        run_migrations()
    app.state.background_tasks = set()
    with SessionLocal() as db:
        recoverable_ids = db.scalars(
            select(Task.id).where(Task.status.in_(("planned", "running", "rejected")))
        ).all()
    for task_id in recoverable_ids:
        _schedule_task(app, task_id)
    yield


app = FastAPI(
    title="WorkPilot AI",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
origins = [origin.strip() for origin in get_config().frontend_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_public_writes(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        try:
            session_id = _session_id(request.headers.get("x-session-id"))
            with SessionLocal() as db:
                reserve_session_request(db, session_id)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
    return await call_next(request)


def _schedule_task(application: FastAPI, task_id: str) -> None:
    job = asyncio.create_task(execute_task(task_id))
    application.state.background_tasks.add(job)
    job.add_done_callback(application.state.background_tasks.discard)


def get_db():
    with SessionLocal() as db:
        yield db


def _session_id(value: str | None) -> str:
    session_id = (value or "").strip()
    if not session_id or len(session_id) > 120:
        raise HTTPException(status_code=400, detail="A browser session identifier is required.")
    return session_id


def _agent_dict(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "instructions": agent.instructions,
        "allowed_tools": agent.allowed_tools,
        "knowledge_access": agent.knowledge_access,
        "source": agent.source,
        "sample": agent.source == "sample",
        "created_at": agent.created_at,
    }


def _task_dict(db: Session, task: Task) -> dict:
    steps = db.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.order_index)
    ).all()
    agent_ids = {step.agent_id for step in steps if step.agent_id}
    agents = {
        agent.id: agent.name
        for agent in db.scalars(select(Agent).where(Agent.id.in_(agent_ids))).all()
    } if agent_ids else {}
    return {
        "id": task.id,
        "instruction": task.instruction,
        "status": task.status,
        "plan": task.plan,
        "shared_context": task.shared_context,
        "session_id": task.session_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "steps": [
            {
                "id": step.id,
                "order_index": step.order_index,
                "description": step.description,
                "agent_id": step.agent_id,
                "agent_name": agents.get(step.agent_id, "Unassigned"),
                "tools": step.tools,
                "depends_on": step.depends_on,
                "status": step.status,
                "output": step.output,
                "sources": step.sources,
                "started_at": step.started_at,
                "completed_at": step.completed_at,
                "duration_seconds": (
                    (step.completed_at - step.started_at).total_seconds()
                    if step.started_at and step.completed_at else None
                ),
            }
            for step in steps
        ],
    }


def _event_dict(event: Event) -> dict:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "message": event.message,
        "details": event.details,
        "model": event.model,
        "created_at": event.created_at,
    }


def _check_session_caps(db: Session, session_id: str) -> None:
    task_count = db.scalar(
        select(func.count()).select_from(Task).where(Task.session_id == session_id)
    ) or 0
    request_cap = int(setting_value(db, "request_cap_per_session"))
    if task_count >= request_cap:
        raise HTTPException(status_code=429, detail="This browser session reached its task request cap.")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        configured = bool(
            get_config().openai_api_key
            and get_config().openai_base_url
            and setting_value(db, "model_name")
            and setting_value(db, "embedding_model_name")
        )
        connector = get_connector(get_config())
    except ValueError as error:
        raise HTTPException(status_code=503, detail=f"Runtime configuration is invalid: {error}") from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {error}") from error
    return {
            "status": "ok",
            "database": "connected",
            "ai_configured": configured,
            "cost_tracking_configured": all(
                float(setting_value(db, key)) > 0
                for key in ("input_cost_per_1k_usd", "output_cost_per_1k_usd", "embedding_cost_per_1k_usd")
            ),
            "connector": connector.name,
        }


@app.get("/api/tools")
def list_tools():
    return [
        {"id": tool, "label": tool.replace("_", " ").title()}
        for tool in sorted(AVAILABLE_TOOLS)
    ]


@app.get("/api/agents")
def list_agents(db: Session = Depends(get_db)):
    return [_agent_dict(agent) for agent in db.scalars(select(Agent).order_by(Agent.created_at)).all()]


@app.post("/api/agents")
def create_agent(payload: AgentInput, db: Session = Depends(get_db)):
    _validate_tools(payload.allowed_tools)
    agent = Agent(**payload.model_dump())
    db.add(agent)
    db.flush()
    db.add(Event(
        event_type="agent_created",
        message="An agent profile was created.",
        details={"agent_id": agent.id, "name": agent.name, "allowed_tools": agent.allowed_tools},
    ))
    db.commit()
    db.refresh(agent)
    return _agent_dict(agent)


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentInput, db: Session = Depends(get_db)):
    _validate_tools(payload.allowed_tools)
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    for key, value in payload.model_dump().items():
        setattr(agent, key, value)
    db.add(Event(
        event_type="agent_updated",
        message="An agent profile was updated.",
        details={"agent_id": agent.id, "name": agent.name, "allowed_tools": agent.allowed_tools},
    ))
    db.commit()
    db.refresh(agent)
    return _agent_dict(agent)


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    db.add(Event(
        event_type="agent_deleted",
        message="An agent profile was deleted.",
        details={"agent_id": agent.id, "name": agent.name},
    ))
    db.delete(agent)
    db.commit()
    return {"deleted": True}


def _validate_tools(tools: list[str]) -> None:
    if len(set(tools)) != len(tools) or not set(tools) <= AVAILABLE_TOOLS:
        raise HTTPException(status_code=422, detail="Choose unique tools from the available tool list.")


@app.get("/api/examples")
def list_examples(db: Session = Depends(get_db)):
    return setting_value(db, "example_instructions", [])


@app.post("/api/tasks")
async def create_task(
    payload: TaskInput,
    x_session_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not payload.instruction.strip():
        raise HTTPException(status_code=422, detail="Enter an instruction before starting a task.")
    session_id = _session_id(x_session_id)
    _check_session_caps(db, session_id)
    task = Task(instruction=payload.instruction.strip(), status="planned", session_id=session_id)
    db.add(task)
    db.flush()
    db.add(Event(
        task_id=task.id,
        event_type="task_created",
        message="A task was queued from the Control Hub instruction.",
        details={"instruction": task.instruction, "status": "planned"},
    ))
    db.add(Event(
        task_id=task.id,
        event_type="instruction_received",
        message="The submitted instruction is stored for orchestration.",
        details={"instruction": task.instruction},
    ))
    db.add(Event(
        task_id=task.id,
        event_type="state_transition",
        message="Task created in the planned state.",
        details={"from": None, "to": "planned"},
    ))
    db.commit()
    _schedule_task(app, task.id)
    db.refresh(task)
    return _task_dict(db, task)


@app.get("/api/tasks")
def list_tasks(limit: int = 25, db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(max(1, min(limit, 100)))).all()
    return [_task_dict(db, task) for task in tasks]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_dict(db, task)


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed tasks can be retried.")
    db.add(Event(task_id=task.id, event_type="retry_requested", message="A reviewer retried the failed task."))
    db.commit()
    _schedule_task(app, task.id)
    return {"status": "retrying"}


@app.get("/api/approvals")
def list_approvals(db: Session = Depends(get_db)):
    tasks = db.scalars(
        select(Task).where(Task.status == "awaiting_approval").order_by(Task.updated_at.desc())
    ).all()
    return [_task_dict(db, task) for task in tasks]


@app.post("/api/approvals/{task_id}")
def review_task(task_id: str, payload: ApprovalInput, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="This task is not awaiting approval.")
    step = db.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.status == "awaiting_approval",
        )
    )
    if step is None:
        raise HTTPException(status_code=409, detail="No reviewable draft is attached to this task.")
    if payload.action == "reject":
        reason = (payload.reason or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="A rejection reason is required.")
        context = dict(task.shared_context or {})
        feedback = dict(context.get("review_feedback") or {})
        feedback[step.id] = reason
        context["review_feedback"] = feedback
        task.shared_context = context
        step.status = "queued"
        step.completed_at = None
        db.add(Event(
            task_id=task.id,
            event_type="step_status",
            message="The reviewer rejected the draft.",
            details={"step_id": step.id, "agent_id": step.agent_id, "status": "rejected"},
        ))
        db.add(Event(
            task_id=task.id,
            event_type="step_status",
            message="The rejected draft was queued for regeneration.",
            details={"step_id": step.id, "agent_id": step.agent_id, "status": "queued"},
        ))
        transition_task(db, task, "rejected", "Reviewer rejected the draft.")
        db.add(Event(
            task_id=task.id,
            event_type="approval_review",
            message="Draft rejected; regeneration started using reviewer feedback.",
            details={"step_id": step.id, "action": "reject", "reason": reason},
        ))
        db.commit()
        _schedule_task(app, task.id)
        return {"status": "regenerating"}

    content = (payload.content or step.output).strip()
    if payload.action == "edit_approve" and not content:
        raise HTTPException(status_code=422, detail="Provide the edited draft before approving.")
    if not content:
        raise HTTPException(status_code=422, detail="The draft is empty.")
    try:
        connector = get_connector(get_config())
        connector.publish(content, payload.recipient)
    except (ValueError, OSError, SQLAlchemyError) as error:
        raise HTTPException(status_code=502, detail=f"The configured connector could not publish this item: {error}") from error
    edited = payload.action == "edit_approve"
    if edited:
        step.output = content
    step.status = "approved"
    db.add(Event(
        task_id=task.id,
        event_type="step_status",
        message="The final agent draft was approved.",
        details={"step_id": step.id, "agent_id": step.agent_id, "status": "approved"},
    ))
    transition_task(db, task, "approved", "Reviewer approved the draft.")
    db.add(Event(
        task_id=task.id,
        event_type="approval_review",
        message="Reviewer approved the draft.",
        details={"step_id": step.id, "action": payload.action, "edited": edited, "recipient": payload.recipient or ""},
    ))
    db.add(SentItem(
        task_id=task.id,
        content=content,
        item_type="approved_work",
        adapter=connector.name,
    ))
    transition_task(db, task, "done", "Approved work was published to the active connector.")
    db.commit()
    return {"status": "done", "adapter": connector.name}


@app.get("/api/events")
def list_events(
    task_id: str | None = None,
    limit: int = 100,
    x_session_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    session_id = _session_id(x_session_id)
    session_task_ids = select(Task.id).where(Task.session_id == session_id)
    statement = (
        select(Event)
        .where(
            or_(
                Event.task_id.in_(session_task_ids),
                Event.details["session_id"].as_string() == session_id,
            )
        )
        .order_by(Event.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if task_id:
        statement = statement.where(Event.task_id == task_id)
    events = db.scalars(statement).all()
    return [_event_dict(event) for event in reversed(events)]


@app.get("/api/tasks/{task_id}/events/stream")
async def stream_task_events(task_id: str, request: Request):
    async def stream():
        seen: set[str] = set()
        while not await request.is_disconnected():
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                if task is None:
                    yield "event: error\ndata: {\"message\":\"Task not found.\"}\n\n"
                    return
                events = db.scalars(
                    select(Event).where(Event.task_id == task_id).order_by(Event.created_at.desc()).limit(50)
                ).all()
                fresh = [event for event in reversed(events) if event.id not in seen]
                if fresh:
                    seen.update(event.id for event in fresh)
                    payload = {
                        "task": _task_dict(db, task),
                        "events": [_event_dict(event) for event in fresh],
                    }
                    yield f"event: update\ndata: {json.dumps(jsonable_encoder(payload), ensure_ascii=False)}\n\n"
                if task.status in {"done", "failed", "rejected"}:
                    return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/knowledge")
def list_documents(db: Session = Depends(get_db)):
    documents = db.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return [
        {
            "id": document.id,
            "name": document.name,
            "content_type": document.content_type,
            "created_at": document.created_at,
            "chunk_count": len(document.chunks),
            "sample": document.name.startswith("sample:"),
        }
        for document in documents
    ]


@app.post("/api/knowledge/upload")
async def upload_document(
    file: UploadFile = File(...),
    x_session_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    session_id = _session_id(x_session_id)
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a .txt, .md, or .pdf document.")
    content = await file.read()
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="The document must be non-empty and no larger than 10 MB.")
    try:
        extracted = extract_text(filename, file.content_type or "", content)
        document = await ingest_document(
            db, filename, file.content_type or "text/plain", extracted, session_id=session_id
        )
    except (ValueError, UnicodeDecodeError, PdfReadError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ModelRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    db.add(Event(
        event_type="knowledge_ingested",
        message="A document was chunked and embedded into the Knowledge Hub.",
        details={"document_id": document.id, "name": document.name, "chunk_count": len(document.chunks)},
    ))
    db.commit()
    return {"id": document.id, "name": document.name, "chunk_count": len(document.chunks)}


class PastedDocumentInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100000)


@app.post("/api/knowledge/paste")
async def paste_document(
    payload: PastedDocumentInput,
    x_session_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    session_id = _session_id(x_session_id)
    name = Path(payload.name).name
    if Path(name).suffix.lower() not in {".txt", ".md"}:
        raise HTTPException(status_code=415, detail="Pasted knowledge must be named as a .txt or .md file.")
    try:
        document = await ingest_document(
            db, name, "text/markdown", payload.content, session_id=session_id
        )
    except ModelRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    db.add(Event(
        event_type="knowledge_ingested",
        message="Pasted text was chunked and embedded into the Knowledge Hub.",
        details={"document_id": document.id, "name": document.name, "chunk_count": len(document.chunks)},
    ))
    db.commit()
    return {"id": document.id, "name": document.name, "chunk_count": len(document.chunks)}


@app.get("/api/enquiries")
def list_enquiries(db: Session = Depends(get_db)):
    return [
        {
            "id": item.id,
            "customer_name": item.customer_name,
            "customer_email": item.customer_email,
            "subject": item.subject,
            "message": item.message,
            "status": item.status,
            "source": item.source,
            "created_at": item.created_at,
        }
        for item in db.scalars(select(Enquiry).order_by(Enquiry.created_at.desc()).limit(200)).all()
    ]


@app.post("/api/enquiries")
def add_enquiry(payload: EnquiryInput, db: Session = Depends(get_db)):
    item = create_enquiry(db, **payload.model_dump())
    return {"id": item.id, "subject": item.subject, "source": item.source, "status": item.status}


@app.post("/api/sales/upload")
async def upload_sales(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="The sales CSV must be non-empty and no larger than 10 MB.")
    try:
        rows, _ = parse_sales_csv(content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    dataset = Path(file.filename or "sales.csv").name
    count = import_sales_rows(db, dataset, rows)
    db.add(Event(
        event_type="sales_imported",
        message="CSV sales data was imported into the database-backed read-only tool.",
        details={"dataset": dataset, "row_count": count},
    ))
    db.commit()
    return {"dataset": dataset, "row_count": count}


@app.post("/api/sales/rows")
def add_sales_rows(payload: SalesRowsInput, db: Session = Depends(get_db)):
    rows = [
        {str(key): str(value) for key, value in row.items()}
        for row in payload.rows
    ]
    count = import_sales_rows(db, payload.dataset, rows)
    db.add(Event(
        event_type="sales_imported",
        message="Sales rows were added to the database-backed read-only tool.",
        details={"dataset": payload.dataset, "row_count": count},
    ))
    db.commit()
    return {"dataset": payload.dataset, "row_count": count}


@app.get("/api/sales/summary")
def get_sales_summary(dataset: str | None = None, db: Session = Depends(get_db)):
    return sales_summary(db, dataset)


@app.get("/api/settings")
def list_settings(db: Session = Depends(get_db)):
    return [
        {"key": entry.key, "value": entry.value, "description": entry.description}
        for entry in sorted(settings_map(db).values(), key=lambda item: item.key)
    ]


@app.put("/api/settings/{key}")
async def update_setting(
    key: str,
    payload: SettingInput,
    x_session_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    setting = db.get(Setting, key)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    current = setting.value
    value = payload.value
    if isinstance(current, bool):
        valid = type(value) is bool
    elif isinstance(current, int):
        valid = type(value) is int
    elif isinstance(current, float):
        valid = type(value) in {int, float}
    elif isinstance(current, list):
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    else:
        valid = isinstance(value, str)
    if not valid:
        raise HTTPException(status_code=422, detail=f"Setting '{key}' requires the same JSON value type.")
    setting.value = value
    if key == "embedding_model_name" and value != current:
        chunks = db.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)).all()
        try:
            for chunk in chunks:
                chunk.embedding = await embed(db, chunk.content, session_id=_session_id(x_session_id))
        except ModelRequestError as error:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Could not rebuild knowledge embeddings: {error}") from error
        db.add(Event(
            event_type="knowledge_reindexed",
            message="Knowledge chunks were re-embedded with the updated embedding model.",
            details={"chunk_count": len(chunks), "model_name": value},
        ))
    db.add(Event(
        event_type="setting_updated",
        message="A runtime setting was updated.",
        details={"key": key},
    ))
    db.commit()
    return {"key": key, "value": setting.value, "description": setting.description}


@app.get("/api/connector")
def connector_status():
    try:
        connector = get_connector(get_config())
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"adapter": connector.name}


@app.get("/api/sent-items")
def list_sent_items(db: Session = Depends(get_db)):
    return [
        {
            "id": item.id,
            "task_id": item.task_id,
            "content": item.content,
            "item_type": item.item_type,
            "adapter": item.adapter,
            "created_at": item.created_at,
        }
        for item in db.scalars(select(SentItem).order_by(SentItem.created_at.desc()).limit(200)).all()
    ]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    events = db.scalars(select(Event).order_by(Event.created_at.desc()).limit(20000)).all()
    events.reverse()
    status_by_task: dict[str, str] = {}
    created_at: dict[str, datetime] = {}
    terminal_time: dict[str, datetime] = {}
    step_status: dict[str, tuple[str, str]] = {}
    approvals = 0
    edits = 0
    for event in events:
        if event.event_type == "task_created" and event.task_id:
            status_by_task[event.task_id] = "planned"
            created_at[event.task_id] = event.created_at
        elif event.event_type == "state_transition" and event.task_id:
            status = (event.details or {}).get("to")
            if status:
                status_by_task[event.task_id] = status
                if status in {"done", "failed", "rejected"}:
                    terminal_time[event.task_id] = event.created_at
        elif event.event_type == "step_status":
            details = event.details or {}
            step_status[details.get("step_id", "")] = (
                details.get("agent_id", ""),
                details.get("status", ""),
            )
        elif event.event_type == "approval_review":
            approvals += 1
            if (event.details or {}).get("edited"):
                edits += 1
    statuses: dict[str, int] = {}
    for status in status_by_task.values():
        statuses[status] = statuses.get(status, 0) + 1
    terminal = statuses.get("done", 0) + statuses.get("failed", 0) + statuses.get("rejected", 0)
    durations = [
        (terminal_time[task_id] - start).total_seconds()
        for task_id, start in created_at.items()
        if task_id in terminal_time and terminal_time[task_id] >= start
    ]
    agent_statuses: dict[str, dict[str, int]] = {}
    for agent_id, status in step_status.values():
        if agent_id:
            counts = agent_statuses.setdefault(agent_id, {})
            counts[status] = counts.get(status, 0) + 1
    return {
        "task_status_counts": statuses,
        "total_tasks": len(status_by_task),
        "success_rate": statuses.get("done", 0) / terminal if terminal else 0,
        "average_duration_seconds": sum(durations) / len(durations) if durations else None,
        "approval_count": approvals,
        "edit_rate": edits / approvals if approvals else 0,
        "agent_status_counts": agent_statuses,
    }
