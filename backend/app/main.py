import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pypdf.errors import PdfReadError
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_config
from app.auth import (
    DUMMY_PASSWORD_HASH,
    create_session,
    get_session_user,
    hash_password,
    revoke_session,
    verify_password,
)
from app.connectors import get_connector
from app.database import SessionLocal
from app.enquiries import create_enquiry
from app.knowledge import extract_text, ingest_document
from app.limits import reserve_session_request
from app.llm import ModelRequestError, embed, provider_is_configured, provider_supports_embeddings
from app.migrate import run_migrations
from app.models import (
    Agent,
    Document,
    Enquiry,
    Event,
    KnowledgeChunk,
    SentItem,
    Setting,
    Task,
    TaskStep,
    User,
    Workspace,
)
from app.orchestrator import AVAILABLE_TOOLS, normalize_agent_tools
from app.sales import import_sales_rows, parse_sales_csv, sales_summary
from app.seed_import import import_seed_data, initialize_workspace
from app.settings_store import setting_value, settings_map, upsert_workspace_setting
from app.state import transition_task
from app.status import StepStatus, TaskStatus
from app.workflow import execute_task


class TaskInput(BaseModel):
    instruction: str = Field(min_length=1, max_length=10000)
    agent_id: str | None = Field(default=None, max_length=36)


class AuthInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


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


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    workspace_id: str
    session_id: str
    email: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_config().seed_on_startup:
        await import_seed_data()
    else:
        run_migrations()
    app.state.background_tasks = set()
    with SessionLocal() as db:
        system_workspace_id = db.scalar(
            select(Workspace.id).where(Workspace.is_system.is_(True))
        )
        recoverable_ids = db.scalars(
            select(Task.id).where(
                Task.workspace_id != system_workspace_id,
                Task.status.in_(
                    (TaskStatus.PLANNED.value, TaskStatus.RUNNING.value, TaskStatus.REJECTED.value)
                )
            )
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
origins = [origin.rstrip("/") for origin in origins]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_public_writes(request: Request, call_next):
    public_paths = {
        "/health",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/docs",
        "/api/openapi.json",
        "/docs/oauth2-redirect",
    }
    if request.method == "OPTIONS":
        return await call_next(request)
    workspace_id = None
    session_id = None
    if request.url.path not in public_paths:
        token = request.cookies.get(get_config().auth_cookie_name)
        with SessionLocal() as db:
            user = get_session_user(db, token)
            if user is None:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Sign in to access this workspace."},
                )
                response.delete_cookie(
                    key=get_config().auth_cookie_name,
                    path="/",
                    secure=get_config().auth_cookie_secure,
                    httponly=True,
                    samesite=get_config().auth_cookie_samesite.lower(),
                )
                return response
            workspace_id = user.workspace_id
            session_id = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
            request.state.auth = AuthContext(
                user_id=user.id,
                workspace_id=user.workspace_id,
                session_id=session_id,
                email=user.email,
            )

    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin or origin not in origins:
            return JSONResponse(
                status_code=403,
                content={"detail": "The request origin is not allowed."},
            )
        try:
            client_host = request.client.host if request.client else "unknown"
            request_key = session_id or (
                "anonymous:" + hashlib.sha256(client_host.encode("utf-8")).hexdigest()
            )
            with SessionLocal() as db:
                reserve_session_request(db, request_key, workspace_id=workspace_id)
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


def current_auth(request: Request) -> AuthContext:
    context = getattr(request.state, "auth", None)
    if context is None:
        raise HTTPException(status_code=401, detail="Sign in to access this workspace.")
    return context


def current_workspace_id(request: Request) -> str:
    return current_auth(request).workspace_id


