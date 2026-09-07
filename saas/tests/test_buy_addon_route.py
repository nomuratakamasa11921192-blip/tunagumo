import pytest

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.models import Tenant as TenantModel

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_buy_addon_requires_auth(client):
    res = await client.post("/api/account/buy-addon")
    assert res.status_code == 401


async def test_buy_addon_without_stripe_customer_returns_400(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_url", "https://app.tunagumo.com")
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/account/buy-addon", headers=headers)
    assert res.status_code == 400


async def test_buy_addon_returns_checkout_url(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_url", "https://app.tunagumo.com")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = "cus_test123"
        await db.commit()

    async def fake_create(*, customer_id, success_url, cancel_url, api_key):
        assert customer_id == "cus_test123"
        return "https://checkout.stripe.com/session/xyz"

    monkeypatch.setattr("src.api.routes.account.create_addon_checkout_session", fake_create)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/account/buy-addon", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/session/xyz"
    assert body["price_jpy"] == 25000
    assert body["credit_usd"] == 5.0
