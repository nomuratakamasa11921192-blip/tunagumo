"""AIが担当者へ回した問い合わせ(エスカレーション)の記録と、担当者へのメール通知(2026-09-15)。
Webチャット(src/api/routes/chat.py)とLINE(src/api/routes/line_webhook.py)の両方から使う。

仕様書15-2の「対応不可と判断 → escalation → 人間が確認」の橋にあたる。Slackを使わない
構成なので、通知はメール、確認は問い合わせ一覧画面(src/api/routes/inquiries.py)で行う。
"""

import html
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.inquiry_triage import URGENT
from src.core.config import settings
from src.core.email import send_email
from src.core.models import Inquiry, Tenant

logger = logging.getLogger(__name__)

CHANNEL_LABELS = {"web": "Webチャット", "line": "LINE", "email": "メール"}


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


async def record_escalation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: str,
    user_message: str,
    reply: str,
    reason: str,
    urgency: str,
    category: str,
    web_chat_session_id: uuid.UUID | None = None,
    external_user_id: str | None = None,
    email_subject: str | None = None,
    email_message_id: str | None = None,
) -> tuple[Inquiry, bool, bool]:
    """同じ会話の未対応の問い合わせがあればそこへ追記し、無ければ新規に作る。
    戻り値: (inquiry, 新規作成したか, 緊急度が今回上がったか)。"""
    query = select(Inquiry).where(Inquiry.tenant_id == tenant_id, Inquiry.status != "resolved")
    if channel in ("line", "email") and external_user_id:
        query = query.where(Inquiry.channel == channel, Inquiry.external_user_id == external_user_id)
    elif web_chat_session_id is not None:
        query = query.where(Inquiry.web_chat_session_id == web_chat_session_id)
    else:
        query = None

    inquiry = (await db.execute(query.order_by(Inquiry.created_at.desc()).limit(1))).scalar_one_or_none() if query is not None else None
    now = _now_iso()
    new_messages = [
        {"role": "user", "content": user_message, "at": now},
        {"role": "assistant", "content": reply, "at": now},
    ]

    if inquiry is None:
        inquiry = Inquiry(
            tenant_id=tenant_id,
            channel=channel,
            external_user_id=external_user_id,
            web_chat_session_id=web_chat_session_id,
            urgency=urgency,
            category=category,
            reason=reason,
            messages=new_messages,
            email_subject=email_subject,
            email_message_id=email_message_id,
        )
        db.add(inquiry)
        await db.commit()
        await db.refresh(inquiry)
        return inquiry, True, urgency == URGENT

    became_urgent = urgency == URGENT and inquiry.urgency != URGENT
    inquiry.messages = list(inquiry.messages or []) + new_messages
    if became_urgent:
        inquiry.urgency = URGENT
        inquiry.category = category
        inquiry.reason = reason
    if email_message_id:
        # 担当者の返信は最新のメールへの返信としてつなげる
        inquiry.email_subject = email_subject or inquiry.email_subject
        inquiry.email_message_id = email_message_id
    await db.commit()
    return inquiry, False, became_urgent


async def append_to_open_inquiry(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: str,
    user_message: str,
    reply: str,
    web_chat_session_id: uuid.UUID | None = None,
    external_user_id: str | None = None,
) -> None:
    """AIが自分で回答できた後続メッセージも、対応中の問い合わせがあれば担当者が経緯を
    追えるよう追記する(新規の問い合わせは作らない)。"""
    query = select(Inquiry).where(Inquiry.tenant_id == tenant_id, Inquiry.status != "resolved")
    if channel in ("line", "email") and external_user_id:
        query = query.where(Inquiry.channel == channel, Inquiry.external_user_id == external_user_id)
    elif web_chat_session_id is not None:
        query = query.where(Inquiry.web_chat_session_id == web_chat_session_id)
    else:
        return
    inquiry = (await db.execute(query.limit(1))).scalar_one_or_none()
    if inquiry is None:
        return
    now = _now_iso()
    inquiry.messages = list(inquiry.messages or []) + [
        {"role": "user", "content": user_message, "at": now},
        {"role": "assistant", "content": reply, "at": now},
    ]
    await db.commit()


def notification_recipient(tenant: Tenant) -> str | None:
    return tenant.inquiry_notify_email or tenant.email


async def notify_escalation(tenant: Tenant, inquiry: Inquiry, *, is_new: bool, became_urgent: bool) -> bool:
    """担当者へメールで知らせる。新しい問い合わせと、緊急に変わった時だけ送る
    (同じ会話でメッセージが続くたびに何通も送らない)。送れたらTrue。"""
    if not (is_new or became_urgent):
        return False
    to = notification_recipient(tenant)
    if not to:
        logger.warning("問い合わせ通知先が未設定のため通知しません: tenant_id=%s inquiry_id=%s", tenant.id, inquiry.id)
        return False

    urgent = inquiry.urgency == URGENT
    subject = f"{'【緊急】' if urgent else ''}【ツナグモ】お問い合わせ({inquiry.category})への対応をお願いします"
    last_user = next((m["content"] for m in reversed(inquiry.messages or []) if m.get("role") == "user"), "")
    link = f"{settings.saas_public_url}/" if settings.saas_public_url else ""
    body = (
        f"<p>{html.escape(CHANNEL_LABELS.get(inquiry.channel, inquiry.channel))}に、AIでは回答できない"
        f"お問い合わせが届きました。{'<strong>緊急の可能性があります。至急ご確認ください。</strong>' if urgent else ''}</p>"
        f"<p>分類: {html.escape(inquiry.category)}</p>"
        f"<p>内容:<br>{html.escape(last_user[:500]).replace(chr(10), '<br>')}</p>"
        "<p>お客様には「担当者から折り返しご連絡いたします」と自動でお伝えしています。"
        "ツナグモの「問い合わせ」画面から内容を確認し、対応してください。</p>"
        + (f'<p><a href="{html.escape(link)}">{html.escape(link)}</a></p>' if link else "")
    )
    return await send_email(to=to, subject=subject, html=body)


async def record_auto_reply(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: str,
    external_user_id: str,
    user_message: str,
    reply: str,
    category: str,
    email_subject: str | None = None,
    email_message_id: str | None = None,
) -> None:
    """AIが自動で返信を送った記録を残す(メールは相手に届く文面なので、担当者が後から確認できるように)。
    対応中の問い合わせがあればそこへ追記し、無ければ「対応済み」として新規に作る。"""
    query = select(Inquiry).where(
        Inquiry.tenant_id == tenant_id,
        Inquiry.status != "resolved",
        Inquiry.channel == channel,
        Inquiry.external_user_id == external_user_id,
    )
    inquiry = (await db.execute(query.limit(1))).scalar_one_or_none()
    now = _now_iso()
    new_messages = [
        {"role": "user", "content": user_message, "at": now},
        {"role": "assistant", "content": reply, "at": now},
    ]
    if inquiry is not None:
        inquiry.messages = list(inquiry.messages or []) + new_messages
        if email_message_id:
            inquiry.email_subject = email_subject or inquiry.email_subject
            inquiry.email_message_id = email_message_id
    else:
        db.add(
            Inquiry(
                tenant_id=tenant_id,
                channel=channel,
                external_user_id=external_user_id,
                category=category,
                status="resolved",
                resolved_at=datetime.utcnow(),
                reason="AIが自動で返信しました",
                messages=new_messages,
                email_subject=email_subject,
                email_message_id=email_message_id,
            )
        )
    await db.commit()
