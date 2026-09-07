import uuid

import pytest

from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.core.db import async_session_factory
from src.core.models import Tenant, TenantUser

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


async def _add_approver(tenant_id, role: str = "approver") -> uuid.UUID:
    async with async_session_factory() as db:
        row = TenantUser(
            tenant_id=tenant_id,
            email=f"{uuid.uuid4()}@example.com",
            role=role,
            is_active=True,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def test_approval_stages_requires_admin_auth(client, tenant):
    res = await client.put(f"/admin/tenants/{tenant['id']}/approval-stages", json={"approval_stages": 2})
    assert res.status_code == 401


async def test_set_approval_stages(client, tenant):
    # 付録J: 多段階承認を有効にするには、approver/ownerロールが最低2名必要
    await _add_approver(tenant["id"], role="approver")
    await _add_approver(tenant["id"], role="owner")

    res = await client.put(
        f"/admin/tenants/{tenant['id']}/approval-stages",
        json={"approval_stages": 3},
        headers=admin_headers(),
    )
    assert res.status_code == 200
    assert res.json()["approval_stages"] == 3

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.approval_stages == 3


async def test_set_approval_stages_rejects_when_fewer_than_two_approvers(client, tenant):
    """付録J「承認者ゼロ問題」対策: approver/ownerが1名以下では多段階承認を有効化できない
    (1名が退職・異動しただけで全案件が承認待ちのまま詰まるのを防ぐため)。"""
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/approval-stages",
        json={"approval_stages": 2},
        headers=admin_headers(),
    )
    assert res.status_code == 400

    await _add_approver(tenant["id"], role="approver")  # 1名だけではまだ足りない
    res2 = await client.put(
        f"/admin/tenants/{tenant['id']}/approval-stages",
        json={"approval_stages": 2},
        headers=admin_headers(),
    )
    assert res2.status_code == 400

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.approval_stages == 1  # 拒否されたままなので既定値のまま


async def test_set_approval_stages_rejects_out_of_range(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/approval-stages",
        json={"approval_stages": 0},
        headers=admin_headers(),
    )
    assert res.status_code == 400

    res2 = await client.put(
        f"/admin/tenants/{tenant['id']}/approval-stages",
        json={"approval_stages": 11},
        headers=admin_headers(),
    )
    assert res2.status_code == 400


async def test_configure_line_channel_encrypts_secrets(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/line-channel",
        json={"line_channel_secret": "line-secret-value", "line_channel_access_token": "line-token-value"},
        headers=admin_headers(),
    )
    assert res.status_code == 200
    assert res.json()["configured"] is True

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.line_channel_secret != "line-secret-value"  # 平文のまま保存されていない
        assert decrypt_secret(row.line_channel_secret) == "line-secret-value"
        assert decrypt_secret(row.line_channel_access_token) == "line-token-value"


async def test_disable_line_channel_clears_fields(client, tenant):
    await client.put(
        f"/admin/tenants/{tenant['id']}/line-channel",
        json={"line_channel_secret": "s", "line_channel_access_token": "t"},
        headers=admin_headers(),
    )
    res = await client.delete(f"/admin/tenants/{tenant['id']}/line-channel", headers=admin_headers())
    assert res.status_code == 204

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.line_channel_secret is None
        assert row.line_channel_access_token is None
