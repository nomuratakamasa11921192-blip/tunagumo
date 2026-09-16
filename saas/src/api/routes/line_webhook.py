"""Phase 15: 顧客(不動産会社)自身のLINE公式アカウントで、入居者・見込み客からの問い合わせを
24時間一次受けするWebhook(2026-09-15)。

LINE Developersコンソールで、Webhook URLに
    https://app.tunagumo.com/webhooks/line/<テナントID>
を登録して使う。署名(X-Line-Signature)をテナントごとのチャネルシークレットで検証し、
LINEには即座に200を返して、応答はバックグラウンドで行う(仕様書15-5)。

応答ロジックはWebチャットと同じsrc/agent/public_responder.py(仕様書15-0: チャネルごとに
応答ロジックを書かない)。社内グラフ(依頼の作成)は起動しない(仕様書15-2: 橋は一方通行)。
"""

import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.agent.cost import compute_cost_usd
from src.agent.llm import build_llm
from src.agent.inquiry_triage import triage
from src.agent.public_responder import (
    ESCALATION_MESSAGE,
    MAX_INPUT_LENGTH,
    PublicResponderResult,
    emergency_result,
    respond,
)
from src.api.deps import DEFAULT_INDUSTRY
from src.channels.inquiries import append_to_open_inquiry, notify_escalation, record_escalation
from src.channels.line import get_or_create_line_session, is_duplicate_event, reply_to_line, verify_line_signature
from src.channels.web import (
    BUSY_MESSAGE,
    DailyCostCapExceededError,
    RateLimitedError,
    append_exchange,
    check_daily_cost_cap,
    check_rate_limit,
    log_request,
)
from src.core.config import active_llm_model_light, settings
from src.core.crypto import decrypt_secret
from src.core.db import async_session_factory, tenant_scoped_session_factory
from src.core.models import Tenant
from src.core.tenant_context import set_tenant_scope
from src.rag.embeddings import EmbeddingError, OpenAIEmbeddingProvider
from src.rag.prompt_safety import render_retrieved_context
from src.rag.search import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/line", tags=["line-webhook"])

NON_TEXT_REPLY = "恐れ入りますが、お問い合わせ内容は文字でお送りください。"
# 1回のWebhookで処理するイベント数の上限(LINEは複数まとめて送ってくることがある)
MAX_EVENTS_PER_REQUEST = 20


async def _load_tenant(tenant_id: uuid.UUID) -> Tenant | None:
    async with async_session_factory() as db:
        return await db.get(Tenant, tenant_id)


@router.post("/{tenant_id}")
async def line_webhook(tenant_id: uuid.UUID, request: Request, background_tasks: BackgroundTasks) -> dict:
    tenant = await _load_tenant(tenant_id)
    if tenant is None or not tenant.line_channel_secret or not tenant.line_channel_access_token:
        raise HTTPException(status_code=404, detail="LINE連携が設定されていません")
    if not tenant.subscription_active:
        raise HTTPException(status_code=403, detail="サブスクリプションが有効ではありません")

    raw_body = await request.body()
    if not verify_line_signature(raw_body, request.headers.get("x-line-signature"), decrypt_secret(tenant.line_channel_secret)):
        raise HTTPException(status_code=401, detail="署名が一致しません")

    try:
        events = json.loads(raw_body).get("events", [])
    except ValueError:
        raise HTTPException(status_code=400, detail="本文を読み取れません") from None

    for event in events[:MAX_EVENTS_PER_REQUEST]:
        background_tasks.add_task(_handle_event, tenant, event, request.app)
    return {"received": True}


async def _handle_event(tenant: Tenant, event: dict, app) -> None:
    try:
        await _process_event(tenant, event, app)
    except Exception:  # noqa: BLE001
        # バックグラウンド処理の失敗でWebhook全体を落とさない(LINEには既に200を返している)
        logger.exception("LINEイベントの処理に失敗しました: tenant_id=%s", tenant.id)


