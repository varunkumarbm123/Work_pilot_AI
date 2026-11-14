import io

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models import Document, KnowledgeChunk
from app.settings_store import setting_value


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
) -> Document:
    chunk_size = max(100, min(int(setting_value(db, "chunk_size")), 8000))
    chunks = [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
    document = Document(name=name, content_type=content_type)
    db.add(document)
    db.flush()
    for index, content in enumerate(chunks):
        db.add(KnowledgeChunk(
            document_id=document.id, chunk_index=index, content=content
        ))
    db.commit()
    db.refresh(document)
    return document
