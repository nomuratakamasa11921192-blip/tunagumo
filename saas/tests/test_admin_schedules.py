import pytest

from src.core.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


async def test_requires_admin_auth(client, tenant):
    res = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "goal": "週次レポート作成"},
    )
    assert res.status_code == 401


async def test_create_schedule_succeeds(client, tenant):
    res = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "timezone": "Asia/Tokyo", "goal": "週次レポート作成", "max_budget_usd": 1.0},
        headers=admin_headers(),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["cron"] == "0 9 * * 1"
    assert body["enabled"] is True
    assert body["consecutive_failures"] == 0


async def test_create_schedule_rejects_invalid_cron(client, tenant):
    res = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "not a cron expression", "goal": "x"},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_create_schedule_rejects_invalid_timezone(client, tenant):
    res = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "timezone": "Not/ARealZone", "goal": "x"},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_create_schedule_rejects_unknown_tenant(client):
    res = await client.post(
        "/admin/tenants/00000000-0000-0000-0000-000000000000/schedules",
        json={"cron": "0 9 * * 1", "goal": "x"},
        headers=admin_headers(),
    )
    assert res.status_code == 404


async def test_list_schedules_returns_only_own_tenants(client, tenant):
    await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "goal": "週次レポート作成"},
        headers=admin_headers(),
    )

    res = await client.get(f"/admin/tenants/{tenant['id']}/schedules", headers=admin_headers())
    assert res.status_code == 200
    assert len(res.json()["schedules"]) >= 1
    assert all(s["tenant_id"] == str(tenant["id"]) for s in res.json()["schedules"])


async def test_update_schedule_disables_and_enables(client, tenant):
    created = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "goal": "x"},
        headers=admin_headers(),
    )
    schedule_id = created.json()["id"]

    disabled = await client.patch(
        f"/admin/schedules/{schedule_id}", json={"enabled": False}, headers=admin_headers()
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    enabled = await client.patch(
        f"/admin/schedules/{schedule_id}", json={"enabled": True}, headers=admin_headers()
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


async def test_update_unknown_schedule_returns_404(client):
    res = await client.patch(
        "/admin/schedules/00000000-0000-0000-0000-000000000000",
        json={"enabled": True},
        headers=admin_headers(),
    )
    assert res.status_code == 404


async def test_delete_schedule(client, tenant):
    created = await client.post(
        f"/admin/tenants/{tenant['id']}/schedules",
        json={"cron": "0 9 * * 1", "goal": "x"},
        headers=admin_headers(),
    )
    schedule_id = created.json()["id"]

    res = await client.delete(f"/admin/schedules/{schedule_id}", headers=admin_headers())
    assert res.status_code == 204

    list_res = await client.get(f"/admin/tenants/{created.json()['tenant_id']}/schedules", headers=admin_headers())
    assert schedule_id not in [s["id"] for s in list_res.json()["schedules"]]
