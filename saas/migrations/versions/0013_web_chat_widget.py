"""Phase 16: Web埋め込みチャット。tenants.web_widget_*、web_chat_sessions、
web_chat_request_log、documents.index_scope("public")の利用開始。

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-25

"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["web_chat_sessions", "web_chat_request_log"]


def upgrade() -> None:
    op.add_column("tenants", sa.Column("web_widget_public_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_tenants_web_widget_public_key", "tenants", ["web_widget_public_key"])
    op.add_column("tenants", sa.Column("web_widget_allowed_origin", sa.String(length=300), nullable=True))

    op.create_table(
        "web_chat_sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("messages", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_web_chat_sessions_tenant_id", "web_chat_sessions", ["tenant_id"])

    op.create_table(
        "web_chat_request_log",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_web_chat_request_log_tenant_id", "web_chat_request_log", ["tenant_id"])
    op.create_index("ix_web_chat_request_log_ip_address", "web_chat_request_log", ["ip_address"])
    op.create_index("ix_web_chat_request_log_created_at", "web_chat_request_log", ["created_at"])

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_role")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM app_role")

    op.drop_index("ix_web_chat_request_log_created_at", table_name="web_chat_request_log")
    op.drop_index("ix_web_chat_request_log_ip_address", table_name="web_chat_request_log")
    op.drop_index("ix_web_chat_request_log_tenant_id", table_name="web_chat_request_log")
    op.drop_table("web_chat_request_log")

    op.drop_index("ix_web_chat_sessions_tenant_id", table_name="web_chat_sessions")
    op.drop_table("web_chat_sessions")

    op.drop_column("tenants", "web_widget_allowed_origin")
    op.drop_constraint("uq_tenants_web_widget_public_key", "tenants", type_="unique")
    op.drop_column("tenants", "web_widget_public_key")
