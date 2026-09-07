import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.models import Chunk, Document
from src.rag.expiry_scan import EXPIRY_WARNING_DAYS

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


async def _make_document(*, tenant_id, title, valid_until, status="ACTIVE") -> Document:
    async with async_session_factory() as db:
        doc = Document(
            tenant_id=tenant_id,
            title=title,
            filename=f"{title}.txt",
            mime_type="text/plain",
            sha256=uuid.uuid4().hex,
            status=status,
            valid_until=valid_until,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        return doc


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == tenant_id))).scalars().all()
        if doc_ids:
            await db.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await db.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await db.commit()


async def test_requires_admin_auth(client):
    res = await client.get("/admin/documents/expiring")
    assert res.status_code == 401


async def test_lists_documents_within_warning_window(client, tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="就業規則", valid_until=date.today() + timedelta(days=EXPIRY_WARNING_DAYS - 1)
    )
    try:
        res = await client.get("/admin/documents/expiring", headers=admin_headers())
        assert res.status_code == 200
        ids = [d["document_id"] for d in res.json()["documents"]]
        assert str(doc.id) in ids
    finally:
        await _cleanup(tenant["id"])


async def test_includes_already_expired_documents(client, tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="旧料金表", valid_until=date.today() - timedelta(days=5), status="EXPIRED"
    )
    try:
        res = await client.get("/admin/documents/expiring", headers=admin_headers())
        assert res.status_code == 200
        row = next(d for d in res.json()["documents"] if d["document_id"] == str(doc.id))
        assert row["days_left"] == -5
    finally:
        await _cleanup(tenant["id"])


async def test_excludes_documents_far_from_expiry(client, tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="まだ余裕のある資料", valid_until=date.today() + timedelta(days=EXPIRY_WARNING_DAYS + 10)
    )
    try:
        res = await client.get("/admin/documents/expiring", headers=admin_headers())
        assert res.status_code == 200
        ids = [d["document_id"] for d in res.json()["documents"]]
        assert str(doc.id) not in ids
    finally:
        await _cleanup(tenant["id"])


async def test_excludes_archived_and_superseded_documents(client, tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="削除済みの資料", valid_until=date.today() + timedelta(days=1), status="ARCHIVED"
    )
    try:
        res = await client.get("/admin/documents/expiring", headers=admin_headers())
        assert res.status_code == 200
        ids = [d["document_id"] for d in res.json()["documents"]]
        assert str(doc.id) not in ids
    finally:
        await _cleanup(tenant["id"])
