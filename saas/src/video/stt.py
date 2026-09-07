"""音声認識(STT)。リール自動編集(2位)の字幕自動生成に使う。

顧客は既にPhase 7 RAG/tts.pyでOpenAI APIキーを登録できるようになっているため、
この機能のためだけに新しいベンダー・新しい顧客キー登録を増やさずに済む
(src/video/tts.pyと同じ「顧客自身が実費を負担する」事業モデルのまま)。
"""

from typing import Protocol

import httpx

OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"


class STTProvider(Protocol):
    async def transcribe_to_srt(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> str:
        """音声データを認識し、SRT形式の字幕テキストを返す。"""
        ...


class STTError(Exception):
    pass


class OpenAISTTProvider:
    """顧客自身のOpenAI APIキーを使う(src/video/tts.pyのOpenAITTSProviderと同じキーを共用できる)。"""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def transcribe_to_srt(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> str:
        if not audio_bytes:
            raise STTError("空の音声データは認識できません")

        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(
                OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                data={"model": OPENAI_TRANSCRIPTION_MODEL, "response_format": "srt"},
                files={"file": (filename, audio_bytes, "audio/wav")},
            )
        if res.status_code >= 400:
            raise STTError(f"OpenAI 音声認識APIエラー(status={res.status_code}): {res.text[:300]}")

        return res.text
