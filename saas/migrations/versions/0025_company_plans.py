"""Contract-specific enterprise allowance. Existing plans remain unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenant_users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tenant_users", sa.Column("login_locked_until", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("enterprise_ai_budget_usd", sa.Float(), nullable=True))
    op.create_check_constraint("ck_enterprise_budget_positive", "tenants",
                               "enterprise_ai_budget_usd IS NULL OR (enterprise_ai_budget_usd > 0 AND enterprise_ai_budget_usd < 'Infinity'::float8)")


def downgrade():
    op.drop_column("tenant_users", "login_locked_until")
    op.drop_column("tenant_users", "failed_login_attempts")
    op.drop_constraint("ck_enterprise_budget_positive", "tenants", type_="check")
    op.drop_column("tenants", "enterprise_ai_budget_usd")
