"""Phase 12: executed_actions(冪等な外部アクション実行の記録)、runtime_flags(緊急停止フラグ)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-25

"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "executed_actions",
        sa.Column("idempotency_key", sa.String(length=128), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="CLAIMED"),
        sa.Column("result", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_executed_actions_tenant_id", "executed_actions", ["tenant_id"])
    op.create_index("ix_executed_actions_session_id", "executed_actions", ["session_id"])

    op.create_table(
        "runtime_flags",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # executed_actionsはテナントの機微な操作記録なので、migrations/0009と同じ
    # Row Level Securityの対象に含める(runtime_flagsはテナント横断の運用設定なので対象外)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON executed_actions TO app_role")
    op.execute("ALTER TABLE executed_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE executed_actions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON executed_actions "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON executed_actions")
    op.execute("ALTER TABLE executed_actions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE executed_actions DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON executed_actions FROM app_role")

    op.drop_table("runtime_flags")
    op.drop_index("ix_executed_actions_session_id", table_name="executed_actions")
    op.drop_index("ix_executed_actions_tenant_id", table_name="executed_actions")
    op.drop_table("executed_actions")
