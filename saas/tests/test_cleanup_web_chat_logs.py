import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from scripts.cleanup_web_chat_logs import cleanup_web_chat_logs
from src.channels.web import SESSION_TTL_HOURS
from src.core.db import async_session_factory
from src.core.models import WebChatRequestLog, WebChatSession
from tests.conftest import tenant  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.tenant_id == tenant_id))
        await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == tenant_id))
        await db.commit()


async def test_deletes_sessions_older_than_ttl_but_keeps_recent(tenant):
    try:
        async with async_session_factory() as db:
            old = WebChatSession(
                tenant_id=tenant["id"],
                messages=[],
                created_at=datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS + 1),
            )
            recent = WebChatSession(tenant_id=tenant["id"], messages=[])
            db.add_all([old, recent])
            await db.commit()
            await db.refresh(old)
            await db.refresh(recent)

        result = await cleanup_web_chat_logs()
        assert result["deleted_sessions"] >= 1

        async with async_session_factory() as db:
            assert await db.get(WebChatSession, old.id) is None
            assert await db.get(WebChatSession, recent.id) is not None
    finally:
        await _cleanup(tenant["id"])


async def test_deletes_request_logs_older_than_retention_but_keeps_recent(tenant):
    from scripts.cleanup_web_chat_logs import REQUEST_LOG_RETENTION_HOURS

    try:
        async with async_session_factory() as db:
            old = WebChatRequestLog(
                tenant_id=tenant["id"],
                ip_address="1.2.3.4",
                created_at=datetime.utcnow() - timedelta(hours=REQUEST_LOG_RETENTION_HOURS + 1),
            )
            recent = WebChatRequestLog(tenant_id=tenant["id"], ip_address="1.2.3.4")
            db.add_all([old, recent])
            await db.commit()
            await db.refresh(old)
            await db.refresh(recent)

        result = await cleanup_web_chat_logs()
        assert result["deleted_request_logs"] >= 1

        async with async_session_factory() as db:
            remaining = (
                await db.execute(select(WebChatRequestLog.id).where(WebChatRequestLog.tenant_id == tenant["id"]))
            ).scalars().all()
            assert old.id not in remaining
            assert recent.id in remaining
    finally:
        await _cleanup(tenant["id"])


async def test_does_not_delete_escalated_sessions_early(tenant):
    """16-4は保持期間を厳格な24時間としており、エスカレーション済みでも例外にしない
    (個人情報の最小化を優先する。管理者は24時間以内に確認する運用が前提)。"""
    try:
        async with async_session_factory() as db:
            old_escalated = WebChatSession(
                tenant_id=tenant["id"],
                messages=[{"role": "user", "content": "苦情です"}],
                escalated=True,
                created_at=datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS + 1),
            )
            db.add(old_escalated)
            await db.commit()
            await db.refresh(old_escalated)

        await cleanup_web_chat_logs()

        async with async_session_factory() as db:
            assert await db.get(WebChatSession, old_escalated.id) is None
    finally:
        await _cleanup(tenant["id"])
