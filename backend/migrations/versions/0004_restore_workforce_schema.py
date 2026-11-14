from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column

from app import models
from app.database import Base


revision = "0004_restore_workforce_schema"
down_revision = "0003_remove_assistant_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[
            models.Agent.__table__,
            models.Task.__table__,
            models.TaskStep.__table__,
            models.Event.__table__,
            models.SentItem.__table__,
        ],
    )
    op.add_column("knowledge_chunks", Column("embedding", Vector(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding")
    op.drop_table("sent_items")
    op.drop_table("task_steps")
    op.drop_table("events")
    op.drop_table("tasks")
    op.drop_table("agents")
