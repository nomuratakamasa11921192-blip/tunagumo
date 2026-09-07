import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from src.channels.web import (
    DAILY_COST_CAP_USD,
    RATE_LIMIT_PER_HOUR,
    RATE_LIMIT_PER_MINUTE,
    SESSION_TTL_HOURS,
    DailyCostCapExceededError,
    OriginNotAllowedError,
    RateLimitedError,
    append_exchange,
    check_daily_cost_cap,
    check_origin,
    check_rate_limit,
    get_or_create_session,
    log_request,
)
from src.core.db import async_session_factory
from src.core.models import Tenant, WebChatRequestLog, WebChatSession
from tests.conftest import tenant  # noqa: F401  (fixture)

asyncio_test = pytest.mark.asyncio(loop_scope="session")


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.tenant_id == tenant_id))
        await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == tenant_id))
        await db.commit()


def _tenant_with_origin(origin: str = "https://example.com") -> Tenant:
    return Tenant(id=uuid.uuid4(), name="x", api_key_hash="x", web_widget_allowed_origin=origin)


def test_check_origin_accepts_matching_origin():
    t = _tenant_with_origin("https://example.com")
    check_origin(t, "https://example.com")  # 例外が出なければOK


def test_check_origin_rejects_mismatched_origin():
    t = _tenant_with_origin("https://example.com")
    with pytest.raises(OriginNotAllowedError):
        check_origin(t, "https://evil.example")


def test_check_origin_rejects_missing_origin_header():
    t = _tenant_with_origin("https://example.com")
    with pytest.raises(OriginNotAllowedError):
        check_origin(t, None)


def test_check_origin_rejects_when_widget_not_configured():
    t = _tenant_with_origin(None)
    with pytest.raises(OriginNotAllowedError):
        check_origin(t, "https://example.com")


@asyncio_test
async def test_rate_limit_per_minute_blocks_after_threshold(tenant):
    try:
        async with async_session_factory() as db:
            for _ in range(RATE_LIMIT_PER_MINUTE):
                await log_request(db, tenant_id=tenant["id"], ip_address="1.2.3.4")

            with pytest.raises(RateLimitedError):
                await check_rate_limit(db, tenant_id=tenant["id"], ip_address="1.2.3.4")
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_rate_limit_does_not_block_different_ip(tenant):
    try:
        async with async_session_factory() as db:
            for _ in range(RATE_LIMIT_PER_MINUTE):
                await log_request(db, tenant_id=tenant["id"], ip_address="1.2.3.4")

            await check_rate_limit(db, tenant_id=tenant["id"], ip_address="9.9.9.9")  # 例外なし
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_rate_limit_per_hour_blocks_after_threshold(tenant):
    try:
        async with async_session_factory() as db:
            for _ in range(RATE_LIMIT_PER_HOUR):
                db.add(
                    WebChatRequestLog(
                        tenant_id=tenant["id"],
                        ip_address="1.2.3.4",
                        cost_usd=0.0,
                        created_at=datetime.utcnow() - timedelta(minutes=5),
                    )
                )
            await db.commit()

            with pytest.raises(RateLimitedError):
                await check_rate_limit(db, tenant_id=tenant["id"], ip_address="1.2.3.4")
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_daily_cost_cap_blocks_after_threshold(tenant):
    try:
        async with async_session_factory() as db:
            await log_request(db, tenant_id=tenant["id"], ip_address="1.2.3.4", cost_usd=DAILY_COST_CAP_USD)

            with pytest.raises(DailyCostCapExceededError):
                await check_daily_cost_cap(db, tenant_id=tenant["id"])
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_daily_cost_cap_ignores_yesterdays_spend(tenant):
    try:
        async with async_session_factory() as db:
            db.add(
                WebChatRequestLog(
                    tenant_id=tenant["id"],
                    ip_address="1.2.3.4",
                    cost_usd=DAILY_COST_CAP_USD,
                    created_at=datetime.utcnow() - timedelta(days=1, hours=1),
                )
            )
            await db.commit()

            await check_daily_cost_cap(db, tenant_id=tenant["id"])  # 例外なし
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_get_or_create_session_creates_new_when_none_given(tenant):
    try:
        async with async_session_factory() as db:
            session = await get_or_create_session(db, tenant_id=tenant["id"], session_id=None)
            assert session.tenant_id == tenant["id"]
            assert session.messages == []
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_get_or_create_session_reuses_existing_within_ttl(tenant):
    try:
        async with async_session_factory() as db:
            first = await get_or_create_session(db, tenant_id=tenant["id"], session_id=None)
            second = await get_or_create_session(db, tenant_id=tenant["id"], session_id=first.id)
            assert second.id == first.id
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_get_or_create_session_starts_fresh_when_expired(tenant):
    try:
        async with async_session_factory() as db:
            expired = WebChatSession(
                tenant_id=tenant["id"],
                messages=[{"role": "user", "content": "古い会話"}],
                created_at=datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS + 1),
            )
            db.add(expired)
            await db.commit()
            await db.refresh(expired)

            fresh = await get_or_create_session(db, tenant_id=tenant["id"], session_id=expired.id)

            assert fresh.id != expired.id
            assert fresh.messages == []
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_get_or_create_session_does_not_leak_other_tenants_session(tenant):
    other_id = uuid.uuid4()
    try:
        async with async_session_factory() as db:
            db.add(Tenant(id=other_id, name="other", api_key_hash=uuid.uuid4().hex))
            await db.commit()

            other_session = WebChatSession(tenant_id=other_id, messages=[])
            db.add(other_session)
            await db.commit()
            await db.refresh(other_session)

            fresh = await get_or_create_session(db, tenant_id=tenant["id"], session_id=other_session.id)

            assert fresh.id != other_session.id
            assert fresh.tenant_id == tenant["id"]
    finally:
        await _cleanup(tenant["id"])
        async with async_session_factory() as db:
            await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == other_id))
            await db.execute(delete(Tenant).where(Tenant.id == other_id))
            await db.commit()


@asyncio_test
async def test_append_exchange_updates_messages_and_count(tenant):
    try:
        async with async_session_factory() as db:
            session = await get_or_create_session(db, tenant_id=tenant["id"], session_id=None)
            await append_exchange(
                db, session=session, user_message="こんにちは", reply="いらっしゃいませ", escalated=False
            )

            assert session.message_count == 1
            assert session.messages == [
                {"role": "user", "content": "こんにちは"},
                {"role": "assistant", "content": "いらっしゃいませ"},
            ]
            assert session.escalated is False
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_append_exchange_sets_escalated_flag_permanently(tenant):
    try:
        async with async_session_factory() as db:
            session = await get_or_create_session(db, tenant_id=tenant["id"], session_id=None)
            await append_exchange(db, session=session, user_message="質問", reply="担当者へ", escalated=True)
            await append_exchange(db, session=session, user_message="続き", reply="回答できます", escalated=False)

            assert session.escalated is True  # 一度エスカレーションしたら戻らない
    finally:
        await _cleanup(tenant["id"])
