from enum import Enum


class TaskStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"


class StepStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CAPACITY = "waiting_capacity"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"


class EnquiryStatus(str, Enum):
    NEW = "new"
