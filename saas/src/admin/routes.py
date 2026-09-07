import secrets
import uuid
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import CroniterBadCronError, croniter
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.config_loader import ConfigError, load_config
from src.agent.llm import StructuredLLM
from src.agent.regression_test import run_regression_tests
from src.api.deps import (
    DEFAULT_INDUSTRY,
    INDUSTRIES,
    get_internal_llm,
    hash_api_key,
    require_admin,
    resolve_app_config,
)
from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.core.db import get_db
from src.core.email import send_email
from src.core.models import Approval, Document, Schedule, Session, Tenant, TenantUser, WebChatSession
from src.core.ai_budget import PLAN_MONTHLY_BUDGET_USD
from src.core.runtime_flags import actions_enabled, set_actions_enabled
from src.core.validation import (
    validate_anthropic_key_format,
    validate_higgsfield_key_format,
    validate_openai_key_format,
)
from src.rag.expiry_scan import EXPIRY_WARNING_DAYS

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _config_path(industry: str) -> Path:
    # industryはINDUSTRIESの許可リストに含まれる値のみ受け付ける(パストラバーサル対策)。
    if industry not in INDUSTRIES:
        raise HTTPException(
            status_code=404, detail=f"未知の業種です: {industry}（利用可能: {INDUSTRIES}）"
        )
    return Path(f"config/{industry}.yaml")


class TenantSummary(BaseModel):
    tenant_id: str
    tenant_name: str
    industry: str
    session_count: int
    error_count: int
    halted_count: int
    pending_approval_count: int
    total_cost_usd: float


class DashboardResponse(BaseModel):
    tenants: list[TenantSummary]


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(db: AsyncSession = Depends(get_db)) -> DashboardResponse:
    """① 横断ダッシュボード: 各社の利用状況・コスト・エラー・承認待ち。"""
    session_stats = await db.execute(
        select(
            Tenant.id,
            Tenant.name,
            Tenant.industry,
            func.count(Session.id).label("session_count"),
            func.coalesce(func.sum(case((Session.status == "ERROR", 1), else_=0)), 0).label(
                "error_count"
            ),
            func.coalesce(func.sum(case((Session.status == "HALTED", 1), else_=0)), 0).label(
                "halted_count"
            ),
            func.coalesce(func.sum(Session.cost_usd), 0.0).label("total_cost_usd"),
        )
        .select_from(Tenant)
        .outerjoin(Session, Session.tenant_id == Tenant.id)
        .group_by(Tenant.id, Tenant.name, Tenant.industry)
    )

    pending_stats = await db.execute(
        select(Approval.tenant_id, func.count(Approval.id))
        .where(Approval.status == "PENDING")
        .group_by(Approval.tenant_id)
    )
    pending_by_tenant = dict(pending_stats.all())

    tenants = [
        TenantSummary(
            tenant_id=str(row.id),
            tenant_name=row.name,
            industry=row.industry,
            session_count=row.session_count,
            error_count=row.error_count,
            halted_count=row.halted_count,
            pending_approval_count=pending_by_tenant.get(row.id, 0),
            total_cost_usd=round(row.total_cost_usd, 6),
        )
        for row in session_stats
    ]

    return DashboardResponse(tenants=tenants)


class ConfigResponse(BaseModel):
    yaml_text: str


class ConfigUpdateRequest(BaseModel):
    yaml_text: str


class ConfigUpdateResponse(BaseModel):
    company_name: str
    departments: list[str]


class IndustryListResponse(BaseModel):
    industries: list[str]


@router.get("/industries", response_model=IndustryListResponse)
async def list_industries() -> IndustryListResponse:
    return IndustryListResponse(industries=INDUSTRIES)


@router.get("/config/{industry}", response_model=ConfigResponse)
async def get_config(industry: str) -> ConfigResponse:
    """② プロンプト一括更新(閲覧側): 指定した業種の設定YAMLを取得する。"""
    return ConfigResponse(yaml_text=_config_path(industry).read_text(encoding="utf-8"))


