import pytest

from src.core.config import settings
from src.main import docs_urls

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_security_headers_on_api_and_static(client):
    for path in ("/api/sessions", "/index.html"):
        res = await client.get(path)
        assert res.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert res.headers["x-content-type-options"] == "nosniff"
        assert "strict-transport-security" not in res.headers  # 開発環境ではHSTSを付けない


async def test_hsts_only_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    res = await client.get("/index.html")
    assert res.headers["strict-transport-security"].startswith("max-age=")


def test_docs_hidden_in_production():
    assert docs_urls("production") == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert docs_urls("development")["docs_url"] == "/docs"
