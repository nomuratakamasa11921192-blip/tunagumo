import asyncio

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_greeting_session_completes_via_api(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは！", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "こんにちは"}, headers=headers)
    assert created.status_code == 202
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("グラフ実行が完了しませんでした")

    assert res.json()["result"]["reply"] == "こんにちは！"


async def test_clarify_then_answer_completes_session_via_api(client, tenant):
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

    assert res.json()["result"]["questions"] == ["対象クライアント", "予算規模", "納期"]

    llm.queue_structured(
        SupervisorDecision(
            verdict="ASSIGN", target_depts=["copy_dept"], instruction="見出しを3案", reason="要件が揃った"
        )
    )
    llm.queue_text("## 見出し3案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="見出し完成", qa_result="合格"))

    answered = await client.post(
        f"/api/sessions/{session_id}/answer",
        json={"answer": {"予算規模": "50万円", "納期": "来週", "対象クライアント": "BtoB SaaS"}},
        headers=headers,
    )
    assert answered.status_code == 202

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認待ちまで進みませんでした")

    assert res.json()["result"]["approval_summary"]["headline"] == "見出し完成"
    # 承認画面では成果物本文も見えること(要約だけでは中身を確認できない)
    assert res.json()["result"]["board"]["copy_dept"] == "## 見出し3案"

    approved = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=headers
    )
    assert approved.status_code == 202

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "APPROVED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認が完了しませんでした")

    # 二度押しは弾かれる(6-2)
    second = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=headers
    )
    assert second.status_code == 409


async def test_reject_via_api_routes_back_and_second_approve_succeeds(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## コピー案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="1回目", qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "コピーを作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認待ちまで進みませんでした")

    llm.queue_structured(
        SupervisorDecision(
            verdict="REVISE", target_depts=["copy_dept"], instruction="直す", reason="却下対応"
        )
    )
    llm.queue_text("## コピー案(修正後)")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="修正後", qa_result="合格"))

    rejected = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"decision": "reject", "comment": "もっと具体的に"},
        headers=headers,
    )
    assert rejected.status_code == 202

    for _ in range(150):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        body = res.json()
        if body["status"] == "AWAITING_APPROVAL" and body["result"]["approval_summary"]["headline"] == "修正後":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("再承認待ちまで進みませんでした")

    approved = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=headers
    )
    assert approved.status_code == 202


async def test_answer_endpoint_rejects_when_not_clarifying(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "こんにちは"}, headers=headers)
    session_id = created.json()["session_id"]

    res = await client.post(
        f"/api/sessions/{session_id}/answer", json={"answer": "何か"}, headers=headers
    )
    assert res.status_code == 409
