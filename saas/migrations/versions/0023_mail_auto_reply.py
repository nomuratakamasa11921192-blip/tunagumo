"""mail auto reply: tenant_mail_accounts, mail_processed_messages, inquiries email columns

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-16

メール即レス(src/channels/mail.py)。
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["tenant_mail_accounts", "mail_processed_messages"]


def upgrade() -> None:
    op.alter_column("inquiries", "external_user_id", type_=sa.String(length=320), existing_nullable=True)
    op.add_column("inquiries", sa.Column("email_subject", sa.String(length=300), nullable=True))
    op.add_column("inquiries", sa.Column("email_message_id", sa.String(length=300), nullable=True))

    op.create_table(
        "tenant_mail_accounts",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("from_address", sa.String(length=320), nullable=False),
        sa.Column("imap_host", sa.String(length=255), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
        sa.Column("username", sa.String(length=320), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_uid", sa.Integer(), nullable=True),
        sa.Column("uidvalidity", sa.Integer(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "mail_processed_messages",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("message_id", sa.String(length=300), primary_key=True),
        sa.Column("sender_address", sa.String(length=320), nullable=True),
        sa.Column("auto_replied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processed_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mail_processed_messages_sender_address", "mail_processed_messages", ["sender_address"])
    op.create_index("ix_mail_processed_messages_processed_at", "mail_processed_messages", ["processed_at"])

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
    op.drop_index("ix_mail_processed_messages_processed_at", table_name="mail_processed_messages")
    op.drop_index("ix_mail_processed_messages_sender_address", table_name="mail_processed_messages")
    op.drop_table("mail_processed_messages")
    op.drop_table("tenant_mail_accounts")
    op.drop_column("inquiries", "email_message_id")
    op.drop_column("inquiries", "email_subject")
    op.alter_column("inquiries", "external_user_id", type_=sa.String(length=64), existing_nullable=True)
