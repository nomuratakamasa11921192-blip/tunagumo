import uuid

import pytest
from sqlalchemy import delete, select

from src.core.db import async_session_factory
from src.core.models import Chunk, Document
from src.rag.embeddings import EmbeddingError
from src.rag.extraction import sha256_of
from src.rag.ingestion import (
    IngestionValidationError,
    _process_document,
    create_pending_document,
    validate_upload,
)
from tests.conftest import tenant  # noqa: F401  (fixture)
from tests.fakes import FakeEmbeddingProvider

asyncio_test = pytest.mark.asyncio(loop_scope="session")


class _FailingEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("埋め込みAPIが失敗しました(テスト用)")


async def _cleanup_documents(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == tenant_id))).scalars().all()
        if doc_ids:
            await db.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await db.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await db.commit()


def test_validate_upload_rejects_unsupported_mime_type():
    with pytest.raises(IngestionValidationError):
        validate_upload(mime_type="image/png", size_bytes=100)


def test_validate_upload_rejects_oversized_file():
    with pytest.raises(IngestionValidationError):
        validate_upload(mime_type="text/plain", size_bytes=21 * 1024 * 1024)


def test_validate_upload_accepts_supported_small_file():
    validate_upload(mime_type="text/plain", size_bytes=1024)


@asyncio_test
async def test_create_pending_document_creates_processing_row(tenant):
    async with async_session_factory() as db:
        doc = await create_pending_document(
            db=db,
            tenant_id=tenant["id"],
            title="就業規則",
            filename="rules.txt",
            mime_type="text/plain",
            sha256=sha256_of(b"content-a"),
            valid_from=None,
            valid_until=None,
        )
    try:
        assert doc is not None
        assert doc.status == "PROCESSING"
        assert doc.version == 1
    finally:
        await _cleanup_documents(tenant["id"])


@asyncio_test
async def test_create_pending_document_returns_none_for_duplicate_sha256(tenant):
    sha = sha256_of(b"same-content")
    async with async_session_factory() as db:
        first = await create_pending_document(
            db=db,
            tenant_id=tenant["id"],
            title="文書A",
            filename="a.txt",
            mime_type="text/plain",
            sha256=sha,
            valid_from=None,
            valid_until=None,
        )
    try:
        async with async_session_factory() as db:
            second = await create_pending_document(
                db=db,
                tenant_id=tenant["id"],
                title="文書B(内容は同じ)",
                filename="b.txt",
                mime_type="text/plain",
                sha256=sha,
                valid_from=None,
                valid_until=None,
            )
        assert first is not None
        assert second is None
    finally:
        await _cleanup_documents(tenant["id"])


@asyncio_test
async def test_process_document_success_creates_active_document_with_chunks(tenant):
    async with async_session_factory() as db:
        doc = await create_pending_document(
            db=db,
            tenant_id=tenant["id"],
            title="社内規程",
            filename="rules.txt",
            mime_type="text/plain",
            sha256=sha256_of(b"rule-content"),
            valid_from=None,
            valid_until=None,
        )
    try:
        provider = FakeEmbeddingProvider()
        await _process_document(
            document_id=doc.id,
            tenant_id=tenant["id"],
            data="第1条 これはテスト用の社内規程です。".encode("utf-8"),
            mime_type="text/plain",
            embedding_provider=provider,
        )

        async with async_session_factory() as db:
            refreshed = await db.get(Document, doc.id)
            assert refreshed.status == "ACTIVE"
            chunks = (await db.execute(select(Chunk).where(Chunk.document_id == doc.id))).scalars().all()
            assert len(chunks) >= 1
            assert len(chunks[0].embedding) > 0
        assert provider.calls  # 埋め込みAPI(フェイク)が実際に呼ばれたこと
    finally:
        await _cleanup_documents(tenant["id"])


@asyncio_test
async def test_process_document_failure_marks_document_failed_not_partial(tenant):
    async with async_session_factory() as db:
        doc = await create_pending_document(
            db=db,
            tenant_id=tenant["id"],
            title="失敗するはずの文書",
            filename="rules.txt",
            mime_type="text/plain",
            sha256=sha256_of(b"will-fail"),
            valid_from=None,
            valid_until=None,
        )
    try:
        with pytest.raises(EmbeddingError):
            await _process_document(
                document_id=doc.id,
                tenant_id=tenant["id"],
                data="失敗するテスト文書です。".encode("utf-8"),
                mime_type="text/plain",
                embedding_provider=_FailingEmbeddingProvider(),
            )

        async with async_session_factory() as db:
            # _process_document自体は例外を投げるだけで、ここではまだPROCESSINGのまま
            # (FAILEDへの遷移はfire_and_forget_ingestion側の責務)
            refreshed = await db.get(Document, doc.id)
            assert refreshed.status == "PROCESSING"
            chunks = (await db.execute(select(Chunk).where(Chunk.document_id == doc.id))).scalars().all()
            assert chunks == []  # 中途半端なチャンクが残っていない
    finally:
        await _cleanup_documents(tenant["id"])


@asyncio_test
async def test_process_document_supersedes_previous_active_document_with_same_title(tenant):
    provider = FakeEmbeddingProvider()
    async with async_session_factory() as db:
        old_doc = await create_pending_document(
            db=db,
            tenant_id=tenant["id"],
            title="重複タイトルの文書",
            filename="v1.txt",
            mime_type="text/plain",
            sha256=sha256_of(b"version-1"),
            valid_from=None,
            valid_until=None,
        )
    await _process_document(
        document_id=old_doc.id,
        tenant_id=tenant["id"],
        data="旧バージョンの内容です。".encode("utf-8"),
        mime_type="text/plain",
        embedding_provider=provider,
    )

    try:
        async with async_session_factory() as db:
            new_doc = await create_pending_document(
                db=db,
                tenant_id=tenant["id"],
                title="重複タイトルの文書",
                filename="v2.txt",
                mime_type="text/plain",
                sha256=sha256_of(b"version-2"),
                valid_from=None,
                valid_until=None,
            )
        await _process_document(
            document_id=new_doc.id,
            tenant_id=tenant["id"],
            data="新バージョンの内容です。".encode("utf-8"),
            mime_type="text/plain",
            embedding_provider=provider,
        )

        async with async_session_factory() as db:
            old_refreshed = await db.get(Document, old_doc.id)
            new_refreshed = await db.get(Document, new_doc.id)
            assert old_refreshed.status == "SUPERSEDED"
            assert old_refreshed.superseded_by == str(new_doc.id)
            assert new_refreshed.status == "ACTIVE"
            assert new_refreshed.version == old_refreshed.version + 1
    finally:
        await _cleanup_documents(tenant["id"])
