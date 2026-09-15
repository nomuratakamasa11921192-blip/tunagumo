"""OpenAIで生成・編集した画像の配信(2026-09-15、Higgsfieldから移行したPart B)。

<img>タグはAuthorizationヘッダを送れないため認証はかけず、推測困難なファイル名
(uuid4の32桁hex)を知っている人だけが見られる方式にする。Higgsfield時代に返していた
CDN URLと同じ扱い。ファイル名は正規表現で厳密に検証し、パストラバーサルを防ぐ。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.core.openai_image_client import generated_image_path

router = APIRouter(prefix="/api/generated-images", tags=["generated-images"])


@router.get("/{name}")
async def get_generated_image(name: str) -> FileResponse:
    path = generated_image_path(name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            # 共有キャッシュ(CDN等)に残さない。ファイル名は不変なのでブラウザには長く持たせる。
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
