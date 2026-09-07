import httpx
import pytest

from src.core.property_url_import import PropertyImportError, import_property_url

# 他のDB利用テストファイルとイベントループをずらさないため明示する
# (conftest.py/test_config_per_tenant.pyに既知のpytest-asyncioの癖として記載あり)
pytestmark = pytest.mark.asyncio(loop_scope="session")

SAMPLE_HTML = """
<html>
<head><title>サンプル物件</title></head>
<body>
<nav>ナビゲーション</nav>
<header>ヘッダー</header>
<script>console.log("x")</script>
<style>.x{color:red}</style>
<main>
  <h1>春日部市の賃貸マンション</h1>
  <p>駅から徒歩5分。2LDK、家賃8万円。</p>
  <img src="/images/photo1.jpg">
  <img src="https://cdn.example.com/photo2.jpg">
  <img src="/images/photo1.jpg">
</main>
<footer>フッター</footer>
</body>
</html>
"""

ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_extracts_text_and_image_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW_ALL)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=SAMPLE_HTML)

    result = await import_property_url("https://example.com/property/1", transport=_transport(handler))

    assert "春日部市の賃貸マンション" in result["text"]
    assert "駅から徒歩5分" in result["text"]
    # nav/header/footer/script/styleの中身は本文に含まれないこと
    assert "ナビゲーション" not in result["text"]
    assert "console.log" not in result["text"]
    # 相対URLは絶対URLに解決され、重複は除去されること
    assert result["image_urls"] == [
        "https://example.com/images/photo1.jpg",
        "https://cdn.example.com/photo2.jpg",
    ]


async def test_robots_disallow_rejects_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_DISALLOW_ALL)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=SAMPLE_HTML)

    with pytest.raises(PropertyImportError, match="robots.txt"):
        await import_property_url("https://example.com/property/1", transport=_transport(handler))


async def test_missing_robots_txt_defaults_to_allowed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=SAMPLE_HTML)

    result = await import_property_url("https://example.com/property/1", transport=_transport(handler))
    assert "春日部市の賃貸マンション" in result["text"]


async def test_rejects_invalid_url_scheme():
    with pytest.raises(PropertyImportError):
        await import_property_url("ftp://example.com/file")


async def test_rejects_non_html_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW_ALL)
        return httpx.Response(200, headers={"content-type": "application/json"}, text="{}")

    with pytest.raises(PropertyImportError, match="HTML"):
        await import_property_url("https://example.com/data.json", transport=_transport(handler))


async def test_rejects_http_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW_ALL)
        return httpx.Response(404, headers={"content-type": "text/html"}, text="not found")

    with pytest.raises(PropertyImportError, match="404"):
        await import_property_url("https://example.com/gone", transport=_transport(handler))
