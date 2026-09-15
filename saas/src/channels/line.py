"""Phase 15の土台: LINE公式アカウントをSaaS本体の顧客対応チャネルにするためのアダプタ。

**実際のLINE認証情報(テナントごとのline_channel_secret/line_channel_access_token)が
無いと本稼働できない**(1社目の顧客が自社のLINE公式アカウントを用意して初めて意味を持つ)。
そのため、実機での送受信テストはまだ書けない。ここに実装するのは、認証情報が無くても
正しさを検証できる部分(署名検証・冪等性判定)と、揃った後にそのまま呼べる送信関数の骨格。

Phase 16のsrc/channels/web.pyと違い、応答ロジック自体(src/agent/public_responder.py)は
完全に共用する想定(15-3のドキュメントに明記済み)。LINE固有の関心事だけをここに置く。
"""

import hashlib
import hmac
import base64
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import LineWebhookEvent

logger = logging.getLogger(__name__)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_REPLY_LENGTH = 4900  # line-bot/src/index.jsのreplyToLineと同じ安全マージン


def verify_line_signature(raw_body: bytes, signature: str | None, channel_secret: str) -> bool:
    """X-Line-Signatureヘッダの検証(line-bot/src/index.jsのverifyLineSignatureのPython版)。

    HMAC-SHA256(channel_secret, raw_body)をbase64エンコードした値と一致するかを
    タイミング攻撃耐性のある比較(hmac.compare_digest)で確認する。
    """
    if not signature:
        return False

    digest = hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def is_duplicate_event(db: AsyncSession, *, tenant_id, event_id: str) -> bool:
    """同じWebhookイベントを二重処理しないための判定(LINEはisRedeliveryで再送してくることがある)。

    (tenant_id, event_id)のUNIQUE制約(主キー)そのものが実体。既に記録済みならTrueを返す
    (呼び出し元はそのイベントを無視してよい)。未記録なら記録してFalseを返す
    (Phase 12のexecuted_actionsと同じ「先に記録してから処理する」順序)。
    """
    db.add(LineWebhookEvent(tenant_id=tenant_id, event_id=event_id))
    try:
        await db.commit()
        return False
    except IntegrityError:
        await db.rollback()
        return True


async def has_seen_event(db: AsyncSession, *, tenant_id, event_id: str) -> bool:
    """記録を追加せず、既に処理済みかどうかだけを確認する(読み取り専用の確認用)。"""
    result = await db.execute(
        select(LineWebhookEvent).where(
            LineWebhookEvent.tenant_id == tenant_id, LineWebhookEvent.event_id == event_id
        )
    )
    return result.scalar_one_or_none() is not None


async def reply_to_line(*, access_token: str, reply_token: str, text: str) -> bool:
    """replyToken(受信から一定時間のみ有効)を使った即時応答。送信できたらTrue。"""
    truncated = text if len(text) <= MAX_REPLY_LENGTH else text[: MAX_REPLY_LENGTH] + "…"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                LINE_REPLY_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
                json={"replyToken": reply_token, "messages": [{"type": "text", "text": truncated}]},
            )
        if res.status_code >= 400:
            logger.error("LINE返信に失敗しました(status=%s): %s", res.status_code, res.text)
            return False
        return True
    except httpx.HTTPError as e:
        logger.error("LINE返信中に通信エラーが発生しました: %s", e)
        return False


async def push_to_line(*, access_token: str, user_id: str, text: str) -> bool:
    """担当者が画面から返信する時など、replyTokenが無い(期限切れの)場合のプッシュ送信。
    LINEのプッシュメッセージは月間の無料枠を超えると従量課金になる(仕様書15-5)。送信できたらTrue。"""
    truncated = text if len(text) <= MAX_REPLY_LENGTH else text[:MAX_REPLY_LENGTH] + "…"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                LINE_PUSH_URL,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
                json={"to": user_id, "messages": [{"type": "text", "text": truncated}]},
            )
        if res.status_code >= 400:
            logger.error("LINEプッシュ送信に失敗しました(status=%s)", res.status_code)
            return False
        return True
    except httpx.HTTPError as e:
        logger.error("LINEプッシュ送信中に通信エラーが発生しました: %s", e)
        return False


async def get_or_create_line_session(db: AsyncSession, *, tenant_id, user_id: str):
    """LINEユーザーごとの会話(直近24時間分の履歴を応答に使う)。webと同じweb_chat_sessionsに
    channel="line"で持つので、保持期間の自動削除も同じジョブで行われる。"""
    from datetime import datetime, timedelta

    from src.channels.web import SESSION_TTL_HOURS
    from src.core.models import WebChatSession

    result = await db.execute(
        select(WebChatSession)
        .where(
            WebChatSession.tenant_id == tenant_id,
            WebChatSession.channel == "line",
            WebChatSession.external_user_id == user_id,
            WebChatSession.created_at >= datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS),
        )
        .order_by(WebChatSession.created_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        session = WebChatSession(tenant_id=tenant_id, messages=[], channel="line", external_user_id=user_id)
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session
