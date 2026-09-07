"""add monthly AI budget tracking to tenants (BYOK removal)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-01

BYOK(顧客自身のAPIキー)を廃止し、AI利用料を運営(ツナグモ)が負担する方式に切り替えた
ことに伴う月間予算上限(src/core/ai_budget.py)。既存テナントはai_cost_this_period_usd=0
addon_credit_usd=0で始まる。
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("ai_cost_this_period_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "ai_cost_period_started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("addon_credit_usd", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "addon_credit_usd")
    op.drop_column("tenants", "ai_cost_period_started_at")
    op.drop_column("tenants", "ai_cost_this_period_usd")
