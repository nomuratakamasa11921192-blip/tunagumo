import base64
from types import SimpleNamespace

import httpx
import openai
import pytest

from src.core import flyer_generator, openai_image_client
from src.core.flyer_generator import FlyerGenerationError, _safe_url_fetcher
from src.core.openai_image_client import (
    OpenAIImageClient,
    OpenAIImageError,
    compute_image_cost_usd,
    generated_image_path,
)

_async = pytest.mark.asyncio(loop_scope="session")

_JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture(autouse=True)
def images_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_image_client, "GENERATED_IMAGES_DIR", tmp_path)
    return tmp_path


def _response(with_usage: bool = True):
    usage = (
        SimpleNamespace(
            input_tokens=1100,
            output_tokens=4000,
            input_tokens_details=SimpleNamespace(text_tokens=100, image_tokens=1000),
        )
        if with_usage
        else None
    )
    return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_JPEG).decode())], usage=usage)


class _FakeImages:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._error = error

    async def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        if self._error:
            raise self._error
        return _response()

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))
        return _response()


def _client(images: _FakeImages, transport=None) -> OpenAIImageClient:
    return OpenAIImageClient(api_key="sk-test", client=SimpleNamespace(images=images), transport=transport)


def test_compute_image_cost_uses_official_per_token_prices():
    # text 100*$5 + image 1000*$8 + output 4000*$30 (百万トークンあたり)
    assert compute_image_cost_usd(_response().usage) == pytest.approx((500 + 8000 + 120000) / 1_000_000)
    assert compute_image_cost_usd(None) == 0.0


def test_generated_image_path_rejects_traversal():
    assert generated_image_path("../../etc/passwd") is None
    assert generated_image_path("A" * 32 + ".jpg") is None
    assert generated_image_path("a" * 32 + ".jpg.png") is None
    assert generated_image_path("a" * 32 + ".jpg") is not None


@_async
async def test_generate_image_saves_file_and_records_cost(images_dir):
    images = _FakeImages()
    client = _client(images)

    url = await client.generate_image("物件の外観", quality="draft")

    assert url.startswith("/api/generated-images/")
    assert (images_dir / url.rsplit("/", 1)[1]).read_bytes() == _JPEG
    assert client.last_cost_usd > 0
    assert images.calls[0][1]["quality"] == "low"


@_async
async def test_generate_image_high_quality_maps_to_high():
    images = _FakeImages()
    await _client(images).generate_image("x", quality="high")
    assert images.calls[0][1]["quality"] == "high"


@_async
async def test_generate_image_moderation_block_has_customer_friendly_message():
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    error = openai.BadRequestError(
        "blocked",
        response=httpx.Response(400, request=request),
        body={"code": "moderation_blocked"},
    )
    error.code = "moderation_blocked"
    with pytest.raises(OpenAIImageError, match="指示の表現を変えて"):
        await _client(_FakeImages(error=error)).generate_image("x")


@_async
async def test_edit_image_reads_own_generated_image_from_disk(images_dir):
    name = "b" * 32 + ".jpg"
    (images_dir / name).write_bytes(_JPEG)
    images = _FakeImages()

    url = await _client(images).edit_image(f"/api/generated-images/{name}", "空を青空に")

    assert url.startswith("/api/generated-images/")
    assert images.calls[0][1]["image"] == (name, _JPEG, "image/jpeg")


@_async
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/room.jpg",
        "file:///etc/passwd",
        "https://127.0.0.1/room.jpg",
        "https://localhost/room.jpg",
        "https://169.254.169.254/latest/meta-data",
        "/api/generated-images/../../etc/passwd",
    ],
)
async def test_edit_image_rejects_unsafe_source_urls(url):
    images = _FakeImages()
    with pytest.raises(OpenAIImageError):
        await _client(images).edit_image(url, "空を青空に")
    assert images.calls == []


@_async
async def test_edit_image_rejects_non_image_content(monkeypatch):
    monkeypatch.setattr(openai_image_client, "is_public_host", lambda host: True)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"))
    images = _FakeImages()
    with pytest.raises(OpenAIImageError, match="JPEG"):
        await _client(images, transport).edit_image("https://example.com/room", "空を青空に")
    assert images.calls == []


@_async
async def test_edit_image_does_not_follow_redirects(monkeypatch):
    monkeypatch.setattr(openai_image_client, "is_public_host", lambda host: True)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    with pytest.raises(OpenAIImageError):
        await _client(_FakeImages(), transport).edit_image("https://example.com/room.jpg", "空を青空に")


@_async
async def test_generated_image_route_serves_file_and_404s_otherwise(client, images_dir):
    name = "c" * 32 + ".jpg"
    (images_dir / name).write_bytes(_JPEG)

    ok = await client.get(f"/api/generated-images/{name}")
    assert ok.status_code == 200
    assert ok.content == _JPEG
    assert ok.headers["content-type"] == "image/jpeg"

    assert (await client.get("/api/generated-images/" + "d" * 32 + ".jpg")).status_code == 404
    assert (await client.get("/api/generated-images/..%2F..%2Fetc%2Fpasswd")).status_code == 404


def test_flyer_fetcher_reads_generated_image_from_disk(images_dir):
    name = "e" * 32 + ".jpg"
    (images_dir / name).write_bytes(_JPEG)
    fetched = _safe_url_fetcher(f"{flyer_generator._INTERNAL_BASE_URL}/api/generated-images/{name}")
    assert fetched["string"] == _JPEG


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "http://example.com/a.jpg", "https://127.0.0.1/a.jpg", "https://tsunagumo.invalid/etc/passwd"],
)
def test_flyer_fetcher_rejects_unsafe_urls(url):
    with pytest.raises(FlyerGenerationError):
        _safe_url_fetcher(url)


class _FakeNetworkStream:
    def __init__(self, ip: str):
        self._ip = ip

    def get_extra_info(self, name):
        return (self._ip, 443) if name == "server_addr" else None


@_async
async def test_edit_image_rejects_when_connected_peer_is_private(monkeypatch):
    # 事前のDNS確認は公開IPでも、実際の接続先が社内アドレスなら本文を使わない(DNSリバインディング対策)
    monkeypatch.setattr(openai_image_client, "is_public_host", lambda host: True)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=_JPEG,
            extensions={"network_stream": _FakeNetworkStream("10.0.0.5")},
        )
    )
    images = _FakeImages()
    with pytest.raises(OpenAIImageError):
        await _client(images, transport).edit_image("https://example.com/room.jpg", "空を青空に")
    assert images.calls == []


def test_connected_to_public_address():
    def res(ip):
        return httpx.Response(200, extensions={"network_stream": _FakeNetworkStream(ip)})

    assert openai_image_client.connected_to_public_address(res("142.251.153.119")) is True
    assert openai_image_client.connected_to_public_address(res("127.0.0.1")) is False
    assert openai_image_client.connected_to_public_address(res("169.254.169.254")) is False
    assert openai_image_client.connected_to_public_address(httpx.Response(200)) is True
