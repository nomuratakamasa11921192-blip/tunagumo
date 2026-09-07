import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from src.agent.deadline_scan import scan_approval_deadlines
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.api.deps import DEFAULT_INDUSTRY
from src.core.db import async_session_factory
from src.core.models import Approval, Tenant
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_awaiting_approval_session(client, headers, llm) -> str:
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## コピー案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "コピーを作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認待ちまで進みませんでした")

    return session_id


async def test_expired_approval_auto_halts_session(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers, app.state.llm)

    async with async_session_factory() as db:
        result = await db.execute(select(Approval).where(Approval.session_id == session_id))
        approval = result.scalar_one()
        approval.deadline_at = datetime.utcnow() - timedelta(hours=1)
        await db.commit()

    summary = await scan_approval_deadlines(
        app_configs=app.state.app_configs,
        default_industry=DEFAULT_INDUSTRY,
        llm_factory=lambda: app.state.llm,
        checkpointer=app.state.checkpointer,
    )
    assert summary["expired"] == 1

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "HALTED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("HALTEDになりませんでした")

    assert "承認期限を超過" in res.json()["result"]["error_message"]

    async with async_session_factory() as db:
        result = await db.execute(select(Approval).where(Approval.session_id == session_id))
        approval = result.scalar_one()
        assert approval.status == "EXPIRED"


async def test_reminder_sent_once_within_24h_and_not_repeated(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers, app.state.llm)

    async with async_session_factory() as db:
        result = await db.execute(select(Approval).where(Approval.session_id == session_id))
        approval = result.scalar_one()
        approval.deadline_at = datetime.utcnow() + timedelta(hours=10)
        await db.commit()

    summary1 = await scan_approval_deadlines(
        app_configs=app.state.app_configs,
        default_industry=DEFAULT_INDUSTRY,
        llm_factory=lambda: app.state.llm,
        checkpointer=app.state.checkpointer,
    )
    assert summary1["reminded"] == 1

    summary2 = await scan_approval_deadlines(
        app_configs=app.state.app_configs,
        default_industry=DEFAULT_INDUSTRY,
        llm_factory=lambda: app.state.llm,
        checkpointer=app.state.checkpointer,
    )
    assert summary2["reminded"] == 0  # 2回目は送らない


async def test_reminder_sends_email_when_tenant_has_email_and_resend_configured(
    client, tenant, monkeypatch
):
    """src.agent.deadline_scan.send_emailを直接差し替える(httpx.AsyncClient.postを
    グローバルに差し替えると、テストクライアント自身のPOSTまで横取りしてしまうため)。"""
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers, app.state.llm)

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.email = "customer@example.com"

        result = await db.execute(select(Approval).where(Approval.session_id == session_id))
        approval = result.scalar_one()
        approval.deadline_at = datetime.utcnow() + timedelta(hours=10)
        await db.commit()

    sent_to = []

    async def fake_send_email(*, to, subject, html):
        sent_to.append(to)
        return True

    monkeypatch.setattr("src.agent.deadline_scan.send_email", fake_send_email)

    summary = await scan_approval_deadlines(
        app_configs=app.state.app_configs,
        default_industry=DEFAULT_INDUSTRY,
        llm_factory=lambda: app.state.llm,
        checkpointer=app.state.checkpointer,
    )

    assert summary["reminded"] == 1
    assert sent_to == ["customer@example.com"]
