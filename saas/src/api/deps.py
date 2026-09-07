import hashlib
import secrets
from datetime import datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anthropic import AsyncAnthropic

from src.agent.config_models import AppConfig
from src.agent.llm import DEFAULT_TIMEOUT_SECONDS, StructuredLLM
from src.core.config import settings
from src.core.db import get_db, get_scoped_db_for_tenant
from src.core.higgsfield_client import HiggsfieldClient
from src.core.models import Tenant, TenantUser, TenantUserToken
from src.rag.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from src.video.stt import OpenAISTTProvider, STTProvider
from src.video.tts import OpenAITTSProvider, TTSProvider

# config/{industry}.yaml として存在を許可する業種の一覧(ハードコードされた許可リスト)。
# Tenant.industry や管理画面からの入力をここに含まれる値かどうかで検証してから
# ファイルパスに使う(パストラバーサル対策も兼ねる)。
# 2026-08-31: 不動産に一本化(config/recruiting.yaml等は削除せず温存。将来の
# 横展開時にこのリストへ戻すだけで再有効化できる)。
INDUSTRIES = ["real_estate"]
DEFAULT_INDUSTRY = "real_estate"


def resolve_app_config(request: Request, industry: str) -> AppConfig:
    configs: dict[str, AppConfig] = request.app.state.app_configs
    return configs.get(industry, configs[DEFAULT_INDUSTRY])


def get_internal_llm(request: Request) -> StructuredLLM:
    """ツナグモ自身のANTHROPIC_API_KEY(.env)を使うLLM。起動時ヘルスチェックや
    管理画面の回帰テストなど、特定の顧客テナントに紐付かない用途にのみ使う。"""
    return request.app.state.internal_llm


def get_checkpointer(request: Request):
    return request.app.state.checkpointer


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def get_current_tenant(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """リクエストごとにtenant_idを解決する。テナント分離の起点(Phase 10で詳述)。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証情報がありません")

    api_key = authorization.removeprefix("Bearer ").strip()
    key_hash = hash_api_key(api_key)

    result = await db.execute(select(Tenant).where(Tenant.api_key_hash == key_hash))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=401, detail="APIキーが無効です")
    # stripe_customer_id未設定(Stripe未連携、管理者が手動発行したテナント等)なら
    # subscription_activeはデフォルトTrueのままなのでここには来ない。Stripe Webhookが
    # 解約等を検知した時だけFalseになる(src/api/routes/stripe_webhook.py)。
    if not tenant.subscription_active:
        raise HTTPException(
            status_code=403, detail="サブスクリプションが有効ではありません。ご契約状況をご確認ください。"
        )

    return tenant


async def get_scoped_db(tenant: Tenant = Depends(get_current_tenant)) -> AsyncSession:
    """Phase 10: RLSでtenant.idにスコープ済みのDBセッション。顧客のAPIキーで認証される
    ルート(sessions/documents/account)はget_dbの代わりにこれを使うこと。管理画面は
    テナント横断の閲覧が正規の用途のため、引き続きget_db(superuser接続)を使う。"""
    async for session in get_scoped_db_for_tenant(tenant.id):
        yield session


async def get_optional_tenant_user(
    x_user_token: str | None = Header(default=None),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> TenantUser | None:
    """Phase 13: 多段階承認で「誰が承認したか」を識別するための追加の認証レイヤー。

    X-User-Tokenが無ければNoneを返す(テナントAPIキーだけの既存の承認方式にフォールバック
    できるかどうかは呼び出し側=sessions.pyがtenant.approval_stagesを見て判断する)。
    ヘッダがあるのに無効・期限切れなら、黙って無視せず401で明示的に拒否する。
    """
    if not x_user_token:
        return None

    token_hash = hash_api_key(x_user_token)
    result = await db.execute(
        select(TenantUserToken).where(
            TenantUserToken.tenant_id == tenant.id,
            TenantUserToken.token_hash == token_hash,
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row is None or token_row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="ユーザートークンが無効か期限切れです")

    user = await db.get(TenantUser, token_row.tenant_user_id)
    if user is None or not user.is_active or user.tenant_id != tenant.id:
        raise HTTPException(status_code=401, detail="ユーザートークンが無効です")

    return user


async def get_app_config(
    request: Request,
    tenant: Tenant = Depends(get_current_tenant),
) -> AppConfig:
    """テナントごとのindustryに応じた設定を返す(業種別マルチテナント対応)。"""
    return resolve_app_config(request, tenant.industry)


async def get_llm(tenant: Tenant = Depends(get_current_tenant)) -> StructuredLLM:
    """ツナグモ自身のAnthropic APIキーでLLMクライアントを作る。

    2026-09-01: 「顧客自身のAPIキーを使う(BYOK)」方式から「運営がAI利用料を負担し、
    月額料金に含める」方式へ全面移行した(旧docs/ai_org_spec_master.md付録Dの前提を
    上書き)。テナントの`anthropic_api_key`列はもう読まない(過去の名残として残して
    あるだけ)。この切り替えにより失われた「顧客自身のAnthropic Console上の利用上限」
    という防御層は、src/core/ai_budget.py の月間予算上限で代替する。
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail="Anthropic APIキーが運営側で設定されていません(設定不備)。",
        )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=DEFAULT_TIMEOUT_SECONDS)
    return StructuredLLM(client=client)


