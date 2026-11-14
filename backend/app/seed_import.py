import asyncio
import csv
import json
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.enquiries import create_enquiry
from app.knowledge import ingest_document
from app.migrate import run_migrations
from app.models import Document, Enquiry, SalesRow, Setting
from app.sales import import_sales_rows
from app.settings_store import upsert_setting


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "seed"


async def import_seed_data() -> None:
    run_migrations()
    with SessionLocal() as db:
        for record in json.loads((SEED / "settings.json").read_text(encoding="utf-8")):
            if db.get(Setting, record["key"]) is None:
                upsert_setting(db, record["key"], record["value"], record["description"])
        db.commit()

        sample_document = db.scalar(select(Document).where(Document.name == "sample: knowledge.md"))
        if sample_document is None:
            content = (SEED / "knowledge.md").read_text(encoding="utf-8")
            await ingest_document(db, "sample: knowledge.md", "text/markdown", content)

        if db.scalar(select(Enquiry).where(Enquiry.source == "sample")) is None:
            for record in json.loads((SEED / "enquiries.json").read_text(encoding="utf-8")):
                create_enquiry(db, **record, source="sample")

        sample_sales = db.scalar(select(SalesRow.id).where(SalesRow.dataset == "sample: sales.csv").limit(1))
        if sample_sales is None:
            with (SEED / "sales.csv").open(newline="", encoding="utf-8") as csv_file:
                import_sales_rows(db, "sample: sales.csv", list(csv.DictReader(csv_file)))


if __name__ == "__main__":
    asyncio.run(import_seed_data())
