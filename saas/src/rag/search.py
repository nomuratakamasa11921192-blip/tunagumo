"""ハイブリッド検索(Phase 7-4)。ベクトル検索単体では固有名詞・型番の検索が実用に耐えない
ため、全文検索(PGroonga)と組み合わせ、RRF(Reciprocal Rank Fusion)で統合する。

7-7-2: 検索時は status='ACTIVE' かつ有効期限内(valid_until)のものだけを対象にする。
"""
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Chunk, Document

RRF_K = 60
CANDIDATES_PER_SOURCE = 20


@dataclass
class SearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    heading: str | None
    content: str
    valid_until: date | None
    score: float


async def hybrid_search(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query_text: str,
    query_embedding: list[float],
    top_k: int = 5,
    index_scope: str = "internal",
    as_of: date | None = None,
) -> list[SearchResult]:
    as_of = as_of or date.today()

    vector_stmt = (
        select(Chunk.id, Chunk.document_id, Chunk.content, Chunk.heading, Document.title, Document.valid_until)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.tenant_id == tenant_id,
            Document.status == "ACTIVE",
            Document.index_scope == index_scope,
            (Document.valid_until.is_(None)) | (Document.valid_until >= as_of),
        )
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(CANDIDATES_PER_SOURCE)
    )
    vector_rows = (await db.execute(vector_stmt)).all()

    fulltext_stmt = text(
        """
        SELECT c.id AS id, c.document_id AS document_id, c.content AS content, c.heading AS heading,
               d.title AS title, d.valid_until AS valid_until
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE d.tenant_id = :tenant_id
          AND d.status = 'ACTIVE'
          AND d.index_scope = :index_scope
          AND (d.valid_until IS NULL OR d.valid_until >= :as_of)
          AND c.content &@~ :query
        ORDER BY pgroonga_score(c.tableoid, c.ctid) DESC
        LIMIT :limit
        """
    )
    fulltext_result = await db.execute(
        fulltext_stmt,
        {
            "tenant_id": str(tenant_id),
            "index_scope": index_scope,
            "as_of": as_of,
            "query": query_text,
            "limit": CANDIDATES_PER_SOURCE,
        },
    )
    fulltext_rows = fulltext_result.all()

    # RRF: score = Σ 1/(60 + rank)  (rankは0始まり)
    rows_by_id: dict = {}
    rrf_scores: dict = {}

    for rank, row in enumerate(vector_rows):
        rows_by_id[row.id] = row
        rrf_scores[row.id] = rrf_scores.get(row.id, 0.0) + 1 / (RRF_K + rank)

    for rank, row in enumerate(fulltext_rows):
        rows_by_id[row.id] = row
        rrf_scores[row.id] = rrf_scores.get(row.id, 0.0) + 1 / (RRF_K + rank)

    ranked_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)[:top_k]

    return [
        SearchResult(
            chunk_id=rows_by_id[cid].id,
            document_id=rows_by_id[cid].document_id,
            document_title=rows_by_id[cid].title,
            heading=rows_by_id[cid].heading,
            content=rows_by_id[cid].content,
            valid_until=rows_by_id[cid].valid_until,
            score=rrf_scores[cid],
        )
        for cid in ranked_ids
    ]