async def get_embedding_provider(tenant: Tenant = Depends(get_current_tenant)) -> EmbeddingProvider | None:
    """ツナグモ自身のOpenAI APIキーで埋め込みプロバイダを作る(get_llmと同じ理由で
    運営負担に統一)。運営側キー未設定ならNoneを返す(RAG機能全体を一時的に無効化する
    運用上の逃げ道として残す。通常は設定済みのはず)。
    """
    if not settings.openai_api_key:
        return None
    return OpenAIEmbeddingProvider(api_key=settings.openai_api_key)


async def get_stt_provider(tenant: Tenant = Depends(get_current_tenant)) -> STTProvider | None:
    """ツナグモ自身のOpenAI APIキーで音声認識プロバイダを作る(get_embedding_providerと
    同じ理由で運営負担に統一)。リール自動編集の字幕自動生成に使う。"""
    if not settings.openai_api_key:
        return None
    return OpenAISTTProvider(api_key=settings.openai_api_key)


async def get_tts_provider(tenant: Tenant = Depends(get_current_tenant)) -> TTSProvider | None:
    """ツナグモ自身のOpenAI APIキーで音声合成プロバイダを作る(get_stt_providerと同じ
    理由で運営負担に統一)。ルームツアー動画生成のナレーション音声合成に使う。"""
    if not settings.openai_api_key:
        return None
    return OpenAITTSProvider(api_key=settings.openai_api_key)


async def get_higgsfield_client(tenant: Tenant = Depends(get_current_tenant)) -> HiggsfieldClient | None:
    """ツナグモ自身のHiggsfield APIキー(cloud.higgsfield.aiの開発者アカウント)で
    画像・動画生成クライアントを作る(get_embedding_providerと同じ理由で運営負担に統一)。
    運営側キー未設定ならNoneを返す(画像・動画機能全体を一時的に無効化する運用上の
    逃げ道として残す)。
    """
    if not settings.higgsfield_api_key_id or not settings.higgsfield_api_key_secret:
        return None
    return HiggsfieldClient(
        key_id=settings.higgsfield_api_key_id, key_secret=settings.higgsfield_api_key_secret
    )


async def require_admin(authorization: str | None = Header(default=None)) -> None:
    """管理者画面用の認証。テナントのAPIキーとは別体系(URLレベルで顧客用と分離する想定)。

    本人(野村さん)しか使わない前提なので、共有シークレット1本での認証にとどめている。
    複数管理者が必要になったらTenant同様のハッシュ照合方式に切り替える。
    """
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="管理者機能が設定されていません(ADMIN_API_KEY未設定)")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証情報がありません")

    api_key = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(api_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="管理者キーが無効です")
