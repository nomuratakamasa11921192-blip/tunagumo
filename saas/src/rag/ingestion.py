"""文書の取り込みパイプライン(Phase 7-1〜7-3、7-7-3)。

- APIリクエスト内で同期実行しない(非同期ジョブとして処理する、7-1-3)
- 失敗時は中途半端なチャンクを残さない(7-1-4): チャンク化・埋め込みを全て終えてから
  まとめてDBに書き込む。失敗したらDocumentをFAILED状態にするだけでchunksは作らない
- 同一SHA256(内容が全く同じファイル)は再取り込みしない(7-1-2)
- 同名の既存ACTIVE文書があれば、新版に差し替える(旧版はSUPERSEDEDにする、7-7-3)
"""

import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import async_session_factory
from src.core.logging_config import RequestContext
from src.core.models import Chunk, Document
from src.rag.chunking import chunk_text
from src.rag.embeddings import EmbeddingError, EmbeddingProvider
from src.rag.extraction import MAX_FILE_SIZE_BYTES, SUPPORTED_MIME_TYPES, ExtractionError, extract_text, sha256_of

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()


class IngestionValidationError(Exception):
    """アップロード時点で分かる検証エラー(APIの応答に使う。ファイル形式・サイズ)。"""


def validate_upload(*, mime_type: str, size_bytes: int) -> None:
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise IngestionValidationError(
            f"対応していないファイル形式です。PDF/DOCX/TXT/MDのみ対応しています(mime_type={mime_type})。"
        )
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise IngestionValidationError(
            f"ファイルサイズが上限({MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB)を超えています。"
        )


async def create_pending_document(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    title: str,
    filename: str,
    mime_type: str,
    sha256: str,
    valid_from,
    valid_until,
    index_scope: str = "internal",
) -> Document | None:
    """PROCESSING状態のDocument行を作る(即座にコミットし、APIはこの時点で202を返す)。
    同一SHA256のACTIVE文書が既にあれば、Noneを返して呼び出し元に重複を伝える。

    index_scope="public"は、Phase 16のWeb埋め込みチャット等、社外向け応答が引ける資料
    (公開してよいFAQ等)を社内資料と別インデックスで管理するためのもの(7-5-4/16-2)。
    既定は"internal"(社内向けのみ)。
    """
    existing = await db.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.sha256 == sha256,
            Document.status.in_(["ACTIVE", "PROCESSING"]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    document = Document(
        tenant_id=tenant_id,
        title=title,
        filename=filename,
        mime_type=mime_type,
        sha256=sha256,
        status="PROCESSING",
        valid_from=valid_from,
        valid_until=valid_until,
        index_scope=index_scope,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


def fire_and_forget_ingestion(
    *, document_id: uuid.UUID, tenant_id: uuid.UUID, data: bytes, mime_type: str, embedding_provider: EmbeddingProvider
) -> None:
    async def _run() -> None:
        with RequestContext(tenant_id=str(tenant_id)):
            try:
                await _process_document(
                    document_id=document_id,
                    tenant_id=tenant_id,
                    data=data,
                    mime_type=mime_type,
                    embedding_provider=embedding_provider,
                )
                logger.info("文書の取り込みが完了しました: document_id=%s", document_id)
            except Exception as e:
                logger.exception("文書の取り込みに失敗しました: document_id=%s", document_id)
                async with async_session_factory() as db:
                    row = await db.get(Document, document_id)
                    if row is not None:
                        row.status = "FAILED"
                        row.error_message = str(e)[:2000]
                        await db.commit()

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _process_document(
    *, document_id: uuid.UUID, tenant_id: uuid.UUID, data: bytes, mime_type: str, embedding_provider: EmbeddingProvider
) -> None:
    # 7-1-5: スキャンPDF等、テキストが取れない場合はここで例外(呼び出し元がFAILEDにする)
    text = extract_text(data, mime_type)
    chunks = chunk_text(text)
    if not chunks:
        raise ExtractionError("文書からチャンクを1件も作成できませんでした。")

    # 埋め込みを全件終えてから書き込む(7-1-4: 中途半端なチャンクを残さない)
    try:
        vectors = await embedding_provider.embed([c.content for c in chunks])
    except EmbeddingError:
        raise

    async with async_session_factory() as db:
        document = await db.get(Document, document_id)
        if document is None:
            return

        for chunk, vector in zip(chunks, vectors):
            db.add(
                Chunk(
                    document_id=document.id,
                    tenant_id=tenant_id,
                    chunk_index=chunk.chunk_index,
                    heading=chunk.heading,
                    content=chunk.content,
                    embedding=vector,
                )
            )

        # 7-7-3: 同名の既存ACTIVE文書があれば、旧版をSUPERSEDEDにして差し替える
        existing_active = await db.execute(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.title == document.title,
                Document.status == "ACTIVE",
                Document.id != document.id,
            )
        )
        previous = existing_active.scalar_one_or_none()
        if previous is not None:
            previous.status = "SUPERSEDED"
            previous.superseded_by = str(document.id)
            document.version = previous.version + 1

        document.status = "ACTIVE"
        document.updated_at = datetime.utcnow()
        await db.commit()
