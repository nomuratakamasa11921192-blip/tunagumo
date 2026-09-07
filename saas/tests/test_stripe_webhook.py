import hashlib
import hmac
import json
import time

import pytest

from src.core.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_SECRET = "whsec_test_secret_for_unit_tests"


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)


async def test_webhook_rejects_missing_signature(client):
    res = await client.post("/webhooks/stripe", content=b"{}")
    assert res.status_code == 400


async def test_webhook_rejects_bad_signature(client):
    body = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {}}}).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": "t=1,v1=deadbeef"}
    )
    assert res.status_code == 400


async def test_webhook_rejects_old_timestamp(client):
    body = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {}}}).encode()
    old_ts = int(time.time()) - 3600
    sig = _sign(body, timestamp=old_ts)
    res = await client.post("/webhooks/stripe", content=body, headers={"stripe-signature": sig})
    assert res.status_code == 400


async def test_webhook_deactivates_tenant_on_subscription_deleted(client, tenant):
    from src.core.db import async_session_factory
    from src.core.models import Tenant as TenantModel

    customer_id = "cus_test_deactivate"
    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = customer_id
        await db.commit()

    body = json.dumps(
        {"type": "customer.subscription.deleted", "data": {"object": {"customer": customer_id}}}
    ).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 200

    # 停止されたテナントのAPIキーはもう使えないこと
    res2 = await client.get("/api/sessions", headers={"Authorization": f"Bearer {tenant['api_key']}"})
    assert res2.status_code == 403


async def test_webhook_reactivates_tenant_on_subscription_updated_active(client, tenant):
    from src.core.db import async_session_factory
    from src.core.models import Tenant as TenantModel

    customer_id = "cus_test_reactivate"
    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = customer_id
        row.subscription_active = False
        await db.commit()

    body = json.dumps(
        {
            "type": "customer.subscription.updated",
            "data": {"object": {"customer": customer_id, "status": "active"}},
        }
    ).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 200

    res2 = await client.get("/api/sessions", headers={"Authorization": f"Bearer {tenant['api_key']}"})
    assert res2.status_code == 200


async def test_webhook_ignores_unknown_customer(client):
    body = json.dumps(
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_does_not_exist_anywhere"}},
        }
    ).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 200


async def test_webhook_returns_401_when_secret_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    body = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {}}}).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 401


async def test_webhook_adds_addon_credit_on_paid_checkout_completed(client, tenant):
    from src.core.db import async_session_factory
    from src.core.models import Tenant as TenantModel

    customer_id = "cus_test_addon"
    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = customer_id
        row.addon_credit_usd = 0.0
        await db.commit()

    body = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": customer_id, "payment_status": "paid"}},
        }
    ).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 200

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        assert row.addon_credit_usd == 5.0


async def test_webhook_ignores_unpaid_checkout_completed(client, tenant):
    from src.core.db import async_session_factory
    from src.core.models import Tenant as TenantModel

    customer_id = "cus_test_addon_unpaid"
    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.stripe_customer_id = customer_id
        row.addon_credit_usd = 0.0
        await db.commit()

    body = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": customer_id, "payment_status": "unpaid"}},
        }
    ).encode()
    res = await client.post(
        "/webhooks/stripe", content=body, headers={"stripe-signature": _sign(body)}
    )
    assert res.status_code == 200

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        assert row.addon_credit_usd == 0.0
