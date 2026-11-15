from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Event, Task
from app.settings_store import setting_value


def session_spend(db: Session, session_id: str, currency: str) -> float:
    if not currency:
        return 0
    task_ids = db.scalars(select(Task.id).where(Task.session_id == session_id)).all()
    conditions = [Event.details["session_id"].as_string() == session_id]
    if task_ids:
        conditions.append(Event.task_id.in_(task_ids))
    events = db.scalars(select(Event).where(or_(*conditions))).all()
    return sum(
        float((event.details or {}).get("cost", 0))
        for event in events
        if (event.details or {}).get("currency") == currency
    )


def reserve_session_request(
    db: Session, session_id: str, workspace_id: str | None = None
) -> None:
    session_match = Event.details["session_id"].as_string() == session_id
    total = db.scalar(
        select(func.count()).select_from(Event).where(
            Event.event_type == "session_request", session_match
        )
    ) or 0
    cap = int(
        setting_value(
            db,
            "request_cap_per_session",
            workspace_id=workspace_id,
        )
    )
    if total >= cap:
        raise HTTPException(status_code=429, detail="This browser session reached its configured request cap.")
    recent = db.scalar(
        select(func.count()).select_from(Event).where(
            Event.event_type == "session_request",
            session_match,
            Event.created_at >= datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    ) or 0
    rate = int(
        setting_value(
            db,
            "rate_limit_per_minute",
            workspace_id=workspace_id,
        )
    )
    if recent >= rate:
        raise HTTPException(status_code=429, detail="This browser session is temporarily rate limited.")
    currency = str(
        setting_value(db, "billing_currency", workspace_id=workspace_id)
    )
    spend = session_spend(db, session_id, currency)
    cost_cap = float(
        setting_value(db, "cost_cap_per_session", workspace_id=workspace_id)
    )
    if currency and spend >= cost_cap:
        raise HTTPException(status_code=429, detail="This browser session reached its configured model cost cap.")
    db.add(Event(
        workspace_id=workspace_id,
        event_type="session_request",
        message="A write request was accepted for this browser session.",
        details={"session_id": session_id},
    ))
    db.commit()
