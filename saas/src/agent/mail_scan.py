"""メール即レスの定期ジョブ(2026-09-16)。スケジューラ(scripts/scheduler.py)から2分ごとに呼ばれ、
メール連携を有効にしている全テナントのメールボックスを確認する。判定・送受信の部品と安全策は
src/channels/mail.pyを参照。
"""

import logging
import uuid
from datetime import datetime
from typing import Callable

from sqlalchemy import select

from src.agent.cost import compute_cost_usd
from src.agent.inquiry_triage import triage
from src.agent.llm import StructuredLLM
from src.agent.public_responder import ESCALATION_MESSAGE, PublicResponderResult, emergency_result, respond
from src.channels.inquiries import notify_escalation, record_auto_reply, record_escalation
from src.channels.mail import (
    MAX_AUTO_REPLIES_PER_SENDER_PER_DAY,
    MAX_AUTO_REPLIES_PER_TENANT_PER_DAY,
    ImapSmtpTransport,
    MailAccountSettings,
    MailConfigError,
    claim_message,
    compose_auto_reply,
    count_recent_auto_replies,
    parse_mail,
    reply_subject,
    run_in_thread,
)
from src.core.ai_budget import BudgetExceededError, ensure_budget_available, record_cost
from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.core.db import async_session_factory, tenant_scoped_session_factory
from src.core.models import Lead, Tenant, TenantMailAccount
from src.core.tenant_context import set_tenant_scope
from src.rag.embeddings import EmbeddingError, OpenAIEmbeddingProvider
from src.rag.prompt_safety import render_retrieved_context
from src.rag.search import hybrid_search

logger = logging.getLogger(__name__)


def account_settings(row: TenantMailAccount) -> MailAccountSettings:
    return MailAccountSettings(
        from_address=row.from_address,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        username=row.username,
        password=decrypt_secret(row.password_encrypted),
    )


async def scan_mailboxes(
    *,
    app_configs: dict,
    default_industry: str,
    llm_factory: Callable[[], StructuredLLM],
    transport=None,
) -> dict:
    transport = transport or ImapSmtpTransport()
    totals = {"accounts": 0, "auto_replied": 0, "escalated": 0, "skipped": 0, "errors": 0}

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(TenantMailAccount, Tenant)
                .join(Tenant, Tenant.id == TenantMailAccount.tenant_id)
                .where(TenantMailAccount.enabled.is_(True), Tenant.subscription_active.is_(True))
            )
        ).all()

    for account_row, tenant in rows:
        totals["accounts"] += 1
        try:
            account = account_settings(account_row)
            fetched = await run_in_thread(
                transport.fetch_new, account, last_uid=account_row.last_uid, uidvalidity=account_row.uidvalidity
            )
        except MailConfigError as e:
            totals["errors"] += 1
            await _save_state(tenant.id, error=str(e))
            continue
        except Exception:  # noqa: BLE001
            totals["errors"] += 1
            logger.exception("メールボックスの確認に失敗しました: tenant_id=%s", tenant.id)
            await _save_state(tenant.id, error="メールの確認中にエラーが発生しました。時間をおいて自動で再試行します。")
            continue

        app_config = app_configs.get(tenant.industry, app_configs[default_industry])
        for uid, raw in fetched.messages:
            try:
                outcome = await _handle_message(
                    tenant=tenant, account=account, uid=uid, raw=raw, uidvalidity=fetched.uidvalidity,
                    app_config=app_config, llm_factory=llm_factory, transport=transport,
                )
                totals[outcome] = totals.get(outcome, 0) + 1
            except Exception:  # noqa: BLE001
                totals["errors"] += 1
                logger.exception("メールの処理に失敗しました: tenant_id=%s uid=%s", tenant.id, uid)

        await _save_state(tenant.id, last_uid=fetched.last_uid, uidvalidity=fetched.uidvalidity)

    return totals


async def _save_state(tenant_id: uuid.UUID, *, last_uid=None, uidvalidity=None, error: str | None = None) -> None:
    async with async_session_factory() as db:
        row = await db.get(TenantMailAccount, tenant_id)
        if row is None:
            return
        row.last_checked_at = datetime.utcnow()
        row.last_error = error
        if error is None:
            row.last_uid = last_uid
            row.uidvalidity = uidvalidity
        await db.commit()


