import httpx
import pytest

from src.core.stripe_client import (
    ADDON_PRICE_JPY,
    StripeClientError,
    create_addon_checkout_session,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_addon_checkout_session_returns_url(monkeypatch):
    async def fake_post(self, url, data=None):
        assert url == "https://api.stripe.com/v1/checkout/sessions"
        assert data["customer"] == "cus_test123"
        assert data["mode"] == "payment"
        assert data["line_items[0][price_data][unit_amount]"] == str(ADDON_PRICE_JPY)
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"url": "https://checkout.stripe.com/session/abc"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    url = await create_addon_checkout_session(
        customer_id="cus_test123",
        success_url="https://app.tunagumo.com/?addon=success",
        cancel_url="https://app.tunagumo.com/?addon=cancelled",
        api_key="sk_test_dummy",
    )

    assert url == "https://checkout.stripe.com/session/abc"


async def test_create_addon_checkout_session_rejects_empty_api_key():
    with pytest.raises(StripeClientError):
        await create_addon_checkout_session(
            customer_id="cus_test123",
            success_url="https://app.tunagumo.com/?addon=success",
            cancel_url="https://app.tunagumo.com/?addon=cancelled",
            api_key="",
        )


async def test_create_addon_checkout_session_raises_on_error_status(monkeypatch):
    async def fake_post(self, url, data=None):
        request = httpx.Request("POST", url)
        return httpx.Response(402, request=request, text="card declined")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(StripeClientError):
        await create_addon_checkout_session(
            customer_id="cus_test123",
            success_url="https://app.tunagumo.com/?addon=success",
            cancel_url="https://app.tunagumo.com/?addon=cancelled",
            api_key="sk_test_dummy",
        )


async def test_create_addon_checkout_session_raises_when_url_missing(monkeypatch):
    async def fake_post(self, url, data=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(StripeClientError):
        await create_addon_checkout_session(
            customer_id="cus_test123",
            success_url="https://app.tunagumo.com/?addon=success",
            cancel_url="https://app.tunagumo.com/?addon=cancelled",
            api_key="sk_test_dummy",
        )
