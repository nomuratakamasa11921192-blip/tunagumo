"""add stripe_customer_id and subscription_active to tenants

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-01

Stripeのサブスク状態と連動してアクセスを止められるようにする列を追加する。
stripe_customer_idが未設定のテナント(管理者が手動発行した既存テナント等)は
subscription_activeのデフォルトTrueのままなので、既存の挙動は変わらない。
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_tenants_stripe_customer_id", "tenants", ["stripe_customer_id"], unique=False
    )
    op.add_column(
        "tenants",
        sa.Column("subscription_active", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "subscription_active")
    op.drop_index("ix_tenants_stripe_customer_id", table_name="tenants")
    op.drop_column("tenants", "stripe_customer_id")