@router.put("/config/{industry}", response_model=ConfigUpdateResponse)
async def update_config(
    industry: str, req: ConfigUpdateRequest, request: Request
) -> ConfigUpdateResponse:
    """② プロンプト一括更新: 1箇所直すと、その業種の全顧客に反映される。

    書き込む前に必ず検証する(2-2と同じ思想: 検証に失敗したものを反映しない)。
    検証に通ったら即座にapp.state.app_configs[industry]を差し替える(再デプロイ不要)。
    """
    config_path = _config_path(industry)
    tmp_path = config_path.with_suffix(".yaml.tmp")
    tmp_path.write_text(req.yaml_text, encoding="utf-8")
    try:
        new_config = load_config(tmp_path)
    except (ConfigError, ValidationError, yaml.YAMLError) as e:
        raise HTTPException(status_code=400, detail=f"設定の検証に失敗しました: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    config_path.write_text(req.yaml_text, encoding="utf-8")
    request.app.state.app_configs[industry] = new_config

    return ConfigUpdateResponse(
        company_name=new_config.company.name, departments=list(new_config.departments)
    )


class RegressionTestResult(BaseModel):
    name: str
    ok: bool
    detail: str


class RegressionTestResponse(BaseModel):
    all_passed: bool
    results: list[RegressionTestResult]


@router.post("/regression-test", response_model=RegressionTestResponse)
async def regression_test(
    request: Request, industry: str = DEFAULT_INDUSTRY, llm: StructuredLLM = Depends(get_internal_llm)
) -> RegressionTestResponse:
    """⑥ 回帰テストの一括実行(8-7)。実際のClaude APIを呼ぶため課金・数分かかる。
    モデル更新後の動作確認に使う。デフォルトはreal_estate(現在の唯一の対応業種)。"""
    app_config = resolve_app_config(request, industry)
    results = await run_regression_tests(app_config=app_config, llm=llm)
    return RegressionTestResponse(
        all_passed=all(r["ok"] for r in results),
        results=[RegressionTestResult(**r) for r in results],
    )


class TenantCreateRequest(BaseModel):
    name: str
    industry: str = DEFAULT_INDUSTRY
    # 2026-09-01: BYOK廃止に伴い任意化(AIは運営自身のキーで動くため、顧客自身の
    # キーはもう必須ではない)。列・バリデーションは過去互換のため残す。
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    email: str | None = None
    # LINEボットのトライアル申込経由で発行する場合、Stripeの顧客IDを渡すことで
    # Stripe Webhook(customer.subscription.deleted等)による自動アクセス停止の対象になる。
    # 未指定(管理者が手動発行する場合等)ならStripeとは連動せず、常にアクセス可能なまま。
    stripe_customer_id: str | None = None
    # プラン別の月間AI予算(src/core/ai_budget.py)。未指定ならモデルの既定"light"。
    plan: str | None = None


class TenantCreateResponse(BaseModel):
    tenant_id: str
    name: str
    industry: str
    api_key: str
    email_sent: bool


def _welcome_email_html(*, name: str, api_key: str) -> str:
    login_line = (
        f'<p>ログインURL: <a href="{settings.saas_public_url}">{settings.saas_public_url}</a></p>'
        if settings.saas_public_url
        else ""
    )
    return (
        f"<p>{name} 様</p>"
        "<p>ツナグモのご利用登録が完了しました。以下のAPIキーでログインしてください。</p>"
        f"{login_line}"
        f'<p>APIキー: <code style="background:#f1e6da;padding:4px 8px;">{api_key}</code></p>'
        "<p>このメールは他の方に転送しないようお願いいたします。</p>"
    )


@router.post("/tenants", response_model=TenantCreateResponse, status_code=201)
async def create_tenant(
    req: TenantCreateRequest, db: AsyncSession = Depends(get_db)
) -> TenantCreateResponse:
    """新規テナントを発行する。APIキー(このSaaSへのログイン用)はこのレスポンスでしか
    平文で見られない(api_key_hashだけを保存するため)。顧客に渡したら、この画面には
    控えを残さない。

    2026-09-01よりBYOK廃止のため、AI利用はすべて運営(ツナグモ)自身のキーで行う。
    anthropic_api_key/openai_api_keyはもう実際の呼び出しには使われない(過去互換の
    ため列・入力欄は残してある)。

    emailを指定すると、発行直後にウェルカムメールを送る(Resend未設定の間はスキップ
    されるだけで、テナント発行自体は失敗しない)。
    """
    if req.industry not in INDUSTRIES:
        raise HTTPException(
            status_code=400, detail=f"未知の業種です: {req.industry}（利用可能: {INDUSTRIES}）"
        )
    if req.anthropic_api_key:
        validate_anthropic_key_format(req.anthropic_api_key)
    if req.openai_api_key:
        validate_openai_key_format(req.openai_api_key)
    if req.plan is not None and req.plan not in PLAN_MONTHLY_BUDGET_USD:
        raise HTTPException(
            status_code=400,
            detail=f"未知のプランです: {req.plan}（利用可能: {list(PLAN_MONTHLY_BUDGET_USD)}）",
        )

    api_key = f"tsg_{secrets.token_urlsafe(32)}"
    tenant = Tenant(
        name=req.name,
        industry=req.industry,
        api_key_hash=hash_api_key(api_key),
        anthropic_api_key=encrypt_secret(req.anthropic_api_key) if req.anthropic_api_key else None,
        openai_api_key=encrypt_secret(req.openai_api_key) if req.openai_api_key else None,
        email=req.email,
        stripe_customer_id=req.stripe_customer_id,
        **({"plan": req.plan} if req.plan is not None else {}),
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    email_sent = False
    if req.email:
        email_sent = await send_email(
            to=req.email,
            subject="【ツナグモ】ご利用登録が完了しました",
            html=_welcome_email_html(name=req.name, api_key=api_key),
        )

    return TenantCreateResponse(
        tenant_id=str(tenant.id),
        name=tenant.name,
        industry=tenant.industry,
        api_key=api_key,
        email_sent=email_sent,
    )


class TenantAnthropicKeyUpdateRequest(BaseModel):
    anthropic_api_key: str


@router.patch("/tenants/{tenant_id}/anthropic-key", status_code=204)
async def update_tenant_anthropic_key(
    tenant_id: str, req: TenantAnthropicKeyUpdateRequest, db: AsyncSession = Depends(get_db)
) -> None:
    """顧客がAnthropic APIキーを再発行した場合などのローテーション用。"""
    validate_anthropic_key_format(req.anthropic_api_key)

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")

    tenant = await db.get(Tenant, tenant_uuid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")

    tenant.anthropic_api_key = encrypt_secret(req.anthropic_api_key)
    await db.commit()


class TenantHiggsfieldKeyUpdateRequest(BaseModel):
    higgsfield_key_id: str
    higgsfield_key_secret: str


@router.patch("/tenants/{tenant_id}/higgsfield-key", status_code=204)
async def update_tenant_higgsfield_key(
    tenant_id: str, req: TenantHiggsfieldKeyUpdateRequest, db: AsyncSession = Depends(get_db)
) -> None:
    """オンライン相談(初期設定)で確認したHiggsfieldキーを、野村さんが代行入力する場合用。
    顧客は後から自分でも設定画面から登録・更新できる(src/api/routes/account.py)。"""
    validate_higgsfield_key_format(req.higgsfield_key_id, req.higgsfield_key_secret)

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")

    tenant = await db.get(Tenant, tenant_uuid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")

    tenant.higgsfield_api_key_id = encrypt_secret(req.higgsfield_key_id)
    tenant.higgsfield_api_key_secret = encrypt_secret(req.higgsfield_key_secret)
    await db.commit()


class ExpiringDocumentSummary(BaseModel):
    tenant_id: str
    tenant_name: str
    document_id: str
    title: str
    valid_until: date
    days_left: int


class ExpiringDocumentsResponse(BaseModel):
    documents: list[ExpiringDocumentSummary]


@router.get("/documents/expiring", response_model=ExpiringDocumentsResponse)
async def list_expiring_documents(db: AsyncSession = Depends(get_db)) -> ExpiringDocumentsResponse:
    """7-7-2-4: 有効期限が近い(または過ぎた)社内資料を横断で見る画面用
    (scripts.scan_document_expiryが顧客へのメール通知を担う一方、こちらは
    野村さん自身が全テナントの状況を一目で確認するためのもの)。"""
    today = date.today()
    threshold = today + timedelta(days=EXPIRY_WARNING_DAYS)

    result = await db.execute(
        select(Document, Tenant.name)
        .join(Tenant, Document.tenant_id == Tenant.id)
        .where(
            Document.status.in_(["ACTIVE", "EXPIRED"]),
            Document.valid_until.is_not(None),
            Document.valid_until <= threshold,
        )
        .order_by(Document.valid_until.asc())
    )

    return ExpiringDocumentsResponse(
        documents=[
            ExpiringDocumentSummary(
                tenant_id=str(doc.tenant_id),
                tenant_name=tenant_name,
                document_id=str(doc.id),
                title=doc.title,
                valid_until=doc.valid_until,
                days_left=(doc.valid_until - today).days,
            )
            for doc, tenant_name in result.all()
        ]
    )


class ActionsEnabledResponse(BaseModel):
    actions_enabled: bool


class ActionsEnabledUpdateRequest(BaseModel):
    enabled: bool


@router.get("/runtime-flags/actions-enabled", response_model=ActionsEnabledResponse)
async def get_actions_enabled() -> ActionsEnabledResponse:
    """Phase 12: 外部への不可逆アクション(メール送信等)実行の緊急停止フラグ。
    既定はFalse(安全側)。ONにするまでは、承認されたアクションもBLOCKEDのまま
    実際には実行されない。"""
    return ActionsEnabledResponse(actions_enabled=await actions_enabled())


@router.put("/runtime-flags/actions-enabled", response_model=ActionsEnabledResponse)
async def update_actions_enabled(req: ActionsEnabledUpdateRequest) -> ActionsEnabledResponse:
    """再デプロイなしで即座に反映される(DBに保存するだけなので)。"""
    await set_actions_enabled(req.enabled)
    return ActionsEnabledResponse(actions_enabled=req.enabled)


def _validate_cron_and_timezone(cron_expr: str, tz_name: str) -> None:
    try:
        croniter(cron_expr)
    except (CroniterBadCronError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"cron式が不正です: {e}") from e
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"未知のタイムゾーンです: {tz_name}") from e


class ScheduleCreateRequest(BaseModel):
    cron: str
    timezone: str = "Asia/Tokyo"
    goal: str
    max_budget_usd: float | None = None
    # 14-3: 省略時はschedule_tick.pyのDEFAULT_MAX_MONTHLY_RUNSが適用される。
    # 毎日1回を超える正当な頻度で使いたい場合のみ明示的に指定する。
    max_monthly_runs: int | None = None


class ScheduleResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    cron: str
    timezone: str
    goal: str
    max_budget_usd: float | None
    max_monthly_runs: int | None
    enabled: bool
    consecutive_failures: int


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]


class ScheduleUpdateRequest(BaseModel):
    enabled: bool


async def _get_tenant_or_404(db: AsyncSession, tenant_id: str) -> Tenant:
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="テナントが見つかりません") from None
    tenant = await db.get(Tenant, tenant_uuid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")
    return tenant


@router.post("/tenants/{tenant_id}/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    tenant_id: str, req: ScheduleCreateRequest, db: AsyncSession = Depends(get_db)
) -> ScheduleResponse:
    """Phase 14: 定期実行の登録。「毎週月曜9時にSNS投稿案を作成」等。
    スケジュール実行でも承認は必須(生成後は必ず承認待ちを経由する。無人で外に出す経路はない)。
    """
    tenant = await _get_tenant_or_404(db, tenant_id)
    _validate_cron_and_timezone(req.cron, req.timezone)

    schedule = Schedule(
        tenant_id=tenant.id,
        cron=req.cron,
        timezone=req.timezone,
        goal=req.goal,
        max_budget_usd=req.max_budget_usd,
        max_monthly_runs=req.max_monthly_runs,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return ScheduleResponse(
        id=schedule.id,
        tenant_id=schedule.tenant_id,
        cron=schedule.cron,
        timezone=schedule.timezone,
        goal=schedule.goal,
        max_budget_usd=schedule.max_budget_usd,
        max_monthly_runs=schedule.max_monthly_runs,
        enabled=schedule.enabled,
        consecutive_failures=schedule.consecutive_failures,
    )


@router.get("/tenants/{tenant_id}/schedules", response_model=ScheduleListResponse)
async def list_schedules(tenant_id: str, db: AsyncSession = Depends(get_db)) -> ScheduleListResponse:
    tenant = await _get_tenant_or_404(db, tenant_id)
    result = await db.execute(select(Schedule).where(Schedule.tenant_id == tenant.id))
    return ScheduleListResponse(
        schedules=[
            ScheduleResponse(
                id=s.id,
                tenant_id=s.tenant_id,
                cron=s.cron,
                timezone=s.timezone,
                goal=s.goal,
                max_budget_usd=s.max_budget_usd,
                max_monthly_runs=s.max_monthly_runs,
                enabled=s.enabled,
                consecutive_failures=s.consecutive_failures,
            )
            for s in result.scalars().all()
        ]
    )


@router.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: uuid.UUID, req: ScheduleUpdateRequest, db: AsyncSession = Depends(get_db)
) -> ScheduleResponse:
    """有効/無効の切り替え(3回連続失敗で自動的に無効化された後、原因を直してから
    再度有効化する場合もここを使う。再有効化時はconsecutive_failuresを0に戻す)。"""
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="スケジュールが見つかりません")

    schedule.enabled = req.enabled
    if req.enabled:
        schedule.consecutive_failures = 0
    await db.commit()
    await db.refresh(schedule)

    return ScheduleResponse(
        id=schedule.id,
        tenant_id=schedule.tenant_id,
        cron=schedule.cron,
        timezone=schedule.timezone,
        goal=schedule.goal,
        max_budget_usd=schedule.max_budget_usd,
        max_monthly_runs=schedule.max_monthly_runs,
        enabled=schedule.enabled,
        consecutive_failures=schedule.consecutive_failures,
    )


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="スケジュールが見つかりません")
    await db.delete(schedule)
    await db.commit()


