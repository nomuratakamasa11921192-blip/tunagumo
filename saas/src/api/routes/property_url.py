"""物件ページのURLを貼るだけで、依頼の下書き(テキスト・画像URL)を取得するAPI
(Phase 2: 不動産特化にあたって追加)。

セッションそのものは作らず、抽出結果を返すだけ。フロントは結果を「ゴール」
入力欄に自動反映し、顧客が必要なら手直ししてから通常通りセッションを作る。
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from src.api.deps import get_current_tenant, get_higgsfield_client
from src.core.higgsfield_client import HiggsfieldClient, HiggsfieldError
from src.core.models import Tenant
from src.core.property_url_import import PropertyImportError, import_property_url
from src.rag.extraction import MAX_FILE_SIZE_BYTES, ExtractionError, extract_text

router = APIRouter(prefix="/api/property-url", tags=["property-url"])

# 取得した内容を利用する権利は顧客自身に確認してもらう(法令チェック注記と同じ位置づけ)。
RIGHTS_NOTICE = "取得した内容(文章・画像)をご利用いただく権利があることを、ご自身でご確認ください。"

# PDFアップロード時にサーバーが受け付けるMIMEタイプ(図面PDFのみ想定、DOCX/TXT等は対象外)。
_PDF_MIME_TYPE = "application/pdf"


class ImportPropertyUrlRequest(BaseModel):
    url: str


class ImportPropertyUrlResponse(BaseModel):
    text: str
    image_urls: list[str]
    notice: str


@router.post("/extract", response_model=ImportPropertyUrlResponse, status_code=200)
async def extract_property_url(
    req: ImportPropertyUrlRequest,
    tenant: Tenant = Depends(get_current_tenant),
) -> ImportPropertyUrlResponse:
    try:
        result = await import_property_url(req.url)
    except PropertyImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ImportPropertyUrlResponse(
        text=result["text"], image_urls=result["image_urls"], notice=RIGHTS_NOTICE
    )


class ImportPropertyPdfResponse(BaseModel):
    text: str
    notice: str


@router.post("/extract-pdf", response_model=ImportPropertyPdfResponse, status_code=200)
async def extract_property_pdf(
    file: UploadFile,
    tenant: Tenant = Depends(get_current_tenant),
) -> ImportPropertyPdfResponse:
    """図面PDF(マイソク等)をアップロードし、本文テキストを抽出する。

    価格・間取り等のフィールド分解はせず、生テキストをそのまま返す(既存のURL版
    import_property_urlと対称的な設計)。反映後の解釈は通常のセッション作成フロー
    (LLMが自由記述から解釈する)に委ねる。
    """
    data = await file.read()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="ファイルサイズが大きすぎます(上限20MB)。")
    try:
        text = extract_text(data, _PDF_MIME_TYPE)
    except ExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ImportPropertyPdfResponse(text=text, notice=RIGHTS_NOTICE)


class StageImageRequest(BaseModel):
    image_url: str
    # 例:「ソファとコーヒーテーブルを置いて」「家具を全部消して」など
    instruction: str


class StageImageResponse(BaseModel):
    image_url: str


@router.post("/stage-image", response_model=StageImageResponse, status_code=201)
async def stage_image(
    req: StageImageRequest,
    tenant: Tenant = Depends(get_current_tenant),
    higgsfield: HiggsfieldClient | None = Depends(get_higgsfield_client),
) -> StageImageResponse:
    """バーチャルステージング(物件写真に家具などを追加する画像編集)。

    未検証機能(HiggsfieldClient.edit_imageのdocstring参照)。うまくいかない場合は
    502エラーになるので、フロント側は失敗を前提にエラー表示を用意すること。
    """
    if higgsfield is None:
        raise HTTPException(
            status_code=402,
            detail="Higgsfield APIキーが設定されていません。設定画面から登録してください。",
        )
    try:
        image_url = await higgsfield.edit_image(req.image_url, req.instruction)
    except HiggsfieldError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return StageImageResponse(image_url=image_url)
