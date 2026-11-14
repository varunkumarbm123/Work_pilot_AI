from alembic import op


revision = "0005_configurable_cost_units"
down_revision = "0004_restore_workforce_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE events SET details = (details - 'cost_usd') || "
        "jsonb_build_object('cost', details->'cost_usd', 'currency', 'USD') "
        "WHERE details ? 'cost_usd'"
    )
    op.execute(
        "INSERT INTO settings (key, value, description) "
        "SELECT 'billing_currency', '\"USD\"'::jsonb, "
        "'Three-letter currency code for configured model prices and cost limits.' "
        "WHERE EXISTS (SELECT 1 FROM settings WHERE key IN ("
        "'cost_cap_usd_per_session', 'input_cost_per_1k_usd', "
        "'output_cost_per_1k_usd', 'embedding_cost_per_1k_usd')) "
        "ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        "UPDATE settings AS old SET key = renamed.new_key, description = renamed.description "
        "FROM (VALUES "
        "('cost_cap_usd_per_session', 'cost_cap_per_session', "
        "'Maximum tracked model cost per browser session, in the configured billing currency.'), "
        "('input_cost_per_1k_usd', 'input_cost_per_1k_tokens', "
        "'Configured input-token price per 1,000 tokens, in the billing currency.'), "
        "('output_cost_per_1k_usd', 'output_cost_per_1k_tokens', "
        "'Configured output-token price per 1,000 tokens, in the billing currency.'), "
        "('embedding_cost_per_1k_usd', 'embedding_cost_per_1k_tokens', "
        "'Configured embedding-token price per 1,000 tokens, in the billing currency.')"
        ") AS renamed(old_key, new_key, description) "
        "WHERE old.key = renamed.old_key AND NOT EXISTS ("
        "SELECT 1 FROM settings AS current WHERE current.key = renamed.new_key)"
    )
    op.execute(
        "DELETE FROM settings WHERE key IN ("
        "'cost_cap_usd_per_session', 'input_cost_per_1k_usd', "
        "'output_cost_per_1k_usd', 'embedding_cost_per_1k_usd')"
    )
def downgrade() -> None:
    raise RuntimeError("Cost settings use configurable currencies and cannot be downgraded safely.")
