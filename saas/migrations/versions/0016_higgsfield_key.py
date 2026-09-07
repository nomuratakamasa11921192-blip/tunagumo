"""顧客自身のHiggsfield APIキー(画像生成。Anthropic/OpenAIと同じ、顧客負担モデル)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-27

"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("higgsfield_api_key_id", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("higgsfield_api_key_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "higgsfield_api_key_secret")
    op.drop_column("tenants", "higgsfield_api_key_id")
