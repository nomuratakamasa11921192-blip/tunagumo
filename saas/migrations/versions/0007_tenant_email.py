"""add email to tenants (承認期限リマインド等の通知先)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-19

"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "email")
