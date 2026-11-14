from alembic import op


revision = "0003_remove_assistant_tables"
down_revision = "0002_remove_provider_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM settings WHERE key IN ("
        "'top_k', 'request_cap_per_session', 'rate_limit_per_minute', "
        "'system_prompt', 'validation_policy', 'retry_limit', "
        "'request_timeout_seconds', 'cost_cap_usd_per_session', 'max_task_steps', "
        "'model_name', 'embedding_model_name', 'input_cost_per_1k_usd', "
        "'output_cost_per_1k_usd', 'embedding_cost_per_1k_usd', "
        "'max_completion_tokens', 'example_instructions')"
    )
    op.execute("DROP TABLE IF EXISTS task_steps CASCADE")
    op.execute("DROP TABLE IF EXISTS sent_items CASCADE")
    op.execute("DROP TABLE IF EXISTS events CASCADE")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS agents CASCADE")


def downgrade() -> None:
    raise RuntimeError("Removed assistant data tables cannot be restored by a downgrade.")
