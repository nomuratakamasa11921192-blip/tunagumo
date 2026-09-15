import httpx
import pytest

from src.core import safe_http
from src.core.safe_http import UnsafeURLError, safe_get

_async = pytest.mark.asyncio(loop_scope="session")

_PRIVATE_HOSTS = {"localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1", "internal.example"}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    # 実際のDNSに頼らずに判定する(社内扱いのホスト名だけFalse)
    monkeypatch.setattr(safe_http, "is_public_host", lambda host: host not in _PRIVATE_HOSTS)


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1", "::1", "no-such-host.invalid"])
def test_is_public_host_real_implementation(host, monkeypatch):
    monkeypatch.undo()
    assert safe_http.is_public_host(host) is False


@_async
@pytest.mark.parametrize(
    "url",
    ["http://example.com/a", "file:///etc/passwd", "https://localhost/a", "https://169.254.169.254/latest/meta-data"],
)
async def test_rejects_unsafe_urls_before_connecting(url):
    def handler(request):
        raise AssertionError("接続してはいけない")

    with pytest.raises(UnsafeURLError):
        await safe_get(url, max_bytes=1000, timeout=5, transport=httpx.MockTransport(handler))


@_async
async def test_follows_redirect_only_to_public_hosts():
    def handler(request):
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})
        raise AssertionError("社内ホストに接続してはいけない")

    with pytest.raises(UnsafeURLError):
        await safe_get("https://example.com/a", max_bytes=1000, timeout=5, transport=httpx.MockTransport(handler))


@_async
async def test_follows_public_redirect_and_returns_final_url():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content="こんにちは".encode())

    res = await safe_get("https://example.com/a", max_bytes=1000, timeout=5, transport=httpx.MockTransport(handler))
    assert res.url == "https://example.com/b"
    assert res.content_type == "text/html"
    assert res.text == "こんにちは"


@_async
async def test_rejects_too_many_redirects():
    transport = httpx.MockTransport(lambda req: httpx.Response(302, headers={"location": "/loop"}))
    with pytest.raises(UnsafeURLError, match="リダイレクト"):
        await safe_get("https://example.com/a", max_bytes=1000, timeout=5, transport=transport)


@_async
async def test_rejects_oversized_body():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 2000))
    with pytest.raises(UnsafeURLError, match="大きすぎる"):
        await safe_get("https://example.com/a", max_bytes=1000, timeout=5, transport=transport)


@_async
async def test_text_of_gzip_encoded_response():
    import gzip

    body = gzip.compress("物件のご案内".encode())
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"}, content=body
        )
    )
    res = await safe_get("https://example.com/a", max_bytes=1000, timeout=5, transport=transport)
    assert res.text == "物件のご案内"
