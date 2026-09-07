"""widen tenants.anthropic_api_key to TEXT (Fernet-encrypted values are much
longer than the original plaintext key; VARCHAR(200) truncation error found live)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19

"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "anthropic_api_key",
        type_=sa.Text(),
        existing_type=sa.String(length=200),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "anthropic_api_key",
        type_=sa.String(length=200),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
