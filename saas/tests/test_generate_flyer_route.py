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


def test_flyer_title_uses_catch_copy_not_internal_headline():
    from types import SimpleNamespace

    from src.api.routes.sessions import _flyer_title_and_body

    session = SimpleNamespace(
        request_text="紹介文を作って",
        result={
            "board": {"copy_dept": "デモ用・架空物件\n\n## キャッチコピー\n- 春日部駅から徒歩8分の1LDK\n\n## 本文\n紹介文です。"},
            "approval_summary": {"headline": "架空物件の紹介文下書き：承認依頼"},
        },
    )
    title, body = _flyer_title_and_body(session)
    assert title == "春日部駅から徒歩8分の1LDK"
    assert "承認依頼" not in title
    assert "紹介文です。" in body


def test_flyer_title_inline_catch_copy_and_fallback():
    from types import SimpleNamespace

    from src.api.routes.sessions import _flyer_title_and_body

    inline = SimpleNamespace(request_text="", result={"board": {"d": "キャッチコピー：駅近の2LDK\n本文です。"}})
    assert _flyer_title_and_body(inline)[0] == "駅近の2LDK"

    none = SimpleNamespace(request_text="", result={"board": {"d": "本文だけです。"},
                                                    "approval_summary": {"headline": "承認依頼"}})
    assert _flyer_title_and_body(none)[0] == "物件のご案内資料"
