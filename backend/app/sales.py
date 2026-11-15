import csv
import io
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import SalesRow


def parse_sales_csv(content: bytes) -> tuple[list[dict[str, str]], list[str]]:
    try:
        decoded = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        headers = [header.strip() for header in (reader.fieldnames or []) if header and header.strip()]
        rows = [
            {key.strip(): (value or "").strip() for key, value in row.items() if key and key.strip()}
            for row in reader
        ]
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError(f"Could not read CSV: {error}") from error
    if not headers or not rows:
        raise ValueError("The CSV must include a header row and at least one data row.")
    if len(rows) > 10000:
        raise ValueError("The CSV exceeds the 10,000-row import limit.")
    return rows, headers


def import_sales_rows(
    db: Session, workspace_id: str, dataset: str, rows: list[dict[str, str]]
) -> int:
    db.execute(
        delete(SalesRow).where(
            SalesRow.workspace_id == workspace_id,
            SalesRow.dataset == dataset,
        )
    )
    db.add_all([
        SalesRow(workspace_id=workspace_id, dataset=dataset, row_data=row)
        for row in rows
    ])
    db.commit()
    return len(rows)


def sales_summary(db: Session, workspace_id: str, dataset: str | None = None) -> dict:
    statement = (
        select(SalesRow)
        .where(SalesRow.workspace_id == workspace_id)
        .order_by(SalesRow.id)
        .limit(2000)
    )
    if dataset:
        statement = statement.where(SalesRow.dataset == dataset)
    records = db.scalars(statement).all()
    totals: dict[str, Decimal] = {}
    numeric_counts: dict[str, int] = {}
    grouped: dict[str, dict[str, dict[str, Decimal]]] = {}
    for record in records:
        categorical = {
            key: str(value)
            for key, value in record.row_data.items()
            if value is not None and not _is_number(value)
        }
        for key, raw_value in record.row_data.items():
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, ValueError):
                continue
            totals[key] = totals.get(key, Decimal(0)) + value
            numeric_counts[key] = numeric_counts.get(key, 0) + 1
            for category, label in categorical.items():
                by_label = grouped.setdefault(category, {})
                metrics = by_label.setdefault(label, {})
                metrics[key] = metrics.get(key, Decimal(0)) + value
    datasets = sorted({record.dataset for record in records})
    return {
        "row_count": len(records),
        "datasets": datasets,
        "numeric_totals": {key: str(value) for key, value in totals.items()},
        "numeric_value_counts": numeric_counts,
        "grouped_numeric_totals": {
            field: {
                label: {metric: str(value) for metric, value in metrics.items()}
                for label, metrics in list(groups.items())[:50]
            }
            for field, groups in grouped.items()
        },
        "row_limit_reached": len(records) == 2000,
    }


def _is_number(value: object) -> bool:
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return True