async def _handle_message(*, tenant, account, uid, raw, uidvalidity, app_config, llm_factory, transport) -> str:
    parsed = parse_mail(raw, own_address=account.from_address, fallback_id=f"<uid-{uidvalidity}-{uid}@tsunagumo.invalid>")

    async with tenant_scoped_session_factory() as db:
        db._rls_tenant_id = tenant.id
        await set_tenant_scope(db, tenant.id)

        claim = await claim_message(db, tenant_id=tenant.id, message_id=parsed.message_id, sender=parsed.reply_address)
        if claim is None:
            return "skipped"
        await _mark_lead_replied(db, tenant.id, {parsed.reply_address, parsed.from_address})
        if parsed.skip_reason:
            return "skipped"

        user_message = f"件名: {parsed.subject}\n{parsed.body}"
        record_kwargs = dict(
            tenant_id=tenant.id, channel="email", external_user_id=parsed.reply_address,
            email_subject=parsed.subject, email_message_id=parsed.message_id,
        )

        # メールのループ防止: 同じ相手・テナント全体の自動返信数の上限を超えたら、返信せず担当者へ回す
        if (
            await count_recent_auto_replies(db, tenant_id=tenant.id, sender=parsed.reply_address) >= MAX_AUTO_REPLIES_PER_SENDER_PER_DAY
            or await count_recent_auto_replies(db, tenant_id=tenant.id) >= MAX_AUTO_REPLIES_PER_TENANT_PER_DAY
        ):
            t = triage(parsed.body)
            inquiry, is_new, became_urgent = await record_escalation(
                db, user_message=user_message, reply="(自動返信の上限に達したため、返信していません)",
                reason="自動返信の上限に達しました", urgency=t.urgency, category=t.category, **record_kwargs,
            )
            await _notify(tenant, inquiry, is_new, became_urgent)
            return "escalated"

        result = emergency_result(parsed.body, emergency_phone=tenant.emergency_contact_phone)
        if result is None:
            result = await _ai_or_escalation(db, tenant, parsed, user_message, app_config, llm_factory)

        body = compose_auto_reply(result.reply, company_name=app_config.company.name)
        sent = True
        try:
            await run_in_thread(
                transport.send, account, to=parsed.reply_address, subject=reply_subject(parsed.subject), body=body,
                in_reply_to=parsed.message_id, references=parsed.references, auto=True,
            )
            claim.auto_replied = True
            await db.commit()
        except MailConfigError:
            sent = False
            logger.warning("自動返信の送信に失敗しました: tenant_id=%s", tenant.id)

        if result.escalated or not sent:
            inquiry, is_new, became_urgent = await record_escalation(
                db, user_message=user_message,
                reply=result.reply if sent else "(自動返信を送信できませんでした)",
                reason=result.reason if sent else "自動返信の送信に失敗しました",
                urgency=result.urgency, category=result.category, **record_kwargs,
            )
            await _notify(tenant, inquiry, is_new, became_urgent)
            return "escalated"

        await record_auto_reply(db, user_message=user_message, reply=result.reply, category=result.category, **record_kwargs)
        return "auto_replied"


async def _mark_lead_replied(db, tenant_id, addresses: set) -> None:
    """見込み客からメールが届いたら返信ありとして記録し、追客メールを止める(src/agent/proposal_scan.py)。"""
    addresses = {a for a in addresses if a}
    if not addresses:
        return
    leads = (await db.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.email.in_(addresses)))).scalars().all()
    for lead in leads:
        lead.last_reply_at = datetime.utcnow()
    if leads:
        await db.commit()


async def _ai_or_escalation(db, tenant, parsed, user_message, app_config, llm_factory) -> PublicResponderResult:
    """AIで回答を試みる。予算切れ・運営キー不備の時は推測で答えず担当者へ回す。"""
    tenant_row = await db.get(Tenant, tenant.id)
    try:
        ensure_budget_available(tenant_row)
        ai_available = bool(settings.openai_api_key)
    except BudgetExceededError:
        ai_available = False
    if not ai_available:
        t = triage(parsed.body)
        return PublicResponderResult(
            reply=ESCALATION_MESSAGE, escalated=True, reason="AI応答を利用できないため担当者へ回しました",
            urgency=t.urgency, category=t.category,
        )

    rag_context = ""
    try:
        vectors = await OpenAIEmbeddingProvider(api_key=settings.openai_api_key).embed([parsed.body])
        results = await hybrid_search(
            db, tenant_id=tenant.id, query_text=parsed.body, query_embedding=vectors[0], index_scope="public", top_k=3
        )
        rag_context = render_retrieved_context(results)
    except EmbeddingError:
        rag_context = ""

    model = settings.openai_model_light or settings.openai_model
    result = await respond(
        llm=llm_factory(), model=model, company_name=app_config.company.name, message=user_message,
        rag_context=rag_context, emergency_phone=tenant.emergency_contact_phone,
    )
    if result.usage:
        record_cost(tenant_row, compute_cost_usd(model, result.usage, app_config.pricing))
        await db.commit()
    return result


async def _notify(tenant, inquiry, is_new, became_urgent) -> None:
    try:
        await notify_escalation(tenant, inquiry, is_new=is_new, became_urgent=became_urgent)
    except Exception:  # noqa: BLE001
        logger.exception("問い合わせ通知に失敗しました: inquiry_id=%s", inquiry.id)
