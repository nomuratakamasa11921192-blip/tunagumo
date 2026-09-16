import asyncio
import json
import random
from typing import TypeVar

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from src.core.config import active_llm_model, active_llm_model_light, settings

ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_API_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0
# 構造化出力がスキーマに従わないことが稀にある(Anthropic時代に、配列型のフィールドに
# 文字列を返すケースを確認済み)。OpenAIのStructured Outputs(strict)ではほぼ発火しない
# はずだが、出力が途中で切れた場合などに備えて残す。API呼び出し自体は成功しているので
# _create_with_retryのリトライ対象外。ここだけ別に少数回リトライする。
MAX_VALIDATION_RETRIES = 2

# 429・5xx・接続エラーは一時的なものとして指数バックオフ+ジッターでリトライする(4-3)。
_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)
# 401(認証)やクレジット不足(400系)は、リトライしても直らないため即座にエラーにする(4-3)。
_NO_RETRY_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.ConflictError,
    openai.UnprocessableEntityError,
)


# Anthropic側の対応するエラー(2026-09-16、提供元を設定で切り替えられるようにした)
_ANTHROPIC_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)
_ANTHROPIC_NO_RETRY_ERRORS = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
    anthropic.ConflictError,
    anthropic.UnprocessableEntityError,
)


class LLMOutputError(Exception):
    pass


class LLMFatalError(Exception):
    """401・クレジット不足など、リトライしても解決しないエラー。呼び出し元でリトライしないこと。"""

    def __init__(self, message: str, original: Exception):
        super().__init__(message)
        self.original = original


async def _call_with_retry(create, *, retryable, no_retry, **kwargs):
    last_error: Exception | None = None
    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return await create(**kwargs)
        except no_retry as e:
            raise LLMFatalError(f"リトライ不可のエラー: {type(e).__name__}: {e}", e) from e
        except retryable as e:
            last_error = e
            if attempt >= MAX_API_RETRIES:
                break
            backoff = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(backoff)
    raise LLMFatalError(
        f"{MAX_API_RETRIES}回リトライしましたが失敗しました: {type(last_error).__name__}: {last_error}",
        last_error,
    )


async def _create_with_retry(client: AsyncOpenAI, **kwargs):
    """OpenAIのchat.completions(画像クライアント等からも使う従来どおりの入口)。"""
    return await _call_with_retry(
        client.chat.completions.create, retryable=_RETRYABLE_ERRORS, no_retry=_NO_RETRY_ERRORS, **kwargs
    )


DEFAULT_TIMEOUT_SECONDS = 60.0


