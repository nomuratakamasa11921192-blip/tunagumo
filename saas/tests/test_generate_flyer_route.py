import asyncio

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_awaiting_approval_session(client, headers) -> str:
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="物件紹介文を作ってほしい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="物件紹介文", reason="要件が揃った")
    )
    llm.queue_text("## 物件紹介文\nテスト物件の紹介文です。")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="物件紹介文が完成しました", qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "物件紹介文を作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            return session_id
        await asyncio.sleep(0.02)
    raise TimeoutError("承認待ちまで進みませんでした")


async def test_generate_flyer_requires_auth(client):
    res = await client.post("/api/sessions/00000000-0000-0000-0000-000000000000/generate-flyer", json={})
    assert res.status_code == 401


async def test_generate_flyer_before_completion_returns_409(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="LPの見出しを作ってほしい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="CLARIFY", missing_info=["対象クライアント"], reason="情報不足")
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

    res = await client.post(f"/api/sessions/{session_id}/generate-flyer", json={}, headers=headers)
    assert res.status_code == 409


async def test_generate_flyer_returns_pdf(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers)

    res = await client.post(
        f"/api/sessions/{session_id}/generate-flyer",
        json={"image_urls": ["https://example.com/a.jpg"]},
        headers=headers,
    )

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