class WebWidgetConfigRequest(BaseModel):
    allowed_origin: str


class WebWidgetConfigResponse(BaseModel):
    tenant_id: str
    public_key: str
    allowed_origin: str


@router.put("/tenants/{tenant_id}/web-widget", response_model=WebWidgetConfigResponse)
async def configure_web_widget(
    tenant_id: str, req: WebWidgetConfigRequest, db: AsyncSession = Depends(get_db)
) -> WebWidgetConfigResponse:
    """Phase 16: Web埋め込みチャットを有効化する。公開鍵は毎回新しく発行し直す
    (ローテーションしたい場合もこのエンドポイントを再度呼べばよい)。
    allowed_originは埋め込み先のドメイン1つ(例: https://example.com、末尾スラッシュなし)。
    """
    tenant = await _get_tenant_or_404(db, tenant_id)

    if not req.allowed_origin.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="allowed_originは https:// から始めてください")

    tenant.web_widget_public_key = f"wpk_{secrets.token_urlsafe(24)}"
    tenant.web_widget_allowed_origin = req.allowed_origin.rstrip("/")
    await db.commit()

    return WebWidgetConfigResponse(
        tenant_id=str(tenant.id),
        public_key=tenant.web_widget_public_key,
        allowed_origin=tenant.web_widget_allowed_origin,
    )


