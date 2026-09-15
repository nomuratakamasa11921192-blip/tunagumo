from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy import text

from src.core.config import settings
from src.core.db import async_session_factory


@dataclass
class HealthCheckResult:
    name: str
    ok: bool
    detail: str = ""


async def check_db() -> HealthCheckResult:
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        return HealthCheckResult(name="db", ok=True)
    except Exception as e:
        return HealthCheckResult(name="db", ok=False, detail=str(e))


async def check_openai(client: AsyncOpenAI | None = None) -> HealthCheckResult:
    """文章生成に使うOpenAIの疎通確認(4-4)。2026-09-15にAnthropicから移行。

    トークンを消費しないモデル情報取得(models.retrieve)で、APIキーの有効性と
    OPENAI_MODEL_LIGHT(未設定ならOPENAI_MODEL)の指定ミスを同時に検出する。
    呼び出し元でクライアントを注入できるようにし、テストでは実際のAPIを叩かない。"""
    client = client or AsyncOpenAI(api_key=settings.openai_api_key or None, max_retries=0)
    try:
        await client.models.retrieve(settings.openai_model_light or settings.openai_model)
        return HealthCheckResult(name="openai", ok=True)
    except Exception as e:
        return HealthCheckResult(name="openai", ok=False, detail=str(e))


async def run_startup_checks(openai_client: AsyncOpenAI | None = None) -> list[HealthCheckResult]:
    """DB接続とOpenAI API疎通を確認する。失敗した項目があれば呼び出し元で
    プロセスを止めるかどうかを判断する(このモジュール自体はプロセスを落とさない)。"""
    return [
        await check_db(),
        await check_openai(openai_client),
    ]
