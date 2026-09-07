import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.core.tenant_context import set_tenant_scope

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


class TenantScopedSession(AsyncSession):
    """Phase 10のRow Level Securityを実際に効かせるためのセッション(顧客向けAPIルート専用。
    管理画面はテナント横断の閲覧が正規の用途なので、引き続きget_db/superuser接続のまま)。

    通常のAsyncSessionと違い、commit()のたびに自動でsrc.core.tenant_context.set_tenant_scope()
    を再適用する。SET LOCALはトランザクション終了(commit/rollback)で自動的に元に戻るため、
    「1リクエスト内で2回commitするコードが将来増えたら、2回目以降のクエリだけ静かに
    RLS保護が外れる」という事故を構造的に防ぐ(ルート関数の書き方に依存させない)。
    """

    _rls_tenant_id: uuid.UUID | None = None

    async def commit(self) -> None:
        await super().commit()
        if self._rls_tenant_id is not None:
            await set_tenant_scope(self, self._rls_tenant_id)


tenant_scoped_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=TenantScopedSession)


async def get_scoped_db_for_tenant(tenant_id: uuid.UUID) -> AsyncSession:
    """テナント向けAPIルートで使う、RLSでスコープ済みのDBセッションを作る。
    src/api/deps.pyのget_scoped_dbから、認証済みのtenant.idを渡して呼ばれる想定。
    """
    async with tenant_scoped_session_factory() as session:
        session._rls_tenant_id = tenant_id
        await set_tenant_scope(session, tenant_id)
        yield session
