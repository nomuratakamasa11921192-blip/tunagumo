import httpx
import pytest

from src.video.stt import OPENAI_TRANSCRIPTION_MODEL, OPENAI_TRANSCRIPTION_URL, OpenAISTTProvider, STTError

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_transcribe_sends_expected_request_and_returns_srt_text(monkeypatch):
    async def fake_post(self, url, headers=None, data=None, files=None):
        assert url == OPENAI_TRANSCRIPTION_URL
        assert headers["Authorization"] == "Bearer sk-test"
        assert data["model"] == OPENAI_TRANSCRIPTION_MODEL
        assert data["response_format"] == "srt"
        assert files["file"][0] == "audio.wav"
        request = httpx.Request("POST", url)
        return httpx.Response(
            status_code=200, request=request, text="1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n"
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAISTTProvider(api_key="sk-test")
    srt = await provider.transcribe_to_srt(b"fake-wav-bytes")

    assert "こんにちは" in srt


async def test_transcribe_raises_on_error_status(monkeypatch):
    async def fake_post(self, url, headers=None, data=None, files=None):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=401, request=request, text="invalid api key")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAISTTProvider(api_key="sk-invalid")
    with pytest.raises(STTError):
        await provider.transcribe_to_srt(b"fake-wav-bytes")


async def test_transcribe_rejects_empty_audio(monkeypatch):
    async def fake_post(self, url, headers=None, data=None, files=None):
        raise AssertionError("空データの場合はAPIを呼び出すべきではない")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAISTTProvider(api_key="sk-test")
    with pytest.raises(STTError):
        await provider.transcribe_to_srt(b"")
