import pytest

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.models import Tenant as TenantModel

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_billing_portal_requires_auth(client):
    res = await client.post("/api/account/billing-portal")
    assert res.status_code == 401


async def test_billing_portal_without_stripe_customer_returns_400(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_url", "https://app.tunagumo.com")
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/account/billing-portal", headers=headers)
    assert res.status_code == 400


async def test_billing_portal_returns_portal_url(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "saas_public_url", "https://app.tunagumo.com")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = "cus_test123"
        await db.commit()

    async def fake_create(*, customer_id, return_url, api_key):
        assert customer_id == "cus_test123"
        assert return_url == "https://app.tunagumo.com"
        return "https://billing.stripe.com/session/xyz"

    monkeypatch.setattr("src.api.routes.account.create_billing_portal_session", fake_create)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/account/billing-portal", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["portal_url"] == "https://billing.stripe.com/session/xyz"
