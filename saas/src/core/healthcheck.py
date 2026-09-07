from dataclasses import dataclass

from anthropic import AsyncAnthropic
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


async def check_anthropic(client: AsyncAnthropic | None = None) -> HealthCheckResult:
    """最小トークンでの1回の呼び出しで疎通確認する(4-4)。呼び出し元でクライアントを
    注入できるようにし、テストでは実際のAPIを叩かない。"""
    client = client or AsyncAnthropic()
    try:
        await client.messages.create(
            model=settings.anthropic_model_light or settings.anthropic_model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return HealthCheckResult(name="anthropic", ok=True)
    except Exception as e:
        return HealthCheckResult(name="anthropic", ok=False, detail=str(e))


async def run_startup_checks(anthropic_client: AsyncAnthropic | None = None) -> list[HealthCheckResult]:
    """DB接続とAnthropic API疎通を確認する。失敗した項目があれば呼び出し元で
    プロセスを止めるかどうかを判断する(このモジュール自体はプロセスを落とさない)。"""
    return [
        await check_db(),
        await check_anthropic(anthropic_client),
    ]
