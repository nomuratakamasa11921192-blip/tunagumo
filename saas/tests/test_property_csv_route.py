import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_export_csv_requires_auth(client):
    res = await client.post("/api/property-export/csv", json={"name": "テスト"})
    assert res.status_code == 401


async def test_export_csv_returns_file(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/property-export/csv",
        json={
            "name": "テストマンション101",
            "deal_type": "賃貸",
            "address": "埼玉県春日部市",
            "price": "8万円",
            "image_urls": ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "text/csv; charset=utf-8"
    text = res.content.decode("utf-8-sig")
    assert "テストマンション101" in text
    assert "https://example.com/a.jpg" in text


async def test_csv_fields_requires_auth(client):
    res = await client.get("/api/property-export/csv-fields")
    assert res.status_code == 401


async def test_csv_fields_lists_columns(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.get("/api/property-export/csv-fields", headers=headers)
    assert res.status_code == 200
    assert "物件名" in res.json()["fields"]
