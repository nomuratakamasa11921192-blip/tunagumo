import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_tenant, get_optional_tenant_user, get_scoped_db, hash_api_key
from src.core.ai_budget import DEFAULT_PLAN, PLAN_MONTHLY_BUDGET_USD, effective_cost_this_period
from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.core.db import get_db
from src.core.email import send_email
from src.core.models import Session as SessionModel
from src.core.models import Tenant, TenantUser, TenantUserToken
from src.core.passwords import hash_password, verify_password
from src.core.stripe_client import (
    ADDON_CREDIT_USD,
    ADDON_PRICE_JPY,
    StripeClientError,
    create_addon_checkout_session,
    create_billing_portal_session,
)
from src.core.validation import (
    validate_anthropic_key_format,
    validate_higgsfield_key_format,
    validate_openai_key_format,
)

router = APIRouter(prefix="/api/account", tags=["account"])

USER_TOKEN_TTL_HOURS = 12  # ログインセッションの有効期限(Phase 13の承認者識別用)
INVITE_TTL_HOURS = 48


class AnthropicKeyUpdateRequest(BaseModel):
    anthropic_api_key: str


@router.patch("/anthropic-key", status_code=204)
async def update_own_anthropic_key(
    req: AnthropicKeyUpdateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    """顧客自身がAnthropic APIキーを更新する(再発行時など、野村さんへの連絡なしで
    セルフサービスできるようにする)。管理画面のPATCH /admin/tenants/{id}/anthropic-key
    と同じ検証・暗号化ロジックを使う。"""
    validate_anthropic_key_format(req.anthropic_api_key)

    row = await db.get(Tenant, tenant.id)
    row.anthropic_api_key = encrypt_secret(req.anthropic_api_key)
    await db.commit()


class OpenAIKeyUpdateRequest(BaseModel):
    openai_api_key: str


@router.patch("/openai-key", status_code=204)
async def update_own_openai_key(
    req: OpenAIKeyUpdateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    """顧客自身がOpenAI APIキーを登録・更新する(Phase 7 RAGの埋め込みに使う。
    社内資料検索を使わない顧客は設定不要)。"""
    validate_openai_key_format(req.openai_api_key)

    row = await db.get(Tenant, tenant.id)
    row.openai_api_key = encrypt_secret(req.openai_api_key)
    await db.commit()


class HiggsfieldKeyUpdateRequest(BaseModel):
    higgsfield_key_id: str
    higgsfield_key_secret: str


@router.patch("/higgsfield-key", status_code=204)
async def update_own_higgsfield_key(
    req: HiggsfieldKeyUpdateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    """顧客自身がHiggsfield APIキーを登録・更新する(画像生成機能に使う。
    Anthropic/OpenAIキーと同じ理由で顧客自身の契約・負担とする。
    画像生成機能を使わない顧客は設定不要)。"""
    validate_higgsfield_key_format(req.higgsfield_key_id, req.higgsfield_key_secret)

    row = await db.get(Tenant, tenant.id)
    row.higgsfield_api_key_id = encrypt_secret(req.higgsfield_key_id)
    row.higgsfield_api_key_secret = encrypt_secret(req.higgsfield_key_secret)
    await db.commit()


class UsageResponse(BaseModel):
    this_month_cost_usd: float
    this_month_session_count: int
    all_time_cost_usd: float
    all_time_session_count: int
    plan: str
    ai_cost_this_period_usd: float
    monthly_ai_budget_usd: float
    addon_credit_usd: float


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> UsageResponse:
    """顧客自身のAI利用コストの目安を返す。Anthropicへの実際の請求額とは、丸め方や
    タイミングのずれにより完全には一致しない可能性がある(あくまで目安)。"""
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    all_time = await db.execute(
        select(
            func.coalesce(func.sum(SessionModel.cost_usd), 0.0),
            func.count(SessionModel.id),
        ).where(SessionModel.tenant_id == tenant.id)
    )
    all_time_cost, all_time_count = all_time.one()

    this_month = await db.execute(
        select(
            func.coalesce(func.sum(SessionModel.cost_usd), 0.0),
            func.count(SessionModel.id),
        ).where(SessionModel.tenant_id == tenant.id, SessionModel.created_at >= month_start)
    )
    this_month_cost, this_month_count = this_month.one()

    return UsageResponse(
        this_month_cost_usd=round(this_month_cost, 6),
        this_month_session_count=this_month_count,
        all_time_cost_usd=round(all_time_cost, 6),
        all_time_session_count=all_time_count,
        plan=tenant.plan,
        ai_cost_this_period_usd=round(effective_cost_this_period(tenant), 6),
        monthly_ai_budget_usd=PLAN_MONTHLY_BUDGET_USD.get(tenant.plan, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN]),
        addon_credit_usd=round(tenant.addon_credit_usd, 6),
    )


class BuyAddonResponse(BaseModel):
    checkout_url: str
    price_jpy: int
    credit_usd: float


@router.post("/buy-addon", response_model=BuyAddonResponse)
async def buy_addon(
    tenant: Tenant = Depends(get_current_tenant),
) -> BuyAddonResponse:
    """追加AI予算チケットの購入用Stripe Checkoutセッションを作る(Part 4)。
    支払い完了はStripe Webhook(checkout.session.completed、src/api/routes/stripe_webhook.py)
    が検知し、addon_credit_usdに加算する(このエンドポイント自体は予算を加算しない)。
    """
    if not tenant.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="お支払い情報が未登録です。運営にお問い合わせください。",
        )
    if not settings.saas_public_url:
        raise HTTPException(status_code=503, detail="購入機能が準備中です(設定不備)。")

    try:
        checkout_url = await create_addon_checkout_session(
            customer_id=tenant.stripe_customer_id,
            success_url=f"{settings.saas_public_url}/?addon=success",
            cancel_url=f"{settings.saas_public_url}/?addon=cancelled",
            api_key=settings.stripe_secret_key,
        )
    except StripeClientError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return BuyAddonResponse(checkout_url=checkout_url, price_jpy=ADDON_PRICE_JPY, credit_usd=ADDON_CREDIT_USD)


class BillingPortalResponse(BaseModel):
    portal_url: str


@router.post("/billing-portal", response_model=BillingPortalResponse)
async def create_billing_portal(
    tenant: Tenant = Depends(get_current_tenant),
) -> BillingPortalResponse:
    """顧客自身が解約・カード変更・請求履歴の確認をできる、Stripeカスタマーポータルの
    セッションを作る(セルフサービス解約)。運営側の手動対応は不要になる。
    """
    if not tenant.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="お支払い情報が未登録です。運営にお問い合わせください。",
        )
    if not settings.saas_public_url:
        raise HTTPException(status_code=503, detail="契約管理機能が準備中です(設定不備)。")

    try:
        portal_url = await create_billing_portal_session(
            customer_id=tenant.stripe_customer_id,
            return_url=settings.saas_public_url,
            api_key=settings.stripe_secret_key,
        )
    except StripeClientError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return BillingPortalResponse(portal_url=portal_url)


