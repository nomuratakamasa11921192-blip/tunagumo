import httpx
import pytest

from src.core.email import send_email

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_send_email_skips_when_resend_not_configured(monkeypatch):
    monkeypatch.setattr("src.core.email.settings.resend_api_key", "")
    monkeypatch.setattr("src.core.email.settings.resend_from_email", "")

    sent = await send_email(to="customer@example.com", subject="件名", html="<p>本文</p>")

    assert sent is False


async def test_send_email_succeeds_when_resend_returns_2xx(monkeypatch):
    monkeypatch.setattr("src.core.email.settings.resend_api_key", "re_test_key")
    monkeypatch.setattr("src.core.email.settings.resend_from_email", "ツナグモ <notify@example.com>")

    async def fake_post(self, url, headers=None, json=None):
        assert url == "https://api.resend.com/emails"
        assert headers["Authorization"] == "Bearer re_test_key"
        assert json["to"] == ["customer@example.com"]
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=200, request=request, json={"id": "abc"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    sent = await send_email(to="customer@example.com", subject="件名", html="<p>本文</p>")

    assert sent is True


async def test_send_email_returns_false_on_resend_error_response(monkeypatch):
    monkeypatch.setattr("src.core.email.settings.resend_api_key", "re_test_key")
    monkeypatch.setattr("src.core.email.settings.resend_from_email", "notify@example.com")

    async def fake_post(self, url, headers=None, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code=422, request=request, json={"message": "invalid"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    sent = await send_email(to="customer@example.com", subject="件名", html="<p>本文</p>")

    assert sent is False


async def test_send_email_returns_false_on_network_error(monkeypatch):
    monkeypatch.setattr("src.core.email.settings.resend_api_key", "re_test_key")
    monkeypatch.setattr("src.core.email.settings.resend_from_email", "notify@example.com")

    async def fake_post(self, url, headers=None, json=None):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    sent = await send_email(to="customer@example.com", subject="件名", html="<p>本文</p>")

    assert sent is False
