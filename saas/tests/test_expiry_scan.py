import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from src.core.db import async_session_factory
from src.core.models import Chunk, Document, Tenant
from src.rag.expiry_scan import EXPIRY_WARNING_DAYS, scan_document_expiry

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_document(*, tenant_id, title, valid_until, status="ACTIVE", notified=False) -> Document:
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
        if notified:
            doc.valid_until_notified_at = doc.created_at
            await db.commit()
        return doc


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == tenant_id))).scalars().all()
        if doc_ids:
            await db.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await db.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await db.commit()


async def test_document_within_warning_window_gets_notified_once(tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="就業規則", valid_until=date.today() + timedelta(days=EXPIRY_WARNING_DAYS - 1)
    )
    try:
        summary1 = await scan_document_expiry()
        assert summary1["warned"] == 1

        summary2 = await scan_document_expiry()
        assert summary2["warned"] == 0  # 2回目は送らない

        async with async_session_factory() as db:
            row = await db.get(Document, doc.id)
            assert row.valid_until_notified_at is not None
            assert row.status == "ACTIVE"
    finally:
        await _cleanup(tenant["id"])


async def test_document_outside_warning_window_is_not_notified(tenant):
    await _make_document(
        tenant_id=tenant["id"], title="料金表", valid_until=date.today() + timedelta(days=EXPIRY_WARNING_DAYS + 5)
    )
    try:
        summary = await scan_document_expiry()
        assert summary["warned"] == 0
        assert summary["expired"] == 0
    finally:
        await _cleanup(tenant["id"])


async def test_document_past_valid_until_becomes_expired(tenant):
    doc = await _make_document(
        tenant_id=tenant["id"], title="旧料金表", valid_until=date.today() - timedelta(days=1)
    )
    try:
        summary = await scan_document_expiry()
        assert summary["expired"] == 1

        async with async_session_factory() as db:
            row = await db.get(Document, doc.id)
            assert row.status == "EXPIRED"
    finally:
        await _cleanup(tenant["id"])


async def test_document_without_valid_until_is_never_scanned(tenant):
    await _make_document(tenant_id=tenant["id"], title="無期限の資料", valid_until=None)
    try:
        summary = await scan_document_expiry()
        assert summary["warned"] == 0
        assert summary["expired"] == 0
    finally:
        await _cleanup(tenant["id"])


async def test_non_active_document_is_not_scanned(tenant):
    await _make_document(
        tenant_id=tenant["id"],
        title="旧版",
        valid_until=date.today() - timedelta(days=1),
        status="SUPERSEDED",
    )
    try:
        summary = await scan_document_expiry()
        assert summary["expired"] == 0
    finally:
        await _cleanup(tenant["id"])


async def test_already_notified_document_is_not_notified_again_but_can_still_expire(tenant):
    doc = await _make_document(
        tenant_id=tenant["id"],
        title="通知済みの資料",
        valid_until=date.today() - timedelta(days=1),
        notified=True,
    )
    try:
        summary = await scan_document_expiry()
        assert summary["warned"] == 0
        assert summary["expired"] == 1

        async with async_session_factory() as db:
            row = await db.get(Document, doc.id)
            assert row.status == "EXPIRED"
    finally:
        await _cleanup(tenant["id"])


async def test_sends_email_when_tenant_has_email_and_resend_configured(tenant, monkeypatch):
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.email = "customer@example.com"
        await db.commit()

    await _make_document(
        tenant_id=tenant["id"], title="就業規則", valid_until=date.today() + timedelta(days=EXPIRY_WARNING_DAYS - 1)
    )

    sent_to = []

    async def fake_send_email(*, to, subject, html):
        sent_to.append(to)
        return True

    monkeypatch.setattr("src.rag.expiry_scan.send_email", fake_send_email)

    try:
        summary = await scan_document_expiry()
        assert summary["warned"] == 1
        assert sent_to == ["customer@example.com"]
    finally:
        await _cleanup(tenant["id"])
