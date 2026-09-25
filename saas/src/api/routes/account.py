import asyncio
import secrets
import uuid
from html import escape
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_tenant, get_optional_tenant_user, get_scoped_db, hash_api_key, require_account_owner
from src.core.ai_budget import (COMPANY_PLANS, PLAN_LABELS, effective_cost_this_period,
                                monthly_budget_usd, next_period_at)
from src.core.config import settings
from src.channels.mail import ImapSmtpTransport, MailAccountSettings, MailConfigError
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.db import get_db
from src.core.email import send_email
from src.core.models import Session as SessionModel
from src.core.models import Tenant, TenantMailAccount, TenantUser, TenantUserToken, AuditLog
from src.core.passwords import hash_password, verify_password
from src.core.stripe_client import (
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


@router.patch("/anthropic-key", status_code=204, dependencies=[Depends(require_account_owner)])
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


@router.patch("/openai-key", status_code=204, dependencies=[Depends(require_account_owner)])
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


@router.patch("/higgsfield-key", status_code=204, dependencies=[Depends(require_account_owner)])
async def update_own_higgsfield_key(
    req: HiggsfieldKeyUpdateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    """顧客自身がHiggsfield APIキーを登録・更新する(動画生成に使う。動画は1本あたりの
    単価が高くコストが読みにくいため、顧客自身の契約・負担とする。2026-09-15決定)。
    画像の生成・編集は運営のOpenAIキー(運営負担)に移行したため、このキーは使わない。
    動画生成を使わない顧客は設定不要。"""
    validate_higgsfield_key_format(req.higgsfield_key_id, req.higgsfield_key_secret)

    row = await db.get(Tenant, tenant.id)
    row.higgsfield_api_key_id = encrypt_secret(req.higgsfield_key_id)
    row.higgsfield_api_key_secret = encrypt_secret(req.higgsfield_key_secret)
    await db.commit()


class InquirySettingsResponse(BaseModel):
    notify_email: str | None
    # 実際に通知が届くアドレス(未入力ならアカウント登録時のメールアドレス)。どちらも無ければNone
    effective_notify_email: str | None
    emergency_phone: str | None
    line_configured: bool
    # LINE Developersコンソールに登録するWebhook URL(SAAS_PUBLIC_URL未設定ならパスのみ)
    line_webhook_url: str


class InquirySettingsRequest(BaseModel):
    # 空文字は「未設定に戻す」。形式は軽く確認する(送信テストはしない)
    notify_email: str = Field(default="", max_length=320, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    emergency_phone: str = Field(default="", max_length=30, pattern=r"^$|^[0-9+\-() ]{6,30}$")


class LineChannelRequest(BaseModel):
    line_channel_secret: str = Field(min_length=10, max_length=200)
    line_channel_access_token: str = Field(min_length=20, max_length=1000)


def _inquiry_settings(tenant: Tenant) -> InquirySettingsResponse:
    path = f"/webhooks/line/{tenant.id}"
    return InquirySettingsResponse(
        notify_email=tenant.inquiry_notify_email,
        effective_notify_email=tenant.inquiry_notify_email or tenant.email,
        emergency_phone=tenant.emergency_contact_phone,
        line_configured=bool(tenant.line_channel_secret and tenant.line_channel_access_token),
        line_webhook_url=f"{settings.saas_public_url.rstrip('/')}{path}" if settings.saas_public_url else path,
    )


@router.get("/inquiry-settings", response_model=InquirySettingsResponse, dependencies=[Depends(require_account_owner)])
async def get_inquiry_settings(tenant: Tenant = Depends(get_current_tenant)) -> InquirySettingsResponse:
    """問い合わせ一次受けの設定(通知先・緊急連絡先・LINE連携の状態)。キーの中身は返さない。"""
    return _inquiry_settings(tenant)


@router.put("/inquiry-settings", response_model=InquirySettingsResponse, dependencies=[Depends(require_account_owner)])
async def update_inquiry_settings(
    req: InquirySettingsRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> InquirySettingsResponse:
    row = await db.get(Tenant, tenant.id)
    row.inquiry_notify_email = req.notify_email.strip() or None
    row.emergency_contact_phone = req.emergency_phone.strip() or None
    await db.commit()
    return _inquiry_settings(row)


@router.put("/line-channel", response_model=InquirySettingsResponse, dependencies=[Depends(require_account_owner)])
async def update_line_channel(
    req: LineChannelRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> InquirySettingsResponse:
    """顧客自身のLINE公式アカウント(Messaging API)のチャネル情報を登録する(暗号化して保存)。
    管理画面のPUT /admin/tenants/{id}/line-channelと同じ保存方法のセルフサービス版。"""
    row = await db.get(Tenant, tenant.id)
    row.line_channel_secret = encrypt_secret(req.line_channel_secret.strip())
    row.line_channel_access_token = encrypt_secret(req.line_channel_access_token.strip())
    await db.commit()
    return _inquiry_settings(row)


@router.delete("/line-channel", response_model=InquirySettingsResponse, dependencies=[Depends(require_account_owner)])
async def delete_line_channel(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> InquirySettingsResponse:
    row = await db.get(Tenant, tenant.id)
    row.line_channel_secret = None
    row.line_channel_access_token = None
    await db.commit()
    return _inquiry_settings(row)


class MailAccountRequest(BaseModel):
    from_address: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    imap_host: str = Field(min_length=3, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    imap_port: int = 993
    smtp_host: str = Field(min_length=3, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    smtp_port: int = 465
    username: str = Field(min_length=1, max_length=320)
    # 更新時に空ならパスワードは変更しない(画面にパスワードを再表示しないため)
    password: str = Field(default="", max_length=500)
    enabled: bool = True


class MailAccountResponse(BaseModel):
    configured: bool
    from_address: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    username: str | None = None
    enabled: bool = False
    last_checked_at: datetime | None = None
    last_error: str | None = None


def _mail_response(row) -> MailAccountResponse:
    if row is None:
        return MailAccountResponse(configured=False)
    return MailAccountResponse(
        configured=True, from_address=row.from_address, imap_host=row.imap_host, imap_port=row.imap_port,
        smtp_host=row.smtp_host, smtp_port=row.smtp_port, username=row.username, enabled=row.enabled,
        last_checked_at=row.last_checked_at, last_error=row.last_error,
    )


@router.get("/mail-account", response_model=MailAccountResponse, dependencies=[Depends(require_account_owner)])
async def get_mail_account(
    tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_scoped_db)
) -> MailAccountResponse:
    """メール即レスの接続設定(パスワードは返さない)。"""
    return _mail_response(await db.get(TenantMailAccount, tenant.id))


@router.put("/mail-account", response_model=MailAccountResponse, dependencies=[Depends(require_account_owner)])
async def update_mail_account(
    req: MailAccountRequest, tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_scoped_db)
) -> MailAccountResponse:
    """接続を試してから保存する(つながらない設定のまま即レスが止まっているのに気付かない、を防ぐ)。"""
    row = await db.get(TenantMailAccount, tenant.id)
    password = req.password or (decrypt_secret(row.password_encrypted) if row is not None else "")
    if not password:
        raise HTTPException(status_code=400, detail="パスワードを入力してください。")
    account = MailAccountSettings(
        from_address=req.from_address.strip(), imap_host=req.imap_host.strip(), imap_port=req.imap_port,
        smtp_host=req.smtp_host.strip(), smtp_port=req.smtp_port, username=req.username.strip(), password=password,
    )
    try:
        await asyncio.to_thread(mail_transport().test_connection, account)
    except MailConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))

    server_changed = row is None or (row.imap_host, row.username) != (account.imap_host, account.username)
    if row is None:
        row = TenantMailAccount(tenant_id=tenant.id)
        db.add(row)
    row.from_address = account.from_address
    row.imap_host, row.imap_port = account.imap_host, account.imap_port
    row.smtp_host, row.smtp_port = account.smtp_host, account.smtp_port
    row.username = account.username
    row.password_encrypted = encrypt_secret(password)
    row.enabled = req.enabled
    row.last_error = None
    if server_changed:
        # 別のメールボックスに変わったら、既存のメールに返信しないよう初回扱いに戻す
        row.last_uid = None
        row.uidvalidity = None
    await db.commit()
    return _mail_response(row)


@router.delete("/mail-account", response_model=MailAccountResponse, dependencies=[Depends(require_account_owner)])
async def delete_mail_account(
    tenant: Tenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_scoped_db)
) -> MailAccountResponse:
    row = await db.get(TenantMailAccount, tenant.id)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return _mail_response(None)


def mail_transport():
    """テストで差し替えられるようにしてある(実際のメールサーバーに接続しない)。"""
    return ImapSmtpTransport()


class UsageResponse(BaseModel):
    """顧客向けの利用状況。2026-09-17より、AI利用料の金額(ドル)は返さない。
    月額に含まれる費用なので、金額を出すと原価・利益率が推測できてしまうため、割合で伝える。
    金額が必要な運営側は、管理画面(GET /admin/dashboard)で確認する。"""

    this_month_session_count: int
    all_time_session_count: int
    plan: str
    plan_label: str
    usage_scope: str = "company"
    usage_state: str
    resets_at: datetime
    self_service_addon: bool
    # 今月のAI利用量(月間枠に対する割合、0〜100)。追加購入分は含めずに計算する
    ai_usage_percent: int
    # 残りの割合(追加購入分を含む。100を超えることがある)
    ai_remaining_percent: int
    # 追加のご利用枠を購入済みか(枚数・金額は出さない)
    addon_purchased: bool
    # 動画生成用のHiggsfieldキーが登録済みか(設定画面の表示用。キーの中身は返さない)
    higgsfield_key_registered: bool


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> UsageResponse:
    """顧客自身の依頼件数と月間利用枠に対する使用・残量の割合を返す。"""
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    all_time = await db.execute(
        select(func.count(SessionModel.id)).where(SessionModel.tenant_id == tenant.id)
    )
    all_time_count = all_time.scalar_one()

    this_month = await db.execute(
        select(func.count(SessionModel.id)).where(SessionModel.tenant_id == tenant.id, SessionModel.created_at >= month_start)
    )
    this_month_count = this_month.scalar_one()

    monthly_budget = monthly_budget_usd(tenant)
    used = effective_cost_this_period(tenant)
    remaining = max(monthly_budget - used, 0.0) + tenant.addon_credit_usd
    return UsageResponse(
        this_month_session_count=this_month_count,
        all_time_session_count=all_time_count,
        plan=tenant.plan,
        plan_label=PLAN_LABELS.get(tenant.plan, tenant.plan),
        usage_state=("exhausted" if remaining <= 0 or monthly_budget <= 0 else
                     "warning" if used >= monthly_budget * .8 else "available"),
        resets_at=next_period_at(),
        self_service_addon=tenant.plan not in COMPANY_PLANS,
        ai_usage_percent=min(round(used / monthly_budget * 100), 100) if monthly_budget > 0 else 0,
        ai_remaining_percent=round(remaining / monthly_budget * 100) if monthly_budget > 0 else 0,
        addon_purchased=tenant.addon_credit_usd > 0,
        higgsfield_key_registered=bool(tenant.higgsfield_api_key_id and tenant.higgsfield_api_key_secret),
    )


class BuyAddonResponse(BaseModel):
    """顧客に返すのは決済ページのURLと日本円の価格だけ(2026-09-17)。
    「25,000円で$5分」のようにドル建ての枠を並べて返すと、原価と利益率が推測できてしまう。
    付与される枠の大きさは、購入後に「設定・利用状況」の残量の割合で確認できる。"""

    checkout_url: str
    price_jpy: int


@router.post("/buy-addon", response_model=BuyAddonResponse, dependencies=[Depends(require_account_owner)])
async def buy_addon(
    tenant: Tenant = Depends(get_current_tenant),
) -> BuyAddonResponse:
    """追加AI予算チケットの購入用Stripe Checkoutセッションを作る(Part 4)。
    支払い完了はStripe Webhook(checkout.session.completed、src/api/routes/stripe_webhook.py)
    が検知し、addon_credit_usdに加算する(このエンドポイント自体は予算を加算しない)。
    """
    if tenant.plan in COMPANY_PLANS:
        raise HTTPException(status_code=409, detail="会社プランの利用枠追加は、上位プランについて運営にご相談ください。自動追加課金はありません。")
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

    return BuyAddonResponse(checkout_url=checkout_url, price_jpy=ADDON_PRICE_JPY)


class BillingPortalResponse(BaseModel):
    portal_url: str


@router.post("/billing-portal", response_model=BillingPortalResponse, dependencies=[Depends(require_account_owner)])
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
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
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
    # 最初の管理者の同時作成・重複招待を会社行のロックで直列化する。
    await db.execute(select(Tenant).where(Tenant.id == tenant.id).with_for_update())
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

    email = req.email.strip().lower()
    duplicate = await db.execute(select(TenantUser).where(
        TenantUser.tenant_id == tenant.id, func.lower(TenantUser.email) == email))
    if duplicate.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="このメールアドレスは登録済みです。未受諾の場合は招待を再送してください。")
    raw_invite_token = secrets.token_urlsafe(32)
    user = TenantUser(
        tenant_id=tenant.id,
        email=email,
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
            f"<p>会社ID: <code>{tenant.id}</code></p>"
            f"<p>招待コード: <code>{raw_invite_token}</code></p>"
            + (f'<p><a href="{escape(settings.saas_public_url.rstrip("/"), quote=True)}/#invite={raw_invite_token}&company={tenant.id}">パスワードを設定する</a></p>' if settings.saas_public_url else "")
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
    invite_token: str = Field(max_length=200)
    password: str = Field(max_length=256)


class AcceptInviteResponse(BaseModel):
    tenant_user_id: str
    company_id: str
    email: str


@router.post("/accept-invite", response_model=AcceptInviteResponse)
async def accept_invite(req: AcceptInviteRequest, db: AsyncSession = Depends(get_db)) -> AcceptInviteResponse:
    """招待された本人が初期パスワードを設定する。招待トークンだけで完結するため、
    テナントAPIキーもX-User-Tokenも要らない(Phase 16のwidget公開鍵と同じ考え方で、
    トークン自体がテナント横断で一意なのでsuperuser接続で直接引く)。"""
    token_hash = hash_api_key(req.invite_token)
    result = await db.execute(select(TenantUser).where(TenantUser.invite_token_hash == token_hash).with_for_update())
    user = result.scalar_one_or_none()

    if user is None or user.invite_expires_at is None or user.invite_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="招待コードが無効か期限切れです")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください")

    # パスワードのハッシュ化は1回あたり0.5秒ほどかかる(pbkdf2 60万回)。そのまま実行すると
    # その間サーバー全体が止まるため、別スレッドで動かす(2026-09-16)
    user.password_hash = await asyncio.to_thread(hash_password, req.password)
    user.is_active = True
    user.invite_token_hash = None
    user.invite_expires_at = None
    await db.commit()

    return AcceptInviteResponse(tenant_user_id=str(user.id), company_id=str(user.tenant_id), email=user.email)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


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
    return await _authenticate_user(req, tenant, db)


class MemberLoginRequest(LoginRequest):
    company_id: uuid.UUID


@router.post("/member-login", response_model=LoginResponse)
async def member_login(req: MemberLoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    tenant = await db.get(Tenant, req.company_id)
    if tenant is None:
        raise HTTPException(status_code=401, detail="会社ID・メールアドレス・パスワードをご確認ください")
    return await _authenticate_user(req, tenant, db)


async def _authenticate_user(req: LoginRequest, tenant: Tenant, db: AsyncSession) -> LoginResponse:
    result = await db.execute(select(TenantUser).where(
        TenantUser.tenant_id == tenant.id, func.lower(TenantUser.email) == req.email.strip().lower()
    ).with_for_update())
    user = result.scalar_one_or_none()
    failure = HTTPException(status_code=401, detail="会社ID・メールアドレス・パスワードをご確認ください。繰り返し失敗した場合は15分後にお試しください")
    now = datetime.utcnow()
    if user is None or not user.is_active or not user.password_hash:
        raise failure
    if user.login_locked_until and user.login_locked_until > now:
        raise failure
    if user.login_locked_until:
        user.failed_login_attempts = 0
        user.login_locked_until = None
    if not await asyncio.to_thread(verify_password, req.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 5:
            user.login_locked_until = now + timedelta(minutes=15)
        await db.commit()
        raise failure
    if not tenant.subscription_active:
        raise HTTPException(status_code=403, detail="ご契約状況をご確認ください")
    user.failed_login_attempts = 0
    user.login_locked_until = None
    raw_token = secrets.token_urlsafe(32)
    db.add(TenantUserToken(tenant_id=tenant.id, tenant_user_id=user.id,
        token_hash=hash_api_key(raw_token), expires_at=now + timedelta(hours=USER_TOKEN_TTL_HOURS)))
    await db.commit()
    return LoginResponse(token=raw_token, tenant_user_id=str(user.id), role=user.role)


@router.get("/me")
async def account_identity(tenant: Tenant = Depends(get_current_tenant),
                           actor: TenantUser | None = Depends(get_optional_tenant_user),
                           db: AsyncSession = Depends(get_scoped_db)):
    count = (await db.execute(select(func.count()).where(TenantUser.tenant_id == tenant.id))).scalar_one()
    pending_owner = None
    if actor is None and count == 1:
        pending_owner = (await db.execute(select(TenantUser.id).where(TenantUser.tenant_id == tenant.id,
                        TenantUser.role == "owner", TenantUser.password_hash.is_(None)))).scalar_one_or_none()
    return {"company_id": str(tenant.id), "company_name": tenant.name,
            "role": actor.role if actor else "company_key", "can_bootstrap_owner": count == 0,
            "pending_owner_id": str(pending_owner) if pending_owner else None,
            "email": actor.email if actor else None}


@router.get("/activity")
async def company_activity(tenant: Tenant = Depends(get_current_tenant),
                           db: AsyncSession = Depends(get_scoped_db)):
    """会社の依頼・成果物の状態と、承認や引き継ぎコメントを共有する。"""
    users = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant.id))).scalars().all()
    names = {str(u.id): u.email for u in users}
    names[str(tenant.id)] = "会社キー・定期実行"
    sessions = (await db.execute(select(SessionModel).where(SessionModel.tenant_id == tenant.id)
                               .order_by(SessionModel.updated_at.desc()).limit(100))).scalars().all()
    ids = [s.id for s in sessions]
    created = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant.id,
               AuditLog.session_id.in_(ids), AuditLog.action == "REQUEST_CREATED"))).scalars().all() if ids else []
    creators = {a.session_id: names.get(a.actor, "会社共有") for a in created}
    logs = (await db.execute(select(AuditLog, SessionModel.request_text)
        .join(SessionModel, SessionModel.id == AuditLog.session_id)
        .where(AuditLog.tenant_id == tenant.id, SessionModel.tenant_id == tenant.id,
               AuditLog.action.in_(["COMMENT", "APPROVE", "REJECT", "IMAGE_GENERATED"]))
        .order_by(AuditLog.created_at.desc()).limit(100))).all()
    return {
        "requests": [{"session_id": str(s.id), "title": s.request_text[:300], "status": s.status,
                      "requester": creators.get(s.id, "会社共有（旧履歴・定期実行）"),
                      "updated_at": s.updated_at, "created_at": s.created_at} for s in sessions],
        "events": [{"session_id": str(a.session_id), "title": title[:150], "action": a.action,
                    "actor": names.get(a.actor, "会社共有"), "comment": a.comment,
                    "created_at": a.created_at} for a, title in logs],
    }


