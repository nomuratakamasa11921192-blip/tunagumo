"""_persist()(src/agent/runner.py)が月間AI予算(src/core/ai_budget.py)に正しく
増分だけを反映すること(同じセッションに対する複数回のグラフ実行で二重計上しないこと)
を検証する。
"""

import asyncio
import uuid

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.core.db import async_session_factory
from src.core.models import Tenant as TenantModel
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_completed_session_adds_its_cost_to_tenant_budget(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは！", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "こんにちは"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("グラフ実行が完了しませんでした")

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        assert row.ai_cost_this_period_usd > 0


async def test_multi_step_session_does_not_double_count_cost(client, tenant):
    """clarify→answerのように_persist()が複数回呼ばれるセッションでも、
    テナントの月間累計には各回の「増分」だけが1回ずつ足されること(合計がSession.cost_usd
    の最終値と一致すること)を確認する。"""
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="LPの見出しを作ってほしい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(
            verdict="CLARIFY", missing_info=["対象クライアント", "予算規模", "納期"], reason="情報不足"
        )
    )

    created = await client.post("/api/sessions", json={"text": "LPの見出しを作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "CLARIFYING":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("要件確認で停止しませんでした")

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        cost_after_clarify = row.ai_cost_this_period_usd

    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="見出しを3案", reason="要件が揃った")
    )
    llm.queue_text("## 見出し3案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="見出し完成", qa_result="合格"))

    await client.post(
        f"/api/sessions/{session_id}/answer",
        json={"answer": {"予算規模": "50万円", "納期": "来週", "対象クライアント": "BtoB SaaS"}},
        headers=headers,
    )

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認待ちまで進みませんでした")

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        final_tenant_cost = row.ai_cost_this_period_usd

    # 2回目の_persist呼び出しでも「増分」だけが足されていること
    # (テナントの累計がclarify後より確実に増えているが、Session.cost_usdの最終値を
    # 超えて二重計上されていないこと)
    assert final_tenant_cost > cost_after_clarify
    # このテナントは他に何もしていないので、累計はこのセッションのcost_usdと一致するはず
    async with async_session_factory() as db:
        from src.core.models import Session as SessionModel

        session_row = await db.get(SessionModel, uuid.UUID(session_id))
        assert final_tenant_cost == pytest.approx(session_row.cost_usd, rel=1e-9)