# --- Phase 13の前提: テナント内ユーザー識別(多段階承認で「誰が承認したか」を区別するため) ---


class InviteUserRequest(BaseModel):
    email: str
    role: str = "member"  # member/approver/owner。テナント最初の1人は常にownerになる(下記参照)


class InviteUserResponse(BaseModel):
    tenant_user_id: str
    email: str
    role: str
    invite_sent: bool


@router.post("/users", response_model=InviteUserResponse, status_code=201)
async def invite_user(
    req: InviteUserRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    actor: TenantUser | None = Depends(get_optional_tenant_user),
) -> InviteUserResponse:
    """新しいテナント内ユーザーを招待する(初期パスワード設定用のリンクをメール送信)。

    テナントにまだ誰もユーザーが居ない場合に限り、テナントAPIキーだけで最初の1人
    (常にowner)を作れる(先有卵鶏問題: owner以外は誰も招待できないので、最初の1人は
    別の入口が必要)。2人目以降は、既存のownerロールのTenantUserとしてログイン
    (X-User-Tokenヘッダ)していないと招待できない。
    """
    existing_count = (
        await db.execute(select(func.count()).where(TenantUser.tenant_id == tenant.id))
    ).scalar_one()

    if existing_count == 0:
        role = "owner"
    else:
        if actor is None or actor.role != "owner":
            raise HTTPException(status_code=403, detail="ユーザーの招待にはowner権限が必要です")
        if req.role not in ("member", "approver", "owner"):
            raise HTTPException(status_code=400, detail="roleはmember/approver/ownerのいずれかです")
        role = req.role

    raw_invite_token = secrets.token_urlsafe(32)
    user = TenantUser(
        tenant_id=tenant.id,
        email=req.email,
        role=role,
        is_active=False,
        invite_token_hash=hash_api_key(raw_invite_token),
        invite_expires_at=datetime.utcnow() + timedelta(hours=INVITE_TTL_HOURS),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    invite_sent = await send_email(
        to=req.email,
        subject="ツナグモ：アカウント招待のご案内",
        html=(
            f"<p>ツナグモへの招待が届いています。以下のリンクから初期パスワードを設定してください"
            f"({INVITE_TTL_HOURS}時間以内)。</p>"
            f"<p>招待コード: <code>{raw_invite_token}</code></p>"
        ),
    )

    return InviteUserResponse(
        tenant_user_id=str(user.id), email=user.email, role=user.role, invite_sent=invite_sent
    )


class UserSummary(BaseModel):
    tenant_user_id: str
    email: str
    role: str
    is_active: bool


class UserListResponse(BaseModel):
    users: list[UserSummary]


@router.get("/users", response_model=UserListResponse)
async def list_users(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    actor: TenantUser | None = Depends(get_optional_tenant_user),
) -> UserListResponse:
    if actor is None or actor.role != "owner":
        raise HTTPException(status_code=403, detail="ユーザー一覧の閲覧にはowner権限が必要です")

    result = await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant.id))
    users = result.scalars().all()
    return UserListResponse(
        users=[
            UserSummary(tenant_user_id=str(u.id), email=u.email, role=u.role, is_active=u.is_active)
            for u in users
        ]
    )


