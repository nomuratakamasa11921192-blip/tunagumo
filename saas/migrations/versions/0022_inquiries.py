"""inquiries (24h inquiry first response) and LINE conversation columns

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-15

問い合わせの一次受け: AIが担当者へ回した問い合わせ(inquiries)、LINEの会話を
web_chat_sessionsで持つための列、テナントの通知先メール・緊急連絡先。
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("inquiry_notify_email", sa.String(length=320), nullable=True))
    op.add_column("tenants", sa.Column("emergency_contact_phone", sa.String(length=30), nullable=True))

    op.add_column("web_chat_sessions", sa.Column("channel", sa.String(length=10), nullable=False, server_default="web"))
    op.add_column("web_chat_sessions", sa.Column("external_user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_web_chat_sessions_external_user_id", "web_chat_sessions", ["external_user_id"])

    op.create_table(
        "inquiries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("external_user_id", sa.String(length=64), nullable=True),
        sa.Column("web_chat_session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("urgency", sa.String(length=10), nullable=False, server_default="normal"),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="その他"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("messages", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inquiries_tenant_id", "inquiries", ["tenant_id"])
    op.create_index("ix_inquiries_created_at", "inquiries", ["created_at"])
    op.create_index("ix_inquiries_web_chat_session_id", "inquiries", ["web_chat_session_id"])

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON inquiries TO app_role")
    op.execute("ALTER TABLE inquiries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inquiries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON inquiries "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON inquiries")
    op.execute("ALTER TABLE inquiries NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inquiries DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON inquiries FROM app_role")
    op.drop_index("ix_inquiries_web_chat_session_id", table_name="inquiries")
    op.drop_index("ix_inquiries_created_at", table_name="inquiries")
    op.drop_index("ix_inquiries_tenant_id", table_name="inquiries")
    op.drop_table("inquiries")
    op.drop_index("ix_web_chat_sessions_external_user_id", table_name="web_chat_sessions")
    op.drop_column("web_chat_sessions", "external_user_id")
    op.drop_column("web_chat_sessions", "channel")
    op.drop_column("tenants", "emergency_contact_phone")
    op.drop_column("tenants", "inquiry_notify_email")
