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
from app.llm import ModelRequestError, provider_is_configured, provider_supports_embeddings
from app.migrate import run_migrations
from app.models import Agent, Document, Enquiry, Event, KnowledgeChunk, SalesRow, Setting, Workspace
from app.sales import import_sales_rows
from app.settings_store import upsert_setting


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "seed"


async def import_seed_data() -> None:
    config = get_config()
    provider = config.llm_provider.strip().lower()
    default_model = {
        "gemini": config.gemini_model,
        "groq": config.groq_model,
        "openai": config.openai_model,
    }.get(provider, "")
    default_embedding_model = {
        "gemini": config.gemini_embedding_model,
        "openai": config.openai_embedding_model,
    }.get(provider, "")
    run_migrations()
    with SessionLocal() as db:
        system_workspace = db.scalar(select(Workspace).where(Workspace.is_system.is_(True)))
        if system_workspace is None:
            raise RuntimeError("The system workspace is missing after migrations.")
        workspace_id = system_workspace.id
        for record in json.loads((SEED / "settings.json").read_text(encoding="utf-8")):
            if db.get(Setting, record["key"]) is None:
                value = record["value"]
                if record["key"] == "model_name" and not value:
                    value = default_model
                elif record["key"] == "embedding_model_name" and not value:
                    value = default_embedding_model
                upsert_setting(db, record["key"], value, record["description"])
        if db.get(Setting, "example_instructions") is None:
            upsert_setting(
                db, "example_instructions",
                json.loads((SEED / "example-instructions.json").read_text(encoding="utf-8")),
                "Sample instructions displayed by the Control Hub.",
            )
        if db.get(Setting, "model_name") is None:
            upsert_setting(db, "model_name", default_model, "Completion model name; provider credentials remain server-side.")
        if db.get(Setting, "embedding_model_name") is None:
            upsert_setting(db, "embedding_model_name", default_embedding_model, "Embedding model name; provider credentials remain server-side.")
        db.flush()
        agent_records = json.loads((SEED / "agents.json").read_text(encoding="utf-8"))
        if db.scalar(
            select(Event).where(
                Event.workspace_id == workspace_id,
                Event.event_type == "seed_agents_imported",
            )
        ) is None:
            for record in agent_records:
                db.add(Agent(**record, workspace_id=workspace_id, source="sample"))
            db.add(Event(
                workspace_id=workspace_id,
                event_type="seed_agents_imported",
                message="Starter agent records were imported from seed files.",
                details={"agent_count": len(agent_records)},
            ))
            db.commit()
        existing_sample = db.scalar(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.name == "sample: knowledge.md",
            )
        )
        db.commit()
        if existing_sample is None:
            if provider_is_configured() and (
                default_embedding_model or not provider_supports_embeddings()
            ):
                content = (SEED / "knowledge.md").read_text(encoding="utf-8")
                try:
                    document = await ingest_document(
                        db,
                        workspace_id,
                        "sample: knowledge.md",
                        "text/markdown",
                        content,
                    )
                    db.add(Event(
                        workspace_id=workspace_id,
                        event_type="seed_knowledge_ready",
                        message="Sample knowledge was indexed for retrieval.",
                        details={
                            "document_id": document.id,
                            "chunk_count": len(document.chunks),
                            "retrieval_mode": "vector" if provider_supports_embeddings() else "full_text",
                        },
                    ))
                    db.commit()
                except ModelRequestError as error:
                    db.rollback()
                    _record_pending_knowledge(db, workspace_id, error)
            else:
                _record_pending_knowledge(db, workspace_id, None)

        if db.scalar(
            select(Enquiry).where(
                Enquiry.workspace_id == workspace_id,
                Enquiry.source == "sample",
            )
        ) is None:
            for record in json.loads((SEED / "enquiries.json").read_text(encoding="utf-8")):
                create_enquiry(db, workspace_id, **record, source="sample")
        with (SEED / "sales.csv").open(newline="", encoding="utf-8") as csv_file:
            rows = list(csv.DictReader(csv_file))
        if db.scalar(
            select(Event).where(
                Event.workspace_id == workspace_id,
                Event.event_type == "seed_imported",
            )
        ) is None:
            import_sales_rows(db, workspace_id, "sample: sales.csv", rows)
            db.add(Event(
                workspace_id=workspace_id,
                event_type="seed_imported",
                message="Sample settings, sales rows, and instructions were imported.",
                details={"dataset": "sample: sales.csv", "sales_rows": len(rows)},
            ))
            db.commit()