def _base_request(*, model: str, system_prompt: str, user_message: str, max_tokens: int) -> dict:
    request = {
        "model": model,
        # 推論モデルでは旧来のmax_tokensは受け付けられず、max_completion_tokensを使う。
        # 推論トークンもこの上限に含まれる点に注意(OPENAI_REASONING_EFFORTで抑える)。
        "max_completion_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if settings.openai_reasoning_effort:
        request["reasoning_effort"] = settings.openai_reasoning_effort
    return request


class StructuredLLM:
    """文章生成の呼び出しラッパー。提供元(Anthropic / OpenAI)の違いをここで吸収する。
    呼び出し側はcall_structured / generate_textだけを使えばよく、テストではこのクラスごと
    フェイクに差し替える。

    提供元は設定(LLM_PROVIDER)で切り替える。切り替えても、使うモデル名は
    src/core/config.pyのactive_llm_model()が返し、単価表(config/*.yaml)はモデル名で
    引くため、コードを変えずに行き来できる。
    """

    def __init__(self, client=None, provider: str | None = None):
        # タイムアウトを明示しないと、ネットワークが一時的に無応答になった場合に
        # 呼び出しが無期限にハングしうる(4-3 5xx/タイムアウトのリトライが発火しない)。
        # 既定のSDK内部リトライは切り、リトライ方針は_call_with_retryに一本化する。
        # キー未設定だとクライアント生成時点で例外になるSDKがあるため、注入が無い場合は
        # 初回呼び出しまで生成を遅らせる(キー無しでもグラフの組み立て自体はできるように)。
        self._injected_client = client
        if provider is not None:
            self._provider = provider
        elif isinstance(client, AsyncAnthropic):
            self._provider = "anthropic"
        elif isinstance(client, AsyncOpenAI):
            self._provider = "openai"
        else:
            self._provider = settings.llm_provider

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def _client(self):
        if self._injected_client is None:
            if self._provider == "anthropic":
                self._injected_client = AsyncAnthropic(
                    api_key=settings.anthropic_api_key or None, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=0
                )
            else:
                self._injected_client = AsyncOpenAI(
                    api_key=settings.openai_api_key or None, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=0
                )
        return self._injected_client

    async def call_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        output_model: type[ModelT],
        max_tokens: int = 2048,
    ) -> tuple[ModelT, dict]:
        last_error: Exception | None = None

        for _ in range(MAX_VALIDATION_RETRIES + 1):
            if self._provider == "anthropic":
                response, payload, refusal = await self._structured_anthropic(
                    model=model, system_prompt=system_prompt, user_message=user_message,
                    output_model=output_model, max_tokens=max_tokens,
                )
            else:
                response, payload, refusal = await self._structured_openai(
                    model=model, system_prompt=system_prompt, user_message=user_message,
                    output_model=output_model, max_tokens=max_tokens,
                )

            if payload is None:
                last_error = LLMOutputError(f"構造化出力が返りませんでした(refusal={refusal!r})")
                continue
            try:
                result = output_model.model_validate(payload)
            except (ValidationError, json.JSONDecodeError) as e:
                last_error = e
                continue
            return result, _extract_usage(response)

        raise LLMOutputError(
            f"構造化出力の形式が{MAX_VALIDATION_RETRIES + 1}回とも不正でした: {last_error}"
        ) from last_error

    async def _structured_openai(self, *, model, system_prompt, user_message, output_model, max_tokens):
        """OpenAIのStructured Outputs(strict)。Pydanticのスキーマをそのまま渡す。"""
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": output_model.__name__,
                "schema": to_strict_json_schema(output_model),
                "strict": True,
            },
        }
        response = await _create_with_retry(
            self._client,
            **_base_request(model=model, system_prompt=system_prompt, user_message=user_message, max_tokens=max_tokens),
            response_format=response_format,
        )
        message = response.choices[0].message if response.choices else None
        refusal = getattr(message, "refusal", None) if message else None
        if message is None or refusal or not message.content:
            return response, None, refusal
        return response, json.loads(message.content), None

    async def _structured_anthropic(self, *, model, system_prompt, user_message, output_model, max_tokens):
        """Anthropicはtool_use(道具呼び出し)で構造化出力を得る。スキーマは同じPydanticモデルから作る。"""
        tool_name = output_model.__name__
        response = await _call_with_retry(
            self._client.messages.create,
            retryable=_ANTHROPIC_RETRYABLE_ERRORS,
            no_retry=_ANTHROPIC_NO_RETRY_ERRORS,
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[
                {
                    "name": tool_name,
                    "description": "構造化された判定結果を返す",
                    "input_schema": output_model.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            return response, None, None
        return response, tool_use.input, None

    async def generate_text(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
    ) -> tuple[str, dict]:
        if self._provider == "anthropic":
            response = await _call_with_retry(
                self._client.messages.create,
                retryable=_ANTHROPIC_RETRYABLE_ERRORS,
                no_retry=_ANTHROPIC_NO_RETRY_ERRORS,
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = "".join(block.text for block in response.content if block.type == "text")
            return text, _extract_usage(response)

        response = await _create_with_retry(
            self._client,
            **_base_request(model=model, system_prompt=system_prompt, user_message=user_message, max_tokens=max_tokens),
        )
        text = (response.choices[0].message.content or "") if response.choices else ""
        return text, _extract_usage(response)


def build_llm() -> StructuredLLM:
    """設定(LLM_PROVIDER)に従ったLLMを作る。呼び出し側で提供元を意識しないための入口。"""
    return StructuredLLM()


def _extract_usage(response) -> dict:
    """提供元ごとのusageを、cost.pyが前提とするキー名(input/output/cache_write/cache_read)に揃える。

    - Anthropic: input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens。
      input_tokensにキャッシュ分は含まれない。
    - OpenAI: prompt_tokensがキャッシュ読み込み・書き込み分を**含んだ**総数で返るため、
      そのまま単価を掛けると二重計上になる。input_tokensにはキャッシュ以外の分だけを入れる。
      (2026-09-15にhttps://developers.openai.com/api/docs/pricing で「cache writes」列と
      SDKのprompt_tokens_details.cache_write_tokensの存在を確認済み。返らないモデルでは0)
    """
    usage = response.usage
    if hasattr(usage, "prompt_tokens"):  # OpenAI
        details = getattr(usage, "prompt_tokens_details", None)
        cache_read = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        cache_write = (getattr(details, "cache_write_tokens", 0) or 0) if details else 0
        prompt_tokens = usage.prompt_tokens or 0
        return {
            "input_tokens": max(prompt_tokens - cache_read - cache_write, 0),
            "output_tokens": usage.completion_tokens or 0,
            "cache_write_tokens": cache_write,
            "cache_read_tokens": cache_read,
        }

    return {  # Anthropic
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
