"""承認期限のスキャン(6-3)。5分間隔のcron/systemd timerから呼ぶ想定。
Phase 8でworker/schedulerサービスを組むまでは、手動または一時的なcronで実行する。

使い方: docker compose exec api python -m scripts.scan_approval_deadlines
"""

import asyncio


from src.agent.checkpointer import CheckpointerLifecycle
from src.agent.config_loader import load_config
from src.agent.deadline_scan import scan_approval_deadlines
from src.agent.llm import StructuredLLM, build_llm
from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES
from src.core.config import settings


def _llm_factory() -> StructuredLLM:
    # 提供元(Anthropic / OpenAI)は設定で切り替わる(src/agent/llm.py)
    return build_llm()


async def main() -> None:
    app_configs = {industry: load_config(f"config/{industry}.yaml") for industry in INDUSTRIES}
    lifecycle = CheckpointerLifecycle()
    checkpointer = await lifecycle.start()

    try:
        result = await scan_approval_deadlines(
            app_configs=app_configs,
            default_industry=DEFAULT_INDUSTRY,
            llm_factory=_llm_factory,
            checkpointer=checkpointer,
        )
        print(f"リマインド送信: {result['reminded']}件, 期限切れ自動却下: {result['expired']}件")
    finally:
        await lifecycle.stop()


if __name__ == "__main__":
    asyncio.run(main())