@router.post("/logout", status_code=204)
async def logout_user(actor: TenantUser | None = Depends(get_optional_tenant_user),
                      db: AsyncSession = Depends(get_scoped_db)):
    if actor is not None:
        # 個人ログアウトは全端末のセッションを失効させる。
        await db.execute(delete(TenantUserToken).where(TenantUserToken.tenant_user_id == actor.id))
        await db.commit()


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(user_id: uuid.UUID, tenant: Tenant = Depends(get_current_tenant),
                          actor: TenantUser | None = Depends(get_optional_tenant_user),
                          db: AsyncSession = Depends(get_scoped_db)):
    if actor is None or actor.role != "owner":
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    await db.execute(select(Tenant).where(Tenant.id == tenant.id).with_for_update())
    user = (await db.execute(select(TenantUser).where(TenantUser.id == user_id,
                            TenantUser.tenant_id == tenant.id).with_for_update())).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="社員が見つかりません")
    if user.role == "owner" and user.is_active:
        owners = (await db.execute(select(func.count()).where(TenantUser.tenant_id == tenant.id,
                  TenantUser.role == "owner", TenantUser.is_active.is_(True)))).scalar_one()
        if owners <= 1:
            raise HTTPException(status_code=409, detail="最後の管理者は利用停止できません")
    user.is_active = False
    user.invite_token_hash = None
    user.invite_expires_at = None
    await db.execute(delete(TenantUserToken).where(TenantUserToken.tenant_user_id == user.id))
    await db.commit()


