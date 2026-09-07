import asyncio
import re

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.core.db import async_session_factory
from src.core.models import Approval, Tenant
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_session_awaiting_approval(client, headers, headline="見出し完成"):
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## コピー案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline=headline, qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "コピーを作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            return session_id
        await asyncio.sleep(0.02)
    raise TimeoutError("承認待ちまで進みませんでした")


async def _set_approval_stages(tenant_id, stages: int) -> None:
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant_id)
        row.approval_stages = stages
        await db.commit()


async def _create_login_user(client, tenant, email, role, monkeypatch) -> str:
    captured = {}

    async def fake_send_email(*, to, subject, html):
        captured["html"] = html
        return True

    monkeypatch.setattr("src.api.routes.account.send_email", fake_send_email)

    body = {"email": email, "role": role}
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/account/users", json=body, headers=headers)
    if res.status_code == 403:
        raise AssertionError("このヘルパーは最初のowner作成専用に使ってください")

    match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    raw_token = match.group(1)
    await client.post(
        "/api/account/accept-invite", json={"invite_token": raw_token, "password": f"{email}-password"}
    )
    login = await client.post(
        "/api/account/login",
        json={"email": email, "password": f"{email}-password"},
        headers=headers,
    )
    return login.json()["token"]


async def test_single_stage_tenant_is_unaffected_by_default(client, tenant):
    """approval_stages既定値(1)のテナントは、これまで通りテナントAPIキーだけで承認できる。"""
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_session_awaiting_approval(client, headers)

    res = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=headers
    )
    assert res.status_code == 202
    assert res.json()["status"] == "RUNNING"


async def test_multi_stage_tenant_rejects_api_key_only_approval(client, tenant):
    await _set_approval_stages(tenant["id"], 2)
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_session_awaiting_approval(client, headers)

    res = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=headers
    )
    assert res.status_code == 403


async def test_member_role_cannot_approve_even_single_stage(client, tenant, monkeypatch):
    member_token = await _create_login_user(client, tenant, "solo-owner@example.com", "member", monkeypatch)
    # 最初の1人はowner固定になる仕様なので、本テストの狙い(member拒否)を確かめるには
    # 2人目をmemberとして招待し直す必要がある。ownerとしてログインして招待する。
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    owner_login = await client.post(
        "/api/account/login",
        json={"email": "solo-owner@example.com", "password": "solo-owner@example.com-password"},
        headers=headers,
    )
    owner_token = owner_login.json()["token"]

    captured = {}

    async def fake_send_email(*, to, subject, html):
        captured["html"] = html
        return True

    monkeypatch.setattr("src.api.routes.account.send_email", fake_send_email)
    await client.post(
        "/api/account/users",
        json={"email": "member2@example.com", "role": "member"},
        headers={**headers, "X-User-Token": owner_token},
    )
    invite_match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    await client.post(
        "/api/account/accept-invite",
        json={"invite_token": invite_match.group(1), "password": "member2-password"},
    )
    member_login = await client.post(
        "/api/account/login",
        json={"email": "member2@example.com", "password": "member2-password"},
        headers=headers,
    )
    member_token = member_login.json()["token"]

    session_id = await _create_session_awaiting_approval(client, headers)
    res = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"decision": "approve"},
        headers={**headers, "X-User-Token": member_token},
    )
    assert res.status_code == 403


async def test_two_stage_approval_requires_both_before_resuming_graph(client, tenant, monkeypatch):
    await _set_approval_stages(tenant["id"], 2)
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}

    owner_token = await _create_login_user(client, tenant, "approver-owner@example.com", "owner", monkeypatch)

    captured = {}

    async def fake_send_email(*, to, subject, html):
        captured["html"] = html
        return True

    monkeypatch.setattr("src.api.routes.account.send_email", fake_send_email)
    await client.post(
        "/api/account/users",
        json={"email": "approver2@example.com", "role": "approver"},
        headers={**headers, "X-User-Token": owner_token},
    )
    invite_match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    await client.post(
        "/api/account/accept-invite",
        json={"invite_token": invite_match.group(1), "password": "approver2-password"},
    )
    login2 = await client.post(
        "/api/account/login",
        json={"email": "approver2@example.com", "password": "approver2-password"},
        headers=headers,
    )
    approver2_token = login2.json()["token"]

    session_id = await _create_session_awaiting_approval(client, headers)

    first = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"decision": "approve"},
        headers={**headers, "X-User-Token": owner_token},
    )
    assert first.status_code == 202
    assert first.json()["status"] == "AWAITING_APPROVAL"
    assert first.json()["next_stage"] == 2

    # まだグラフは進んでいない(2段目待ち)ことを確認
    still_pending = await client.get(f"/api/sessions/{session_id}", headers=headers)
    assert still_pending.json()["status"] == "AWAITING_APPROVAL"

    second = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"decision": "approve"},
        headers={**headers, "X-User-Token": approver2_token},
    )
    assert second.status_code == 202
    assert second.json()["status"] == "RUNNING"

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "APPROVED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("2段階承認後にAPPROVEDまで進みませんでした")

    async with async_session_factory() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(Approval).where(Approval.session_id.in_([session_id])).order_by(Approval.stage)
        )
        approvals = result.scalars().all()
    assert len(approvals) == 2
    assert [a.stage for a in approvals] == [1, 2]
    assert all(a.status == "APPROVED" for a in approvals)
    assert approvals[0].approver_user_id is not None
    assert approvals[1].approver_user_id is not None
    assert approvals[0].approver_user_id != approvals[1].approver_user_id


async def test_reject_at_first_stage_does_not_wait_for_second(client, tenant, monkeypatch):
    await _set_approval_stages(tenant["id"], 2)
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    owner_token = await _create_login_user(client, tenant, "reject-owner@example.com", "owner", monkeypatch)

    session_id = await _create_session_awaiting_approval(client, headers, headline="却下対象")

    app.state.llm.queue_structured(
        SupervisorDecision(verdict="REVISE", target_depts=["copy_dept"], instruction="直す", reason="却下対応")
    )
    app.state.llm.queue_text("## コピー案(修正後)")
    app.state.llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    app.state.llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    app.state.llm.queue_structured(ApprovalSummary(headline="修正後", qa_result="合格"))

    rejected = await client.post(
        f"/api/sessions/{session_id}/approve",
        json={"decision": "reject", "comment": "やり直し"},
        headers={**headers, "X-User-Token": owner_token},
    )
    assert rejected.status_code == 202
    assert rejected.json()["status"] == "RUNNING"  # 却下は段数を待たず即座にグラフを進める

    for _ in range(150):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        body = res.json()
        if body["status"] == "AWAITING_APPROVAL" and body["result"]["approval_summary"]["headline"] == "修正後":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("却下後の再承認待ちまで進みませんでした")
