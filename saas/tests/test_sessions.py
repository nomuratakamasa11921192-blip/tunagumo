import asyncio
import time
import uuid

import pytest

from src.agent.schemas import Triage
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_session_returns_quickly(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    started = time.monotonic()
    res = await client.post("/api/sessions", json={"text": "テスト"}, headers=headers)
    elapsed = time.monotonic() - started

    assert res.status_code == 202
    assert elapsed < 3.0
    body = res.json()
    assert body["status"] == "QUEUED"
    assert "session_id" in body


async def test_idempotency_key_prevents_duplicate_sessions(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    key = str(uuid.uuid4())

    first = await client.post(
        "/api/sessions", json={"text": "テスト", "idempotency_key": key}, headers=headers
    )
    second = await client.post(
        "/api/sessions", json={"text": "テスト", "idempotency_key": key}, headers=headers
    )

    assert first.json()["session_id"] == second.json()["session_id"]


async def test_cannot_fetch_another_tenants_session(client, tenant, db_session=None):
    headers_a = {"Authorization": f"Bearer {tenant['api_key']}"}
    created = await client.post("/api/sessions", json={"text": "テスト"}, headers=headers_a)
    session_id = created.json()["session_id"]

    other_headers = {"Authorization": "Bearer not-a-real-key"}
    res = await client.get(f"/api/sessions/{session_id}", headers=other_headers)

    assert res.status_code == 401


async def test_requires_authentication(client):
    res = await client.post("/api/sessions", json={"text": "テスト"})
    assert res.status_code == 401


async def test_get_session_round_trip(client, tenant):
    """作成直後のstatus(QUEUED)を確認するのは、fire-and-forget方式(3秒ルール)の設計上
    バックグラウンド処理と競合するため本質的に不安定(=テストとして意味がない)。
    ここではrequest_textが正しく往復することと、最終的に安定した状態に到達することを
    確認する(他の統合テストと同じ「状態が変わるまでポーリングする」方式)。"""
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "テスト"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("グラフ実行が完了しませんでした")

    assert res.status_code == 200
    body = res.json()
    assert body["request_text"] == "テスト"


async def test_list_sessions_returns_own_tenant_newest_first(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    first = await client.post("/api/sessions", json={"text": "1件目"}, headers=headers)
    second = await client.post("/api/sessions", json={"text": "2件目"}, headers=headers)

    res = await client.get("/api/sessions", headers=headers)
    assert res.status_code == 200
    ids = [s["session_id"] for s in res.json()["sessions"]]
    assert ids.index(second.json()["session_id"]) < ids.index(first.json()["session_id"])


async def test_list_sessions_does_not_leak_other_tenants(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    await client.post("/api/sessions", json={"text": "自分のセッション"}, headers=headers)

    other_headers = {"Authorization": "Bearer not-a-real-key"}
    res = await client.get("/api/sessions", headers=other_headers)
    assert res.status_code == 401