@router.post("/users/{user_id}/resend-invite")
async def resend_invite(user_id: uuid.UUID, tenant: Tenant = Depends(get_current_tenant),
                        actor: TenantUser | None = Depends(get_optional_tenant_user),
                        db: AsyncSession = Depends(get_scoped_db)):
    await db.execute(select(Tenant).where(Tenant.id == tenant.id).with_for_update())
    user = (await db.execute(select(TenantUser).where(TenantUser.id == user_id,
                            TenantUser.tenant_id == tenant.id).with_for_update())).scalar_one_or_none()
    count = (await db.execute(select(func.count()).where(TenantUser.tenant_id == tenant.id))).scalar_one()
    bootstrap = actor is None and count == 1 and user is not None and user.role == "owner" and not user.password_hash
    if not bootstrap and (actor is None or actor.role != "owner"):
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    if user is None:
        raise HTTPException(status_code=404, detail="社員が見つかりません")
    if user.is_active or user.password_hash:
        raise HTTPException(status_code=409, detail="初回招待を受諾済みです")
    token = secrets.token_urlsafe(32)
    user.invite_token_hash = hash_api_key(token)
    user.invite_expires_at = datetime.utcnow() + timedelta(hours=INVITE_TTL_HOURS)
    await db.commit()
    url = f'{settings.saas_public_url.rstrip("/")}/#invite={token}&company={tenant.id}'
    sent = await send_email(to=user.email, subject="ツナグモ：アカウント招待のご案内",
        html=f'<p>会社ID: {tenant.id}</p><p>招待コード: <code>{token}</code></p>'
             + (f'<p><a href="{escape(url, quote=True)}">パスワードを設定する</a>（48時間以内）</p>' if settings.saas_public_url else ''))
    return {"invite_sent": sent}
