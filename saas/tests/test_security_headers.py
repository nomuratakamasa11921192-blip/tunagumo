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


async def test_csp_blocks_external_script_and_exfiltration(client):
    """画面は顧客のAPIキーをlocalStorageに置くため、万一の文字列混入(XSS)に備えて
    外部スクリプトの読み込みと外部への送信をCSPで塞いでいること。"""
    csp = (await client.get("/index.html")).headers["content-security-policy"]
    directives = dict(
        (d.strip().split(" ", 1) + [""])[:2] for d in csp.split(";") if d.strip()
    )

    assert directives["default-src"] == "'self'"
    # 外部ドメインのスクリプトを読み込ませない(インラインは画面の現行構造上必要)
    assert directives["script-src"] == "'self' 'unsafe-inline'"
    # fetch等でキーを外部へ送り出せないようにする
    assert directives["connect-src"] == "'self'"
    assert directives["base-uri"] == "'none'"
    assert directives["form-action"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"


async def test_csp_allows_same_origin_images_and_higgsfield_videos(client):
    """生成画像は同一オリジン配信、動画はHiggsfieldのCDNから直接再生するため、
    画像は自分自身のみ・動画はhttpsを許可していること(誤って締めすぎると表示が消える)。"""
    csp = (await client.get("/index.html")).headers["content-security-policy"]

    assert "img-src 'self' data: blob:" in csp
    assert "media-src 'self' https: blob:" in csp
