import pytest

from src.api.deps import get_image_client
from src.core.ai_budget import ESTIMATED_OPENAI_IMAGE_COST_USD
from src.core.db import async_session_factory
from src.core.models import Tenant as TenantModel
from src.core.openai_image_client import OpenAIImageError
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BODY = {"image_url": "https://example.com/room.jpg", "instruction": "add a sofa"}


async def test_stage_image_requires_auth(client):
    res = await client.post("/api/property-url/stage-image", json=_BODY)
    assert res.status_code == 401


async def test_stage_image_without_operator_openai_key_returns_402(client, tenant):
    # 画像編集は運営のOpenAIキーを使う。運営側キーが未設定(get_image_clientがNone)なら402。
    app.dependency_overrides[get_image_client] = lambda: None
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post("/api/property-url/stage-image", json=_BODY, headers=headers)
        assert res.status_code == 402
    finally:
        app.dependency_overrides.pop(get_image_client, None)


class _FakeImageClient:
    def __init__(self, *, fail: bool = False, cost: float = 0.12):
        self._fail = fail
        self._cost = cost
        self.last_cost_usd = 0.0

    async def edit_image(self, image_url, instruction):
        if self._fail:
            raise OpenAIImageError("画像の作成に失敗しました。")
        self.last_cost_usd = self._cost
        return "/api/generated-images/" + "a" * 32 + ".jpg"


async def _cost(tenant_id) -> float:
    async with async_session_factory() as db:
        return (await db.get(TenantModel, tenant_id)).ai_cost_this_period_usd


async def test_stage_image_returns_url_and_records_actual_cost(client, tenant):
    app.dependency_overrides[get_image_client] = lambda: _FakeImageClient(cost=0.12)
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post("/api/property-url/stage-image", json=_BODY, headers=headers)
        assert res.status_code == 201
        assert res.json()["image_url"] == "/api/generated-images/" + "a" * 32 + ".jpg"
        # 画像は運営負担なので、実コストが月間AI予算から差し引かれる
        assert await _cost(tenant["id"]) == pytest.approx(0.12)
    finally:
        app.dependency_overrides.pop(get_image_client, None)


async def test_stage_image_falls_back_to_estimated_cost_without_usage(client, tenant):
    app.dependency_overrides[get_image_client] = lambda: _FakeImageClient(cost=0.0)
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post("/api/property-url/stage-image", json=_BODY, headers=headers)
        assert res.status_code == 201
        assert await _cost(tenant["id"]) == pytest.approx(ESTIMATED_OPENAI_IMAGE_COST_USD)
    finally:
        app.dependency_overrides.pop(get_image_client, None)


async def test_stage_image_propagates_failure_as_502_without_cost(client, tenant):
    app.dependency_overrides[get_image_client] = lambda: _FakeImageClient(fail=True)
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post("/api/property-url/stage-image", json=_BODY, headers=headers)
        assert res.status_code == 502
        assert await _cost(tenant["id"]) == 0
    finally:
        app.dependency_overrides.pop(get_image_client, None)


async def test_stage_image_blocked_when_budget_exhausted(client, tenant):
    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.plan = "light"
        row.ai_cost_this_period_usd = 10.0  # lightプランの月間予算($10)を使い切った状態
        await db.commit()

    app.dependency_overrides[get_image_client] = lambda: _FakeImageClient()
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        res = await client.post("/api/property-url/stage-image", json=_BODY, headers=headers)
        assert res.status_code == 429
    finally:
        app.dependency_overrides.pop(get_image_client, None)


class _FakeGenerateClient:
    def __init__(self):
        self.last_cost_usd = 0.0

    async def generate_image(self, prompt, quality="draft"):
        self.last_cost_usd = 0.05
        return "/api/generated-images/" + "f" * 32 + ".jpg"


async def test_generate_session_image_records_cost_and_saves_url(client, tenant):
    from tests.test_video_quota_route import _create_awaiting_approval_session

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers)
    before = await _cost(tenant["id"])

    app.dependency_overrides[get_image_client] = lambda: _FakeGenerateClient()
    try:
        res = await client.post(
            f"/api/sessions/{session_id}/generate-image", json={"quality": "draft"}, headers=headers
        )
    finally:
        app.dependency_overrides.pop(get_image_client, None)

    assert res.status_code == 201
    assert await _cost(tenant["id"]) == pytest.approx(before + 0.05)
    detail = await client.get(f"/api/sessions/{session_id}", headers=headers)
    assert detail.json()["result"]["generated_images"][-1]["url"].endswith("f" * 32 + ".jpg")


async def test_stage_image_rejects_overlong_instruction(client, tenant):
    app.dependency_overrides[get_image_client] = lambda: _FakeImageClient()
    try:
        headers = {"Authorization": f"Bearer {tenant['api_key']}"}
        body = {"image_url": "https://example.com/room.jpg", "instruction": "あ" * 1001}
        res = await client.post("/api/property-url/stage-image", json=body, headers=headers)
        assert res.status_code == 422
        assert await _cost(tenant["id"]) == 0
    finally:
        app.dependency_overrides.pop(get_image_client, None)
