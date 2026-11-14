import asyncio
import csv
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import SessionLocal
from app.enquiries import create_enquiry
from app.knowledge import ingest_document
from app.llm import ModelRequestError
from app.migrate import run_migrations
from app.models import Agent, Document, Enquiry, Event, Setting
from app.sales import import_sales_rows
from app.settings_store import upsert_setting


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "seed"


async def import_seed_data() -> None:
    config = get_config()
    run_migrations()
    with SessionLocal() as db:
        for record in json.loads((SEED / "settings.json").read_text(encoding="utf-8")):
            if db.get(Setting, record["key"]) is None:
                value = record["value"]
                if record["key"] == "model_name" and not value:
                    value = config.openai_model
                elif record["key"] == "embedding_model_name" and not value:
                    value = config.openai_embedding_model
                upsert_setting(db, record["key"], value, record["description"])
        if db.get(Setting, "example_instructions") is None:
            upsert_setting(
                db, "example_instructions",
                json.loads((SEED / "example-instructions.json").read_text(encoding="utf-8")),
                "Sample instructions displayed by the Control Hub.",
            )
        if db.get(Setting, "model_name") is None:
            upsert_setting(db, "model_name", config.openai_model, "Chat model name; provider credentials remain server-side.")
        if db.get(Setting, "embedding_model_name") is None:
            upsert_setting(db, "embedding_model_name", config.openai_embedding_model, "Embedding model name; provider credentials remain server-side.")
        db.flush()
        agent_records = json.loads((SEED / "agents.json").read_text(encoding="utf-8"))
        if db.scalar(select(Event).where(Event.event_type == "seed_agents_imported")) is None:
            for record in agent_records:
                db.add(Agent(**record, source="sample"))
            db.add(Event(
                event_type="seed_agents_imported",
                message="Starter agent records were imported from seed files.",
                details={"agent_count": len(agent_records)},
            ))
            db.commit()
        existing_sample = db.scalar(select(Document).where(Document.name == "sample: knowledge.md"))
        db.commit()
        if existing_sample is None:
            if config.openai_api_key and config.openai_base_url and config.openai_embedding_model:
                content = (SEED / "knowledge.md").read_text(encoding="utf-8")
                try:
                    document = await ingest_document(db, "sample: knowledge.md", "text/markdown", content)
                    db.add(Event(
                        event_type="seed_knowledge_ready",
                        message="Sample knowledge was embedded and indexed.",
                        details={"document_id": document.id, "chunk_count": len(document.chunks)},
                    ))
                    db.commit()
                except ModelRequestError as error:
                    db.rollback()
                    _record_pending_knowledge(db, error)
            else:
                _record_pending_knowledge(db, None)

        if db.scalar(select(Enquiry).where(Enquiry.source == "sample")) is None:
            for record in json.loads((SEED / "enquiries.json").read_text(encoding="utf-8")):
                create_enquiry(db, **record, source="sample")
        with (SEED / "sales.csv").open(newline="", encoding="utf-8") as csv_file:
            rows = list(csv.DictReader(csv_file))
        if db.scalar(select(Event).where(Event.event_type == "seed_imported")) is None:
            import_sales_rows(db, "sample: sales.csv", rows)
            db.add(Event(
                event_type="seed_imported",
                message="Sample settings, sales rows, and instructions were imported.",
                details={"dataset": "sample: sales.csv", "sales_rows": len(rows)},
            ))
            db.commit()


def _record_pending_knowledge(db: Session, error: ModelRequestError | None) -> None:
    if db.scalar(select(Event).where(Event.event_type == "seed_knowledge_pending")) is None:
        db.add(Event(
            event_type="seed_knowledge_pending",
            message=(
                "Sample knowledge embedding is pending provider configuration."
                if error is None
                else f"Sample knowledge embedding failed: {error}"
            ),
            details={
                "required": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_EMBEDDING_MODEL"],
                "error_type": type(error).__name__ if error is not None else "",
            },
        ))
        db.commit()


if __name__ == "__main__":
    asyncio.run(import_seed_data())
