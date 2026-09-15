"""properties, leads, proposals (property proposal and follow-up emails)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

UUID = sa.dialects.postgresql.UUID(as_uuid=True)
JSONB = sa.dialects.postgresql.JSONB()
TABLES = ["properties", "leads", "proposals"]


def upgrade() -> None:
    op.add_column("tenants", sa.Column("marketing_sender_name", sa.String(length=200), nullable=True))
    op.add_column("tenants", sa.Column("marketing_sender_address", sa.String(length=300), nullable=True))
    op.add_column("tenants", sa.Column("marketing_sender_contact", sa.String(length=200), nullable=True))
    op.add_column("tenants", sa.Column("proposal_auto_send", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenants", sa.Column("follow_up_interval_days", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("tenants", sa.Column("follow_up_max", sa.Integer(), nullable=False, server_default="3"))

    op.create_table(
        "properties",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("deal_type", sa.String(length=10), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("station", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("walk_minutes", sa.Integer(), nullable=True),
        sa.Column("price_yen", sa.Integer(), nullable=False),
        sa.Column("layout", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("floor_area_sqm", sa.Float(), nullable=True),
        sa.Column("built_year", sa.Integer(), nullable=True),
        sa.Column("features", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_properties_tenant_id", "properties", ["tenant_id"])

    op.create_table(
        "leads",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("consent_at", sa.DateTime(), nullable=True),
        sa.Column("consent_source", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("deal_type", sa.String(length=10), nullable=True),
        sa.Column("areas", JSONB, nullable=False, server_default="[]"),
        sa.Column("max_price_yen", sa.Integer(), nullable=True),
        sa.Column("layouts", JSONB, nullable=False, server_default="[]"),
        sa.Column("max_walk_minutes", sa.Integer(), nullable=True),
        sa.Column("min_floor_area_sqm", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("unsubscribe_token", sa.String(length=64), nullable=False, unique=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_reply_at", sa.DateTime(), nullable=True),
        sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_leads_email", "leads", ["email"])

    op.create_table(
        "proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("property_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("ai_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_proposals_tenant_id", "proposals", ["tenant_id"])
    op.create_index("ix_proposals_lead_id", "proposals", ["lead_id"])
    op.create_index("ix_proposals_created_at", "proposals", ["created_at"])

    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_role")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM app_role")
    op.drop_table("proposals")
    op.drop_table("leads")
    op.drop_table("properties")
    for col in ("follow_up_max", "follow_up_interval_days", "proposal_auto_send",
                "marketing_sender_contact", "marketing_sender_address", "marketing_sender_name"):
        op.drop_column("tenants", col)
