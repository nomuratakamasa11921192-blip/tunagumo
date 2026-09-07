import uuid

import pytest
from sqlalchemy import delete

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.models import Tenant, WebChatSession

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == tenant_id))
        await db.commit()


async def test_requires_admin_auth(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/web-widget", json={"allowed_origin": "https://example.com"}
    )
    assert res.status_code == 401


async def test_configure_widget_issues_public_key(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/web-widget",
        json={"allowed_origin": "https://example.com"},
        headers=admin_headers(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["public_key"].startswith("wpk_")
    assert body["allowed_origin"] == "https://example.com"

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.web_widget_public_key == body["public_key"]
        row.web_widget_public_key = None
        row.web_widget_allowed_origin = None
        await db.commit()


async def test_configure_widget_rejects_non_https_origin(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/web-widget",
        json={"allowed_origin": "example.com"},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_configure_widget_strips_trailing_slash(client, tenant):
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/web-widget",
        json={"allowed_origin": "https://example.com/"},
        headers=admin_headers(),
    )
    assert res.json()["allowed_origin"] == "https://example.com"

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.web_widget_public_key = None
        row.web_widget_allowed_origin = None
        await db.commit()


async def test_disable_widget_clears_config(client, tenant):
    await client.put(
        f"/admin/tenants/{tenant['id']}/web-widget",
        json={"allowed_origin": "https://example.com"},
        headers=admin_headers(),
    )

    res = await client.delete(f"/admin/tenants/{tenant['id']}/web-widget", headers=admin_headers())
    assert res.status_code == 204

    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert row.web_widget_public_key is None
        assert row.web_widget_allowed_origin is None


async def test_list_escalated_chats_returns_only_escalated(client, tenant):
    try:
        async with async_session_factory() as db:
            db.add(
                WebChatSession(
                    tenant_id=tenant["id"],
                    messages=[{"role": "user", "content": "料金は？"}],
                    escalated=True,
                )
            )
            db.add(WebChatSession(tenant_id=tenant["id"], messages=[{"role": "user", "content": "普通の質問"}]))
            await db.commit()

        res = await client.get(f"/admin/tenants/{tenant['id']}/web-chat/escalated", headers=admin_headers())
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["messages"][0]["content"] == "料金は？"
    finally:
        await _cleanup(tenant["id"])
