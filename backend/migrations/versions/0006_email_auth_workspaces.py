import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0006_email_auth_workspaces"
down_revision = "0005_configurable_cost_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(bind)
    if offline:
        system_workspace_id = None
    elif not inspector.has_table("workspaces"):
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        system_workspace_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO workspaces (id, name, is_system, created_at) "
                "VALUES (:id, 'Legacy sample workspace', true, now())"
            ),
            {"id": system_workspace_id},
        )
    else:
        system_workspace_id = bind.execute(
            sa.text("SELECT id FROM workspaces WHERE is_system IS TRUE LIMIT 1")
        ).scalar()
        if system_workspace_id is None:
            system_workspace_id = str(uuid.uuid4())
            bind.execute(
                sa.text(
                    "INSERT INTO workspaces (id, name, is_system, created_at) "
                    "VALUES (:id, 'Legacy sample workspace', true, now())"
                ),
                {"id": system_workspace_id},
            )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "workspace_settings",
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )

    for table in (
        "agents",
        "documents",
        "tasks",
        "events",
        "sales_rows",
        "enquiries",
        "sent_items",
    ):
        nullable = table == "events"
        if offline:
            needs_workspace_column = table in {"documents", "sales_rows", "enquiries"}
            if needs_workspace_column:
                op.add_column(
                    table,
                    sa.Column("workspace_id", sa.String(length=36), nullable=True),
                )
                op.execute(
                    sa.text(
                        f"UPDATE {table} SET workspace_id = "
                        "(SELECT id FROM workspaces WHERE is_system IS TRUE LIMIT 1) "
                        "WHERE workspace_id IS NULL"
                    )
                )
                op.create_foreign_key(
                    f"fk_{table}_workspace_id_workspaces",
                    table,
                    "workspaces",
                    ["workspace_id"],
                    ["id"],
                )
                op.alter_column(table, "workspace_id", nullable=False)
                op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
            continue

        columns = {column["name"]: column for column in inspector.get_columns(table)}
        if "workspace_id" not in columns:
            op.add_column(
                table,
                sa.Column("workspace_id", sa.String(length=36), nullable=True),
            )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET workspace_id = "
                "COALESCE(workspace_id, :workspace_id)"
            ),
            {"workspace_id": system_workspace_id},
        )
        foreign_keys = inspector.get_foreign_keys(table)
        if not any(fk.get("constrained_columns") == ["workspace_id"] for fk in foreign_keys):
            op.create_foreign_key(
                f"fk_{table}_workspace_id_workspaces",
                table,
                "workspaces",
                ["workspace_id"],
                ["id"],
            )
        if not nullable and columns.get("workspace_id", {}).get("nullable", True):
            op.alter_column(table, "workspace_id", nullable=False)
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        if f"ix_{table}_workspace_id" not in indexes:
            op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])

    if offline:
        op.execute(
            sa.text(
                "UPDATE tasks SET session_id = "
                "(SELECT id FROM workspaces WHERE is_system IS TRUE LIMIT 1)"
            )
        )
    else:
        bind.execute(
            sa.text("UPDATE tasks SET session_id = :workspace_id"),
            {"workspace_id": system_workspace_id},
        )


def downgrade() -> None:
    raise RuntimeError("Workspace authentication data cannot be downgraded safely.")
