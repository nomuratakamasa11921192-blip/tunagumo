import asyncio

from src.core.models import EMBEDDING_DIM


class FakeEmbeddingProvider:
    """EmbeddingProviderと同じインターフェースを持つテスト用フェイク。
    実際のOpenAI APIを叩かず、テキストの先頭文字コードから決定的にベクトルを作る
    (同じテキストは常に同じベクトルになるので、検索テストで類似度の傾向を確認できる)。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        vectors = []
        for text in texts:
            seed = (sum(ord(c) for c in text) % 1000) / 1000.0
            vectors.append([seed] * EMBEDDING_DIM)
        return vectors


class FakeLLM:
    """StructuredLLMと同じインターフェースを持つテスト用フェイク。
    実際のAnthropic APIを叩かず、事前にqueueした応答を順番に返す。"""

    def __init__(self, delay: float = 0.0):
        self._structured_queue: list = []
        self._text_queue: list = []
        self.structured_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.delay = delay  # 全呼び出しに付与する遅延(並列実行の検証用)

    def queue_structured(self, obj) -> None:
        self._structured_queue.append(obj)

    def queue_text(self, text: str | Exception) -> None:
        """textにExceptionインスタンスを渡すと、そのdept呼び出し時にraiseされる(部分失敗のテスト用)。"""
        self._text_queue.append(text)

    async def call_structured(self, *, model, system_prompt, user_message, output_model, max_tokens=2048):
        self.structured_calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "output_model": output_model,
            }
        )
        obj = self._structured_queue.pop(0)
        return obj, {"input_tokens": 10, "output_tokens": 10}

    async def generate_text(self, *, model, system_prompt, user_message, max_tokens=4096):
        self.text_calls.append({"model": model, "system_prompt": system_prompt, "user_message": user_message})
        if self.delay:
            await asyncio.sleep(self.delay)
        text = self._text_queue.pop(0)
        if isinstance(text, Exception):
            raise text
        return text, {"input_tokens": 10, "output_tokens": 10}
