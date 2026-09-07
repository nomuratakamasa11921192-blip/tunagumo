"""Phase 16-4/16-7: Web埋め込みチャットの個人情報保持期間を守るための削除ジョブ。
web_chat_sessionsは会話内容(個人情報を含みうる)を持つため、24時間を超えたら
エスカレーション済みかどうかに関わらず削除する(仕様書16-4: 「保持期間は24時間」)。
web_chat_request_logはレート制限・日次コスト上限の判定にしか使わないので、
判定に必要な期間(直近1時間+当日分)より十分長い25時間を超えたら削除する。

使い方: docker compose exec api python -m scripts.cleanup_web_chat_logs
cron例: 0 * * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.cleanup_web_chat_logs >> /var/log/tsunagumo_scan.log 2>&1
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import delete

from src.channels.web import SESSION_TTL_HOURS
from src.core.db import async_session_factory
from src.core.models import WebChatRequestLog, WebChatSession

REQUEST_LOG_RETENTION_HOURS = 25


async def cleanup_web_chat_logs() -> dict:
    async with async_session_factory() as db:
        session_cutoff = datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
        sessions_result = await db.execute(delete(WebChatSession).where(WebChatSession.created_at < session_cutoff))

        log_cutoff = datetime.utcnow() - timedelta(hours=REQUEST_LOG_RETENTION_HOURS)
        logs_result = await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.created_at < log_cutoff))

        await db.commit()

    return {"deleted_sessions": sessions_result.rowcount, "deleted_request_logs": logs_result.rowcount}


async def main() -> None:
    result = await cleanup_web_chat_logs()
    print(
        f"削除: web_chat_sessions {result['deleted_sessions']}件, "
        f"web_chat_request_log {result['deleted_request_logs']}件"
    )


if __name__ == "__main__":
    asyncio.run(main())
