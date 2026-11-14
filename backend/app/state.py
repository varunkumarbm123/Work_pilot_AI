from sqlalchemy.orm import Session

from app.models import Event, Task


ALLOWED_TRANSITIONS = {
    "planned": {"running", "failed"},
    "running": {"awaiting_approval", "approved", "done", "failed"},
    "awaiting_approval": {"running", "approved", "rejected", "failed"},
    "approved": {"running", "done", "failed"},
    "rejected": {"running", "done", "failed"},
    "done": set(),
    "failed": {"running"},
}


class InvalidTransition(ValueError):
    pass


def transition_task(db: Session, task: Task, next_status: str, message: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(task.status)
    if allowed is None or next_status not in allowed:
        raise InvalidTransition(f"Task cannot transition from {task.status} to {next_status}.")
    previous = task.status
    task.status = next_status
    db.add(Event(
        task_id=task.id,
        event_type="state_transition",
        message=message,
        details={"from": previous, "to": next_status},
    ))
