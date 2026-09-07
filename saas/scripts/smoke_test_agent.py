"""実際のAnthropic APIを使って、統括AIグラフが最低限動くことを確認する手動スモークテスト。
pytestスイートには含めない(実APIを叩くため、CIでは実行しない)。

使い方: docker compose exec api python -m scripts.smoke_test_agent
"""

import asyncio

from src.agent.config_loader import load_config
from src.agent.graph import build_graph
from src.agent.state import new_state


async def main() -> None:
    config = load_config("config/default.yaml")
    graph = build_graph(config)

    print("=== 1. 挨拶 ===")
    state = new_state("smoke-tenant", "smoke-1", "user-1", "こんにちは")
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "smoke-tenant:smoke-1"}})
    print("status:", result["status"])
    print("reply:", result["approval_summary"].get("reply"))

    print("\n=== 2. 曖昧な業務依頼(要件確認で停止するはず) ===")
    cfg = {"configurable": {"thread_id": "smoke-tenant:smoke-2"}}
    state = new_state("smoke-tenant", "smoke-2", "user-1", "LPの見出しを作って")
    result = await graph.ainvoke(state, config=cfg)
    print("status:", result["status"])
    snapshot = await graph.aget_state(cfg)
    print("next:", snapshot.next)
    if snapshot.tasks and snapshot.tasks[0].interrupts:
        print("questions:", snapshot.tasks[0].interrupts[0].value)


if __name__ == "__main__":
    asyncio.run(main())
