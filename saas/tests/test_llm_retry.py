import anthropic
import httpx
import pytest
from pydantic import BaseModel

from src.agent.llm import LLMFatalError, LLMOutputError, StructuredLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=status_code, request=request)


class _Echo(BaseModel):
    ok: bool


class _WithList(BaseModel):
    items: list[str] = []


class _FakeAnthropicClient:
    """anthropic.AsyncAnthropicの.messages.createだけを模したスタブ。"""

    def __init__(self, side_effects: list):
        self._side_effects = list(side_effects)
        self.call_count = 0

        class _Messages:
            def __init__(self, outer):
                self._outer = outer

            async def create(self, **kwargs):
                self._outer.call_count += 1
                effect = self._outer._side_effects.pop(0)
                if isinstance(effect, Exception):
                    raise effect
                return effect

        self.messages = _Messages(self)


class _FakeResponse:
    def __init__(self, content, usage_input=5, usage_output=5):
        self.content = content

        class _Usage:
            input_tokens = usage_input
            output_tokens = usage_output

        self.usage = _Usage()


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, input_dict):
        self.input = input_dict


async def test_rate_limit_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("src.agent.llm.BASE_BACKOFF_SECONDS", 0.001)
    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=_http_response(429), body=None
    )
    success_response = _FakeResponse(content=[_FakeToolUseBlock({"ok": True})])

    client = _FakeAnthropicClient([rate_limit_error, rate_limit_error, success_response])
    llm = StructuredLLM(client=client)

    result, usage = await llm.call_structured(
        model="claude-sonnet-5",
        system_prompt="test",
        user_message="test",
        output_model=_Echo,
    )

    assert result.ok is True
    assert client.call_count == 3  # 2回失敗して3回目で成功


async def test_authentication_error_does_not_retry():
    auth_error = anthropic.AuthenticationError(
        "invalid api key", response=_http_response(401), body=None
    )
    client = _FakeAnthropicClient([auth_error])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.call_structured(
            model="claude-sonnet-5",
            system_prompt="test",
            user_message="test",
            output_model=_Echo,
        )

    assert client.call_count == 1  # リトライしていないこと


async def test_credit_exhaustion_bad_request_does_not_retry():
    bad_request_error = anthropic.BadRequestError(
        "credit balance too low", response=_http_response(400), body=None
    )
    client = _FakeAnthropicClient([bad_request_error])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.generate_text(model="claude-sonnet-5", system_prompt="test", user_message="test")

    assert client.call_count == 1


async def test_malformed_structured_output_retries_then_succeeds(monkeypatch):
    """実際のClaude APIで、配列型フィールドに文字列を返すことがあると判明した挙動の再現
    (2026-08-19、管理画面の回帰テストを実機で動かして発見)。API呼び出し自体は成功して
    いるので_create_with_retryの対象外だが、model_validateの失敗だけを別途リトライする。"""
    malformed_response = _FakeResponse(content=[_FakeToolUseBlock({"items": "not-a-list"})])
    success_response = _FakeResponse(content=[_FakeToolUseBlock({"items": ["a", "b"]})])

    client = _FakeAnthropicClient([malformed_response, success_response])
    llm = StructuredLLM(client=client)

    result, usage = await llm.call_structured(
        model="claude-sonnet-5",
        system_prompt="test",
        user_message="test",
        output_model=_WithList,
    )

    assert result.items == ["a", "b"]
    assert client.call_count == 2


async def test_malformed_structured_output_exhausts_retries_raises_output_error(monkeypatch):
    monkeypatch.setattr("src.agent.llm.MAX_VALIDATION_RETRIES", 1)
    malformed = _FakeResponse(content=[_FakeToolUseBlock({"items": "not-a-list"})])
    client = _FakeAnthropicClient([malformed, malformed])
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMOutputError):
        await llm.call_structured(
            model="claude-sonnet-5",
            system_prompt="test",
            user_message="test",
            output_model=_WithList,
        )

    assert client.call_count == 2  # 初回 + リトライ1回


async def test_exhausting_all_retries_raises_fatal_error(monkeypatch):
    monkeypatch.setattr("src.agent.llm.BASE_BACKOFF_SECONDS", 0.001)
    monkeypatch.setattr("src.agent.llm.MAX_API_RETRIES", 2)
    errors = [
        anthropic.InternalServerError("server error", response=_http_response(500), body=None)
        for _ in range(3)
    ]
    client = _FakeAnthropicClient(errors)
    llm = StructuredLLM(client=client)

    with pytest.raises(LLMFatalError):
        await llm.generate_text(model="claude-sonnet-5", system_prompt="test", user_message="test")

    assert client.call_count == 3  # 初回 + リトライ2回
