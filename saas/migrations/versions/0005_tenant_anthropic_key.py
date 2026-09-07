"""add anthropic_api_key to tenants (顧客が自分のAnthropic APIキーを持つ事業モデルへの変更)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-19

"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("anthropic_api_key", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "anthropic_api_key")
