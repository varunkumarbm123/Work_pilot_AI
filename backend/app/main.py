from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf.errors import PdfReadError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import SessionLocal
from app.enquiries import create_enquiry
from app.knowledge import extract_text, ingest_document
from app.migrate import run_migrations
from app.models import Document, Enquiry, SalesRow, Setting
from app.sales import import_sales_rows, parse_sales_csv, sales_summary
from app.seed_import import import_seed_data
from app.settings_store import settings_map


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


class PastedDocumentInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_config().seed_on_startup:
        await import_seed_data()
    else:
        run_migrations()
    yield


app = FastAPI(
    title="WorkPilot",
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


def get_db():
    with SessionLocal() as db:
        yield db


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {error}") from error
    return {"status": "ok", "database": "connected"}


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return {
        "document_count": db.scalar(select(func.count()).select_from(Document)) or 0,
        "enquiry_count": db.scalar(select(func.count()).select_from(Enquiry)) or 0,
        "sales": sales_summary(db),
    }


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
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a .txt, .md, or .pdf document.")
    content = await file.read()
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="The document must be non-empty and no larger than 10 MB.")
    try:
        extracted = extract_text(filename, file.content_type or "", content)
        document = await ingest_document(db, filename, file.content_type or "text/plain", extracted)
    except (ValueError, UnicodeDecodeError, PdfReadError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"id": document.id, "name": document.name, "chunk_count": len(document.chunks)}


@app.post("/api/knowledge/paste")
async def paste_document(payload: PastedDocumentInput, db: Session = Depends(get_db)):
    name = Path(payload.name).name
    if Path(name).suffix.lower() not in {".txt", ".md"}:
        raise HTTPException(status_code=415, detail="Pasted text must be named as a .txt or .md file.")
    document = await ingest_document(db, name, "text/markdown", payload.content)
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
    return {"dataset": dataset, "row_count": count}


@app.post("/api/sales/rows")
def add_sales_rows(payload: SalesRowsInput, db: Session = Depends(get_db)):
    rows = [{str(key): str(value) for key, value in row.items()} for row in payload.rows]
    count = import_sales_rows(db, payload.dataset, rows)
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
def update_setting(key: str, payload: SettingInput, db: Session = Depends(get_db)):
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
    db.commit()
    return {"key": key, "value": setting.value, "description": setting.description}


@app.get("/api/sales/datasets")
def list_sales_datasets(db: Session = Depends(get_db)):
    return db.scalars(select(SalesRow.dataset).distinct().order_by(SalesRow.dataset)).all()
