import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from src.agent.checkpointer import psycopg_dsn
from src.agent.graph import build_graph
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.agent.state import new_state
from tests.fakes import FakeLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_graph_resumes_after_simulated_process_restart(config):
    """5-5: プロセスを落として再起動しても、要件確認待ちから再開できることを確認する。

    MemorySaverではなく実際のPostgresSaverを使い、1回目と2回目で完全に別の
    graph/checkpointerインスタンスを作ることで、状態がPythonプロセスのメモリではなく
    Postgresに永続化されていることを検証する。
    """
    # thread_idは毎回ユニークにする(固定値だと、テストを繰り返し実行したときに
    # 前回の実行結果(retry_countsなど)がPostgres上に残っていて再現性が崩れる)
    session_id = f"durability-session-{uuid.uuid4()}"
    thread_id = f"durability-tenant:{session_id}"
    cfg = {"configurable": {"thread_id": thread_id}}

    llm1 = FakeLLM()
    llm1.queue_structured(Triage(intent="WORK_REQUEST", goal="LPの見出しを作ってほしい", reason="業務依頼"))
    llm1.queue_structured(
        SupervisorDecision(verdict="CLARIFY", missing_info=["予算規模", "納期"], reason="情報不足")
    )

    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn()) as saver1:
        await saver1.setup()
        graph1 = build_graph(config, llm=llm1, checkpointer=saver1)
        state = new_state("durability-tenant", session_id, "user-1", "LPの見出しを作って")
        result1 = await graph1.ainvoke(state, config=cfg)

    assert result1["status"] == "CLARIFYING"
    # ここでPythonプロセスが落ちたと仮定する。graph1・saver1・llm1は以降一切使わない。

    llm2 = FakeLLM()
    llm2.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN", target_depts=["copy_dept"], instruction="見出しを3案", reason="要件が揃った"
        )
    )
    llm2.queue_text("## 見出し3案(再起動後に生成)")
    llm2.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm2.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm2.queue_structured(ApprovalSummary(headline="再起動後も完了", qa_result="合格"))

    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn()) as saver2:
        graph2 = build_graph(config, llm=llm2, checkpointer=saver2)
        result2 = await graph2.ainvoke(
            Command(resume={"予算規模": "80万円", "納期": "再来週"}), config=cfg
        )

    assert result2["status"] == "AWAITING_APPROVAL"
    assert result2["board"]["copy_dept"] == "## 見出し3案(再起動後に生成)"
    assert result2["clarifications"] == {"予算規模": "80万円", "納期": "再来週"}
