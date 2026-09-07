import httpx
import pytest

from src.video.tts import DEFAULT_VOICE, OPENAI_TTS_MODEL, OpenAITTSProvider, TTSError

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_synthesize_sends_expected_request_and_returns_audio_bytes(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        assert url == "https://api.openai.com/v1/audio/speech"
        assert headers["Authorization"] == "Bearer sk-test"
        assert json["model"] == OPENAI_TTS_MODEL
        assert json["input"] == "こんにちは"
        assert json["voice"] == DEFAULT_VOICE
        assert json["response_format"] == "mp3"
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=200, request=request, content=b"fake-mp3-bytes")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAITTSProvider(api_key="sk-test")
    audio = await provider.synthesize("こんにちは")

    assert audio == b"fake-mp3-bytes"


async def test_synthesize_uses_requested_voice(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        assert json["voice"] == "nova"
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=200, request=request, content=b"audio")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAITTSProvider(api_key="sk-test")
    await provider.synthesize("テスト", voice="nova")


async def test_synthesize_raises_on_error_status(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=401, request=request, text="invalid api key")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAITTSProvider(api_key="sk-invalid")
    with pytest.raises(TTSError):
        await provider.synthesize("テスト")


async def test_synthesize_rejects_empty_text(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        raise AssertionError("空テキストの場合はAPIを呼び出すべきではない")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAITTSProvider(api_key="sk-test")
    with pytest.raises(TTSError):
        await provider.synthesize("   ")
