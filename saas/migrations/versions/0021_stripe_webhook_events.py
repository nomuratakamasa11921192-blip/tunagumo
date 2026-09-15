"""add stripe_webhook_events for webhook idempotency

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-15

Stripeは同じWebhookイベントを複数回送ることがあるため、処理済みのevent_idを記録して
追加AI予算の二重加算などを防ぐ(src/api/routes/stripe_webhook.py)。
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("stripe_webhook_events")
