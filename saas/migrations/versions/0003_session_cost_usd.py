"""add cost_usd to sessions (admin dashboard)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19

"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("sessions", "cost_usd")
