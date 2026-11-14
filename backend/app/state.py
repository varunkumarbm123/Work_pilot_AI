from sqlalchemy.orm import Session

from app.models import Event, Task
from app.status import TaskStatus


ALLOWED_TRANSITIONS = {
    TaskStatus.PLANNED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.APPROVED,
        TaskStatus.DONE,
        TaskStatus.FAILED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.RUNNING,
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.FAILED,
    },
    TaskStatus.APPROVED: {TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.REJECTED: {TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: {TaskStatus.RUNNING},
}


class InvalidTransition(ValueError):
    pass


def transition_task(
    db: Session,
    task: Task,
    next_status: TaskStatus | str,
    message: str,
) -> None:
    try:
        current_status = TaskStatus(task.status)
        target_status = TaskStatus(next_status)
    except ValueError as error:
        raise InvalidTransition(f"Task transition contains an unknown state: {error}.") from error
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTransition(
            f"Task cannot transition from {current_status.value} to {target_status.value}."
        )
    previous = task.status
    task.status = target_status.value
    db.add(Event(
        task_id=task.id,
        event_type="state_transition",
        message=message,
        details={"from": previous, "to": target_status.value},
    ))
