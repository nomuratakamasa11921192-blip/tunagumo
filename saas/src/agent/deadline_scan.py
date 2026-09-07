import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import select

from src.agent.config_models import AppConfig
from src.agent.llm import StructuredLLM
from src.agent.runner import submit_approval_decision
from src.core.config import settings
from src.core.db import async_session_factory
from src.core.email import send_email
from src.core.logging_config import RequestContext
from src.core.models import Approval, Tenant

logger = logging.getLogger(__name__)


def _reminder_email_html(*, hours_left: float) -> str:
    login_line = (
        f'<p>ログインしてご確認ください: <a href="{settings.saas_public_url}">{settings.saas_public_url}</a></p>'
        if settings.saas_public_url
        else "<p>ツナグモにログインしてご確認ください。</p>"
    )
    return (
        f"<p>承認待ちの内容があります(期限まで残り約{hours_left:.0f}時間)。</p>"
        f"{login_line}"
        "<p>期限を過ぎると、この依頼は自動的に却下扱いとなります。</p>"
    )


async def scan_approval_deadlines(
    *,
    app_configs: dict[str, AppConfig],
    default_industry: str,
    llm_factory: Callable[[], StructuredLLM],
    checkpointer,
) -> dict:
    """5分間隔のcron/systemd timerから呼ぶ想定(6-3)。まだ実際のスケジューラは
    組んでいない(Phase 8のworker/schedulerサービスで配線する)ので、このモジュールは
    呼び出し可能な形にしてある。

    - 期限24時間前でまだリマインドしていないものに1回だけリマインド
      (Resend経由でメール送信する。テナントにemail未設定、またはRESEND_API_KEY未設定の
      場合はログに記録するだけになる。src/core/email.py参照)
    - 期限を過ぎたものは自動的に却下してセッションを終了する(HALTED。REVISEループには入らない)

    2026-09-01: AI利用料は運営(ツナグモ)が負担する方式に統一したため、全テナント共通の
    llm_factory()(引数なし)でLLMクライアントを作る(以前はテナントごとのAnthropic
    APIキーを個別に復号して渡していた)。industryだけはテナントごとに異なるので、
    承認1件ごとにテナント情報を引いて対応するapp_configを選ぶ。
    """
    reminded = 0
    expired = 0
    now = datetime.utcnow()

    async with async_session_factory() as db:
        pending = await db.execute(select(Approval).where(Approval.status == "PENDING"))
        approvals = list(pending.scalars().all())
        tenants = await db.execute(select(Tenant.id, Tenant.industry, Tenant.email))
        tenant_info = {row[0]: (row[1], row[2]) for row in tenants.all()}

    for approval in approvals:
        industry, tenant_email = tenant_info.get(approval.tenant_id, (default_industry, None))
        app_config = app_configs.get(industry, app_configs[default_industry])

        llm = llm_factory()

        if approval.deadline_at <= now:
            async with async_session_factory() as db:
                row = await db.get(Approval, approval.id)
                if row is None or row.status != "PENDING":
                    continue
                row.status = "EXPIRED"
                await db.commit()

            await submit_approval_decision(
                tenant_id=str(approval.tenant_id),
                session_id=str(approval.session_id),
                decision="expire",
                comment=None,
                app_config=app_config,
                llm=llm,
                checkpointer=checkpointer,
            )
            expired += 1
            continue

        hours_left = (approval.deadline_at - now).total_seconds() / 3600
        if hours_left <= 24 and approval.reminder_sent_at is None:
            with RequestContext(tenant_id=str(approval.tenant_id), session_id=str(approval.session_id)):
                logger.warning("承認期限が近づいています(残り%.1f時間)", hours_left)
                if tenant_email:
                    await send_email(
                        to=tenant_email,
                        subject="【ツナグモ】承認期限が近づいています",
                        html=_reminder_email_html(hours_left=hours_left),
                    )
                else:
                    logger.warning("テナントのメールアドレス未設定のため、リマインドメールは送れません")
            async with async_session_factory() as db:
                row = await db.get(Approval, approval.id)
                if row is None or row.reminder_sent_at is not None:
                    continue
                row.reminder_sent_at = now
                await db.commit()
            reminded += 1

    return {"reminded": reminded, "expired": expired}
