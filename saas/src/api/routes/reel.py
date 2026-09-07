"""スマホの生動画をアップロードし、AIが無音カット・字幕自動生成・BGM合成を行う
リール自動編集API(2位、Phase F)。

処理は同一リクエスト内で完結する(既存のgenerate-video等と同じ「ブロックして待つ」
パターン)。アップロード動画・中間ファイルはworkspace/配下に一時保存し、
レスポンス送信後にバックグラウンドで削除する。
"""

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from src.api.deps import get_current_tenant, get_stt_provider
from src.core.models import Tenant
from src.video.paths import WORKSPACE_ROOT
from src.video.reel_editor import ReelEditError, edit_reel, new_job_dir
from src.video.stt import STTProvider

router = APIRouter(prefix="/api/reel", tags=["reel"])

MAX_VIDEO_SIZE_BYTES = 300 * 1024 * 1024  # 300MB(スマホの数分の動画を想定)
ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-m4v"}
UPLOAD_CHUNK_SIZE = 1024 * 1024

# 著作権フリーのBGM音源をここに配置する(saas/assets/bgm/*.mp3)。
BGM_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "bgm"


class BgmOption(BaseModel):
    id: str
    label: str


@router.get("/bgm-options", response_model=list[BgmOption])
async def list_bgm_options(tenant: Tenant = Depends(get_current_tenant)) -> list[BgmOption]:
    if not BGM_DIR.exists():
        return []
    return [BgmOption(id=p.stem, label=p.stem) for p in sorted(BGM_DIR.glob("*.mp3"))]


@router.post("/edit", status_code=200)
async def edit_reel_endpoint(
    file: UploadFile,
    bgm: str | None = None,
    tenant: Tenant = Depends(get_current_tenant),
    stt_provider: STTProvider | None = Depends(get_stt_provider),
) -> FileResponse:
    if stt_provider is None:
        raise HTTPException(
            status_code=402,
            detail="OpenAI APIキーが設定されていません。設定画面から登録してください"
            "(字幕の自動生成に使います)。",
        )
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="対応していない動画形式です(mp4/movのみ対応)。")

    bgm_path: Path | None = None
    if bgm:
        candidate = BGM_DIR / f"{bgm}.mp3"
        if not candidate.exists():
            raise HTTPException(status_code=400, detail="指定されたBGMが見つかりません。")
        bgm_path = candidate

    job_dir = new_job_dir()
    input_path_rel = job_dir / "input.mp4"
    input_path = WORKSPACE_ROOT / input_path_rel

    size = 0
    with open(input_path, "wb") as f:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_VIDEO_SIZE_BYTES:
                shutil.rmtree(WORKSPACE_ROOT / job_dir, ignore_errors=True)
                raise HTTPException(status_code=400, detail="動画ファイルが大きすぎます(上限300MB)。")
            f.write(chunk)

    try:
        result_path = await edit_reel(
            input_path_rel, stt_provider=stt_provider, bgm_path=bgm_path, job_dir=job_dir
        )
    except ReelEditError as e:
        shutil.rmtree(WORKSPACE_ROOT / job_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=str(e))

    cleanup = BackgroundTask(shutil.rmtree, WORKSPACE_ROOT / job_dir, ignore_errors=True)
    return FileResponse(result_path, media_type="video/mp4", filename="reel.mp4", background=cleanup)
