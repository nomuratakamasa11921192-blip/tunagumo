import asyncio
import json
import random
from typing import TypeVar

import openai
from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from src.core.config import settings

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


class LLMOutputError(Exception):
    pass


class LLMFatalError(Exception):
    """401・クレジット不足など、リトライしても解決しないエラー。呼び出し元でリトライしないこと。"""

    def __init__(self, message: str, original: Exception):
        super().__init__(message)
        self.original = original


async def _create_with_retry(client: AsyncOpenAI, **kwargs):
    last_error: Exception | None = None
    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return await client.chat.completions.create(**kwargs)
        except _NO_RETRY_ERRORS as e:
            raise LLMFatalError(f"リトライ不可のエラー: {type(e).__name__}: {e}", e) from e
        except _RETRYABLE_ERRORS as e:
            last_error = e
            if attempt >= MAX_API_RETRIES:
                break
            backoff = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(backoff)
    raise LLMFatalError(
        f"{MAX_API_RETRIES}回リトライしましたが失敗しました: {type(last_error).__name__}: {last_error}",
        last_error,
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
    """OpenAI呼び出しのラッパー。テストではこのクラスをフェイクに差し替える。"""

    def __init__(self, client: AsyncOpenAI | None = None):
        # タイムアウトを明示しないと、ネットワークが一時的に無応答になった場合に
        # 呼び出しが無期限にハングしうる(4-3 5xx/タイムアウトのリトライが発火しない)。
        # 既定のSDK内部リトライは切り、リトライ方針は_create_with_retryに一本化する。
        # AsyncOpenAIはキー未設定だと生成時点で例外を出すため、注入が無い場合は
        # 初回呼び出しまで生成を遅らせる(キー無しでもグラフの組み立て自体はできるように)。
        self._injected_client = client

    @property
    def _client(self) -> AsyncOpenAI:
        if self._injected_client is None:
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
        # Structured Outputs(strict)。Pydanticのスキーマを、OpenAI公式SDKが
        # chat.completions.parse()内部で使うのと同じ変換でstrict形式にして渡す。
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": output_model.__name__,
                "schema": to_strict_json_schema(output_model),
                "strict": True,
            },
        }
        last_error: Exception | None = None

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            response = await _create_with_retry(
                self._client,
                **_base_request(
                    model=model, system_prompt=system_prompt, user_message=user_message, max_tokens=max_tokens
                ),
                response_format=response_format,
            )

            message = response.choices[0].message if response.choices else None
            if message is None or getattr(message, "refusal", None) or not message.content:
                reason = getattr(message, "refusal", None) if message else None
                last_error = LLMOutputError(f"構造化出力が返りませんでした(refusal={reason!r})")
                continue

            try:
                result = output_model.model_validate(json.loads(message.content))
            except (ValidationError, json.JSONDecodeError) as e:
                last_error = e
                continue

            return result, _extract_usage(response)

        raise LLMOutputError(
            f"構造化出力の形式が{MAX_VALIDATION_RETRIES + 1}回とも不正でした: {last_error}"
        ) from last_error

    async def generate_text(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
    ) -> tuple[str, dict]:
        response = await _create_with_retry(
            self._client,
            **_base_request(model=model, system_prompt=system_prompt, user_message=user_message, max_tokens=max_tokens),
        )

        text = (response.choices[0].message.content or "") if response.choices else ""
        return text, _extract_usage(response)


def _extract_usage(response) -> dict:
    """OpenAIのusageを、cost.pyが前提とするキー名に変換する。

    Anthropicと違い、OpenAIのprompt_tokensはキャッシュ読み込み・書き込み分を
    **含んだ**総数で返る。そのまま単価を掛けると二重計上になるため、input_tokensには
    キャッシュに関係しない分だけを入れる。

    指示書作成時点ではOpenAIに「キャッシュ書き込み」課金は無い想定だったが、
    2026-09-15にhttps://developers.openai.com/api/docs/pricing で「cache writes」列の
    存在と、SDKのprompt_tokens_details.cache_write_tokensを確認したため、実値を使う
    (返らないモデルでは0になる)。
    """
    usage = response.usage
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
