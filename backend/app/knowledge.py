import io
import re
from dataclasses import dataclass

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm import embed, provider_supports_embeddings
from app.models import Document, KnowledgeChunk
from app.settings_store import setting_value


@dataclass
class RetrievedSource:
    document: str
    chunk_id: str
    content: str
    distance: float


def _full_text_query(query: str) -> str:
    terms = list(dict.fromkeys(re.findall(r"[A-Za-z0-9_]+", query.lower())))[:64]
    return " | ".join(terms)


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
    workspace_id: str,
    name: str,
    content_type: str,
    text: str,
    task_id: str | None = None,
    step_id: str | None = None,
    session_id: str | None = None,
) -> Document:
    chunk_size = max(
        100, min(int(setting_value(db, "chunk_size", workspace_id=workspace_id)), 8000)
    )
    overlap = max(
        0,
        min(
            int(setting_value(db, "chunk_overlap", workspace_id=workspace_id)),
            chunk_size // 2,
        ),
    )
    stride = chunk_size - overlap
    chunks = []
    for index in range(0, len(text), stride):
        chunks.append(text[index : index + chunk_size])
        if index + chunk_size >= len(text):
            break
    document = Document(
        workspace_id=workspace_id,
        name=name,
        content_type=content_type,
    )
    db.add(document)
    db.flush()
    for index, content in enumerate(chunks):
        vector = None
        if provider_supports_embeddings():
            vector = await embed(
                db,
                content,
                task_id=task_id,
                step_id=step_id,
                session_id=session_id or workspace_id,
                workspace_id=workspace_id,
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
    workspace_id: str,
    top_k: int | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
) -> list[RetrievedSource]:
    count = (
        int(setting_value(db, "top_k", workspace_id=workspace_id))
        if top_k is None
        else top_k
    )
    count = max(1, min(count, 20))
    if provider_supports_embeddings():
        vector = await embed(
            db, query, task_id=task_id, step_id=step_id, workspace_id=workspace_id
        )
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        statement = (
            select(Document.name, KnowledgeChunk.id, KnowledgeChunk.content, distance.label("distance"))
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .where(
                Document.workspace_id == workspace_id,
                KnowledgeChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(count)
        )
    else:
        search_vector = func.to_tsvector("simple", KnowledgeChunk.content)
        query_terms = _full_text_query(query)
        if not query_terms:
            return []
        query_vector = func.to_tsquery("simple", query_terms)
        rank = func.ts_rank_cd(search_vector, query_vector)
        statement = (
            select(Document.name, KnowledgeChunk.id, KnowledgeChunk.content, (1 - rank).label("distance"))
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .where(
                Document.workspace_id == workspace_id,
                search_vector.op("@@")(query_vector),
            )
            .order_by(rank.desc())
            .limit(count)
        )
    rows = db.execute(statement).all()
    return [
        RetrievedSource(document=row.name, chunk_id=row.id, content=row.content, distance=float(row.distance))
        for row in rows
    ]
