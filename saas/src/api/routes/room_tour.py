"""実際の写真・生動画からルームツアー動画を自動生成するAPI(Part 5)。素材は必ず
顧客がアップロードした実写のみ(Higgsfieldによる部屋の想像生成はしない、既定経路)。
写真(スライドショー)・生動画(無音カット)のどちらか一方を指定する(動画優先)。

処理は同一リクエスト内で完結する(既存のreel.py/generate-videoと同じ「ブロックして
待つ」パターン)。アップロード写真・動画・中間ファイルはworkspace/配下に一時保存し、
レスポンス送信後にバックグラウンドで削除する。
"""

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from src.agent.llm import StructuredLLM
from src.api.deps import get_app_config, get_current_tenant, get_llm, get_scoped_db, get_tts_provider
from src.core.ai_budget import (
    ESTIMATED_ROOM_TOUR_COST_USD,
    BudgetExceededError,
    ensure_budget_available,
    record_cost,
)
from src.core.config import settings
from src.core.models import Tenant
from src.video.paths import WORKSPACE_ROOT
from src.video.room_tour import RoomTourError, generate_room_tour, new_job_dir
from src.video.tts import TTSProvider

router = APIRouter(prefix="/api/room-tour", tags=["room-tour"])

MAX_PHOTOS = 20
MAX_PHOTO_SIZE_BYTES = 20 * 1024 * 1024  # 20MB/枚
MAX_VIDEO_SIZE_BYTES = 300 * 1024 * 1024  # 300MB(reel.pyと同じ上限)
ALLOWED_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-m4v"}
UPLOAD_CHUNK_SIZE = 1024 * 1024

# reel.py用に用意したBGM音源を共用する(saas/assets/bgm/*.mp3)。
BGM_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "bgm"


async def _save_upload(f: UploadFile, dest: Path, *, max_size: int) -> int:
    size = 0
    with open(dest, "wb") as out:
        while chunk := await f.read(UPLOAD_CHUNK_SIZE):
            size += len(chunk)
            if size > max_size:
                return size
            out.write(chunk)
    return size


@router.post("/generate", status_code=200)
async def generate_room_tour_endpoint(
    photos: list[UploadFile] = File(default=[]),
    video: UploadFile | None = File(default=None),
    property_info: str = Form(...),
    bgm: str | None = Form(None),
    tenant: Tenant = Depends(get_current_tenant),
    db=Depends(get_scoped_db),
    llm: StructuredLLM = Depends(get_llm),
    tts_provider: TTSProvider | None = Depends(get_tts_provider),
    app_config=Depends(get_app_config),
) -> FileResponse:
    if tts_provider is None:
        raise HTTPException(
            status_code=402,
            detail="OpenAI APIキーが運営側で設定されていません(設定不備、ナレーション音声合成に使います)。",
        )
    if not property_info.strip():
        raise HTTPException(status_code=400, detail="物件情報を入力してください。")

    photos = [p for p in photos if p.filename]
    has_video = video is not None and video.filename
    if not photos and not has_video:
        raise HTTPException(status_code=400, detail="写真または動画のいずれかをアップロードしてください。")
    if len(photos) > MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"写真は{MAX_PHOTOS}枚までにしてください。")
    for f in photos:
        if f.content_type not in ALLOWED_PHOTO_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="対応していない画像形式です(jpg/png/webpのみ対応)。")
    if has_video and video.content_type not in ALLOWED_VIDEO_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="対応していない動画形式です(mp4/movのみ対応)。")

    tenant_row = await db.get(Tenant, tenant.id)
    try:
        ensure_budget_available(tenant_row)
    except BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))

    bgm_path: Path | None = None
    if bgm:
        candidate = BGM_DIR / f"{bgm}.mp3"
        if not candidate.exists():
            raise HTTPException(status_code=400, detail="指定されたBGMが見つかりません。")
        bgm_path = candidate

    job_dir = new_job_dir()

    video_path_rel: Path | None = None
    image_paths: list[Path] = []

    if has_video:
        video_path_rel = job_dir / "input.mp4"
        size = await _save_upload(video, WORKSPACE_ROOT / video_path_rel, max_size=MAX_VIDEO_SIZE_BYTES)
        if size > MAX_VIDEO_SIZE_BYTES:
            shutil.rmtree(WORKSPACE_ROOT / job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="動画ファイルが大きすぎます(上限300MB)。")
    else:
        photos_dir_rel = job_dir / "photos"
        (WORKSPACE_ROOT / photos_dir_rel).mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(photos):
            dest_rel = photos_dir_rel / f"photo_{i:03d}.jpg"
            size = await _save_upload(f, WORKSPACE_ROOT / dest_rel, max_size=MAX_PHOTO_SIZE_BYTES)
            if size > MAX_PHOTO_SIZE_BYTES:
                shutil.rmtree(WORKSPACE_ROOT / job_dir, ignore_errors=True)
                raise HTTPException(status_code=400, detail="写真1枚のサイズが大きすぎます(上限20MB)。")
            image_paths.append(dest_rel)

    model = settings.anthropic_model_light or settings.anthropic_model

    try:
        result_path = await generate_room_tour(
            llm=llm,
            model=model,
            property_info=property_info,
            image_paths=image_paths,
            video_path=video_path_rel,
            tts_provider=tts_provider,
            bgm_path=bgm_path,
            job_dir=job_dir,
        )
    except RoomTourError as e:
        shutil.rmtree(WORKSPACE_ROOT / job_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=str(e))

    record_cost(tenant_row, ESTIMATED_ROOM_TOUR_COST_USD)
    await db.commit()

    cleanup = BackgroundTask(shutil.rmtree, WORKSPACE_ROOT / job_dir, ignore_errors=True)
    return FileResponse(
        result_path, media_type="video/mp4", filename="room_tour.mp4", background=cleanup
    )
