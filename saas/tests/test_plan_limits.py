from datetime import datetime, timedelta, timezone

import pytest

from src.core.plan_limits import (
    VIDEO_GENERATION_LIMITS,
    VideoQuotaExceededError,
    consume_video_quota,
    effective_video_count,
    ensure_video_quota_available,
)


class _FakeTenant:
    def __init__(self, plan="light", count=0, period_started_at=None):
        self.plan = plan
        self.video_generations_this_period = count
        self.video_period_started_at = period_started_at or datetime.now(timezone.utc)


def test_light_plan_allows_up_to_limit():
    tenant = _FakeTenant(plan="light", count=19)
    ensure_video_quota_available(tenant)  # 19本目 < 20本上限、例外なし


def test_light_plan_blocks_at_limit():
    tenant = _FakeTenant(plan="light", count=20)
    with pytest.raises(VideoQuotaExceededError):
        ensure_video_quota_available(tenant)


def test_unlimited_plan_never_blocks():
    tenant = _FakeTenant(plan="unlimited", count=100000)
    ensure_video_quota_available(tenant)


def test_unknown_plan_falls_back_to_default_limit():
    tenant = _FakeTenant(plan="does-not-exist", count=20)
    with pytest.raises(VideoQuotaExceededError) as exc_info:
        ensure_video_quota_available(tenant)
    assert exc_info.value.limit == VIDEO_GENERATION_LIMITS["light"]


def test_consume_increments_count():
    tenant = _FakeTenant(plan="light", count=5)
    consume_video_quota(tenant)
    assert tenant.video_generations_this_period == 6


def test_period_resets_when_month_changes():
    old_period = datetime.now(timezone.utc) - timedelta(days=45)
    tenant = _FakeTenant(plan="light", count=20, period_started_at=old_period)

    # 前月の上限(20本)に達していても、月が変わっていれば今はブロックされない
    ensure_video_quota_available(tenant)
    assert effective_video_count(tenant) == 0

    consume_video_quota(tenant)
    assert tenant.video_generations_this_period == 1
    assert tenant.video_period_started_at.month == datetime.now(timezone.utc).month


def test_effective_video_count_does_not_mutate_tenant():
    old_period = datetime.now(timezone.utc) - timedelta(days=45)
    tenant = _FakeTenant(plan="light", count=7, period_started_at=old_period)

    assert effective_video_count(tenant) == 0
    # 表示専用なので、tenant自体の値は変わっていないこと
    assert tenant.video_generations_this_period == 7
    assert tenant.video_period_started_at == old_period