def _record_pending_knowledge(
    db: Session, workspace_id: str, error: ModelRequestError | None
) -> None:
    if db.scalar(
        select(Event).where(
            Event.workspace_id == workspace_id,
            Event.event_type == "seed_knowledge_pending",
        )
    ) is None:
        db.add(Event(
            workspace_id=workspace_id,
            event_type="seed_knowledge_pending",
            message=(
                "Sample knowledge embedding is pending provider configuration."
                if error is None
                else f"Sample knowledge embedding failed: {error}"
            ),
            details={
                "required": ["provider API key, base URL, and embedding model server settings"],
                "error_type": type(error).__name__ if error is not None else "",
            },
        ))
        db.commit()


def initialize_workspace(db: Session, workspace_id: str) -> None:
    system_workspace_id = db.scalar(
        select(Workspace.id).where(Workspace.is_system.is_(True))
    )
    if system_workspace_id is None:
        raise RuntimeError("Sample workspace data is unavailable.")

    sample_agents = db.scalars(
        select(Agent).where(
            Agent.workspace_id == system_workspace_id,
            Agent.source == "sample",
        )
    ).all()
    db.add_all(
        [
            Agent(
                workspace_id=workspace_id,
                name=agent.name,
                role=agent.role,
                instructions=agent.instructions,
                allowed_tools=agent.allowed_tools,
                knowledge_access=agent.knowledge_access,
                source="sample",
            )
            for agent in sample_agents
        ]
    )

    sample_documents = db.scalars(
        select(Document).where(
            Document.workspace_id == system_workspace_id,
            Document.name.startswith("sample:"),
        )
    ).all()
    for source_document in sample_documents:
        document = Document(
            workspace_id=workspace_id,
            name=source_document.name,
            content_type=source_document.content_type,
        )
        db.add(document)
        db.flush()
        db.add_all(
            [
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    embedding=chunk.embedding,
                )
                for chunk in source_document.chunks
            ]
        )

    sample_sales = db.scalars(
        select(SalesRow).where(
            SalesRow.workspace_id == system_workspace_id,
            SalesRow.dataset.startswith("sample:"),
        )
    ).all()
    db.add_all(
        [
            SalesRow(
                workspace_id=workspace_id,
                dataset=row.dataset,
                row_data=dict(row.row_data),
            )
            for row in sample_sales
        ]
    )

    sample_enquiries = db.scalars(
        select(Enquiry).where(
            Enquiry.workspace_id == system_workspace_id,
            Enquiry.source == "sample",
        )
    ).all()
    db.add_all(
        [
            Enquiry(
                workspace_id=workspace_id,
                customer_name=item.customer_name,
                customer_email=item.customer_email,
                subject=item.subject,
                message=item.message,
                status=item.status,
                source="sample",
            )
            for item in sample_enquiries
        ]
    )
    db.add(Event(
        workspace_id=workspace_id,
        event_type="workspace_initialized",
        message="Sample business records were copied into a private workspace.",
        details={
            "agents": len(sample_agents),
            "documents": len(sample_documents),
            "sales_rows": len(sample_sales),
            "enquiries": len(sample_enquiries),
        },
    ))


if __name__ == "__main__":
    asyncio.run(import_seed_data())