class AcceptInviteRequest(BaseModel):
    invite_token: str
    password: str


class AcceptInviteResponse(BaseModel):
    tenant_user_id: str


@router.post("/accept-invite", response_model=AcceptInviteResponse)
async def accept_invite(req: AcceptInviteRequest, db: AsyncSession = Depends(get_db)) -> AcceptInviteResponse:
    """招待された本人が初期パスワードを設定する。招待トークンだけで完結するため、
    テナントAPIキーもX-User-Tokenも要らない(Phase 16のwidget公開鍵と同じ考え方で、
    トークン自体がテナント横断で一意なのでsuperuser接続で直接引く)。"""
    token_hash = hash_api_key(req.invite_token)
    result = await db.execute(select(TenantUser).where(TenantUser.invite_token_hash == token_hash))
    user = result.scalar_one_or_none()

    if user is None or user.invite_expires_at is None or user.invite_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="招待コードが無効か期限切れです")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください")

    user.password_hash = hash_password(req.password)
    user.is_active = True
    user.invite_token_hash = None
    user.invite_expires_at = None
    await db.commit()

    return AcceptInviteResponse(tenant_user_id=str(user.id))


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    tenant_user_id: str
    role: str


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> LoginResponse:
    """テナントAPIキーで自テナントだと確認したうえで、その中の個人ユーザーとして
    ログインする(Phase 13の承認者識別に使うX-User-Tokenを発行する)。"""
    result = await db.execute(
        select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.email == req.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが違います")

    raw_token = secrets.token_urlsafe(32)
    db.add(
        TenantUserToken(
            tenant_id=tenant.id,
            tenant_user_id=user.id,
            token_hash=hash_api_key(raw_token),
            expires_at=datetime.utcnow() + timedelta(hours=USER_TOKEN_TTL_HOURS),
        )
    )
    await db.commit()

    return LoginResponse(token=raw_token, tenant_user_id=str(user.id), role=user.role)
