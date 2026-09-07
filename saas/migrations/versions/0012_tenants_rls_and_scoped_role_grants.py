"""Phase 10 (続き): tenantsテーブル自体にもRLSを適用し、app_roleが自分自身の行だけ
SELECT/UPDATEできるようにする(顧客のセルフサービスAPIキー更新エンドポイント用)。
テナント作成・削除は引き続き管理画面(superuser接続)経由のみ。

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-25

"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # INSERT/DELETEは付与しない(テナントの作成・削除は管理画面=superuser接続のみで行う)
    op.execute("GRANT SELECT, UPDATE ON tenants TO app_role")
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenants "
        "USING (id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenants")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE SELECT, UPDATE ON tenants FROM app_role")
