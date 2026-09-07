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

# プランごとの月間AI予算(USD)。ハードコードされた許可リスト。
# 2026-09-01、ユーザー確定の月額(税別目安、Stripe側の商品設定と一致させること):
#   light:     月額 \39,800  (AI予算 $10  ≒ \1,500)
#   standard:  月額 \98,000  (AI予算 $40  ≒ \6,000)
#   unlimited: 月額 \198,000 (AI予算 $120 ≒ \18,000)
PLAN_MONTHLY_BUDGET_USD: dict[str, float] = {
    "light": 10.0,
    "standard": 40.0,
    "unlimited": 120.0,
}
DEFAULT_PLAN = "light"

# Higgsfield生成1回あたりの概算コスト($)。cloud.higgsfield.aiで実キーを取得したら、
# 実際のクレジット消費を計測して補正すること(higgsfield_client.pyのIMAGE_EDIT_PATHと
# 同じ「未検証」扱い)。$5/100クレジット換算、画像1枚≒5クレジット、動画1本≒40クレジットという
# 仮の見積もり。
ESTIMATED_HIGGSFIELD_IMAGE_COST_USD = 0.25
ESTIMATED_HIGGSFIELD_VIDEO_COST_USD = 2.0
# ルームツアー動画生成1回あたりの概算コスト($)。台本生成(Anthropic、数千トークン程度)+
# ナレーション音声合成(OpenAI TTS)の見積もり。実測してから補正すること。
ESTIMATED_ROOM_TOUR_COST_USD = 0.15


class BudgetExceededError(Exception):
    def __init__(self, plan: str, budget: float):
        self.plan = plan
        self.budget = budget
        super().__init__(
            f"今月のAI利用予算(${budget:.2f}、{plan}プラン)の上限に達しました。"
            "プランのアップグレードまたは追加チケットのご購入をご検討いただくか、運営にお問い合わせください。"
        )


def _current_period_key(dt: datetime) -> tuple[int, int]:
    return (dt.year, dt.month)


def _reset_period_if_needed(tenant, now: datetime) -> None:
    period_started_at = tenant.ai_cost_period_started_at
    if period_started_at.tzinfo is None:
        period_started_at = period_started_at.replace(tzinfo=timezone.utc)
    if _current_period_key(period_started_at) != _current_period_key(now):
        tenant.ai_cost_this_period_usd = 0.0
        tenant.ai_cost_period_started_at = now


def _remaining_budget(tenant) -> float:
    monthly_budget = PLAN_MONTHLY_BUDGET_USD.get(tenant.plan, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN])
    return (monthly_budget - tenant.ai_cost_this_period_usd) + tenant.addon_credit_usd


def ensure_budget_available(tenant, *, now: datetime | None = None) -> None:
    """今月のAI予算が残っているかチェックする(状態は変更しない)。
    残っていなければBudgetExceededErrorを送出する。実際のAI呼び出しの「前」に呼ぶ。
    """
    now = now or datetime.now(timezone.utc)
    _reset_period_if_needed(tenant, now)
    if _remaining_budget(tenant) <= 0:
        monthly_budget = PLAN_MONTHLY_BUDGET_USD.get(tenant.plan, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN])
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

    monthly_budget = PLAN_MONTHLY_BUDGET_USD.get(tenant.plan, PLAN_MONTHLY_BUDGET_USD[DEFAULT_PLAN])
    remaining_monthly = monthly_budget - tenant.ai_cost_this_period_usd

    if cost_usd <= remaining_monthly:
        tenant.ai_cost_this_period_usd += cost_usd
        return

    # 月間予算を使い切る。超過分だけaddon_credit_usdから取り崩す(マイナスにはしない)。
    overage = cost_usd - max(remaining_monthly, 0.0)
    tenant.ai_cost_this_period_usd = monthly_budget
    tenant.addon_credit_usd = max(tenant.addon_credit_usd - overage, 0.0)
