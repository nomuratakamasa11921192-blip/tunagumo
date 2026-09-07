import asyncio
import random
from typing import TypeVar

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_API_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0
# tool_useのinputがスキーマに従わないことが稀にある(実際のClaude APIで、配列型の
# フィールドに文字列を返すケースを確認済み)。API呼び出し自体は成功しているので
# _create_with_retryのリトライ対象外。ここだけ別に少数回リトライする。
MAX_VALIDATION_RETRIES = 2

# 429・5xx・接続エラーは一時的なものとして指数バックオフ+ジッターでリトライする(4-3)。
_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
)
# 401(認証)やクレジット不足(400系)は、リトライしても直らないため即座にエラーにする(4-3)。
_NO_RETRY_ERRORS = (
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


async def _create_with_retry(client: AsyncAnthropic, **kwargs):
    last_error: Exception | None = None
    for attempt in range(MAX_API_RETRIES + 1):
        try:
            return await client.messages.create(**kwargs)
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


class StructuredLLM:
    """Claude呼び出しのラッパー。テストではこのクラスをフェイクに差し替える。"""

    def __init__(self, client: AsyncAnthropic | None = None):
        # タイムアウトを明示しないと、ネットワークが一時的に無応答になった場合に
        # 呼び出しが無期限にハングしうる(4-3 5xx/タイムアウトのリトライが発火しない)。
        self._client = client or AsyncAnthropic(timeout=DEFAULT_TIMEOUT_SECONDS)

    async def call_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        output_model: type[ModelT],
        max_tokens: int = 2048,
    ) -> tuple[ModelT, dict]:
        tool_name = "submit_result"
        last_error: Exception | None = None

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            response = await _create_with_retry(
                self._client,
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
                last_error = LLMOutputError("構造化出力（tool_use）が返りませんでした")
                continue

            try:
                result = output_model.model_validate(tool_use.input)
            except ValidationError as e:
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
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(block.text for block in response.content if block.type == "text")
        return text, _extract_usage(response)


def _extract_usage(response) -> dict:
    usage = response.usage
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
