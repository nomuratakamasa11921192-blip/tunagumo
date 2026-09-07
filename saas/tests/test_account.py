import pytest

from src.core.crypto import decrypt_secret
from src.core.db import async_session_factory
from src.core.models import Tenant

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_customer_can_update_own_anthropic_key(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    new_key = "sk-ant-api03-newkeyfromselfservice000000"

    res = await client.patch(
        "/api/account/anthropic-key", json={"anthropic_api_key": new_key}, headers=headers
    )
    assert res.status_code == 204

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert decrypt_secret(row.anthropic_api_key) == new_key


async def test_customer_key_update_rejects_malformed_key(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.patch(
        "/api/account/anthropic-key", json={"anthropic_api_key": "not-a-real-key"}, headers=headers
    )
    assert res.status_code == 400


async def test_customer_key_update_requires_auth(client):
    res = await client.patch(
        "/api/account/anthropic-key", json={"anthropic_api_key": "sk-ant-api03-x"}
    )
    assert res.status_code == 401


async def test_customer_cannot_update_another_tenants_key(client, tenant):
    """自分のAPIキーでは、自分のテナントしか更新できないこと(認証されたテナントに
    対してのみ操作する設計なので、テナントIDを指定する余地が無いことを確認する)。"""
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    new_key = "sk-ant-api03-onlyaffectsownaccount0000"

    await client.patch("/api/account/anthropic-key", json={"anthropic_api_key": new_key}, headers=headers)

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert decrypt_secret(row.anthropic_api_key) == new_key


async def test_customer_can_update_own_higgsfield_key(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}

    res = await client.patch(
        "/api/account/higgsfield-key",
        json={"higgsfield_key_id": "hf_key_id_123", "higgsfield_key_secret": "hf_key_secret_456"},
        headers=headers,
    )
    assert res.status_code == 204

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert decrypt_secret(row.higgsfield_api_key_id) == "hf_key_id_123"
        assert decrypt_secret(row.higgsfield_api_key_secret) == "hf_key_secret_456"


async def test_higgsfield_key_update_rejects_empty_values(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.patch(
        "/api/account/higgsfield-key",
        json={"higgsfield_key_id": "", "higgsfield_key_secret": "hf_key_secret_456"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_usage_returns_zero_for_tenant_with_no_sessions(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.get("/api/account/usage", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["this_month_cost_usd"] == 0.0
    assert body["this_month_session_count"] == 0
    assert body["all_time_cost_usd"] == 0.0
    assert body["all_time_session_count"] == 0
    assert body["plan"] == "light"
    assert body["ai_cost_this_period_usd"] == 0.0
    assert body["monthly_ai_budget_usd"] == 10.0
    assert body["addon_credit_usd"] == 0.0


async def test_usage_reflects_completed_sessions(client, tenant):
    from src.agent.schemas import Triage

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    from src.main import app

    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "こんにちは"}, headers=headers)
    session_id = created.json()["session_id"]

    import asyncio

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.02)
    else:
        raise TimeoutError("セッションが完了しませんでした")

    usage = await client.get("/api/account/usage", headers=headers)
    assert usage.status_code == 200
    body = usage.json()
    assert body["all_time_session_count"] >= 1
    assert body["this_month_session_count"] >= 1