@router.delete("/tenants/{tenant_id}/web-widget", status_code=204)
async def disable_web_widget(tenant_id: str, db: AsyncSession = Depends(get_db)) -> None:
    tenant = await _get_tenant_or_404(db, tenant_id)
    tenant.web_widget_public_key = None
    tenant.web_widget_allowed_origin = None
    await db.commit()


class ApprovalStagesRequest(BaseModel):
    approval_stages: int


class ApprovalStagesResponse(BaseModel):
    tenant_id: str
    approval_stages: int


@router.put("/tenants/{tenant_id}/approval-stages", response_model=ApprovalStagesResponse)
async def set_approval_stages(
    tenant_id: str, req: ApprovalStagesRequest, db: AsyncSession = Depends(get_db)
) -> ApprovalStagesResponse:
    """Phase 13: テナントごとの承認段数を設定する。既定は1(今までと同じ、テナントAPIキー
    だけで承認可能)。2以上にする場合、そのテナントにapprover/ownerロールのTenantUserが
    最低2名いることを要求する(付録J「承認者ゼロ問題」対策: 1名しかいないと、その人が
    退職・異動しただけで全案件が承認待ちのまま詰まってしまうため)。
    """
    tenant = await _get_tenant_or_404(db, tenant_id)
    if req.approval_stages < 1 or req.approval_stages > 10:
        raise HTTPException(status_code=400, detail="approval_stagesは1〜10の範囲で指定してください")

    if req.approval_stages >= 2:
        approver_count = await db.scalar(
            select(func.count())
            .select_from(TenantUser)
            .where(
                TenantUser.tenant_id == tenant.id,
                TenantUser.role.in_(("approver", "owner")),
                TenantUser.is_active.is_(True),
            )
        )
        if approver_count < 2:
            raise HTTPException(
                status_code=400,
                detail=(
                    "多段階承認を有効にするには、approver/ownerロールで有効なユーザーが"
                    "最低2名必要です(1名だけだと、その人が抜けた時点で全案件が承認待ちのまま"
                    "詰まってしまうため)。先にPOST /api/account/usersで招待してください。"
                ),
            )

    tenant.approval_stages = req.approval_stages
    await db.commit()

    return ApprovalStagesResponse(tenant_id=str(tenant.id), approval_stages=tenant.approval_stages)


