"""音声合成(TTS)プロバイダの抽象化(Phase 9-2: 音声合成(外部API) ○、ただし従量課金は
別途発生)。OpenAIの音声合成APIを使う: 顧客はPhase 7 RAGで既にOpenAI APIキーを登録できる
ようになっているため、動画機能のためだけに新しいベンダー・新しい顧客キー登録を
増やさずに済む(同じ「顧客自身が実費を負担する」事業モデルのまま)。

src/rag/embeddings.pyのEmbeddingProviderと同じ抽象化パターン(差し替え可能にしておく)。
"""

from typing import Protocol

import httpx

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "alloy"


class TTSProvider(Protocol):
    async def synthesize(self, text: str, *, voice: str = DEFAULT_VOICE) -> bytes:
        """テキストを読み上げた音声データ(mp3)をバイト列で返す。"""
        ...


class TTSError(Exception):
    pass


class OpenAITTSProvider:
    """顧客自身のOpenAI APIキーを使う(src/rag/embeddings.pyのOpenAIEmbeddingProviderと
    同じキーを共用できる)。"""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def synthesize(self, text: str, *, voice: str = DEFAULT_VOICE) -> bytes:
        if not text.strip():
            raise TTSError("空のテキストは音声合成できません")

        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                OPENAI_TTS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": OPENAI_TTS_MODEL,
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                },
            )
        if res.status_code >= 400:
            raise TTSError(f"OpenAI TTS APIエラー(status={res.status_code}): {res.text[:300]}")

        return res.content
