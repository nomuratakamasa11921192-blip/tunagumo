import pytest

from src.agent.nodes import ceo_office_node
from src.agent.schemas import Triage
from src.agent.state import new_state
from tests.fakes import FakeLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_cost_usd_accumulates_from_greeting_call(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))

    state = new_state("t", "s", "u", "こんにちは")
    result = await ceo_office_node(state, app_config=config, llm=llm)

    rate = config.pricing.models[config.departments["ceo_office"].triage_model or config.departments["ceo_office"].model]
    expected = (10 * rate.input_per_mtok + 10 * rate.output_per_mtok) / 1_000_000
    assert result["cost_usd"] == expected
    assert result["cost_usd"] > 0


async def test_budget_stopper_halts_before_calling_llm(config):
    config.limits.max_budget_usd = 0.01

    llm = FakeLLM()  # 何もqueueしない: 呼ばれたらIndexErrorで即座に分かる

    state = new_state("t", "s", "u", "何か作って")
    state["cost_usd"] = 0.02  # 既に予算超過

    result = await ceo_office_node(state, app_config=config, llm=llm)

    assert result["status"] == "HALTED"
    assert "予算上限" in result["error_message"]
    assert llm.structured_calls == []  # LLMを呼ばずに止まったこと


async def test_total_steps_stopper_halts_before_calling_llm(config):
    config.limits.max_total_steps = 5

    llm = FakeLLM()

    state = new_state("t", "s", "u", "何か作って")
    state["total_steps"] = 5

    result = await ceo_office_node(state, app_config=config, llm=llm)

    assert result["status"] == "HALTED"
    assert "ステップ数上限" in result["error_message"]
    assert llm.structured_calls == []


async def test_retry_count_stopper_halts_as_last_resort_safety_net(config):
    config.limits.max_retries_per_dept = 2

    llm = FakeLLM()

    state = new_state("t", "s", "u", "何か作って")
    state["goal"] = "既に分類済み"
    state["retry_counts"] = {"copy_dept": 3}  # 上限(2)を超えている想定外の状態

    result = await ceo_office_node(state, app_config=config, llm=llm)

    assert result["status"] == "HALTED"
    assert "差し戻し回数が上限を超えました" in result["error_message"]
    assert llm.structured_calls == []