class PlanRequest(BaseModel):
    plan: str


class PlanResponse(BaseModel):
    tenant_id: str
    plan: str
    monthly_ai_budget_usd: float


@router.put("/tenants/{tenant_id}/plan", response_model=PlanResponse)
async def set_tenant_plan(
    tenant_id: str, req: PlanRequest, db: AsyncSession = Depends(get_db)
) -> PlanResponse:
    """テナントのプランを変更する(src/core/ai_budget.py参照。月間AI予算のみが
    プランで変わり、金額の請求自体はStripe側で別途行う)。"""
    if req.plan not in PLAN_MONTHLY_BUDGET_USD:
        raise HTTPException(
            status_code=400, detail=f"未知のプランです: {req.plan}（利用可能: {list(PLAN_MONTHLY_BUDGET_USD)}）"
        )
    tenant = await _get_tenant_or_404(db, tenant_id)
    tenant.plan = req.plan
    await db.commit()

    return PlanResponse(
        tenant_id=str(tenant.id), plan=tenant.plan, monthly_ai_budget_usd=PLAN_MONTHLY_BUDGET_USD[req.plan]
    )


class LineChannelConfigRequest(BaseModel):
    line_channel_secret: str
    line_channel_access_token: str


class LineChannelConfigResponse(BaseModel):
    tenant_id: str
    configured: bool


