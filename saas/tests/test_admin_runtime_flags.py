import pytest

from src.core.config import settings
from src.core.runtime_flags import set_actions_enabled

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


@pytest.fixture(autouse=True)
async def _reset_flag():
    await set_actions_enabled(False)
    yield
    await set_actions_enabled(False)


async def test_requires_admin_auth(client):
    res = await client.get("/admin/runtime-flags/actions-enabled")
    assert res.status_code == 401


async def test_defaults_to_disabled(client):
    res = await client.get("/admin/runtime-flags/actions-enabled", headers=admin_headers())
    assert res.status_code == 200
    assert res.json()["actions_enabled"] is False


async def test_can_enable_and_disable(client):
    enable_res = await client.put(
        "/admin/runtime-flags/actions-enabled", json={"enabled": True}, headers=admin_headers()
    )
    assert enable_res.status_code == 200
    assert enable_res.json()["actions_enabled"] is True

    get_res = await client.get("/admin/runtime-flags/actions-enabled", headers=admin_headers())
    assert get_res.json()["actions_enabled"] is True

    disable_res = await client.put(
        "/admin/runtime-flags/actions-enabled", json={"enabled": False}, headers=admin_headers()
    )
    assert disable_res.json()["actions_enabled"] is False
