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
from src.core.models import Inquiry, MailProcessedMessage, WebChatRequestLog, WebChatSession

REQUEST_LOG_RETENTION_HOURS = 25
# 2026-09-15: 担当者へ回した問い合わせ(inquiries)の保持期間。個人情報を含みうるため、
# 対応済みは30日、未対応のまま放置されたものも90日で削除する。
RESOLVED_INQUIRY_RETENTION_DAYS = 30
MAX_INQUIRY_RETENTION_DAYS = 90
# メール即レスの処理済み記録(二重返信防止・ループ防止の判定用)。判定に必要なのは直近24時間だが、
# 遅れて再配信されたメールにも二重返信しないよう30日残す
MAIL_PROCESSED_RETENTION_DAYS = 30


async def cleanup_web_chat_logs() -> dict:
    async with async_session_factory() as db:
        session_cutoff = datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
        sessions_result = await db.execute(delete(WebChatSession).where(WebChatSession.created_at < session_cutoff))

        log_cutoff = datetime.utcnow() - timedelta(hours=REQUEST_LOG_RETENTION_HOURS)
        logs_result = await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.created_at < log_cutoff))

        now = datetime.utcnow()
        inquiries_result = await db.execute(
            delete(Inquiry).where(
                ((Inquiry.status == "resolved") & (Inquiry.updated_at < now - timedelta(days=RESOLVED_INQUIRY_RETENTION_DAYS)))
                | (Inquiry.created_at < now - timedelta(days=MAX_INQUIRY_RETENTION_DAYS))
            )
        )

        await db.execute(
            delete(MailProcessedMessage).where(
                MailProcessedMessage.processed_at < now - timedelta(days=MAIL_PROCESSED_RETENTION_DAYS)
            )
        )

        await db.commit()

    return {
        "deleted_sessions": sessions_result.rowcount,
        "deleted_request_logs": logs_result.rowcount,
        "deleted_inquiries": inquiries_result.rowcount,
    }


async def main() -> None:
    result = await cleanup_web_chat_logs()
    print(
        f"削除: web_chat_sessions {result['deleted_sessions']}件, "
        f"web_chat_request_log {result['deleted_request_logs']}件, "
        f"inquiries {result['deleted_inquiries']}件"
    )


if __name__ == "__main__":
    asyncio.run(main())
