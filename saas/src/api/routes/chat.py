"""Phase 16: Webサイト埋め込みチャットの公開エンドポイント。

**認証はBearerトークンではない**(匿名の訪問者からのリクエストのため)。代わりに、
ウィジェット埋め込みタグのdata-key(tenant.web_widget_public_key、ブラウザに出しても
問題ない公開鍵)とOriginヘッダの組み合わせで、どのテナント宛かを識別・検証する。
Anthropic/OpenAIキーは一切ブラウザに渡らない(16-6)。

各テナントの許可オリジンが異なるため、静的なCORSMiddlewareでは扱えない。この
ルートだけ手動でCORSヘッダを組み立てる(OPTIONSプリフライトも自前で処理する)。
"""

import uuid

from anthropic import AsyncAnthropic
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from src.agent.cost import compute_cost_usd
from src.agent.llm import DEFAULT_TIMEOUT_SECONDS, StructuredLLM
from src.agent.public_responder import MAX_INPUT_LENGTH, respond
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
from src.core.config import settings
from src.core.db import async_session_factory, tenant_scoped_session_factory
from src.core.models import Tenant
from src.core.tenant_context import set_tenant_scope
from src.rag.embeddings import EmbeddingError, OpenAIEmbeddingProvider
from src.rag.prompt_safety import render_retrieved_context
from src.rag.search import hybrid_search

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
    # Caddy等のリバースプロキシ経由では、実際のクライアントIPはX-Forwarded-Forに入る。
    # VPSデプロイ時にCaddyfileでこのヘッダが正しく設定されることを前提にする
    ip_address = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )

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
            await check_daily_cost_cap(db, tenant_id=tenant.id)
        except (RateLimitedError, DailyCostCapExceededError):
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            session = await get_or_create_session(db, tenant_id=tenant.id, session_id=req.session_id)
            return ChatResponse(session_id=session.id, reply=BUSY_MESSAGE, escalated=False)

        session = await get_or_create_session(db, tenant_id=tenant.id, session_id=req.session_id)

        if not settings.anthropic_api_key:
            # 運営側のAnthropicキーが未設定(設定不備)の場合のフェイルセーフとして
            # エスカレーション扱いにする(AIを呼ばずに定型文で終える)
            await log_request(db, tenant_id=tenant.id, ip_address=ip_address)
            await append_exchange(
                db, session=session, user_message=message, reply=BUSY_MESSAGE, escalated=True
            )
            return ChatResponse(session_id=session.id, reply=BUSY_MESSAGE, escalated=True)

        app_config = resolve_app_config(request, tenant.industry)
        model = settings.anthropic_model_light or settings.anthropic_model
        llm = StructuredLLM(
            client=AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=DEFAULT_TIMEOUT_SECONDS)
        )

        rag_context = ""
        if settings.openai_api_key:
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
        )

        cost = compute_cost_usd(model, result.usage, app_config.pricing) if result.usage else 0.0
        await log_request(db, tenant_id=tenant.id, ip_address=ip_address, cost_usd=cost)
        await append_exchange(
            db, session=session, user_message=message, reply=result.reply, escalated=result.escalated
        )

        return ChatResponse(session_id=session.id, reply=result.reply, escalated=result.escalated)
