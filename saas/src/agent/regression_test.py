import uuid

from langgraph.checkpoint.memory import MemorySaver

from src.agent.config_models import AppConfig
from src.agent.graph import build_graph
from src.agent.llm import StructuredLLM
from src.agent.qa import check_forbidden
from src.agent.state import new_state

# 管理画面⑥「回帰テストの一括実行」用のゴールデンケース(8-7)。
# 実際のClaude APIを叩く(課金が発生する)ため、自動テストには含めず、
# 管理者が明示的にボタンを押したときだけ実行する。
GOLDEN_CASES = [
    {
        "name": "挨拶",
        "text": "こんにちは",
        "expect_status": "COMPLETED",
    },
    {
        "name": "コピー作成(情報あり)",
        "text": (
            "LPの見出しを作って。対象はBtoB SaaS企業向けの経営者、"
            "訴求はコスト削減、納期は来週、掲載媒体は自社サイト。"
        ),
        "expect_status_in": ["AWAITING_APPROVAL", "CLARIFYING"],
    },
]


async def run_regression_tests(*, app_config: AppConfig, llm: StructuredLLM) -> list[dict]:
    """ゴールデンケースを実際に実行し、(1)期待した状態に到達するか (2)禁止ワードを
    含んでいないか を検査する。モデル更新時の回帰確認に使う(8-7)。"""
    results = []

    for case in GOLDEN_CASES:
        session_id = f"regression-{uuid.uuid4()}"
        checkpointer = MemorySaver()  # 実顧客のセッションと混ざらないよう毎回使い捨て
        graph = build_graph(app_config, llm=llm, checkpointer=checkpointer)
        cfg = {"configurable": {"thread_id": f"regression-tenant:{session_id}"}}
        state = new_state("regression-tenant", session_id, "admin", case["text"])

        entry = {"name": case["name"], "ok": False, "detail": ""}
        try:
            result = await graph.ainvoke(state, config=cfg)
        except Exception as e:
            entry["detail"] = f"実行時エラー: {e}"
            results.append(entry)
            continue

        status = result.get("status")
        expected = case.get("expect_status_in") or [case.get("expect_status")]
        if status not in expected:
            entry["detail"] = f"想定外のstatus: {status}(期待: {expected})"
            results.append(entry)
            continue

        forbidden_hits = {
            dept_id: check_forbidden(text, app_config.compliance.forbidden_words)
            for dept_id, text in (result.get("board") or {}).items()
            if text
        }
        hits = {d: words for d, words in forbidden_hits.items() if words}
        if hits:
            entry["detail"] = f"禁止ワードを検出: {hits}"
            results.append(entry)
            continue

        entry["ok"] = True
        entry["detail"] = f"status={status}"
        results.append(entry)

    return results
