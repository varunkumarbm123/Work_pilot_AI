import io
from dataclasses import dataclass

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import embed
from app.models import Document, KnowledgeChunk
from app.settings_store import setting_value


@dataclass
class RetrievedSource:
    document: str
    chunk_id: str
    content: str
    distance: float


def extract_text(name: str, content_type: str, content: bytes) -> str:
    if name.lower().endswith(".pdf") or content_type == "application/pdf":
        pages = PdfReader(io.BytesIO(content)).pages
        extracted = "\n".join(page.extract_text() or "" for page in pages)
    else:
        extracted = content.decode("utf-8-sig")
    if not extracted.strip():
        raise ValueError("The uploaded document contains no readable text.")
    return extracted


async def ingest_document(
    db: Session,
    name: str,
    content_type: str,
    text: str,
    task_id: str | None = None,
    step_id: str | None = None,
    session_id: str | None = None,
) -> Document:
    chunk_size = max(100, min(int(setting_value(db, "chunk_size")), 8000))
    chunks = [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
    document = Document(name=name, content_type=content_type)
    db.add(document)
    db.flush()
    for index, content in enumerate(chunks):
        vector = await embed(
            db, content, task_id=task_id, step_id=step_id, session_id=session_id
        )
        db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=index, content=content, embedding=vector
        ))
    db.commit()
    db.refresh(document)
    return document


async def retrieve(
    db: Session,
    query: str,
    top_k: int | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
) -> list[RetrievedSource]:
    vector = await embed(db, query, task_id=task_id, step_id=step_id)
    count = int(setting_value(db, "top_k")) if top_k is None else top_k
    count = max(1, min(count, 20))
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    rows = db.execute(
        select(Document.name, KnowledgeChunk.id, KnowledgeChunk.content, distance.label("distance"))
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(KnowledgeChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(count)
    ).all()
    return [
        RetrievedSource(document=row.name, chunk_id=row.id, content=row.content, distance=float(row.distance))
        for row in rows
    ]
