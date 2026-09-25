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

    usage = await client.get("/api/account/usage", headers=headers)
    assert usage.json()["higgsfield_key_registered"] is True
    # キーの中身はレスポンスに含めない
    assert "hf_key" not in usage.text


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
    assert body["this_month_session_count"] == 0
    assert body["all_time_session_count"] == 0
    assert body["plan"] == "light"
    assert body["ai_usage_percent"] == 0
    assert body["ai_remaining_percent"] == 100
    assert body["addon_purchased"] is False
    assert not any("usd" in key or "cost" in key or "budget" in key for key in body)
    assert body["higgsfield_key_registered"] is False


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


async def test_login_does_not_block_the_server(client, tenant):
    """パスワード照合(0.5秒ほどかかる)の間もサーバーが他の処理を続けられること。
    照合を別スレッドで動かしていないと、その間サーバー全体が止まる(全顧客の操作が待たされる)。"""
    import asyncio

    from src.core.models import TenantUser
    from src.core.passwords import hash_password

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    async with async_session_factory() as db:
        db.add(
            TenantUser(
                tenant_id=tenant["id"], email="login-test@example.com", role="member",
                is_active=True, password_hash=await asyncio.to_thread(hash_password, "correct-password"),
            )
        )
        await db.commit()

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        res = await client.post(
            "/api/account/login",
            json={"email": "login-test@example.com", "password": "wrong-password"},
            headers=headers,
        )
    finally:
        beat.cancel()

    assert res.status_code == 401
    assert ticks >= 10, f"照合中にサーバーが止まっていた(他の処理が{ticks}回しか動けていない)"


@pytest.mark.parametrize(
    "plan,cost,addon,previous_month,usage,remaining",
    [
        ("light", 4.0, 0.0, False, 40, 60),
        ("light", 4.0, 5.0, False, 40, 110),
        ("light", 10.0, 3.0, False, 100, 30),
        ("light", 10.0, 0.0, False, 100, 0),
        ("light", 10.0, 5.0, True, 0, 150),
        ("unknown", 4.0, 0.0, False, 40, 60),
        ("standard", 20.0, 0.0, False, 50, 50),
    ],
)
async def test_usage_reports_percentages_without_exposing_or_mutating_costs(
    client, tenant, plan, cost, addon, previous_month, usage, remaining
):
    from datetime import datetime, timedelta

    started = datetime.utcnow() - timedelta(days=45 if previous_month else 0)
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.plan = plan
        row.ai_cost_this_period_usd = cost
        row.addon_credit_usd = addon
        row.ai_cost_period_started_at = started
        await db.commit()

    res = await client.get("/api/account/usage", headers={"Authorization": f"Bearer {tenant['api_key']}"})
    assert res.status_code == 200
    body = res.json()
    assert body["ai_usage_percent"] == usage
    assert body["ai_remaining_percent"] == remaining
    assert body["addon_purchased"] is (addon > 0)
    assert set(body) == {
        "this_month_session_count", "all_time_session_count", "plan",
        "ai_usage_percent", "ai_remaining_percent", "addon_purchased", "higgsfield_key_registered",
        "plan_label", "usage_scope", "usage_state", "resets_at", "self_service_addon",
    }
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.ai_cost_this_period_usd == cost
        assert row.addon_credit_usd == addon


async def test_usage_requires_authentication(client):
    res = await client.get("/api/account/usage")
    assert res.status_code == 401