def _set_auth_cookie(response: JSONResponse, token: str) -> None:
    config = get_config()
    same_site = config.auth_cookie_samesite.lower()
    if same_site not in {"lax", "strict", "none"}:
        raise HTTPException(status_code=500, detail="AUTH_COOKIE_SAMESITE must be lax, strict, or none.")
    if same_site == "none" and not config.auth_cookie_secure:
        raise HTTPException(
            status_code=500,
            detail="AUTH_COOKIE_SECURE must be true when AUTH_COOKIE_SAMESITE is none.",
        )
    lifetime_days = max(1, min(config.auth_session_days, 90))
    max_age = lifetime_days * 24 * 60 * 60
    response.set_cookie(
        key=config.auth_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=config.auth_cookie_secure,
        samesite=same_site,
        path="/",
    )


@app.post("/api/auth/signup")
def signup(payload: AuthInput, db: Session = Depends(get_db)):
    if len(payload.password) < 12:
        raise HTTPException(
            status_code=422,
            detail="Use a password with at least 12 characters.",
        )
    email = payload.email.strip().lower()
    if not re.fullmatch(
        r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}",
        email,
        flags=re.IGNORECASE,
    ):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    workspace = Workspace(name="Business workspace")
    db.add(workspace)
    db.flush()
    user = User(
        workspace_id=workspace.id,
        email=email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.flush()
        initialize_workspace(db, workspace.id)
        token, _ = create_session(db, user.id, max(1, min(get_config().auth_session_days, 90)))
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account could not be created for those details.",
        ) from error
    response = JSONResponse({"email": user.email})
    _set_auth_cookie(response, token)
    return response


