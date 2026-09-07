import base64
import hashlib
import hmac

import pytest

from src.channels.line import has_seen_event, is_duplicate_event, verify_line_signature
from src.core.db import async_session_factory

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_verify_line_signature_accepts_correct_signature():
    secret = "test-channel-secret"
    body = b'{"events":[]}'
    signature = _sign(body, secret)
    assert verify_line_signature(body, signature, secret) is True


def test_verify_line_signature_rejects_wrong_secret():
    body = b'{"events":[]}'
    signature = _sign(body, "correct-secret")
    assert verify_line_signature(body, signature, "wrong-secret") is False


def test_verify_line_signature_rejects_tampered_body():
    secret = "test-channel-secret"
    signature = _sign(b'{"events":[]}', secret)
    assert verify_line_signature(b'{"events":["tampered"]}', signature, secret) is False


def test_verify_line_signature_rejects_missing_signature():
    assert verify_line_signature(b"{}", None, "any-secret") is False
    assert verify_line_signature(b"{}", "", "any-secret") is False


async def test_is_duplicate_event_detects_repeat(tenant):
    async with async_session_factory() as db:
        first = await is_duplicate_event(db, tenant_id=tenant["id"], event_id="evt-1")
        assert first is False

    async with async_session_factory() as db:
        second = await is_duplicate_event(db, tenant_id=tenant["id"], event_id="evt-1")
        assert second is True


async def test_is_duplicate_event_distinguishes_event_ids(tenant):
    async with async_session_factory() as db:
        assert await is_duplicate_event(db, tenant_id=tenant["id"], event_id="evt-a") is False
    async with async_session_factory() as db:
        assert await is_duplicate_event(db, tenant_id=tenant["id"], event_id="evt-b") is False


async def test_has_seen_event_does_not_record(tenant):
    async with async_session_factory() as db:
        assert await has_seen_event(db, tenant_id=tenant["id"], event_id="evt-never-recorded") is False

    async with async_session_factory() as db:
        # is_duplicate_eventと違い、has_seen_eventは記録を追加しない
        assert await has_seen_event(db, tenant_id=tenant["id"], event_id="evt-never-recorded") is False
