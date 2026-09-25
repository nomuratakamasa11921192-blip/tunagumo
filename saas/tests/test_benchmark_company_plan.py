from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.benchmark_company_plan import BenchmarkStopped, Meter
from src.agent.config_models import Pricing


def response():
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


def meter(create, cap=1):
    pricing = Pricing.model_validate({"updated_at": "2026-09-25", "models": {
        "test-model": {"input_per_mtok": 2, "output_per_mtok": 10}}})
    return Meter(create, pricing, cap=cap)


REQUEST = {"model": "test-model", "max_completion_tokens": 4096, "messages": []}


@pytest.mark.asyncio
async def test_budget_refuses_next_call_before_network():
    create = AsyncMock(return_value=response())
    subject = meter(create, cap=0.06)
    await subject(**REQUEST)
    with pytest.raises(BenchmarkStopped):
        await subject(**REQUEST)
    assert create.await_count == 1
    assert subject.calls[0]["estimated_usd"] == pytest.approx(0.0004)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), SimpleNamespace(usage=None)])
async def test_unknown_outcome_disables_following_calls(failure):
    create = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=failure)
    subject = meter(create)
    for _ in range(2):
        with pytest.raises(BenchmarkStopped):
            await subject(**REQUEST)
    assert create.await_count == 1
    assert subject.unknown_usage


@pytest.mark.asyncio
async def test_missing_price_never_calls_provider():
    create = AsyncMock()
    subject = meter(create)
    with pytest.raises(BenchmarkStopped):
        await subject(**{**REQUEST, "model": "unpriced-model"})
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_excess_usage_retained_and_stops():
    oversized = response()
    oversized.usage.completion_tokens = 10000
    create = AsyncMock(return_value=oversized)
    subject = meter(create)
    with pytest.raises(BenchmarkStopped):
        await subject(**REQUEST)
    with pytest.raises(BenchmarkStopped):
        await subject(**REQUEST)
    assert create.await_count == 1
    assert subject.calls[0]["estimated_usd"] == pytest.approx(0.1002)
