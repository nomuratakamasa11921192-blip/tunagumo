"""Phase 10: テナント分離の自動テスト。他テナントのレコードがSELECT/UPDATE/DELETEで
一切触れないことを、実際のAPI経由で確認する(このアーキテクチャは「1社1VPS」ではなく
共有DB+tenant_idカラムでの分離なので、各クエリが正しくtenant_idで絞り込まれている
ことが分離の生命線になる)。
"""

import uuid

import pytest
from sqlalchemy import select

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.api.deps import hash_api_key
from src.core.db import async_session_factory
from src.core.models import Chunk, Document, Tenant
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def other_tenant():
    api_key = f"test-key-{uuid.uuid4()}"
    async with async_session_factory() as db:
        t = Tenant(name="pytest-other-tenant", api_key_hash=hash_api_key(api_key))
        db.add(t)
        await db.commit()
        await db.refresh(t)

    yield {"id": t.id, "api_key": api_key}

    async with async_session_factory() as db:
        doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == t.id))).scalars().all()
        if doc_ids:
            await db.execute(Chunk.__table__.delete().where(Chunk.document_id.in_(doc_ids)))
            await db.execute(Document.__table__.delete().where(Document.tenant_id == t.id))
        await db.execute(Tenant.__table__.delete().where(Tenant.id == t.id))
        await db.commit()


async def _create_awaiting_approval_session(client, headers) -> str:
    app.state.llm.queue_structured(Triage(intent="WORK_REQUEST", goal="コピーを作って", reason="業務依頼"))
    app.state.llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    app.state.llm.queue_text("## コピー案")
    app.state.llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    app.state.llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    app.state.llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "コピーを作って"}, headers=headers)
    session_id = created.json()["session_id"]

    import asyncio

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("承認待ちまで進みませんでした")

    return session_id


async def test_cannot_read_other_tenants_session(client, tenant, other_tenant):
    owner_headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}

    created = await client.post("/api/sessions", json={"text": "テスト依頼"}, headers=owner_headers)
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))
    session_id = created.json()["session_id"]

    res = await client.get(f"/api/sessions/{session_id}", headers=intruder_headers)
    assert res.status_code == 404


async def test_cannot_answer_other_tenants_session(client, tenant, other_tenant):
    owner_headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}

    app.state.llm.queue_structured(Triage(intent="WORK_REQUEST", goal="販促", reason="業務依頼"))
    app.state.llm.queue_structured(
        SupervisorDecision(verdict="CLARIFY", missing_info=["予算"], reason="情報不足")
    )
    created = await client.post("/api/sessions", json={"text": "販促やって"}, headers=owner_headers)
    session_id = created.json()["session_id"]

    import asyncio

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=owner_headers)
        if res.json()["status"] == "CLARIFYING":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("要件確認で停止しませんでした")

    res = await client.post(
        f"/api/sessions/{session_id}/answer", json={"answer": "妨害"}, headers=intruder_headers
    )
    assert res.status_code == 404

    # 妨害後も、正当な所有者は問題なく回答できること(他テナントの介入で壊れていないか)
    res2 = await client.post(
        f"/api/sessions/{session_id}/answer", json={"answer": {"予算": "50万円"}}, headers=owner_headers
    )
    assert res2.status_code == 202


async def test_cannot_approve_other_tenants_session(client, tenant, other_tenant):
    owner_headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}

    session_id = await _create_awaiting_approval_session(client, owner_headers)

    res = await client.post(
        f"/api/sessions/{session_id}/approve", json={"decision": "approve"}, headers=intruder_headers
    )
    assert res.status_code == 404


async def test_cannot_list_other_tenants_sessions(client, tenant, other_tenant):
    owner_headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}

    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))
    await client.post("/api/sessions", json={"text": "テスト依頼"}, headers=owner_headers)

    res = await client.get("/api/sessions", headers=intruder_headers)
    assert res.status_code == 200
    assert res.json()["sessions"] == []


async def test_cannot_read_or_delete_other_tenants_document(client, tenant, other_tenant):
    import asyncio

    from src.api.deps import get_embedding_provider
    from tests.fakes import FakeEmbeddingProvider

    owner_headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}

    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    try:
        files = {"file": ("secret.txt", b"tenant-a secret content", "text/plain")}
        created = await client.post("/api/documents", files=files, headers=owner_headers)
        document_id = created.json()["document_id"]

        res = await client.get(f"/api/documents/{document_id}", headers=intruder_headers)
        assert res.status_code == 404

        res2 = await client.delete(f"/api/documents/{document_id}", headers=intruder_headers)
        assert res2.status_code == 404

        list_res = await client.get("/api/documents", headers=intruder_headers)
        assert list_res.json()["documents"] == []

        # バックグラウンドの取り込み(チャンク作成)が終わってからテストを終える。
        # 先に終えると、テナントfixtureの後片付け(DELETE)と競合しうるため。
        for _ in range(100):
            detail = await client.get(f"/api/documents/{document_id}", headers=owner_headers)
            if detail.json()["status"] in ("ACTIVE", "FAILED"):
                break
            await asyncio.sleep(0.02)
        else:
            raise TimeoutError("文書の取り込みが完了しませんでした")
    finally:
        app.dependency_overrides.pop(get_embedding_provider, None)


async def test_cannot_update_other_tenants_anthropic_key(client, tenant, other_tenant):
    """PATCHは対象を検索条件でなくtenant依存で更新するので、そもそも他テナントの行に
    到達できない設計になっているはず(自分自身のtenant.idしか使わない)ことを確認する。"""
    intruder_headers = {"Authorization": f"Bearer {other_tenant['api_key']}"}
    res = await client.patch(
        "/api/account/anthropic-key",
        json={"anthropic_api_key": "sk-ant-hijacked0000000000000000000"},
        headers=intruder_headers,
    )
    assert res.status_code == 204  # 自分自身のキーとしてなら更新できる(妨害ではない)

    # 元のテナントのキーは変わっていないこと
    async with async_session_factory() as db:
        original = await db.get(Tenant, tenant["id"])
        from src.core.crypto import decrypt_secret

        assert decrypt_secret(original.anthropic_api_key) != "sk-ant-hijacked0000000000000000000"
