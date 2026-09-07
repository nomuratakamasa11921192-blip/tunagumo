"""Phase 10: Row Level Securityによるテナント分離(多層防御)。

アプリ側は既に全クエリでtenant_idを絞り込んでいる(SELECT/UPDATE/DELETEの
WHERE句)が、それだけに頼ると「1箇所書き忘れたら他テナントのデータが見える」
という事故が起こり得る。RLSはその最後の防波堤として、DB自身に
「app.tenant_idと一致する行しか見せない」ことを強制させる。

このマイグレーションは「使える状態にする」だけで、まだアプリのDB接続を
app_role経由には切り替えていない(=まだ有効化されていない)。切り替えるには、
アプリ側で1トランザクションの先頭で以下を実行する必要がある:
    SET LOCAL ROLE app_role;
    SET LOCAL app.tenant_id = '<tenant_uuidの文字列>';
(SET LOCALはトランザクションスコープでのみ有効。コネクションプールで
 SETを使い回すと前のリクエストのtenant_idが残って情報漏洩するため、
 必ずSET LOCALを使うこと。SETではない)

管理画面(admin routes)はテナント横断の閲覧が正規の用途のため、app_roleには
切り替えず、現状通りsuperuser(tsunagumo)接続のまま運用する想定。

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-24

"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# tenant_idカラムを持ち、顧客リクエスト経由でアクセスされるテーブル。
# tenants自体は対象外(管理画面がテナント横断で読む必要があるため、
# RLSではなくアプリ層のアクセス制御で守る)。
TENANT_SCOPED_TABLES = ["sessions", "approvals", "audit_logs", "documents", "chunks"]


def upgrade() -> None:
    # NOLOGIN: 直接ログインするロールではなく、tsunagumo(既存のログインロール)が
    # SET LOCAL ROLEで一時的になり替わる先の「権限の器」として使う
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN "
        "CREATE ROLE app_role NOLOGIN; "
        "END IF; "
        "END $$;"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TENANT_SCOPED_TABLES)} TO app_role")
    op.execute("GRANT app_role TO tsunagumo")

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # current_setting(..., true)は未設定ならNULLを返す(エラーにしない)。
        # tenant_id = NULLは常にfalseなので、app.tenant_id未設定なら1行も見えない
        # (fail-closed: 設定し忘れても情報が漏れる方向には倒れない)。
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("REVOKE app_role FROM tsunagumo")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {', '.join(TENANT_SCOPED_TABLES)} FROM app_role")
    op.execute("DROP ROLE IF EXISTS app_role")