async def _process_event(tenant: Tenant, event: dict, app) -> None:
    if event.get("type") != "message":
        return
    event_id = event.get("webhookEventId")
    user_id = (event.get("source") or {}).get("userId")
    reply_token = event.get("replyToken")
    if not event_id or not user_id or not reply_token:
        return
    access_token = decrypt_secret(tenant.line_channel_access_token)

    async with tenant_scoped_session_factory() as db:
        db._rls_tenant_id = tenant.id
        await set_tenant_scope(db, tenant.id)

        # LINEは同じイベントを再送することがあるため、二重に応答しない(仕様書15-5)
        if await is_duplicate_event(db, tenant_id=tenant.id, event_id=event_id):
            return

        rate_key = f"line:{user_id}"
        message_obj = event.get("message") or {}
        if message_obj.get("type") != "text":
            await reply_to_line(access_token=access_token, reply_token=reply_token, text=NON_TEXT_REPLY)
            return
        message = (message_obj.get("text") or "").strip()[:MAX_INPUT_LENGTH]
        if not message:
            return

        try:
            await check_rate_limit(db, tenant_id=tenant.id, ip_address=rate_key)
        except RateLimitedError:
            await log_request(db, tenant_id=tenant.id, ip_address=rate_key)
            await reply_to_line(access_token=access_token, reply_token=reply_token, text=BUSY_MESSAGE)
            return

        session = await get_or_create_line_session(db, tenant_id=tenant.id, user_id=user_id)

        # 緊急はAIを使わない定型の安全案内。コスト上限やキー不備でも必ず先に返す
        result = emergency_result(message, emergency_phone=tenant.emergency_contact_phone)
        cost = 0.0
        if result is None:
            try:
                await check_daily_cost_cap(db, tenant_id=tenant.id)
                ai_available = bool(settings.openai_api_key)
            except DailyCostCapExceededError:
                ai_available = False

            if not ai_available:
                # AIを呼べない時は推測で答えず、担当者へ回す(仕様書15-6)
                t = triage(message)
                result = PublicResponderResult(
                    reply=ESCALATION_MESSAGE, escalated=True, reason="AI応答を利用できないため担当者へ回しました",
                    urgency=t.urgency, category=t.category,
                )
            else:
                result, cost = await _ai_respond(db, tenant, session, message, app)

        await reply_to_line(access_token=access_token, reply_token=reply_token, text=result.reply)
        await log_request(db, tenant_id=tenant.id, ip_address=rate_key, cost_usd=cost)
        await append_exchange(db, session=session, user_message=message, reply=result.reply, escalated=result.escalated)

        if result.escalated:
            inquiry, is_new, became_urgent = await record_escalation(
                db, tenant_id=tenant.id, channel="line", user_message=message, reply=result.reply,
                reason=result.reason, urgency=result.urgency, category=result.category, external_user_id=user_id,
            )
            try:
                await notify_escalation(tenant, inquiry, is_new=is_new, became_urgent=became_urgent)
            except Exception:  # noqa: BLE001
                logger.exception("問い合わせ通知に失敗しました: inquiry_id=%s", inquiry.id)
        else:
            await append_to_open_inquiry(
                db, tenant_id=tenant.id, channel="line", user_message=message, reply=result.reply, external_user_id=user_id
            )


async def _ai_respond(db, tenant: Tenant, session, message: str, app):
    configs = app.state.app_configs
    app_config = configs.get(tenant.industry, configs[DEFAULT_INDUSTRY])
    model = active_llm_model_light()
    llm = build_llm()

    rag_context = ""
    try:
        vectors = await OpenAIEmbeddingProvider(api_key=settings.openai_api_key).embed([message])
        results = await hybrid_search(
            db, tenant_id=tenant.id, query_text=message, query_embedding=vectors[0], index_scope="public", top_k=3
        )
        rag_context = render_retrieved_context(results)
    except EmbeddingError:
        rag_context = ""

    result = await respond(
        llm=llm,
        model=model,
        company_name=app_config.company.name,
        message=message,
        history=session.messages,
        rag_context=rag_context,
        emergency_phone=tenant.emergency_contact_phone,
    )
    cost = compute_cost_usd(model, result.usage, app_config.pricing) if result.usage else 0.0
    return result, cost
