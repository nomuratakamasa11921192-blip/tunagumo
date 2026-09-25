"""テナント単位の月間AI予算上限(2026-09-01追加)。

BYOK(顧客自身のAPIキー)を廃止し、AI利用料を運営(ツナグモ)が負担する方式に切り替えた
ことで、「顧客自身のAnthropic Console上の利用上限設定」という一番信頼できた防御層が
失われた(旧docs/ai_org_spec_master.md付録D)。この月間予算上限が、その代わりの主防御層
になる。

`src/core/plan_limits.py`(動画本数のプラン別上限)と同じ「参照時リセット」パターンを
踏襲する: 月次バッチを別途動かす必要がなく、参照・記録のたびに月が変わっていれば
その場でリセットする。
"""

from datetime import datetime, timezone
import math
import uuid
import asyncio
from functools import wraps

from sqlalchemy import select, text

# プランごとの月間AI予算(USD)。ハードコードされた許可リスト。
# 2026-09-01、ユーザー確定の月額(税別目安、Stripe側の商品設定と一致させること):
#   light:     月額 \39,800  (AI予算 $10  ≒ \1,500)
#   standard:  月額 \98,000  (AI予算 $40  ≒ \6,000)
#   unlimited: 月額 \198,000 (AI予算 $120 ≒ \18,000)
PLAN_MONTHLY_BUDGET_USD: dict[str, float] = {
    "basic": 30.0,
    "business": 60.0,
    "enterprise": 0.0,  # 契約別上限が設定されるまで生成不可
    "light": 10.0,
    "standard": 40.0,
    "unlimited": 120.0,
}
DEFAULT_PLAN = "light"
COMPANY_PLANS = frozenset({"basic", "business", "enterprise"})
PLAN_LABELS = {
    "basic": "ベーシック", "business": "ビジネス", "enterprise": "エンタープライズ",
    "light": "ライト（旧プラン）", "standard": "スタンダード（旧プラン）",
    "unlimited": "プレミアム（旧プラン）",
}
PLAN_MONTHLY_PRICE_JPY = {"basic": 39800, "business": 59800}  # 税込・1社単位
# 待機ジョブだけでDB接続プールを埋めず、グラフの結果保存用接続を確保する。
_background_generation_slots = asyncio.Semaphore(4)


def monthly_budget_usd(tenant) -> float:
    if tenant.plan == "enterprise":
        value = getattr(tenant, "enterprise_ai_budget_usd", None)
        return float(value) if value is not None and math.isfinite(value) and value > 0 else 0.0
    return PLAN_MONTHLY_BUDGET_USD.get(tenant.plan, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN])


def next_period_at(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)

# Higgsfield生成1回あたりの概算コスト($)。
#
# 【現在は未使用】2026-09-15、Higgsfieldは顧客自身のAPIキーで課金される顧客負担方式に
# したため、運営の月間AI予算からは差し引かない(sessions.pyのrecord_cost呼び出しを削除済み)。
# 動画は1本あたりの単価が高くコストが読みにくいのが理由。単価が十分下がって運営負担に
# 切り替える判断をした際に、この定数を再び使う想定で残してある。
#
# $5/100クレジット換算、画像1枚≒5クレジット、動画1本≒40クレジットという仮の見積もり。
# 運営負担に切り替える場合は、実際のクレジット消費を計測して必ず補正すること。
ESTIMATED_HIGGSFIELD_IMAGE_COST_USD = 0.25
ESTIMATED_HIGGSFIELD_VIDEO_COST_USD = 2.0
# OpenAI画像(生成・編集)1枚あたりのコスト($)の【フォールバック値】(2026-09-15追加)。
#
# 画像は運営負担なので月間AI予算から差し引く。通常はAPIが返すusage(トークン数)と
# src/core/openai_image_client.pyの公式単価から実コストを計算して記録するため、この値は
# 使われない。usageが返らなかった場合だけ、予算の引き漏れを防ぐためにこの値を記録する。
# 【要確認】OpenAIは1枚あたりの価格を公開していない(トークン課金)ため、この値は
# 公式価格ではない暫定値(Higgsfield時代の画像見積もりと同額)。実測後に補正すること。
ESTIMATED_OPENAI_IMAGE_COST_USD = 0.25
# ルームツアー動画生成1回あたりの概算コスト($)。台本生成(OpenAI、数千トークン程度)+
# ナレーション音声合成(OpenAI TTS)の見積もり。実測してから補正すること。
ESTIMATED_ROOM_TOUR_COST_USD = 0.15


class BudgetExceededError(Exception):
    """顧客に表示する文面には金額を出さない(2026-09-17)。AI利用料は月額に含まれるため、
    ドル建ての予算額を見せると原価・利益率が推測できてしまう。割合だけを伝える。"""

    def __init__(self, plan: str, budget: float):
        self.plan = plan
        self.budget = budget  # 内部処理・管理画面用(顧客には表示しない)
        super().__init__(
            "今月のAI利用量が上限に達しました。"
            "翌月の枠の更新をお待ちいただくか、上位プランについて運営にご相談ください。"
            "追加料金は自動では発生しません。"
        )


def _current_period_key(dt: datetime) -> tuple[int, int]:
    return (dt.year, dt.month)


