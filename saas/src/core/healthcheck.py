from dataclasses import dataclass

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import text

from src.core.config import active_llm_model_light, settings
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


async def check_llm(client=None) -> HealthCheckResult:
    """文章生成に使うAPIの疎通確認(4-4)。提供元(LLM_PROVIDER)に応じてAnthropic/OpenAIを見る。

    トークンをほぼ消費しないモデル情報取得(models.retrieve)で、APIキーの有効性と
    モデル名の指定ミスを同時に検出する。呼び出し元でクライアントを注入できるようにし、
    テストでは実際のAPIを叩かない。"""
    provider = settings.llm_provider
    name = "anthropic" if provider == "anthropic" else "openai"
    if client is None:
        client = (
            AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=0)
            if provider == "anthropic"
            else AsyncOpenAI(api_key=settings.openai_api_key or None, max_retries=0)
        )
    try:
        await client.models.retrieve(active_llm_model_light())
        return HealthCheckResult(name=name, ok=True)
    except Exception as e:
        return HealthCheckResult(name=name, ok=False, detail=str(e))


# 旧名(テスト・既存コードからの参照用)
check_openai = check_llm


async def run_startup_checks(openai_client=None) -> list[HealthCheckResult]:
    """DB接続と、文章生成に使うAPIの疎通を確認する。失敗した項目があれば呼び出し元で
    プロセスを止めるかどうかを判断する(このモジュール自体はプロセスを落とさない)。"""
    return [
        await check_db(),
        await check_llm(openai_client),
    ]
