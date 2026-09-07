"""add industry to tenants (per-tenant config resolution)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("industry", sa.String(length=50), nullable=False, server_default="web_agency"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "industry")
