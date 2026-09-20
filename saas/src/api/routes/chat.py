"""Phase 16: Webサイト埋め込みチャットの公開エンドポイント。

**認証はBearerトークンではない**(匿名の訪問者からのリクエストのため)。代わりに、
ウィジェット埋め込みタグのdata-key(tenant.web_widget_public_key、ブラウザに出しても
問題ない公開鍵)とOriginヘッダの組み合わせで、どのテナント宛かを識別・検証する。
OpenAIキーは一切ブラウザに渡らない(16-6)。

各テナントの許可オリジンが異なるため、静的なCORSMiddlewareでは扱えない。この
ルートだけ手動でCORSヘッダを組み立てる(OPTIONSプリフライトも自前で処理する)。
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from src.agent.cost import compute_cost_usd
from src.agent.llm import build_llm
from src.agent.public_responder import MAX_INPUT_LENGTH, emergency_result, respond
from src.api.deps import resolve_app_config
from src.channels.web import (
    BUSY_MESSAGE,
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
from src.channels.inquiries import append_to_open_inquiry, notify_escalation, record_escalation
from src.core.config import active_llm_model_light, settings
from src.core.db import async_session_factory, tenant_scoped_session_factory
from src.core.models import Tenant
from src.core.tenant_context import set_tenant_scope
from src.rag.embeddings import EmbeddingError, OpenAIEmbeddingProvider
from src.rag.prompt_safety import render_retrieved_context
from src.rag.search import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: uuid.UUID | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    reply: str
    escalated: bool


async def _find_tenant_by_public_key(public_key: str) -> Tenant | None:
    async with async_session_factory() as db:
        result = await db.execute(select(Tenant).where(Tenant.web_widget_public_key == public_key))
        return result.scalar_one_or_none()


def _client_ip(request: Request) -> str:
    """レート制限の単位に使う訪問者のIPアドレスを求める。

    X-Forwarded-Forは訪問者自身がブラウザから付けて送れるヘッダで、目の前のCaddyは
    それを消さずに接続元IPを**末尾に足す**。そのため先頭を読むと好きな値に偽装でき、
    1分5件の制限をいくらでもすり抜けられてしまう(2026-09-20修正)。末尾を読めば、
    ヘッダを足す構成でも上書きする構成でも、必ず目の前のCaddyが入れた実際の接続元になる。

    前提: APIのポートは127.0.0.1だけに公開し、Caddyを迂回させないこと
    (docker/docker-compose.yml参照)。迂回できると、この値ごと偽装される。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@router.options("/{public_key}")
async def chat_preflight(public_key: str, request: Request) -> Response:
    origin = request.headers.get("origin")
    tenant = await _find_tenant_by_public_key(public_key)

    headers = {}
    if tenant is not None and tenant.web_widget_allowed_origin and origin == tenant.web_widget_allowed_origin:
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
        }
    return Response(status_code=204, headers=headers)


@router.post("/{public_key}", response_model=ChatResponse)
async def chat(public_key: str, req: ChatRequest, request: Request, response: Response) -> ChatResponse:
    origin = request.headers.get("origin")
    ip_address = _client_ip(request)

    tenant = await _find_tenant_by_public_key(public_key)
    if tenant is None or not tenant.web_widget_allowed_origin:
        raise HTTPException(status_code=404, detail="ウィジェットが見つかりません")

    try:
        check_origin(tenant, origin)
    except OriginNotAllowedError:
        raise HTTPException(status_code=403, detail="許可されていないOriginです") from None

    # 検証済みのoriginなので、以降のレスポンス(エラー含む)にCORSヘッダを付与してよい
    response.headers["Access-Control-Allow-Origin"] = origin

    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください")
    if len(message) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail=f"メッセージは{MAX_INPUT_LENGTH}文字以内にしてください")

    async with tenant_scoped_session_factory() as db:
        db._rls_tenant_id = tenant.id
        await set_tenant_scope(db, tenant.id)

        try:
            await check_rate_limit(db, tenant_id=tenant.id, ip_address=ip_address)
        except RateLimitedError:
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            session = await get_or_create_session(db, tenant_id=tenant.id, session_id=req.session_id)
            return ChatResponse(session_id=session.id, reply=BUSY_MESSAGE, escalated=False)

        session = await get_or_create_session(db, tenant_id=tenant.id, session_id=req.session_id)

        # 緊急(火災・ガス・水漏れ等)はAIを使わない定型の安全案内なので、日次コスト上限や
        # 運営キーの不備でAIを呼べない時でも返し、担当者へ通知する(2026-09-15)。
        emergency = emergency_result(message, emergency_phone=tenant.emergency_contact_phone)
        if emergency is not None:
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            await append_exchange(db, session=session, user_message=message, reply=emergency.reply, escalated=True)
            await _escalate(db, tenant, session.id, message, emergency)
            return ChatResponse(session_id=session.id, reply=emergency.reply, escalated=True)

        try:
            await check_daily_cost_cap(db, tenant_id=tenant.id)
        except DailyCostCapExceededError:
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            return ChatResponse(session_id=session.id, reply=BUSY_MESSAGE, escalated=False)

        if not settings.openai_api_key:
            # 運営側のOpenAIキーが未設定(設定不備)の場合のフェイルセーフとして
            # エスカレーション扱いにする(AIを呼ばずに定型文で終える)
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            await append_exchange(
                db, session=session, user_message=message, reply=BUSY_MESSAGE, escalated=True
            )
            return ChatResponse(session_id=session.id, reply=BUSY_MESSAGE, escalated=True)

        app_config = resolve_app_config(request, tenant.industry)
        model = active_llm_model_light()
        llm = build_llm()

        rag_context = ""
        # 埋め込みも文章生成と同じ運営のOpenAIキーを使う(上で設定済みを確認済み)
        try:
            embedding_provider = OpenAIEmbeddingProvider(api_key=settings.openai_api_key)
            vectors = await embedding_provider.embed([message])
            results = await hybrid_search(
                db,
                tenant_id=tenant.id,
                query_text=message,
                query_embedding=vectors[0],
                index_scope="public",
                top_k=3,
            )
            rag_context = render_retrieved_context(results)
        except EmbeddingError:
            rag_context = ""  # 検索失敗は無視して空の文脈で続行(社外向けなので静かに縮退)

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
        await log_request(db, tenant_id=tenant.id, ip_address=ip_address, cost_usd=cost)
        await append_exchange(
            db, session=session, user_message=message, reply=result.reply, escalated=result.escalated
        )
        if result.escalated:
            await _escalate(db, tenant, session.id, message, result)
        else:
            await append_to_open_inquiry(
                db, tenant_id=tenant.id, channel="web", user_message=message, reply=result.reply,
                web_chat_session_id=session.id,
            )

        return ChatResponse(session_id=session.id, reply=result.reply, escalated=result.escalated)


async def _escalate(db, tenant: Tenant, session_id: uuid.UUID, message: str, result) -> None:
    """担当者へ回す問い合わせとして記録し、必要ならメールで通知する。通知の失敗で
    訪問者への応答を止めないよう、例外はログに残して握りつぶす。"""
    inquiry, is_new, became_urgent = await record_escalation(
        db,
        tenant_id=tenant.id,
        channel="web",
        user_message=message,
        reply=result.reply,
        reason=result.reason,
        urgency=result.urgency,
        category=result.category,
        web_chat_session_id=session_id,
    )
    try:
        await notify_escalation(tenant, inquiry, is_new=is_new, became_urgent=became_urgent)
    except Exception:  # noqa: BLE001
        logger.exception("問い合わせ通知に失敗しました: inquiry_id=%s", inquiry.id)
