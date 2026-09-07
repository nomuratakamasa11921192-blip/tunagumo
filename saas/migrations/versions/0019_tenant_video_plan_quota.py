"""add plan and video generation quota tracking to tenants

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-01

プランごとの動画生成本数制限(src/core/plan_limits.py)。既存テナントはplan='light'
video_generations_this_period=0で始まる(既存の挙動を壊さない)。
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("plan", sa.String(length=20), nullable=False, server_default="light"),
    )
    op.add_column(
        "tenants",
        sa.Column("video_generations_this_period", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "video_period_started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "video_period_started_at")
    op.drop_column("tenants", "video_generations_this_period")
    op.drop_column("tenants", "plan")
