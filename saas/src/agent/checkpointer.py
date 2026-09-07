from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.core.config import settings


def psycopg_dsn() -> str:
    """DATABASE_URLはSQLAlchemy(asyncpg)用の形式なので、psycopg用に読み替える。"""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


class CheckpointerLifecycle:
    """アプリ起動時に1回だけコネクションプールを開き、プロセス終了まで使い回す。

    Phase 2ではMemorySaver(プロセス内のみ)を使っていたが、Phase 5でPostgresSaverに
    切り替える。thread_idごとにDBへ状態が保存されるため、プロセスを再起動しても
    途中(要件確認待ち・承認待ち)から再開できる。

    AsyncPostgresSaver.from_conn_string()は単一コネクションしか作らないため、
    サーバーが複数リクエストを同時/連続で処理すると1本の接続を奪い合って詰まる。
    そのため、明示的にAsyncConnectionPoolを渡して初期化する。
    """

    def __init__(self) -> None:
        self._pool: AsyncConnectionPool | None = None
        self.saver: AsyncPostgresSaver | None = None

    async def start(self) -> AsyncPostgresSaver:
        self._pool = AsyncConnectionPool(
            psycopg_dsn(),
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
        )
        await self._pool.open()
        self.saver = AsyncPostgresSaver(conn=self._pool)
        await self.saver.setup()  # 初回のみテーブル作成。以降は冪等
        return self.saver

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self.saver = None
