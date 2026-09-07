import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_extract_requires_auth(client):
    res = await client.post("/api/property-url/extract", json={"url": "https://example.com/property/1"})
    assert res.status_code == 401


async def test_extract_returns_text_and_notice(client, tenant, monkeypatch):
    async def fake_import(url, **kwargs):
        return {"text": "テスト物件の紹介文", "image_urls": ["https://example.com/a.jpg"]}

    monkeypatch.setattr("src.api.routes.property_url.import_property_url", fake_import)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/property-url/extract", json={"url": "https://example.com/property/1"}, headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["text"] == "テスト物件の紹介文"
    assert body["image_urls"] == ["https://example.com/a.jpg"]
    assert "権利" in body["notice"]


async def test_extract_rejects_when_import_fails(client, tenant, monkeypatch):
    from src.core.property_url_import import PropertyImportError

    async def fake_import(url, **kwargs):
        raise PropertyImportError("取得できませんでした")

    monkeypatch.setattr("src.api.routes.property_url.import_property_url", fake_import)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/property-url/extract", json={"url": "https://example.com/property/1"}, headers=headers
    )
    assert res.status_code == 400
