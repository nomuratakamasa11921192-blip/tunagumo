"""Phase 14: schedules(定期実行の定義)、schedule_runs(起動記録・二重起動防止)

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-25

"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["schedules", "schedule_runs"]


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("cron", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="Asia/Tokyo"),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("max_budget_usd", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])

    op.create_table(
        "schedule_runs",
        sa.Column("schedule_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("schedules.id"), primary_key=True),
        sa.Column("scheduled_for", sa.DateTime(), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="CLAIMED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schedule_runs_tenant_id", "schedule_runs", ["tenant_id"])

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

    op.drop_index("ix_schedule_runs_tenant_id", table_name="schedule_runs")
    op.drop_table("schedule_runs")
    op.drop_index("ix_schedules_tenant_id", table_name="schedules")
    op.drop_table("schedules")
