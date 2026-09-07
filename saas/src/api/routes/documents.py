"""社内資料検索(RAG, Phase 7)の文書アップロード・一覧・削除API。顧客自身のOpenAI APIキーが
必要(get_embedding_provider未設定ならRAGは使えない、事業モデルはAnthropicキーと同じ)。
"""

import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_tenant, get_embedding_provider, get_scoped_db
from src.core.models import Document, Tenant
from src.rag.embeddings import EmbeddingProvider
from src.rag.extraction import sha256_of
from src.rag.ingestion import (
    IngestionValidationError,
    create_pending_document,
    fire_and_forget_ingestion,
    validate_upload,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    filename: str
    status: str
    version: int
    valid_from: date | None
    valid_until: date | None
    superseded_by: str | None
    error_message: str | None
    index_scope: str
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    status: str


def _require_embedding_provider(embedding_provider: EmbeddingProvider | None) -> EmbeddingProvider:
    if embedding_provider is None:
        raise HTTPException(
            status_code=402,
            detail="OpenAI APIキーが設定されていません。社内資料検索(RAG)を使うには、"
            "アカウント設定でOpenAI APIキーを登録してください。",
        )
    return embedding_provider


@router.post("", response_model=UploadResponse, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    valid_from: date | None = Form(default=None),
    valid_until: date | None = Form(default=None),
    index_scope: Literal["internal", "public"] = Form(default="internal"),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
    embedding_provider: EmbeddingProvider | None = Depends(get_embedding_provider),
) -> UploadResponse:
    """文書をアップロードする。取り込み(テキスト抽出・チャンク化・埋め込み)は
    非同期で行われる(3秒ルール: APIリクエスト内で同期実行しない、7-1-3)。

    index_scope="public"は、Phase 16のWeb埋め込みチャット等、社外からの問い合わせに
    そのまま答えてよいFAQ・公開資料を指す。既定は"internal"(社内向けのみ、Web埋め込み
    チャットからは絶対に参照されない)。
    """
    embedding_provider = _require_embedding_provider(embedding_provider)

    data = await file.read()
    mime_type = (file.content_type or "").split(";")[0].strip()

    try:
        validate_upload(mime_type=mime_type, size_bytes=len(data))
    except IngestionValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    document = await create_pending_document(
        db=db,
        tenant_id=tenant.id,
        title=title or file.filename or "無題の文書",
        filename=file.filename or "unnamed",
        mime_type=mime_type,
        sha256=sha256_of(data),
        valid_from=valid_from,
        valid_until=valid_until,
        index_scope=index_scope,
    )
    if document is None:
        raise HTTPException(status_code=409, detail="同じ内容の文書が既に登録されています")

    fire_and_forget_ingestion(
        document_id=document.id,
        tenant_id=tenant.id,
        data=data,
        mime_type=mime_type,
        embedding_provider=embedding_provider,
    )

    return UploadResponse(document_id=document.id, status=document.status)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> DocumentListResponse:
    result = await db.execute(
        select(Document).where(Document.tenant_id == tenant.id).order_by(Document.created_at.desc())
    )
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(d, from_attributes=True) for d in result.scalars().all()]
    )


async def _get_owned_document(db: AsyncSession, tenant: Tenant, document_id: uuid.UUID) -> Document:
    document = await db.get(Document, document_id)
    if document is None or document.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="文書が見つかりません")
    return document


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> DocumentResponse:
    document = await _get_owned_document(db, tenant, document_id)
    return DocumentResponse.model_validate(document, from_attributes=True)


@router.delete("/{document_id}", status_code=204)
async def archive_document(
    document_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    """文書を検索対象から外す(7-7-2: 監査のため実データは削除せず、ARCHIVEDにするだけ)。"""
    document = await _get_owned_document(db, tenant, document_id)
    document.status = "ARCHIVED"
    document.updated_at = datetime.utcnow()
    await db.commit()
