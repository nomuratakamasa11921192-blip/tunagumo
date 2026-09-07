"""Phase 16: Webサイト埋め込みチャットの、Web固有の関心事(Origin検証・レート制限・
セッション管理・日次コスト上限)。応答ロジック自体はsrc/agent/public_responder.pyに
共通化されている(LINE側が実装されたら同じものを使う)。

**最も無防備なチャネル(16-1)**: URLを知れば誰でも投げられる。すべての防御を
実装するまで公開しないこと(16-3)。
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Tenant, WebChatRequestLog, WebChatSession

SESSION_TTL_HOURS = 24  # 16-4: 保持期間は24時間、超過分は自動削除
RATE_LIMIT_PER_MINUTE = 5  # 16-3-2: 1IP 1分5件
RATE_LIMIT_PER_HOUR = 30  # 16-3-2: 1時間30件
DAILY_COST_CAP_USD = 2.0  # 16-3-4: 全体の日次コスト上限。実運用データが無いため暫定値
BUSY_MESSAGE = "現在混み合っています。しばらくしてから再度お試しください。"


class OriginNotAllowedError(Exception):
    pass


class RateLimitedError(Exception):
    pass


class DailyCostCapExceededError(Exception):
    pass


def check_origin(tenant: Tenant, origin: str | None) -> None:
    """16-3-1: 設定に列挙した顧客ドメインからのリクエストのみ受け付ける。"""
    if not tenant.web_widget_allowed_origin or origin != tenant.web_widget_allowed_origin:
        raise OriginNotAllowedError("許可されていないOriginからのリクエストです")


async def check_rate_limit(db: AsyncSession, *, tenant_id: uuid.UUID, ip_address: str) -> None:
    now = datetime.utcnow()

    per_minute = await db.execute(
        select(func.count()).where(
            WebChatRequestLog.tenant_id == tenant_id,
            WebChatRequestLog.ip_address == ip_address,
            WebChatRequestLog.created_at >= now - timedelta(minutes=1),
        )
    )
    if per_minute.scalar_one() >= RATE_LIMIT_PER_MINUTE:
        raise RateLimitedError(f"1分あたりの上限({RATE_LIMIT_PER_MINUTE}件)に達しました")

    per_hour = await db.execute(
        select(func.count()).where(
            WebChatRequestLog.tenant_id == tenant_id,
            WebChatRequestLog.ip_address == ip_address,
            WebChatRequestLog.created_at >= now - timedelta(hours=1),
        )
    )
    if per_hour.scalar_one() >= RATE_LIMIT_PER_HOUR:
        raise RateLimitedError(f"1時間あたりの上限({RATE_LIMIT_PER_HOUR}件)に達しました")


async def check_daily_cost_cap(db: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.coalesce(func.sum(WebChatRequestLog.cost_usd), 0.0)).where(
            WebChatRequestLog.tenant_id == tenant_id,
            WebChatRequestLog.created_at >= today_start,
        )
    )
    if result.scalar_one() >= DAILY_COST_CAP_USD:
        raise DailyCostCapExceededError(f"本日の上限(${DAILY_COST_CAP_USD:.2f})に達しました")


async def log_request(db: AsyncSession, *, tenant_id: uuid.UUID, ip_address: str, cost_usd: float = 0.0) -> None:
    db.add(WebChatRequestLog(tenant_id=tenant_id, ip_address=ip_address, cost_usd=cost_usd))
    await db.commit()


async def get_or_create_session(
    db: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID | None
) -> WebChatSession:
    if session_id is not None:
        existing = await db.get(WebChatSession, session_id)
        if (
            existing is not None
            and existing.tenant_id == tenant_id
            and existing.created_at >= datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
        ):
            return existing

    session = WebChatSession(tenant_id=tenant_id, messages=[])
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def append_exchange(
    db: AsyncSession, *, session: WebChatSession, user_message: str, reply: str, escalated: bool
) -> None:
    messages = list(session.messages or [])
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": reply})
    session.messages = messages
    session.message_count += 1
    if escalated:
        session.escalated = True
    session.last_activity_at = datetime.utcnow()
    await db.commit()
