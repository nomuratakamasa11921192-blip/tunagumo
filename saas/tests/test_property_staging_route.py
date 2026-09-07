import pytest

from src.api.deps import get_higgsfield_client
from src.core.higgsfield_client import HiggsfieldError
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_stage_image_requires_auth(client):
    res = await client.post(
        "/api/property-url/stage-image",
        json={"image_url": "https://example.com/room.jpg", "instruction": "add a sofa"},
    )
    assert res.status_code == 401


async def test_stage_image_without_higgsfield_key_returns_402(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/property-url/stage-image",
        json={"image_url": "https://example.com/room.jpg", "instruction": "add a sofa"},
        headers=headers,
    )
    assert res.status_code == 402


class _FakeHiggsfieldClient:
    def __init__(self, *, fail: bool = False):
        self._fail = fail

    async def edit_image(self, image_url, instruction):
        if self._fail:
            raise HiggsfieldError("編集に失敗しました")
        return "https://cdn.example.com/staged.jpg"


async def test_stage_image_returns_staged_url(client, tenant):
    app.dependency_overrides[get_higgsfield_client] = lambda: _FakeHiggsfieldClient()
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post(
            "/api/property-url/stage-image",
            json={"image_url": "https://example.com/room.jpg", "instruction": "add a sofa"},
            headers=headers,
        )
        assert res.status_code == 201
        assert res.json()["image_url"] == "https://cdn.example.com/staged.jpg"
    finally:
        app.dependency_overrides.pop(get_higgsfield_client, None)


async def test_stage_image_propagates_failure_as_502(client, tenant):
    app.dependency_overrides[get_higgsfield_client] = lambda: _FakeHiggsfieldClient(fail=True)
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post(
            "/api/property-url/stage-image",
            json={"image_url": "https://example.com/room.jpg", "instruction": "add a sofa"},
            headers=headers,
        )
        assert res.status_code == 502
    finally:
        app.dependency_overrides.pop(get_higgsfield_client, None)
