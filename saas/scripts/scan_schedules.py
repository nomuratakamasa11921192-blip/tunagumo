"""Phase 14: 顧客スケジュールの起動チェック(1分間隔のcron/systemd timerから呼ぶ想定)。
Phase 8のscheduler常駐サービスを使わない場合は、こちらをcronに登録する。

使い方: docker compose exec api python -m scripts.scan_schedules
"""

import asyncio
from datetime import datetime

from openai import AsyncOpenAI

from src.agent.checkpointer import CheckpointerLifecycle
from src.agent.config_loader import load_config
from src.agent.llm import DEFAULT_TIMEOUT_SECONDS, StructuredLLM
from src.agent.schedule_tick import tick
from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES
from src.core.config import settings


def _llm_factory() -> StructuredLLM:
    # 運営(ツナグモ)自身のOpenAI APIキーでクライアントを作る(2026-09-01 BYOK廃止、2026-09-15 OpenAIへ移行)。
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=0)
    return StructuredLLM(client=client)


async def main() -> None:
    app_configs = {industry: load_config(f"config/{industry}.yaml") for industry in INDUSTRIES}
    lifecycle = CheckpointerLifecycle()
    checkpointer = await lifecycle.start()

    try:
        result = await tick(
            now=datetime.utcnow(),
            app_configs=app_configs,
            default_industry=DEFAULT_INDUSTRY,
            llm_factory=_llm_factory,
            checkpointer=checkpointer,
        )
        print(
            f"起動: {result['started']}件, 遅延スキップ: {result['skipped_late']}件, "
            f"未設定スキップ: {result['skipped_disabled']}件"
        )
    finally:
        await lifecycle.stop()


if __name__ == "__main__":
    asyncio.run(main())
