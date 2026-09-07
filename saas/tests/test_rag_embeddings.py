import httpx
import pytest

from src.core.models import EMBEDDING_DIM
from src.rag.embeddings import EmbeddingError, OpenAIEmbeddingProvider

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _fake_vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIM


async def test_embed_empty_list_returns_empty_without_calling_api(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        raise AssertionError("空リストの場合はAPIを呼び出すべきではない")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    result = await provider.embed([])

    assert result == []


async def test_embed_sends_texts_and_returns_vectors_in_order(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        assert url == "https://api.openai.com/v1/embeddings"
        assert headers["Authorization"] == "Bearer sk-test"
        assert json["model"] == "text-embedding-3-small"
        assert json["input"] == ["テキストA", "テキストB"]
        request = httpx.Request("POST", url)
        # OpenAIの応答順が入れ替わっていても、index基準で並べ直されることを確認するため
        # わざと逆順で返す
        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "data": [
                    {"index": 1, "embedding": _fake_vector(0.2)},
                    {"index": 0, "embedding": _fake_vector(0.1)},
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    vectors = await provider.embed(["テキストA", "テキストB"])

    assert len(vectors) == 2
    assert vectors[0][0] == 0.1
    assert vectors[1][0] == 0.2


async def test_embed_raises_on_error_status(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=401, request=request, text="invalid api key")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAIEmbeddingProvider(api_key="sk-invalid")
    with pytest.raises(EmbeddingError):
        await provider.embed(["テキスト"])


async def test_embed_raises_on_dimension_mismatch(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            status_code=200,
            request=request,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    with pytest.raises(EmbeddingError):
        await provider.embed(["テキスト"])
