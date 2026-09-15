"""物件提案・追客メールの定期ジョブ(2026-09-16)。スケジューラから1時間ごとに呼ばれる。

- 見込み客の希望条件に合う新しい物件があれば「物件のご案内」を作る
- 新しい物件が無く、前回の送信から一定日数(既定7日)返信が無ければ「追客メール」を作る(既定3回まで)
- テナントが自動送信を有効にしていれば、ひな形どおりの提案は承認なしで送る。無効(既定)なら承認待ちにし、
  担当者へ「承認待ちの提案があります」とメールで知らせる
- 同じ見込み客へのメールは3日に1通まで、自動送信は日本時間9時〜20時のみ(夜間・早朝に送らない)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.channels.inquiries import notification_recipient
from src.channels.mail import ImapSmtpTransport, MailConfigError
from src.core.config import settings
from src.core.db import async_session_factory, tenant_scoped_session_factory
from src.core.email import send_email
from src.core.models import Lead, Property, Proposal, Tenant, TenantMailAccount
from src.core.proposals import (
    MAX_PROPERTIES_PER_PROPOSAL,
    build_follow_up,
    build_proposal,
    compose_footer,
    is_match,
    sender_info_missing,
    unsubscribe_headers,
)
from src.core.tenant_context import set_tenant_scope

logger = logging.getLogger(__name__)

MIN_DAYS_BETWEEN_EMAILS = 3
MAX_NEW_PROPOSALS_PER_TENANT_PER_RUN = 50
SEND_HOURS_JST = range(9, 20)
JST = timezone(timedelta(hours=9))


class ProposalBlockedError(Exception):
    """設定不足などで今は送れない(提案は承認待ちのまま残す)。メッセージは顧客に表示してよい文面。"""


def within_send_hours(now: datetime) -> bool:
    aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return aware.astimezone(JST).hour in SEND_HOURS_JST


async def send_proposal(db, tenant: Tenant, lead: Lead, proposal: Proposal, *, transport=None, now: datetime | None = None) -> None:
    """提案メールを送る。送れない設定なら ProposalBlockedError、送信自体の失敗は MailConfigError。"""
    from src.agent.mail_scan import account_settings

    now = now or datetime.utcnow()
    if lead.status != "active":
        raise ProposalBlockedError("この見込み客は配信停止または一時停止中のため送信できません。")
    if lead.consent_at is None:
        raise ProposalBlockedError("この見込み客から広告メールの受信同意を得ていないため送信できません。")
    missing = sender_info_missing(tenant)
    if missing:
        raise ProposalBlockedError(f"送信者情報({'・'.join(missing)})が未設定のため送信できません。「物件提案・追客」の設定から登録してください。")
    account = await db.get(TenantMailAccount, tenant.id)
    if account is None:
        raise ProposalBlockedError("送信に使うメールアドレスが未連携です。「設定・利用状況」のメールの即レスから連携してください。")

    try:
        await asyncio.to_thread(
            (transport or ImapSmtpTransport()).send,
            account_settings(account),
            to=lead.email,
            subject=proposal.subject,
            body=proposal.body + compose_footer(tenant, lead),
            in_reply_to=None,
            references=None,
            auto=False,
            extra_headers=unsubscribe_headers(lead),
        )
    except MailConfigError as e:
        proposal.status = "failed"
        proposal.error = str(e)
        await db.commit()
        raise

    proposal.status = "sent"
    proposal.sent_at = now
    proposal.error = None
    lead.last_sent_at = now
    lead.follow_up_count = lead.follow_up_count + 1 if proposal.kind == "follow_up" else 0
    await db.commit()


async def generate_for_tenant(
    db, tenant: Tenant, *, now: datetime, transport=None, lead_ids: list | None = None, allow_auto_send: bool = True
) -> dict:
    """1テナント分の提案を作る(自動送信が有効なら送る)。lead_idsを渡すとその見込み客だけを対象にする。"""
    result = {"created": 0, "sent": 0, "pending": 0}
    auto_sending = allow_auto_send and tenant.proposal_auto_send and within_send_hours(now)
    if auto_sending and lead_ids is None:
        # 夜間に作られた・設定不足で送れなかった提案を、送信できる時間帯になったら送る(AIで書き換えたものは除く)
        waiting = (
            await db.execute(
                select(Proposal, Lead)
                .join(Lead, Lead.id == Proposal.lead_id)
                .where(Proposal.tenant_id == tenant.id, Proposal.status == "pending", Proposal.ai_edited.is_(False))
                .order_by(Proposal.created_at)
                .limit(MAX_NEW_PROPOSALS_PER_TENANT_PER_RUN)
            )
        ).all()
        for proposal, lead in waiting:
            if lead.last_sent_at and now - lead.last_sent_at < timedelta(days=MIN_DAYS_BETWEEN_EMAILS):
                continue
            try:
                await send_proposal(db, tenant, lead, proposal, transport=transport, now=now)
                result["sent"] += 1
            except ProposalBlockedError as e:
                proposal.error = str(e)
                await db.commit()
            except MailConfigError:
                pass

    lead_query = select(Lead).where(Lead.tenant_id == tenant.id, Lead.status == "active", Lead.consent_at.is_not(None))
    if lead_ids is not None:
        lead_query = lead_query.where(Lead.id.in_(lead_ids))
    leads = (await db.execute(lead_query)).scalars().all()
    if not leads:
        return result
    properties = (
        await db.execute(
            select(Property)
            .where(Property.tenant_id == tenant.id, Property.status == "available")
            .order_by(Property.created_at.desc())
        )
    ).scalars().all()
    existing = (
        await db.execute(
            select(Proposal).where(Proposal.tenant_id == tenant.id, Proposal.status.in_(("pending", "sent")))
        )
    ).scalars().all()
    proposed: dict = {}
    has_pending: set = set()
    for p in existing:
        proposed.setdefault(p.lead_id, set()).update(p.property_ids or [])
        if p.status == "pending":
            has_pending.add(p.lead_id)

    for lead in leads:
        if result["created"] >= MAX_NEW_PROPOSALS_PER_TENANT_PER_RUN:
            break
        if lead.id in has_pending:
            continue
        if lead.last_sent_at and now - lead.last_sent_at < timedelta(days=MIN_DAYS_BETWEEN_EMAILS):
            continue

        already = proposed.get(lead.id, set())
        matches = [p for p in properties if is_match(p, lead) and str(p.id) not in already][:MAX_PROPERTIES_PER_PROPOSAL]
        if matches:
            subject, body = build_proposal(tenant, lead, matches)
            proposal = Proposal(
                tenant_id=tenant.id, lead_id=lead.id, kind="proposal",
                property_ids=[str(p.id) for p in matches], subject=subject, body=body,
            )
        elif (
            lead.last_sent_at is not None
            and now - lead.last_sent_at >= timedelta(days=tenant.follow_up_interval_days)
            and lead.follow_up_count < tenant.follow_up_max
            and not (lead.last_reply_at and lead.last_reply_at >= lead.last_sent_at)
        ):
            subject, body = build_follow_up(tenant, lead)
            proposal = Proposal(tenant_id=tenant.id, lead_id=lead.id, kind="follow_up", property_ids=[], subject=subject, body=body)
        else:
            continue

        db.add(proposal)
        await db.commit()
        result["created"] += 1

        if auto_sending:
            try:
                await send_proposal(db, tenant, lead, proposal, transport=transport, now=now)
                result["sent"] += 1
                continue
            except ProposalBlockedError as e:
                proposal.error = str(e)
                await db.commit()
            except MailConfigError:
                continue
        result["pending"] += 1
    return result


async def scan_proposals(*, transport=None, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    totals = {"tenants": 0, "created": 0, "sent": 0, "pending": 0}
    async with async_session_factory() as db:
        tenants = (
            await db.execute(
                select(Tenant).where(
                    Tenant.subscription_active.is_(True),
                    Tenant.id.in_(select(Lead.tenant_id).where(Lead.status == "active")),
                )
            )
        ).scalars().all()

    for tenant in tenants:
        try:
            async with tenant_scoped_session_factory() as db:
                db._rls_tenant_id = tenant.id
                await set_tenant_scope(db, tenant.id)
                result = await generate_for_tenant(db, tenant, now=now, transport=transport)
        except Exception:  # noqa: BLE001
            logger.exception("物件提案の作成に失敗しました: tenant_id=%s", tenant.id)
            continue
        totals["tenants"] += 1
        for key in ("created", "sent", "pending"):
            totals[key] += result[key]
        if result["pending"]:
            await _notify_pending(tenant, result["pending"])
    return totals


async def _notify_pending(tenant: Tenant, count: int) -> None:
    to = notification_recipient(tenant)
    if not to:
        return
    link = f"{settings.saas_public_url}/" if settings.saas_public_url else ""
    try:
        await send_email(
            to=to,
            subject=f"【ツナグモ】承認待ちの物件提案・追客メールが{count}件あります",
            html=(
                f"<p>見込み客への物件提案・追客メールが{count}件作成され、承認待ちになっています。</p>"
                "<p>ツナグモの「物件提案・追客」画面で内容を確認し、送信してください。</p>"
                + (f'<p><a href="{link}">{link}</a></p>' if link else "")
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("承認待ち通知に失敗しました: tenant_id=%s", tenant.id)
