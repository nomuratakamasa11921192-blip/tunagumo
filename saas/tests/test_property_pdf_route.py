import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_extract_pdf_requires_auth(client):
    files = {"file": ("floor_plan.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    res = await client.post("/api/property-url/extract-pdf", files=files)
    assert res.status_code == 401


async def test_extract_pdf_returns_text_and_notice(client, tenant, monkeypatch):
    def fake_extract_text(data, mime_type):
        assert mime_type == "application/pdf"
        return "テスト物件の図面テキスト"

    monkeypatch.setattr("src.api.routes.property_url.extract_text", fake_extract_text)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("floor_plan.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    res = await client.post("/api/property-url/extract-pdf", files=files, headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["text"] == "テスト物件の図面テキスト"
    assert "権利" in body["notice"]


async def test_extract_pdf_rejects_when_extraction_fails(client, tenant, monkeypatch):
    from src.rag.extraction import ExtractionError

    def fake_extract_text(data, mime_type):
        raise ExtractionError("文書からテキストを取得できませんでした")

    monkeypatch.setattr("src.api.routes.property_url.extract_text", fake_extract_text)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("floor_plan.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    res = await client.post("/api/property-url/extract-pdf", files=files, headers=headers)

    assert res.status_code == 400


async def test_extract_pdf_rejects_oversized_file(client, tenant, monkeypatch):
    monkeypatch.setattr("src.api.routes.property_url.MAX_FILE_SIZE_BYTES", 10)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("floor_plan.pdf", b"%PDF-1.4 way more than ten bytes", "application/pdf")}
    res = await client.post("/api/property-url/extract-pdf", files=files, headers=headers)

    assert res.status_code == 400
