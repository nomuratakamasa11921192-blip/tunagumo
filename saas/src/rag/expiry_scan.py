"""社内資料(RAG)の有効期限スキャン(7-7-2-4)。5分〜1日間隔のcron/systemd timerから
呼ぶ想定(承認期限スキャンと同じ、Phase 8のworker/schedulerサービスで配線するまでは
手動または一時的なcronで実行する)。

- valid_untilの30日前になったらまだ通知していないACTIVE文書に1回だけ通知
  (Resend経由でメール送信。テナントにemail未設定、またはRESEND_API_KEY未設定の場合は
  ログに記録するだけになる)
- valid_untilを過ぎたACTIVE文書はEXPIREDに遷移させる(検索は既にvalid_untilで
  絞り込んでいるが、7-7-1のライフサイクル仕様通りstatusにも反映させ、
  「人間が古い資料を引いていることに気づける」ようにする)
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.email import send_email
from src.core.logging_config import RequestContext
from src.core.models import Document, Tenant

logger = logging.getLogger(__name__)

EXPIRY_WARNING_DAYS = 30


def _expiry_warning_email_html(*, title: str, valid_until: date) -> str:
    login_line = (
        f'<p>ログインしてご確認ください: <a href="{settings.saas_public_url}">{settings.saas_public_url}</a></p>'
        if settings.saas_public_url
        else "<p>ツナグモにログインしてご確認ください。</p>"
    )
    return (
        f"<p>社内資料「{title}」の有効期限が近づいています(期限: {valid_until.isoformat()})。</p>"
        "<p>最新の内容であれば、そのままでも問題ありません。内容が古くなっている場合は、"
        "新しい版を再アップロードしてください(同じタイトルで再アップロードすると、"
        "旧版は自動的に検索対象から外れます)。</p>"
        f"{login_line}"
    )


async def scan_document_expiry() -> dict:
    """戻り値: {"warned": 警告メールを送った件数, "expired": EXPIREDに遷移させた件数}"""
    warned = 0
    expired = 0
    today = date.today()
    warning_threshold = today + timedelta(days=EXPIRY_WARNING_DAYS)

    async with async_session_factory() as db:
        active = await db.execute(
            select(Document).where(
                Document.status == "ACTIVE",
                Document.valid_until.is_not(None),
            )
        )
        documents = list(active.scalars().all())
        tenants = await db.execute(select(Tenant.id, Tenant.email))
        tenant_email = {row[0]: row[1] for row in tenants.all()}

    for doc in documents:
        if doc.valid_until < today:
            async with async_session_factory() as db:
                row = await db.get(Document, doc.id)
                if row is None or row.status != "ACTIVE":
                    continue
                row.status = "EXPIRED"
                row.updated_at = datetime.utcnow()
                await db.commit()
            expired += 1
            continue

        if doc.valid_until <= warning_threshold and doc.valid_until_notified_at is None:
            with RequestContext(tenant_id=str(doc.tenant_id)):
                logger.warning(
                    "社内資料の有効期限が近づいています(document_id=%s, valid_until=%s)",
                    doc.id,
                    doc.valid_until,
                )
                email = tenant_email.get(doc.tenant_id)
                if email:
                    await send_email(
                        to=email,
                        subject="【ツナグモ】社内資料の有効期限が近づいています",
                        html=_expiry_warning_email_html(title=doc.title, valid_until=doc.valid_until),
                    )
                else:
                    logger.warning("テナントのメールアドレス未設定のため、期限通知メールは送れません")

            async with async_session_factory() as db:
                row = await db.get(Document, doc.id)
                if row is None or row.valid_until_notified_at is not None:
                    continue
                row.valid_until_notified_at = datetime.utcnow()
                await db.commit()
            warned += 1

    return {"warned": warned, "expired": expired}