@router.put("/tenants/{tenant_id}/line-channel", response_model=LineChannelConfigResponse)
async def configure_line_channel(
    tenant_id: str, req: LineChannelConfigRequest, db: AsyncSession = Depends(get_db)
) -> LineChannelConfigResponse:
    """Phase 15: そのテナントの顧客対応をLINE公式アカウント経由でも受け付けられるようにする。
    line_channel_secret/access_tokenは、そのテナントの顧客自身のLINE Developersコンソールで
    発行したもの(顧客対応窓口の実LINE公式アカウント。line-bot/の営業用アカウントとは別物)。
    """
    tenant = await _get_tenant_or_404(db, tenant_id)
    tenant.line_channel_secret = encrypt_secret(req.line_channel_secret)
    tenant.line_channel_access_token = encrypt_secret(req.line_channel_access_token)
    await db.commit()

    return LineChannelConfigResponse(tenant_id=str(tenant.id), configured=True)


@router.delete("/tenants/{tenant_id}/line-channel", status_code=204)
async def disable_line_channel(tenant_id: str, db: AsyncSession = Depends(get_db)) -> None:
    tenant = await _get_tenant_or_404(db, tenant_id)
    tenant.line_channel_secret = None
    tenant.line_channel_access_token = None
    await db.commit()


class EscalatedChatSummary(BaseModel):
    session_id: str
    message_count: int
    last_activity_at: str
    messages: list[dict]


class EscalatedChatListResponse(BaseModel):
    sessions: list[EscalatedChatSummary]


@router.get("/tenants/{tenant_id}/web-chat/escalated", response_model=EscalatedChatListResponse)
async def list_escalated_web_chats(tenant_id: str, db: AsyncSession = Depends(get_db)) -> EscalatedChatListResponse:
    """16-2: web_responderが対応不可と判断した会話の一覧(Slackが無いこのアーキテクチャでは、
    「エスカレーション」は人間(野村さん/顧客)がここを見に来る形で代替する)。"""
    tenant = await _get_tenant_or_404(db, tenant_id)
    result = await db.execute(
        select(WebChatSession)
        .where(WebChatSession.tenant_id == tenant.id, WebChatSession.escalated.is_(True))
        .order_by(WebChatSession.last_activity_at.desc())
    )
    return EscalatedChatListResponse(
        sessions=[
            EscalatedChatSummary(
                session_id=str(s.id),
                message_count=s.message_count,
                last_activity_at=s.last_activity_at.isoformat(),
                messages=s.messages,
            )
            for s in result.scalars().all()
        ]
    )
