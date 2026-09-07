"""Phase 13の前提となるテナント内ユーザー識別(tenant_users, tenant_user_tokens)と、
多段階承認(tenants.approval_stages, approvals.stage/approver_user_id)。
Phase 15の土台としてtenants.line_channel_secret/line_channel_access_tokenも追加。

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-26

"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["tenant_users", "tenant_user_tokens", "line_webhook_events"]


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("approval_stages", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("tenants", sa.Column("line_channel_secret", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("line_channel_access_token", sa.Text(), nullable=True))

    op.add_column(
        "approvals",
        sa.Column("stage", sa.Integer(), nullable=False, server_default="1"),
    )

    # tenant_usersを先に作ってから、それを参照するapprovals.approver_user_idを追加する
    op.create_table(
        "tenant_users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("invite_token_hash", sa.String(length=64), nullable=True),
        sa.Column("invite_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "email", name="uq_tenant_users_tenant_email"),
        sa.UniqueConstraint("invite_token_hash", name="uq_tenant_users_invite_token_hash"),
    )
    op.create_index("ix_tenant_users_tenant_id", "tenant_users", ["tenant_id"])

    op.create_table(
        "tenant_user_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "tenant_user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant_users.id"), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tenant_user_tokens_tenant_id", "tenant_user_tokens", ["tenant_id"])
    op.create_index("ix_tenant_user_tokens_tenant_user_id", "tenant_user_tokens", ["tenant_user_id"])

    op.add_column(
        "approvals",
        sa.Column(
            "approver_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant_users.id"),
            nullable=True,
        ),
    )

    op.create_table(
        "line_webhook_events",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("event_id", sa.String(length=100), primary_key=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )

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

    op.drop_column("approvals", "approver_user_id")

    op.drop_table("line_webhook_events")

    op.drop_index("ix_tenant_user_tokens_tenant_user_id", table_name="tenant_user_tokens")
    op.drop_index("ix_tenant_user_tokens_tenant_id", table_name="tenant_user_tokens")
    op.drop_table("tenant_user_tokens")

    op.drop_index("ix_tenant_users_tenant_id", table_name="tenant_users")
    op.drop_table("tenant_users")

    op.drop_column("approvals", "stage")

    op.drop_column("tenants", "line_channel_access_token")
    op.drop_column("tenants", "line_channel_secret")
    op.drop_column("tenants", "approval_stages")
