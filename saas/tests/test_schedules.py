"""顧客セルフサービス版の定期実行API(/api/schedules)。src/admin/routes.pyの管理者版
(test_admin_schedules.py)と違い、こちらは顧客自身のAPIキーで、自分のテナント分だけを
操作できることを確認する(他テナントのスケジュールには一切触れられないこと含む)。
"""

import uuid

import pytest
from sqlalchemy import delete, select

from src.api.deps import hash_api_key
from src.core.db import async_session_factory
from src.core.models import Schedule, ScheduleRun, Tenant

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
        schedule_ids = (await db.execute(select(Schedule.id).where(Schedule.tenant_id == t.id))).scalars().all()
        if schedule_ids:
            await db.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id.in_(schedule_ids)))
        await db.execute(delete(Schedule).where(Schedule.tenant_id == t.id))
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


def _headers(tenant: dict) -> dict:
    return {"Authorization": f"Bearer {tenant['api_key']}"}


async def test_create_schedule_requires_auth(client):
    res = await client.post("/api/schedules", json={"cron": "0 9 * * 1", "goal": "週次レポート作成"})
    assert res.status_code == 401


async def test_create_schedule_succeeds(client, tenant):
    res = await client.post(
        "/api/schedules",
        json={"cron": "0 9 * * 1", "timezone": "Asia/Tokyo", "goal": "週次レポート作成"},
        headers=_headers(tenant),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["cron"] == "0 9 * * 1"
    assert body["goal"] == "週次レポート作成"
    assert body["enabled"] is True
    assert body["consecutive_failures"] == 0
    # 顧客からは予算上限等を指定させない設計なので、レスポンスにも含めない
    assert "tenant_id" not in body
    assert "max_budget_usd" not in body


async def test_create_schedule_rejects_invalid_cron(client, tenant):
    res = await client.post(
        "/api/schedules", json={"cron": "not a cron expression", "goal": "x"}, headers=_headers(tenant)
    )
    assert res.status_code == 400


async def test_create_schedule_rejects_invalid_timezone(client, tenant):
    res = await client.post(
        "/api/schedules",
        json={"cron": "0 9 * * 1", "timezone": "Not/ARealZone", "goal": "x"},
        headers=_headers(tenant),
    )
    assert res.status_code == 400


async def test_list_schedules_returns_only_own_tenant(client, tenant, other_tenant):
    await client.post("/api/schedules", json={"cron": "0 9 * * 1", "goal": "自分の分"}, headers=_headers(tenant))
    await client.post(
        "/api/schedules", json={"cron": "0 10 * * 1", "goal": "他社の分"}, headers=_headers(other_tenant)
    )

    res = await client.get("/api/schedules", headers=_headers(tenant))
    assert res.status_code == 200
    goals = [s["goal"] for s in res.json()["schedules"]]
    assert "自分の分" in goals
    assert "他社の分" not in goals


async def test_update_schedule_disables_and_enables(client, tenant):
    created = await client.post("/api/schedules", json={"cron": "0 9 * * 1", "goal": "x"}, headers=_headers(tenant))
    schedule_id = created.json()["id"]

    disabled = await client.patch(
        f"/api/schedules/{schedule_id}", json={"enabled": False}, headers=_headers(tenant)
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    enabled = await client.patch(
        f"/api/schedules/{schedule_id}", json={"enabled": True}, headers=_headers(tenant)
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


async def test_update_unknown_schedule_returns_404(client, tenant):
    res = await client.patch(
        "/api/schedules/00000000-0000-0000-0000-000000000000",
        json={"enabled": True},
        headers=_headers(tenant),
    )
    assert res.status_code == 404


async def test_cannot_update_another_tenants_schedule(client, tenant, other_tenant):
    created = await client.post(
        "/api/schedules", json={"cron": "0 9 * * 1", "goal": "他社の分"}, headers=_headers(other_tenant)
    )
    schedule_id = created.json()["id"]

    res = await client.patch(
        f"/api/schedules/{schedule_id}", json={"enabled": False}, headers=_headers(tenant)
    )
    assert res.status_code == 404


async def test_cannot_delete_another_tenants_schedule(client, tenant, other_tenant):
    created = await client.post(
        "/api/schedules", json={"cron": "0 9 * * 1", "goal": "他社の分"}, headers=_headers(other_tenant)
    )
    schedule_id = created.json()["id"]

    res = await client.delete(f"/api/schedules/{schedule_id}", headers=_headers(tenant))
    assert res.status_code == 404

    # 他社側からは変わらず見えること(消えていないことの確認)
    still_there = await client.get("/api/schedules", headers=_headers(other_tenant))
    assert schedule_id in [s["id"] for s in still_there.json()["schedules"]]


async def test_delete_own_schedule(client, tenant):
    created = await client.post("/api/schedules", json={"cron": "0 9 * * 1", "goal": "x"}, headers=_headers(tenant))
    schedule_id = created.json()["id"]

    res = await client.delete(f"/api/schedules/{schedule_id}", headers=_headers(tenant))
    assert res.status_code == 204

    list_res = await client.get("/api/schedules", headers=_headers(tenant))
    assert schedule_id not in [s["id"] for s in list_res.json()["schedules"]]
