"""default tenants.industry to real_estate (product narrowed to real estate)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-31

不動産に一本化したため、新規テナントのデフォルト業種をweb_agencyから
real_estateに変更する。既存行のindustry値は変更しない(recruiting/legal/
web_agencyのconfigファイルは削除せず温存しているので、既存テナントは
引き続き動作する)。
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "industry",
        existing_type=sa.String(length=50),
        server_default="real_estate",
    )


def downgrade() -> None:
    op.alter_column(
        "tenants",
        "industry",
        existing_type=sa.String(length=50),
        server_default="web_agency",
    )
