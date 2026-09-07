from datetime import datetime, timedelta, timezone

import pytest

from src.core.ai_budget import (
    PLAN_MONTHLY_BUDGET_USD,
    BudgetExceededError,
    effective_cost_this_period,
    ensure_budget_available,
    record_cost,
)


class _FakeTenant:
    def __init__(self, plan="light", cost=0.0, addon=0.0, period_started_at=None):
        self.plan = plan
        self.ai_cost_this_period_usd = cost
        self.addon_credit_usd = addon
        self.ai_cost_period_started_at = period_started_at or datetime.now(timezone.utc)


def test_light_plan_allows_spending_under_budget():
    tenant = _FakeTenant(plan="light", cost=9.0)
    ensure_budget_available(tenant)  # $9使用済み < $10上限、例外なし


def test_light_plan_blocks_at_budget():
    tenant = _FakeTenant(plan="light", cost=10.0)
    with pytest.raises(BudgetExceededError):
        ensure_budget_available(tenant)


def test_addon_credit_extends_budget():
    tenant = _FakeTenant(plan="light", cost=10.0, addon=5.0)
    ensure_budget_available(tenant)  # 予算切れだがaddonが残っているのでOK


def test_unknown_plan_falls_back_to_default_budget():
    tenant = _FakeTenant(plan="does-not-exist", cost=10.0)
    with pytest.raises(BudgetExceededError) as exc_info:
        ensure_budget_available(tenant)
    assert exc_info.value.budget == PLAN_MONTHLY_BUDGET_USD["light"]


def test_record_cost_within_monthly_budget():
    tenant = _FakeTenant(plan="light", cost=2.0)
    record_cost(tenant, 3.0)
    assert tenant.ai_cost_this_period_usd == 5.0
    assert tenant.addon_credit_usd == 0.0


def test_record_cost_overage_draws_from_addon():
    tenant = _FakeTenant(plan="light", cost=9.0, addon=5.0)
    record_cost(tenant, 3.0)  # $9+$3=$12 > $10上限、$2ぶんaddonから取り崩す
    assert tenant.ai_cost_this_period_usd == 10.0
    assert tenant.addon_credit_usd == 3.0


def test_record_cost_overage_without_addon_does_not_go_negative():
    tenant = _FakeTenant(plan="light", cost=9.0, addon=0.0)
    record_cost(tenant, 5.0)
    assert tenant.ai_cost_this_period_usd == 10.0
    assert tenant.addon_credit_usd == 0.0


def test_period_resets_when_month_changes():
    old_period = datetime.now(timezone.utc) - timedelta(days=45)
    tenant = _FakeTenant(plan="light", cost=10.0, period_started_at=old_period)

    ensure_budget_available(tenant)  # 前月分は使い切っていても、月が変われば問題ない
    assert effective_cost_this_period(tenant) == 0.0

    record_cost(tenant, 1.0)
    assert tenant.ai_cost_this_period_usd == 1.0
    assert tenant.ai_cost_period_started_at.month == datetime.now(timezone.utc).month


def test_effective_cost_this_period_does_not_mutate_tenant():
    old_period = datetime.now(timezone.utc) - timedelta(days=45)
    tenant = _FakeTenant(plan="light", cost=7.0, period_started_at=old_period)

    assert effective_cost_this_period(tenant) == 0.0
    assert tenant.ai_cost_this_period_usd == 7.0
    assert tenant.ai_cost_period_started_at == old_period


def test_record_cost_ignores_non_positive_amounts():
    tenant = _FakeTenant(plan="light", cost=5.0)
    record_cost(tenant, 0.0)
    record_cost(tenant, -1.0)
    assert tenant.ai_cost_this_period_usd == 5.0
