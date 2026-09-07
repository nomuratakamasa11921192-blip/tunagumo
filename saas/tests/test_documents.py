import asyncio

import pytest

from src.api.deps import get_embedding_provider
from src.main import app
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def with_fake_embedding_provider():
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    yield
    app.dependency_overrides.pop(get_embedding_provider, None)


async def _wait_until_terminal(client, document_id, headers) -> dict:
    """バックグラウンドの取り込みが終わる(ACTIVE/FAILED)まで待つ。
    テナントfixtureの後片付け(DELETE)と、取り込み中のチャンク書き込みが
    競合しないよう、アップロードするテストは必ずこれで完了を待ってから終わる。"""
    for _ in range(100):
        detail = await client.get(f"/api/documents/{document_id}", headers=headers)
        body = detail.json()
        if body["status"] in ("ACTIVE", "FAILED"):
            return body
        await asyncio.sleep(0.02)
    raise TimeoutError("文書の取り込みが完了しませんでした")


async def test_upload_without_openai_key_returns_402(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("rules.txt", b"content", "text/plain")}

    res = await client.post("/api/documents", files=files, headers=headers)

    assert res.status_code == 402


async def test_upload_txt_document_becomes_active(client, tenant, with_fake_embedding_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("rules.txt", "第1条 これはテスト用の社内規程です。".encode("utf-8"), "text/plain")}

    res = await client.post("/api/documents", files=files, data={"title": "社内規程"}, headers=headers)
    assert res.status_code == 202
    document_id = res.json()["document_id"]
    assert res.json()["status"] == "PROCESSING"

    body = await _wait_until_terminal(client, document_id, headers)

    assert body["status"] == "ACTIVE"
    assert body["title"] == "社内規程"
    assert body["filename"] == "rules.txt"


async def test_upload_unsupported_mime_type_returns_400(client, tenant, with_fake_embedding_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("image.png", b"\x89PNG", "image/png")}

    res = await client.post("/api/documents", files=files, headers=headers)

    assert res.status_code == 400


async def test_upload_duplicate_content_returns_409(client, tenant, with_fake_embedding_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("a.txt", b"same content here", "text/plain")}

    first = await client.post("/api/documents", files=files, headers=headers)
    assert first.status_code == 202

    files2 = {"file": ("b.txt", b"same content here", "text/plain")}
    second = await client.post("/api/documents", files=files2, headers=headers)

    assert second.status_code == 409

    await _wait_until_terminal(client, first.json()["document_id"], headers)


async def test_list_documents_only_returns_own_tenant(client, tenant, with_fake_embedding_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("c.txt", b"unique content for listing test", "text/plain")}
    created = await client.post("/api/documents", files=files, headers=headers)

    res = await client.get("/api/documents", headers=headers)

    assert res.status_code == 200
    assert len(res.json()["documents"]) >= 1
    assert all("id" in d for d in res.json()["documents"])

    await _wait_until_terminal(client, created.json()["document_id"], headers)


async def test_archive_document_sets_status_archived(client, tenant, with_fake_embedding_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("d.txt", b"content to archive", "text/plain")}
    created = await client.post("/api/documents", files=files, headers=headers)
    document_id = created.json()["document_id"]

    # バックグラウンドの取り込み(チャンク作成)が終わってから削除する。先に削除すると、
    # 取り込み中のチャンク書き込みとテナントfixtureの後片付け(DELETE)が競合しうるため。
    await _wait_until_terminal(client, document_id, headers)

    res = await client.delete(f"/api/documents/{document_id}", headers=headers)
    assert res.status_code == 204

    detail = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert detail.json()["status"] == "ARCHIVED"


async def test_get_nonexistent_document_returns_404(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.get("/api/documents/00000000-0000-0000-0000-000000000000", headers=headers)
    assert res.status_code == 404
