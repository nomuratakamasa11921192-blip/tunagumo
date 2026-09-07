"""src/rag/search.py の検証。PGroonga(全文検索)の演算子(&@~)とpgroonga_score()の
構文が、実際のDockerのPostgres上で動くかどうかは未検証だったため、実DBに対して確認する。
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from src.api.deps import hash_api_key
from src.core.db import async_session_factory
from src.core.models import EMBEDDING_DIM, Chunk, Document, Tenant
from src.rag.search import hybrid_search
from tests.conftest import tenant  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIM


async def _make_document(db, *, tenant_id, title, status="ACTIVE", valid_until=None, index_scope="internal") -> Document:
    doc = Document(
        tenant_id=tenant_id,
        title=title,
        filename=f"{title}.txt",
        mime_type="text/plain",
        sha256=uuid.uuid4().hex,
        status=status,
        valid_until=valid_until,
        index_scope=index_scope,
    )
    db.add(doc)
    await db.flush()
    return doc


async def _add_chunk(db, *, document, tenant_id, content, embedding, chunk_index=0) -> Chunk:
    chunk = Chunk(
        document_id=document.id,
        tenant_id=tenant_id,
        chunk_index=chunk_index,
        heading=None,
        content=content,
        embedding=embedding,
    )
    db.add(chunk)
    await db.flush()
    return chunk


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == tenant_id))).scalars().all()
        if doc_ids:
            await db.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
            await db.execute(delete(Document).where(Document.tenant_id == tenant_id))
            await db.commit()


async def test_hybrid_search_finds_result_by_vector_similarity(tenant):
    async with async_session_factory() as db:
        doc = await _make_document(db, tenant_id=tenant["id"], title="ベクトル一致文書")
        await _add_chunk(
            db, document=doc, tenant_id=tenant["id"],
            content="この文にはクエリと共通する単語がありません。",
            embedding=_vector(0.9),
        )
        await db.commit()

    try:
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=tenant["id"],
                query_text="まったく無関係な検索語",
                query_embedding=_vector(0.9),
                top_k=5,
            )
        assert any(r.document_id == doc.id for r in results)
    finally:
        await _cleanup(tenant["id"])


async def test_hybrid_search_finds_result_by_fulltext_keyword(tenant):
    async with async_session_factory() as db:
        doc = await _make_document(db, tenant_id=tenant["id"], title="全文一致文書")
        await _add_chunk(
            db, document=doc, tenant_id=tenant["id"],
            content="当社のパスワードポリシーは、8文字以上の複雑な文字列を要求します。",
            embedding=_vector(0.1),
        )
        await db.commit()

    try:
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=tenant["id"],
                query_text="パスワードポリシー",
                query_embedding=_vector(0.999),  # ベクトルは意図的に無関係にする
                top_k=5,
            )
        assert any(r.document_id == doc.id for r in results)
    finally:
        await _cleanup(tenant["id"])


async def test_hybrid_search_excludes_non_active_documents(tenant):
    async with async_session_factory() as db:
        doc = await _make_document(db, tenant_id=tenant["id"], title="下書き文書", status="PROCESSING")
        await _add_chunk(
            db, document=doc, tenant_id=tenant["id"],
            content="これはまだ処理中の文書の内容です。",
            embedding=_vector(0.5),
        )
        await db.commit()

    try:
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=tenant["id"],
                query_text="処理中の文書",
                query_embedding=_vector(0.5),
                top_k=5,
            )
        assert all(r.document_id != doc.id for r in results)
    finally:
        await _cleanup(tenant["id"])


async def test_hybrid_search_excludes_expired_documents(tenant):
    async with async_session_factory() as db:
        doc = await _make_document(
            db, tenant_id=tenant["id"], title="期限切れ文書",
            valid_until=date.today() - timedelta(days=1),
        )
        await _add_chunk(
            db, document=doc, tenant_id=tenant["id"],
            content="これは有効期限が切れている文書の内容です。",
            embedding=_vector(0.5),
        )
        await db.commit()

    try:
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=tenant["id"],
                query_text="有効期限が切れている",
                query_embedding=_vector(0.5),
                top_k=5,
            )
        assert all(r.document_id != doc.id for r in results)
    finally:
        await _cleanup(tenant["id"])


async def test_hybrid_search_does_not_leak_across_tenants(tenant):
    async with async_session_factory() as db:
        other_tenant = Tenant(
            name="pytest-other-tenant",
            api_key_hash=hash_api_key(f"other-{uuid.uuid4()}"),
        )
        db.add(other_tenant)
        await db.flush()
        other_tenant_id = other_tenant.id
        doc = await _make_document(db, tenant_id=other_tenant_id, title="他テナントの文書")
        await _add_chunk(
            db, document=doc, tenant_id=other_tenant_id,
            content="他のテナントに属する秘密の内容です。",
            embedding=_vector(0.5),
        )
        await db.commit()

    try:
        async with async_session_factory() as db:
            results = await hybrid_search(
                db,
                tenant_id=tenant["id"],
                query_text="秘密の内容",
                query_embedding=_vector(0.5),
                top_k=5,
            )
        assert all(r.document_id != doc.id for r in results)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            await db.execute(delete(Document).where(Document.id == doc.id))
            await db.execute(delete(Tenant).where(Tenant.id == other_tenant_id))
            await db.commit()
