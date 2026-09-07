"""再デプロイなしで機能を止められる緊急停止フラグ(Phase 12-1)。DBに保存するので、
プロセスを再起動しなくても管理画面から即座に反映できる。

既定値はFalse(安全側)。行が無い(=一度も設定されていない)場合もFalseとして扱う。
"""

from src.core.db import async_session_factory
from src.core.models import RuntimeFlag

ACTIONS_ENABLED_KEY = "actions_enabled"


async def actions_enabled() -> bool:
    async with async_session_factory() as db:
        row = await db.get(RuntimeFlag, ACTIONS_ENABLED_KEY)
        return bool(row and row.value)


async def set_actions_enabled(value: bool) -> None:
    async with async_session_factory() as db:
        row = await db.get(RuntimeFlag, ACTIONS_ENABLED_KEY)
        if row is None:
            db.add(RuntimeFlag(key=ACTIONS_ENABLED_KEY, value=value))
        else:
            row.value = value
        await db.commit()
