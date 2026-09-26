"""Limit mailbox tests by subject without changing existing mailbox behavior."""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenant_mail_accounts", sa.Column("subject_prefix", sa.String(80), nullable=False, server_default=""))


def downgrade():
    op.drop_column("tenant_mail_accounts", "subject_prefix")
