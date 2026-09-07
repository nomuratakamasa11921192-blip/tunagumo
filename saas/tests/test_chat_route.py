"""POST/OPTIONS /api/chat/{public_key} の検証。respond()自体はtest_public_responder.pyで
別途検証済みなので、ここではルート固有の関心事(Origin検証・レート制限・入力検証・
セッション継続・CORSヘッダ)をmonkeypatchしたrespond()で検証する。
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

import src.api.routes.chat as chat_module
from src.agent.public_responder import MAX_INPUT_LENGTH, PublicResponderResult
from src.core.crypto import encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Tenant, WebChatRequestLog, WebChatSession

pytestmark = pytest.mark.asyncio(loop_scope="session")

ALLOWED_ORIGIN = "https://example.com"


async def _configure_widget(tenant_id: uuid.UUID, *, with_anthropic_key: bool = True) -> str:
    public_key = f"wpk_test_{uuid.uuid4().hex}"
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant_id)
        row.web_widget_public_key = public_key
        row.web_widget_allowed_origin = ALLOWED_ORIGIN
        # tenantフィクスチャは既定でanthropic_api_keyを設定済みなので、
        # with_anthropic_key=Falseの場合は明示的にクリアする
        row.anthropic_api_key = encrypt_secret("sk-ant-test00000000000000000000") if with_anthropic_key else None
        await db.commit()
    return public_key


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.tenant_id == tenant_id))
        await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == tenant_id))
        await db.commit()


@pytest.fixture(autouse=True)
def _mock_respond(monkeypatch):
    async def fake_respond(**kwargs):
        return PublicResponderResult(reply="ご質問ありがとうございます。", escalated=False, reason="test", usage={})

    monkeypatch.setattr(chat_module, "respond", fake_respond)


async def test_unknown_public_key_returns_404(client):
    res = await client.post(
        "/api/chat/does-not-exist", json={"message": "こんにちは"}, headers={"Origin": ALLOWED_ORIGIN}
    )
    assert res.status_code == 404


async def test_mismatched_origin_returns_403(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(
            f"/api/chat/{public_key}", json={"message": "こんにちは"}, headers={"Origin": "https://evil.example"}
        )
        assert res.status_code == 403
    finally:
        await _cleanup(tenant["id"])


async def test_missing_origin_header_returns_403(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(f"/api/chat/{public_key}", json={"message": "こんにちは"})
        assert res.status_code == 403
    finally:
        await _cleanup(tenant["id"])


async def test_empty_message_returns_400(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(
            f"/api/chat/{public_key}", json={"message": "   "}, headers={"Origin": ALLOWED_ORIGIN}
        )
        assert res.status_code == 400
    finally:
        await _cleanup(tenant["id"])


async def test_too_long_message_returns_400(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(
            f"/api/chat/{public_key}",
            json={"message": "あ" * (MAX_INPUT_LENGTH + 1)},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert res.status_code == 400
    finally:
        await _cleanup(tenant["id"])


async def test_successful_chat_returns_reply_with_cors_header(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(
            f"/api/chat/{public_key}", json={"message": "こんにちは"}, headers={"Origin": ALLOWED_ORIGIN}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["reply"] == "ご質問ありがとうございます。"
        assert body["escalated"] is False
        assert "session_id" in body
        assert res.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    finally:
        await _cleanup(tenant["id"])


async def test_session_continues_across_requests(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        first = await client.post(
            f"/api/chat/{public_key}", json={"message": "1つ目"}, headers={"Origin": ALLOWED_ORIGIN}
        )
        session_id = first.json()["session_id"]

        second = await client.post(
            f"/api/chat/{public_key}",
            json={"session_id": session_id, "message": "2つ目"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert second.json()["session_id"] == session_id

        async with async_session_factory() as db:
            row = await db.get(WebChatSession, uuid.UUID(session_id))
            assert row.message_count == 2
    finally:
        await _cleanup(tenant["id"])


async def test_missing_anthropic_key_falls_back_to_busy_message_without_error(client, tenant, monkeypatch):
    # 2026-09-01: BYOK廃止によりAI呼び出しは運営(ツナグモ)自身のキーで行うため、
    # ここでのフェイルセーフは運営側キー未設定(settings.anthropic_api_key)をシミュレートする。
    from src.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")

    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.post(
            f"/api/chat/{public_key}", json={"message": "こんにちは"}, headers={"Origin": ALLOWED_ORIGIN}
        )
        assert res.status_code == 200
        assert res.json()["escalated"] is True
    finally:
        await _cleanup(tenant["id"])


async def test_rate_limited_ip_gets_busy_message_not_llm_reply(client, tenant, monkeypatch):
    public_key = await _configure_widget(tenant["id"])

    async def fail_if_called(**kwargs):
        raise AssertionError("レート制限中はrespond()を呼ぶべきではない")

    monkeypatch.setattr(chat_module, "respond", fail_if_called)

    try:
        async with async_session_factory() as db:
            for _ in range(5):
                db.add(WebChatRequestLog(tenant_id=tenant["id"], ip_address="1.2.3.4", cost_usd=0.0))
            await db.commit()

        res = await client.post(
            f"/api/chat/{public_key}",
            json={"message": "こんにちは"},
            headers={"Origin": ALLOWED_ORIGIN, "X-Forwarded-For": "1.2.3.4"},
        )
        assert res.status_code == 200
        assert "混み合って" in res.json()["reply"]
    finally:
        await _cleanup(tenant["id"])


async def test_preflight_returns_cors_headers_for_allowed_origin(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.options(f"/api/chat/{public_key}", headers={"Origin": ALLOWED_ORIGIN})
        assert res.status_code == 204
        assert res.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    finally:
        await _cleanup(tenant["id"])


async def test_preflight_omits_cors_headers_for_disallowed_origin(client, tenant):
    public_key = await _configure_widget(tenant["id"])
    try:
        res = await client.options(f"/api/chat/{public_key}", headers={"Origin": "https://evil.example"})
        assert res.status_code == 204
        assert "access-control-allow-origin" not in res.headers
    finally:
        await _cleanup(tenant["id"])
