from alembic import op


revision = "0002_remove_provider_embeddings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding")


def downgrade() -> None:
    raise RuntimeError("Removed provider embeddings cannot be restored by a downgrade.")
