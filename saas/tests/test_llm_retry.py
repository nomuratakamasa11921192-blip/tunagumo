import openai
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from src.agent.llm import LLMFatalError, LLMOutputError, StructuredLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx.Response(status_code=status_code, request=request)


class _Echo(BaseModel):
    ok: bool


class _WithList(BaseModel):
    items: list[str] = []


class _FakeOpenAIClient:
    """openai.AsyncOpenAIの.chat.completions.createだけを模したスタブ。"""

    def __init__(self, side_effects: list):
        self._side_effects = list(side_effects)
        self.call_count = 0
        self.last_kwargs: dict = {}

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            async def create(self, **kwargs):
                self._outer.call_count += 1
                self._outer.last_kwargs = kwargs
                effect = self._outer._side_effects.pop(0)
                if isinstance(effect, Exception):
                    raise effect
                return effect

        self.chat = SimpleNamespace(completions=_Completions(self))


def _fake_response(content: str | None, *, prompt_tokens=5, completion_tokens=5, cached=0, cache_write=0):
    """chat.completions.createの戻り値のうち、StructuredLLMが読む部分だけを再現する。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=cache_write),
        ),
    )


async def test_rate_limit_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("src.agent.llm.BASE_BACKOFF_SECONDS", 0.001)
    rate_limit_error = openai.RateLimitError(
        "rate limited", response=_http_response(429), body=None
    )
    success_response = _fake_response(json.dumps({"ok": True}))

    client = _FakeOpenAIClient([rate_limit_error, rate_limit_error, success_response])
    llm = StructuredLLM(client=client)

    result, usage = await llm.call_structured(
        model="gpt-5.6-terra",
        system_prompt="test",
        user_message="test",
        output_model=_Echo,
    )

    assert result.ok is True
    assert client.call_count == 3  # 2回失敗して3回目で成功


async def test_authentication_error_does_not_retry():
    auth_error = openai.AuthenticationError(
        "invalid api key", response=_http_response(401), body=None
    )
    client = _FakeOpenAIClient([auth_error])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.call_structured(
            model="gpt-5.6-terra",
            system_prompt="test",
            user_message="test",
            output_model=_Echo,
        )

    assert client.call_count == 1  # リトライしていないこと


async def test_credit_exhaustion_bad_request_does_not_retry():
    bad_request_error = openai.BadRequestError(
        "credit balance too low", response=_http_response(400), body=None
    )
    client = _FakeOpenAIClient([bad_request_error])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.generate_text(model="gpt-5.6-terra", system_prompt="test", user_message="test")

    assert client.call_count == 1


async def test_malformed_structured_output_retries_then_succeeds(monkeypatch):
    """実際のClaude API(移行前)で、配列型フィールドに文字列を返すことがあると判明した挙動の再現
    (2026-08-19、管理画面の回帰テストを実機で動かして発見)。API呼び出し自体は成功して
    いるので_create_with_retryの対象外だが、model_validateの失敗だけを別途リトライする。"""
    malformed_response = _fake_response(json.dumps({"items": "not-a-list"}))
    success_response = _fake_response(json.dumps({"items": ["a", "b"]}))

    client = _FakeOpenAIClient([malformed_response, success_response])
    llm = StructuredLLM(client=client)

    result, usage = await llm.call_structured(
        model="gpt-5.6-terra",
        system_prompt="test",
        user_message="test",
        output_model=_WithList,
    )

    assert result.items == ["a", "b"]
    assert client.call_count == 2


async def test_malformed_structured_output_exhausts_retries_raises_output_error(monkeypatch):
    monkeypatch.setattr("src.agent.llm.MAX_VALIDATION_RETRIES", 1)
    malformed = _fake_response(json.dumps({"items": "not-a-list"}))
    client = _FakeOpenAIClient([malformed, malformed])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMOutputError):
        await llm.call_structured(
            model="gpt-5.6-terra",
            system_prompt="test",
            user_message="test",
            output_model=_WithList,
        )

    assert client.call_count == 2  # 初回 + リトライ1回


async def test_exhausting_all_retries_raises_fatal_error(monkeypatch):
    monkeypatch.setattr("src.agent.llm.BASE_BACKOFF_SECONDS", 0.001)
    monkeypatch.setattr("src.agent.llm.MAX_API_RETRIES", 2)
    errors = [
        openai.InternalServerError("server error", response=_http_response(500), body=None)
        for _ in range(3)
    ]
    client = _FakeOpenAIClient(errors)
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.generate_text(model="gpt-5.6-terra", system_prompt="test", user_message="test")

    assert client.call_count == 3  # 初回 + リトライ2回


async def test_structured_call_sends_strict_json_schema():
    """Anthropicのtool_useに代わり、OpenAIのStructured Outputs(strict)を使っていること。"""
    client = _FakeOpenAIClient([_fake_response(json.dumps({"ok": True}))])
    llm = StructuredLLM(client=client)

    await llm.call_structured(model="gpt-5.6-terra", system_prompt="sys", user_message="u", output_model=_Echo)

    fmt = client.last_kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert client.last_kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert "max_completion_tokens" in client.last_kwargs


async def test_refusal_is_treated_as_output_error_and_retried():
    refusal = _fake_response(None)
    refusal.choices[0].message.refusal = "お答えできません"
    client = _FakeOpenAIClient([refusal, _fake_response(json.dumps({"ok": True}))])
    llm = StructuredLLM(client=client)

    result, _ = await llm.call_structured(model="gpt-5.6-terra", system_prompt="s", user_message="u", output_model=_Echo)

    assert result.ok is True
    assert client.call_count == 2


async def test_usage_excludes_cached_tokens_from_input_to_avoid_double_billing():
    """OpenAIのprompt_tokensはキャッシュ分を含む総数なので、cost.pyで二重計上しないよう
    input_tokensからは差し引く。返り値のキー名はcost.pyが依存しているので変えない。"""
    response = _fake_response("こんにちは", prompt_tokens=1000, completion_tokens=50, cached=600, cache_write=100)
    client = _FakeOpenAIClient([response])
    llm = StructuredLLM(client=client)

    text, usage = await llm.generate_text(model="gpt-5.6-terra", system_prompt="s", user_message="u")

    assert text == "こんにちは"
    assert usage == {
        "input_tokens": 300,
        "output_tokens": 50,
        "cache_write_tokens": 100,
        "cache_read_tokens": 600,
    }


async def test_llm_can_be_constructed_without_api_key(monkeypatch):
    """キー未設定でもグラフの組み立て(StructuredLLM())自体は失敗しないこと。"""
    monkeypatch.setattr("src.agent.llm.settings.openai_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    StructuredLLM()
