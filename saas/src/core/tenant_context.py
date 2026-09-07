"""Row Level Security(migrations/versions/0009_row_level_security.py)を実際に効かせるための
ヘルパー。src/api/deps.pyのget_scoped_dbから、テナント向けAPIルート(顧客のAPIキーで
認証されるエンドポイント)のDBセッション取得時に呼ばれる。管理画面(admin routes)は
テナント横断の閲覧が正規の用途のため、これは使わずget_db/superuser接続のまま運用する。
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_scope(db: AsyncSession, tenant_id: uuid.UUID | str) -> None:
    """このセッションが開始した現在のトランザクションに限り、権限をapp_roleへ落とし、
    app.tenant_idをセットする。SET LOCALはトランザクション終了で自動的に元に戻るため、
    コネクションプールで前のリクエストの設定が残る事故を構造的に防げる(SETではなく
    必ずSET LOCALを使うこと)。呼び出し前提: 呼び出し元は既にトランザクション内にいること
    (AsyncSessionは既定でautobeginするため、最初のexecute呼び出しで暗黙に開始される)。

    PostgresのSET文はバインドパラメータ($1形式)を受け付けないため、文字列として
    埋め込む必要がある。uuid.UUID()に通すことで、16進数とハイフンだけの正規形に
    限定してからf-stringに埋め込み、SQLインジェクションを構造的に防ぐ
    (tenant_idが不正な値なら、ここでValueErrorとして先に落ちる)。
    """
    normalized = str(uuid.UUID(str(tenant_id)))
    await db.execute(text("SET LOCAL ROLE app_role"))
    await db.execute(text(f"SET LOCAL app.tenant_id = '{normalized}'"))