def _reset_period_if_needed(tenant, now: datetime) -> None:
    period_started_at = tenant.ai_cost_period_started_at
    if period_started_at.tzinfo is None:
        period_started_at = period_started_at.replace(tzinfo=timezone.utc)
    if _current_period_key(period_started_at) != _current_period_key(now):
        tenant.ai_cost_this_period_usd = 0.0
        # DB列はTIMESTAMP WITHOUT TIME ZONE。UTCへ揃えてnaive値で保存する。
        tenant.ai_cost_period_started_at = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def _remaining_budget(tenant) -> float:
    monthly_budget = monthly_budget_usd(tenant)
    return (monthly_budget - tenant.ai_cost_this_period_usd) + tenant.addon_credit_usd


def ensure_budget_available(tenant, *, now: datetime | None = None) -> None:
    """今月のAI予算が残っているかチェックする(状態は変更しない)。
    残っていなければBudgetExceededErrorを送出する。実際のAI呼び出しの「前」に呼ぶ。
    """
    now = now or datetime.now(timezone.utc)
    _reset_period_if_needed(tenant, now)
    if monthly_budget_usd(tenant) <= 0 or _remaining_budget(tenant) <= 0:
        monthly_budget = monthly_budget_usd(tenant)
        raise BudgetExceededError(tenant.plan, monthly_budget)


def effective_cost_this_period(tenant, *, now: datetime | None = None) -> float:
    """表示用: 月が変わっていれば0扱いで今期の使用額を返す(tenantは変更しない)。"""
    now = now or datetime.now(timezone.utc)
    period_started_at = tenant.ai_cost_period_started_at
    if period_started_at.tzinfo is None:
        period_started_at = period_started_at.replace(tzinfo=timezone.utc)
    if _current_period_key(period_started_at) != _current_period_key(now):
        return 0.0
    return tenant.ai_cost_this_period_usd


def record_cost(tenant, cost_usd: float, *, now: datetime | None = None) -> None:
    """実際に発生したコストを月間累計に加算する。月間予算を超えた分はaddon_credit_usd
    (追加購入分、月次リセットされない)から取り崩す(0未満にはしない)。
    """
    if cost_usd <= 0:
        return
    now = now or datetime.now(timezone.utc)
    _reset_period_if_needed(tenant, now)

    monthly_budget = monthly_budget_usd(tenant)
    remaining_monthly = monthly_budget - tenant.ai_cost_this_period_usd

    if tenant.plan in COMPANY_PLANS:
        # 実費を上限で切り捨てない。実行中の一回で超えた額も運営の原価として残す。
        credit_used = min(tenant.addon_credit_usd, max(cost_usd - max(remaining_monthly, 0.0), 0.0))
        tenant.addon_credit_usd -= credit_used
        tenant.ai_cost_this_period_usd += cost_usd - credit_used
        return

    if cost_usd <= remaining_monthly:
        tenant.ai_cost_this_period_usd += cost_usd
        return

    # 月間予算を使い切る。超過分だけaddon_credit_usdから取り崩す(マイナスにはしない)。
    overage = cost_usd - max(remaining_monthly, 0.0)
    tenant.ai_cost_this_period_usd = monthly_budget
    tenant.addon_credit_usd = max(tenant.addon_credit_usd - overage, 0.0)


async def lock_budget_tenant(db, tenant_id):
    """会社単位で生成の残量判定と計上を直列化。commit/rollbackで自動解放。"""
    from src.core.models import Tenant
    identifier = uuid.UUID(str(tenant_id))
    lock_key = identifier.int & ((1 << 63) - 1)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    return (await db.execute(select(Tenant).where(Tenant.id == identifier)
                           .execution_options(populate_existing=True))).scalar_one()


def serialize_company_generation(fn):
    """バックグラウンド生成にも同じ会社ロックを適用する。別DBの原価保存を妨げない。"""
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        from src.core.db import async_session_factory
        from src.core.models import Session, Tenant
        async with _background_generation_slots, async_session_factory() as db:
            tenant = await lock_budget_tenant(db, kwargs["tenant_id"])
            # 月替わりの参照時リセットをこの接続でflushすると、別接続の
            # _persistがTenant行ロックを待って相互待機になる。判定用は切り離す。
            db.expunge(tenant)
            if kwargs.get("decision") != "approve":
                ensure_budget_available(tenant)
            # グラフ全体の既存上限と、今月残量の小さい方を使用する。
            config = kwargs["app_config"].model_copy(deep=True)
            previous = await db.get(Session, uuid.UUID(kwargs["session_id"]))
            previous_cost = previous.cost_usd if previous and fn.__name__ != "run_new_session" else 0.0
            config.limits.max_budget_usd = min(config.limits.max_budget_usd,
                                              previous_cost + max(_remaining_budget(tenant), 0.0))
            kwargs["app_config"] = config
            embedding = kwargs.get("embedding_provider")
            before = getattr(embedding, "total_cost_usd", 0.0)
            try:
                return await fn(*args, **kwargs)
            finally:
                extra = getattr(embedding, "total_cost_usd", 0.0) - before
                if extra > 0:
                    tenant = await db.get(Tenant, tenant.id, with_for_update=True)
                    record_cost(tenant, extra)
                    await db.commit()
    return wrapped
