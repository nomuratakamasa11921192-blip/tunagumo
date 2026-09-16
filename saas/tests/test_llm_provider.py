"""文章生成の提供元切り替え(2026-09-16)の検証。Anthropic(Claude)とOpenAIのどちらでも、
呼び出し側から見た振る舞い(構造化出力・本文生成・usageの集計キー)が同じであることを確認する。
実際のAPIには接続せず、フェイクのクライアントを注入する。"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.agent.cost import compute_cost_usd
from src.agent.llm import StructuredLLM, build_llm
from src.core.config import active_llm_model, active_llm_model_light, settings

_async = pytest.mark.asyncio(loop_scope="session")


class Decision(BaseModel):
    verdict: str
    reason: str


class FakeAnthropicMessages:
    def __init__(self, blocks, usage):
        self.blocks = blocks
        self.usage = usage
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.blocks, usage=self.usage)


def _anthropic_client(blocks, usage=None):
    usage = usage or SimpleNamespace(
        input_tokens=1000, output_tokens=200, cache_creation_input_tokens=50, cache_read_input_tokens=30
    )
    messages = FakeAnthropicMessages(blocks, usage)
    return SimpleNamespace(messages=messages), messages


@_async
async def test_anthropic_structured_output_uses_tool_use():
    blocks = [SimpleNamespace(type="tool_use", input={"verdict": "ASSIGN", "reason": "要件が揃った"})]
    client, messages = _anthropic_client(blocks)
    llm = StructuredLLM(client=client, provider="anthropic")

    result, usage = await llm.call_structured(
        model="claude-sonnet-5", system_prompt="あなたは統括AIです", user_message="物件紹介文を作って",
        output_model=Decision,
    )

    assert result.verdict == "ASSIGN"
    sent = messages.calls[0]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["system"] == "あなたは統括AIです"
    assert sent["tools"][0]["name"] == "Decision"
    assert sent["tool_choice"] == {"type": "tool", "name": "Decision"}
    # usageのキー名は提供元が変わっても同じ(cost.pyが依存している)
    assert usage == {"input_tokens": 1000, "output_tokens": 200, "cache_write_tokens": 50, "cache_read_tokens": 30}


@_async
async def test_anthropic_text_generation():
    blocks = [SimpleNamespace(type="text", text="ご希望の条件に合う物件をご案内します。")]
    client, messages = _anthropic_client(blocks)
    llm = StructuredLLM(client=client, provider="anthropic")

    text, usage = await llm.generate_text(model="claude-sonnet-5", system_prompt="広告担当です", user_message="紹介文を")

    assert text == "ご希望の条件に合う物件をご案内します。"
    assert usage["output_tokens"] == 200
    assert "tools" not in messages.calls[0]


@_async
async def test_anthropic_invalid_structure_retries_then_raises():
    from src.agent.llm import LLMOutputError

    blocks = [SimpleNamespace(type="tool_use", input={"verdict": "ASSIGN"})]  # reasonが無い
    client, messages = _anthropic_client(blocks)
    llm = StructuredLLM(client=client, provider="anthropic")

    with pytest.raises(LLMOutputError):
        await llm.call_structured(
            model="claude-sonnet-5", system_prompt="s", user_message="u", output_model=Decision
        )
    assert len(messages.calls) == 3  # 初回+リトライ2回


def test_provider_is_inferred_from_settings_and_models_switch(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_model_light", "")
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-5")
    monkeypatch.setattr(settings, "anthropic_model_light", "claude-haiku-4.5")
    assert build_llm().provider == "anthropic"
    assert active_llm_model() == "claude-sonnet-5"
    assert active_llm_model_light() == "claude-haiku-4.5"

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_model", "gpt-5.6-terra")
    monkeypatch.setattr(settings, "openai_model_light", "gpt-5.6-luna")
    assert build_llm().provider == "openai"
    assert active_llm_model() == "gpt-5.6-terra"

    # LLM_MODELを明示した場合はそちらが優先される(将来のモデル更新用)
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5")
    assert active_llm_model() == "claude-opus-5"


def test_pricing_table_covers_both_providers(config):
    """提供元を切り替えても単価が0にならないこと(単価表に無いモデルはコスト0として扱われるため)。"""
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_write_tokens": 0, "cache_read_tokens": 0}
    for model, expected in (
        ("gpt-5.6-terra", 14.0),   # 2.0 + 12.0
        ("gpt-5.6-luna", 1.4),     # 0.2 + 1.2
        ("claude-sonnet-5", 12.0), # 2.0 + 10.0
        ("claude-haiku-4.5", 6.0), # 1.0 + 5.0
    ):
        assert compute_cost_usd(model, usage, config.pricing) == pytest.approx(expected), model