@app.post("/api/auth/login")
def login(payload: AuthInput, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    password_ok = (
        verify_password(payload.password, user.password_hash)
        if user is not None
        else verify_password(payload.password, DUMMY_PASSWORD_HASH)
    )
    if user is None or not password_ok:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    token, _ = create_session(db, user.id, max(1, min(get_config().auth_session_days, 90)))
    db.commit()
    response = JSONResponse({"email": user.email})
    _set_auth_cookie(response, token)
    return response


@app.get("/api/auth/me")
def auth_me(
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
):
    workspace = db.get(Workspace, auth.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=401, detail="This account's workspace is unavailable.")
    return {"email": auth.email, "workspace": workspace.name}


@app.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    revoke_session(
        db,
        request.cookies.get(get_config().auth_cookie_name),
    )
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(
        key=get_config().auth_cookie_name,
        path="/",
        secure=get_config().auth_cookie_secure,
        httponly=True,
        samesite=get_config().auth_cookie_samesite.lower(),
    )
    return response


def _agent_dict(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "instructions": agent.instructions,
        "allowed_tools": normalize_agent_tools(agent.allowed_tools),
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
        for agent in db.scalars(
            select(Agent).where(
                Agent.workspace_id == task.workspace_id,
                Agent.id.in_(agent_ids),
            )
        ).all()
    } if agent_ids else {}
    return {
        "id": task.id,
        "instruction": task.instruction,
        "status": task.status,
        "plan": task.plan,
        "shared_context": task.shared_context,
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


def _workspace_task(db: Session, task_id: str, workspace_id: str) -> Task:
    task = db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.workspace_id == workspace_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


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


def _check_session_caps(
    db: Session, session_id: str, workspace_id: str
) -> None:
    task_count = db.scalar(
        select(func.count()).select_from(Task).where(Task.session_id == session_id)
    ) or 0
    request_cap = int(
        setting_value(
            db,
            "request_cap_per_session",
            workspace_id=workspace_id,
        )
    )
    if task_count >= request_cap:
        raise HTTPException(status_code=429, detail="This sign-in session reached its task request cap.")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        configured = bool(
            provider_is_configured()
            and setting_value(db, "model_name")
            and (
                setting_value(db, "embedding_model_name")
                or not provider_supports_embeddings()
            )
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
            "ai_provider": get_config().llm_provider,
            "billing_currency": str(setting_value(db, "billing_currency")),
            "status_codes": {
                "task": {status.name.lower(): status.value for status in TaskStatus},
                "step": {status.name.lower(): status.value for status in StepStatus},
            },
            "cost_tracking_configured": all(
                float(setting_value(db, key)) > 0
                for key in (
                    "input_cost_per_1k_tokens",
                    "output_cost_per_1k_tokens",
                    *(
                        ("embedding_cost_per_1k_tokens",)
                        if provider_supports_embeddings()
                        else ()
                    ),
                )
            ) and bool(str(setting_value(db, "billing_currency")).strip()),
            "connector": connector.name,
        }


@app.get("/api/tools")
def list_tools():
    return [
        {"id": tool, "label": tool.replace("_", " ").title()}
        for tool in sorted(AVAILABLE_TOOLS)
    ]


@app.get("/api/agents")
def list_agents(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    agents = db.scalars(
        select(Agent)
        .where(Agent.workspace_id == workspace_id)
        .order_by(Agent.created_at)
    ).all()
    return [_agent_dict(agent) for agent in agents]


@app.post("/api/agents")
def create_agent(
    payload: AgentInput,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    _validate_tools(payload.allowed_tools)
    agent = Agent(workspace_id=workspace_id, **payload.model_dump())
    db.add(agent)
    db.flush()
    db.add(Event(
        workspace_id=workspace_id,
        event_type="agent_created",
        message="An agent profile was created.",
        details={"agent_id": agent.id, "name": agent.name, "allowed_tools": agent.allowed_tools},
    ))
    db.commit()
    db.refresh(agent)
    return _agent_dict(agent)


@app.put("/api/agents/{agent_id}")
def update_agent(
    agent_id: str,
    payload: AgentInput,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    _validate_tools(payload.allowed_tools)
    agent = db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.workspace_id == workspace_id,
        )
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    for key, value in payload.model_dump().items():
        setattr(agent, key, value)
    db.add(Event(
        workspace_id=workspace_id,
        event_type="agent_updated",
        message="An agent profile was updated.",
        details={"agent_id": agent.id, "name": agent.name, "allowed_tools": agent.allowed_tools},
    ))
    db.commit()
    db.refresh(agent)
    return _agent_dict(agent)


@app.delete("/api/agents/{agent_id}")
def delete_agent(
    agent_id: str,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    agent = db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.workspace_id == workspace_id,
        )
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    db.add(Event(
        workspace_id=workspace_id,
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
def list_examples(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    return setting_value(
        db, "example_instructions", [], workspace_id=workspace_id
    )


@app.post("/api/tasks")
async def create_task(
    payload: TaskInput,
    auth: AuthContext = Depends(current_auth),
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    if not payload.instruction.strip():
        raise HTTPException(status_code=422, detail="Enter an instruction before starting a task.")
    _check_session_caps(db, auth.session_id, workspace_id)
    shared_context = {}
    if payload.agent_id:
        agent = db.scalar(
            select(Agent).where(
                Agent.id == payload.agent_id,
                Agent.workspace_id == workspace_id,
            )
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="Selected agent not found.")
        shared_context["agent_filter"] = [agent.id]
    task = Task(
        workspace_id=workspace_id,
        instruction=payload.instruction.strip(),
        status=TaskStatus.PLANNED.value,
        session_id=auth.session_id,
        shared_context=shared_context,
    )
    db.add(task)
    db.flush()
    db.add(Event(
        workspace_id=workspace_id,
        task_id=task.id,
        event_type="task_created",
        message="A task was queued from the Control Hub instruction.",
        details={
            "instruction": task.instruction,
            "status": TaskStatus.PLANNED.value,
            "agent_filter": shared_context.get("agent_filter", []),
        },
    ))
    db.add(Event(
        workspace_id=workspace_id,
        task_id=task.id,
        event_type="instruction_received",
        message="The submitted instruction is stored for orchestration.",
        details={"instruction": task.instruction},
    ))
    db.add(Event(
        workspace_id=workspace_id,
        task_id=task.id,
        event_type="state_transition",
        message="Task created in the planned state.",
        details={"from": None, "to": TaskStatus.PLANNED.value},
    ))
    db.commit()
    _schedule_task(app, task.id)
    db.refresh(task)
    return _task_dict(db, task)


@app.get("/api/tasks")
def list_tasks(
    limit: int = 25,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    tasks = db.scalars(
        select(Task)
        .where(Task.workspace_id == workspace_id)
        .order_by(Task.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [_task_dict(db, task) for task in tasks]


@app.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    task = _workspace_task(db, task_id, workspace_id)
    return _task_dict(db, task)


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    task = _workspace_task(db, task_id, workspace_id)
    if task.status != TaskStatus.FAILED.value:
        raise HTTPException(status_code=409, detail="Only failed tasks can be retried.")
    db.add(Event(task_id=task.id, event_type="retry_requested", message="A reviewer retried the failed task."))
    db.commit()
    _schedule_task(app, task.id)
    return {"status": "retrying"}


@app.get("/api/approvals")
def list_approvals(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    tasks = db.scalars(
        select(Task)
        .where(
            Task.workspace_id == workspace_id,
            Task.status == TaskStatus.AWAITING_APPROVAL.value,
        )
        .order_by(Task.updated_at.desc())
    ).all()
    return [_task_dict(db, task) for task in tasks]


@app.post("/api/approvals/{task_id}")
async def review_task(
    task_id: str,
    payload: ApprovalInput,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    task = _workspace_task(db, task_id, workspace_id)
    if task.status != TaskStatus.AWAITING_APPROVAL.value:
        raise HTTPException(status_code=409, detail="This task is not awaiting approval.")
    step = db.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.status == StepStatus.AWAITING_APPROVAL.value,
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
        step.status = StepStatus.QUEUED.value
        step.completed_at = None
        db.add(Event(
            task_id=task.id,
            event_type="step_status",
            message="The reviewer rejected the draft.",
            details={
                "step_id": step.id,
                "agent_id": step.agent_id,
                "status": StepStatus.REJECTED.value,
            },
        ))
        db.add(Event(
            task_id=task.id,
            event_type="step_status",
            message="The rejected draft was queued for regeneration.",
            details={
                "step_id": step.id,
                "agent_id": step.agent_id,
                "status": StepStatus.QUEUED.value,
            },
        ))
        transition_task(db, task, TaskStatus.REJECTED, "Reviewer rejected the draft.")
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
    step.status = StepStatus.APPROVED.value
    db.add(Event(
        task_id=task.id,
        event_type="step_status",
        message="The final agent draft was approved.",
        details={
            "step_id": step.id,
            "agent_id": step.agent_id,
            "status": StepStatus.APPROVED.value,
        },
    ))
    transition_task(db, task, TaskStatus.APPROVED, "Reviewer approved the draft.")
    db.add(Event(
        task_id=task.id,
        event_type="approval_review",
        message="Reviewer approved the draft.",
        details={"step_id": step.id, "action": payload.action, "edited": edited, "recipient": payload.recipient or ""},
    ))
    db.add(SentItem(
        workspace_id=workspace_id,
        task_id=task.id,
        content=content,
        item_type="approved_work",
        adapter=connector.name,
    ))
    transition_task(db, task, TaskStatus.DONE, "Approved work was published to the active connector.")
    db.commit()
    return {"status": TaskStatus.DONE.value, "adapter": connector.name}


@app.get("/api/events")
def list_events(
    task_id: str | None = None,
    limit: int = 100,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    session_task_ids = select(Task.id).where(Task.workspace_id == workspace_id)
    statement = (
        select(Event)
        .where(
            or_(
                Event.workspace_id == workspace_id,
                Event.task_id.in_(session_task_ids),
                Event.details["session_id"].as_string() == workspace_id,
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
async def stream_task_events(
    task_id: str,
    request: Request,
    workspace_id: str = Depends(current_workspace_id),
):
    async def stream():
        seen: set[str] = set()
        while not await request.is_disconnected():
            with SessionLocal() as db:
                task = db.scalar(
                    select(Task).where(
                        Task.id == task_id,
                        Task.workspace_id == workspace_id,
                    )
                )
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
                if task.status in {
                    TaskStatus.DONE.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.REJECTED.value,
                }:
                    return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/knowledge")
def list_documents(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    documents = db.scalars(
        select(Document)
        .where(Document.workspace_id == workspace_id)
        .order_by(Document.created_at.desc())
    ).all()
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
    auth: AuthContext = Depends(current_auth),
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a .txt, .md, or .pdf document.")
    content = await file.read()
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="The document must be non-empty and no larger than 10 MB.")
    try:
        extracted = extract_text(filename, file.content_type or "", content)
        document = await ingest_document(
            db,
            workspace_id,
            filename,
            file.content_type or "text/plain",
            extracted,
            session_id=auth.session_id,
        )
    except (ValueError, UnicodeDecodeError, PdfReadError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ModelRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    db.add(Event(
        workspace_id=workspace_id,
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
    auth: AuthContext = Depends(current_auth),
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    name = Path(payload.name).name
    if Path(name).suffix.lower() not in {".txt", ".md"}:
        raise HTTPException(status_code=415, detail="Pasted knowledge must be named as a .txt or .md file.")
    try:
        document = await ingest_document(
            db,
            workspace_id,
            name,
            "text/markdown",
            payload.content,
            session_id=auth.session_id,
        )
    except ModelRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    db.add(Event(
        workspace_id=workspace_id,
        event_type="knowledge_ingested",
        message="Pasted text was chunked and embedded into the Knowledge Hub.",
        details={"document_id": document.id, "name": document.name, "chunk_count": len(document.chunks)},
    ))
    db.commit()
    return {"id": document.id, "name": document.name, "chunk_count": len(document.chunks)}


@app.get("/api/enquiries")
def list_enquiries(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
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
        for item in db.scalars(
            select(Enquiry)
            .where(Enquiry.workspace_id == workspace_id)
            .order_by(Enquiry.created_at.desc())
            .limit(200)
        ).all()
    ]


@app.post("/api/enquiries")
def add_enquiry(
    payload: EnquiryInput,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    item = create_enquiry(db, workspace_id, **payload.model_dump())
    return {"id": item.id, "subject": item.subject, "source": item.source, "status": item.status}


@app.post("/api/sales/upload")
async def upload_sales(
    file: UploadFile = File(...),
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="The sales CSV must be non-empty and no larger than 10 MB.")
    try:
        rows, _ = parse_sales_csv(content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    dataset = Path(file.filename or "sales.csv").name
    count = import_sales_rows(db, workspace_id, dataset, rows)
    db.add(Event(
        workspace_id=workspace_id,
        event_type="sales_imported",
        message="CSV sales data was imported into the database-backed read-only tool.",
        details={"dataset": dataset, "row_count": count},
    ))
    db.commit()
    return {"dataset": dataset, "row_count": count}


@app.post("/api/sales/rows")
def add_sales_rows(
    payload: SalesRowsInput,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    rows = [
        {str(key): str(value) for key, value in row.items()}
        for row in payload.rows
    ]
    count = import_sales_rows(db, workspace_id, payload.dataset, rows)
    db.add(Event(
        workspace_id=workspace_id,
        event_type="sales_imported",
        message="Sales rows were added to the database-backed read-only tool.",
        details={"dataset": payload.dataset, "row_count": count},
    ))
    db.commit()
    return {"dataset": payload.dataset, "row_count": count}


@app.get("/api/sales/summary")
def get_sales_summary(
    dataset: str | None = None,
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    return sales_summary(db, workspace_id, dataset)


@app.get("/api/settings")
def list_settings(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    return [
        {"key": entry.key, "value": entry.value, "description": entry.description}
        for entry in sorted(
            settings_map(db, workspace_id).values(),
            key=lambda item: item.key,
        )
    ]


@app.put("/api/settings/{key}")
async def update_setting(
    key: str,
    payload: SettingInput,
    auth: AuthContext = Depends(current_auth),
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    default_setting = db.get(Setting, key)
    if default_setting is None:
        raise HTTPException(status_code=404, detail="Setting not found.")
    current = setting_value(db, key, workspace_id=workspace_id)
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
    if key == "billing_currency" and value and not re.fullmatch(r"[A-Z]{3}", value):
        raise HTTPException(status_code=422, detail="Enter a three-letter uppercase billing currency code.")
    if key == "embedding_model_name" and not provider_supports_embeddings():
        raise HTTPException(
            status_code=409,
            detail="The selected model provider has no embedding API; knowledge retrieval uses PostgreSQL full-text search.",
        )
    setting = upsert_workspace_setting(
        db,
        workspace_id,
        key,
        value,
        default_setting.description,
    )
    if key == "embedding_model_name" and value != current:
        chunks = db.scalars(
            select(KnowledgeChunk)
            .join(Document, KnowledgeChunk.document_id == Document.id)
            .where(Document.workspace_id == workspace_id)
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
        ).all()
        try:
            for chunk in chunks:
                chunk.embedding = await embed(
                    db,
                    chunk.content,
                    session_id=auth.session_id,
                    workspace_id=workspace_id,
                )
        except ModelRequestError as error:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Could not rebuild knowledge embeddings: {error}") from error
        db.add(Event(
            workspace_id=workspace_id,
            event_type="knowledge_reindexed",
            message="Knowledge chunks were re-embedded with the updated embedding model.",
            details={"chunk_count": len(chunks), "model_name": value},
        ))
    db.add(Event(
        workspace_id=workspace_id,
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
def list_sent_items(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    return [
        {
            "id": item.id,
            "task_id": item.task_id,
            "content": item.content,
            "item_type": item.item_type,
            "adapter": item.adapter,
            "created_at": item.created_at,
        }
        for item in db.scalars(
            select(SentItem)
            .where(SentItem.workspace_id == workspace_id)
            .order_by(SentItem.created_at.desc())
            .limit(200)
        ).all()
    ]


@app.get("/api/dashboard")
def dashboard(
    workspace_id: str = Depends(current_workspace_id),
    db: Session = Depends(get_db),
):
    workspace_task_ids = select(Task.id).where(Task.workspace_id == workspace_id)
    events = db.scalars(
        select(Event)
        .where(
            or_(
                Event.workspace_id == workspace_id,
                Event.task_id.in_(workspace_task_ids),
            )
        )
        .order_by(Event.created_at.desc())
        .limit(20000)
    ).all()
    events.reverse()
    status_by_task: dict[str, str] = {}
    created_at: dict[str, datetime] = {}
    terminal_time: dict[str, datetime] = {}
    step_status: dict[str, tuple[str, str]] = {}
    approvals = 0
    edits = 0
    for event in events:
        if event.event_type == "task_created" and event.task_id:
            status_by_task[event.task_id] = TaskStatus.PLANNED.value
            created_at[event.task_id] = event.created_at
        elif event.event_type == "state_transition" and event.task_id:
            status = (event.details or {}).get("to")
            if status:
                status_by_task[event.task_id] = status
                if status in {
                    TaskStatus.DONE.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.REJECTED.value,
                }:
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
    terminal = sum(
        statuses.get(status.value, 0)
        for status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.REJECTED)
    )
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
        "success_rate": statuses.get(TaskStatus.DONE.value, 0) / terminal if terminal else 0,
        "average_duration_seconds": sum(durations) / len(durations) if durations else None,
        "approval_count": approvals,
        "edit_rate": edits / approvals if approvals else 0,
        "agent_status_counts": agent_statuses,
    }
