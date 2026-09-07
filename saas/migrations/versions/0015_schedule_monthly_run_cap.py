"""Phase 14-3: schedules.max_monthly_runs(月間の総実行回数上限、異常発火の安全弁)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-26

"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("max_monthly_runs", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "max_monthly_runs")
