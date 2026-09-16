"""問い合わせ一次受け(2026-09-15)の検証: 緊急判定・Webチャット/LINEからのエスカレーション記録と
担当者通知・LINE Webhookの署名/重複・問い合わせ一覧API・設定API・保持期間。"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

import src.api.routes.chat as chat_module
import src.api.routes.line_webhook as line_module
import src.channels.inquiries as inquiries_module
from src.agent.inquiry_triage import URGENT, triage
from src.agent.public_responder import PublicResponderResult, respond
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Inquiry, LineWebhookEvent, Tenant, WebChatRequestLog, WebChatSession
from tests.fakes import FakeEmbeddingProvider, FakeLLM

_async = pytest.mark.asyncio(loop_scope="session")

ORIGIN = "https://example.com"
LINE_SECRET = "line-channel-secret-for-tests"


# ---------- 判定ロジック ----------

@pytest.mark.parametrize(
    "message,label",
    [
        ("部屋でガスの臭いがします", "ガス"),
        ("天井から水漏れしています！", "水漏れ"),
        ("隣の部屋から煙が出ています", "火災・煙"),
        ("鍵をなくして部屋に入れない", "鍵の紛失・閉め出し"),
        ("コンセントから焦げ臭いにおい", "漏電・焦げ臭さ"),
    ],
)
def test_triage_detects_emergencies(message, label):
    t = triage(message)
    assert t.urgency == URGENT
    assert t.emergency is not None and t.emergency.label == label


@pytest.mark.parametrize(
    "message,category",
    [
        ("エアコンが動かないです", "設備の故障"),
        ("上の階の足音がうるさい", "騒音・近隣"),
        ("来月の家賃の振込について", "家賃・契約"),
        ("空室の内見はできますか", "内見・物件"),
        ("口内炎が痛い", "その他"),  # 「炎」だけでは火災にしない
    ],
)
def test_triage_categories_without_false_emergency(message, category):
    t = triage(message)
    assert t.urgency == "normal"
    assert t.category == category


@_async
async def test_emergency_reply_is_fixed_template_without_calling_ai():
    llm = FakeLLM()  # 何もキューしない: AIが呼ばれたら失敗する
    result = await respond(
        llm=llm, model="m", company_name="テスト不動産", message="ガス漏れしてるかも", emergency_phone="0120-000-000"
    )
    assert result.escalated is True
    assert result.urgency == URGENT
    assert "火を使わず" in result.reply
    assert "0120-000-000" in result.reply
    assert "担当者から折り返し" in result.reply
    assert llm.structured_calls == []


# ---------- 共通のテスト用設定 ----------

@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    async def fake_send_email(*, to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(inquiries_module, "send_email", fake_send_email)
    return sent


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-test-dummy")
    for module in (chat_module, line_module):
        monkeypatch.setattr(module, "OpenAIEmbeddingProvider", lambda api_key: FakeEmbeddingProvider())

        async def fake_hybrid_search(*args, **kwargs):
            return []

        monkeypatch.setattr(module, "hybrid_search", fake_hybrid_search)


def _set_ai_reply(monkeypatch, *, escalated: bool, reply: str = "内見は土日も可能です。", category: str = "その他"):
    async def fake_respond(**kwargs):
        return PublicResponderResult(
            reply=reply if not escalated else "担当者から折り返しご連絡いたします。少々お待ちください。",
            escalated=escalated,
            reason="test",
            usage={},
            category=category,
        )

    monkeypatch.setattr(chat_module, "respond", fake_respond)
    monkeypatch.setattr(line_module, "respond", fake_respond)


async def _configure(tenant_id, **values):
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant_id)
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


async def _inquiries(tenant_id):
    async with async_session_factory() as db:
        return (await db.execute(select(Inquiry).where(Inquiry.tenant_id == tenant_id))).scalars().all()


async def _cleanup_channels(tenant_id):
    async with async_session_factory() as db:
        for model in (Inquiry, WebChatRequestLog, WebChatSession, LineWebhookEvent):
            await db.execute(delete(model).where(model.tenant_id == tenant_id))
        await db.commit()


def _headers(tenant):
    return {"Authorization": f"Bearer {tenant['api_key']}"}


# ---------- Webチャット ----------

@_async
async def test_web_emergency_is_answered_and_notified_even_over_daily_cost_cap(client, tenant, sent_emails, monkeypatch):
    public_key = f"wpk_test_{uuid.uuid4().hex}"
    await _configure(
        tenant["id"], web_widget_public_key=public_key, web_widget_allowed_origin=ORIGIN,
        email="owner@example.com", emergency_contact_phone="03-0000-0000",
    )

    async def cap_exceeded(db, *, tenant_id):
        raise chat_module.DailyCostCapExceededError("cap")

    monkeypatch.setattr(chat_module, "check_daily_cost_cap", cap_exceeded)
    try:
        res = await client.post(f"/api/chat/{public_key}", json={"message": "水漏れで床が水浸しです"}, headers={"Origin": ORIGIN})
        assert res.status_code == 200
        body = res.json()
        assert body["escalated"] is True
        assert "止水栓" in body["reply"] and "03-0000-0000" in body["reply"]

        rows = await _inquiries(tenant["id"])
        assert len(rows) == 1 and rows[0].urgency == URGENT and rows[0].channel == "web"
        assert len(sent_emails) == 1
        assert sent_emails[0]["to"] == "owner@example.com"
        assert sent_emails[0]["subject"].startswith("【緊急】")
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_web_escalation_creates_one_inquiry_per_conversation_and_notifies_once(client, tenant, sent_emails, monkeypatch):
    public_key = f"wpk_test_{uuid.uuid4().hex}"
    await _configure(
        tenant["id"], web_widget_public_key=public_key, web_widget_allowed_origin=ORIGIN,
        inquiry_notify_email="staff@example.com",
    )
    _set_ai_reply(monkeypatch, escalated=True, category="家賃・契約")
    try:
        first = await client.post(f"/api/chat/{public_key}", json={"message": "更新料について"}, headers={"Origin": ORIGIN})
        session_id = first.json()["session_id"]
        await client.post(
            f"/api/chat/{public_key}", json={"session_id": session_id, "message": "追加の質問です"}, headers={"Origin": ORIGIN}
        )

        # AIが答えられた後続メッセージも、対応中の問い合わせに追記される
        _set_ai_reply(monkeypatch, escalated=False)
        await client.post(
            f"/api/chat/{public_key}", json={"session_id": session_id, "message": "内見は？"}, headers={"Origin": ORIGIN}
        )

        rows = await _inquiries(tenant["id"])
        assert len(rows) == 1
        assert [m["role"] for m in rows[0].messages].count("user") == 3
        assert len(sent_emails) == 1 and sent_emails[0]["to"] == "staff@example.com"
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_web_answered_question_does_not_create_inquiry(client, tenant, sent_emails, monkeypatch):
    public_key = f"wpk_test_{uuid.uuid4().hex}"
    await _configure(tenant["id"], web_widget_public_key=public_key, web_widget_allowed_origin=ORIGIN, email="o@example.com")
    _set_ai_reply(monkeypatch, escalated=False)
    try:
        res = await client.post(f"/api/chat/{public_key}", json={"message": "営業時間は？"}, headers={"Origin": ORIGIN})
        assert res.json()["escalated"] is False
        assert await _inquiries(tenant["id"]) == []
        assert sent_emails == []
    finally:
        await _cleanup_channels(tenant["id"])


# ---------- LINE Webhook ----------

def _line_body(*, text=None, message_type="text", event_id=None, user_id="Uline_user_1"):
    message = {"type": message_type, "id": "1"}
    if text is not None:
        message["text"] = text
    return json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "webhookEventId": event_id or f"evt_{uuid.uuid4().hex}",
                    "replyToken": "reply-token",
                    "source": {"type": "user", "userId": user_id},
                    "message": message,
                }
            ]
        }
    ).encode()


def _line_sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture
def line_replies(monkeypatch):
    replies = []

    async def fake_reply(*, access_token, reply_token, text):
        replies.append({"access_token": access_token, "text": text})
        return True

    monkeypatch.setattr(line_module, "reply_to_line", fake_reply)
    return replies


async def _configure_line(tenant_id, **extra):
    await _configure(
        tenant_id,
        line_channel_secret=encrypt_secret(LINE_SECRET),
        line_channel_access_token=encrypt_secret("line-access-token"),
        **extra,
    )


@_async
async def test_line_webhook_rejects_bad_signature_and_unconfigured(client, tenant):
    body = _line_body(text="こんにちは")
    res = await client.post(f"/webhooks/line/{tenant['id']}", content=body, headers={"x-line-signature": _line_sign(body)})
    assert res.status_code == 404  # LINE未設定

    await _configure_line(tenant["id"])
    res = await client.post(f"/webhooks/line/{tenant['id']}", content=body, headers={"x-line-signature": "invalid"})
    assert res.status_code == 401


@_async
async def test_line_text_message_gets_ai_reply_once_even_if_redelivered(client, tenant, line_replies, monkeypatch):
    await _configure_line(tenant["id"])
    _set_ai_reply(monkeypatch, escalated=False, reply="内見は土日も可能です。")
    body = _line_body(text="内見できますか", event_id="evt_redelivery_test")
    try:
        for _ in range(2):
            res = await client.post(f"/webhooks/line/{tenant['id']}", content=body, headers={"x-line-signature": _line_sign(body)})
            assert res.status_code == 200
        assert [r["text"] for r in line_replies] == ["内見は土日も可能です。"]
        assert line_replies[0]["access_token"] == "line-access-token"
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_line_emergency_replies_with_guidance_and_notifies(client, tenant, line_replies, sent_emails):
    await _configure_line(tenant["id"], email="owner@example.com")
    body = _line_body(text="ガスの臭いがします", user_id="Uemergency")
    try:
        res = await client.post(f"/webhooks/line/{tenant['id']}", content=body, headers={"x-line-signature": _line_sign(body)})
        assert res.status_code == 200
        assert "火を使わず" in line_replies[0]["text"]
        rows = await _inquiries(tenant["id"])
        assert len(rows) == 1 and rows[0].channel == "line" and rows[0].external_user_id == "Uemergency"
        assert sent_emails and sent_emails[0]["subject"].startswith("【緊急】")
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_line_non_text_message_gets_fixed_reply(client, tenant, line_replies):
    await _configure_line(tenant["id"])
    body = _line_body(message_type="sticker")
    try:
        await client.post(f"/webhooks/line/{tenant['id']}", content=body, headers={"x-line-signature": _line_sign(body)})
        assert line_replies[0]["text"] == line_module.NON_TEXT_REPLY
    finally:
        await _cleanup_channels(tenant["id"])


# ---------- 問い合わせ一覧API ----------

async def _make_inquiry(tenant_id, **values) -> uuid.UUID:
    async with async_session_factory() as db:
        row = Inquiry(
            tenant_id=tenant_id,
            channel=values.pop("channel", "line"),
            external_user_id=values.pop("external_user_id", "Uuser"),
            messages=[{"role": "user", "content": values.pop("text", "エアコンが壊れた"), "at": "2026-09-15T00:00:00Z"}],
            **values,
        )
        db.add(row)
        await db.commit()
        return row.id


@_async
async def test_inquiry_list_puts_urgent_first_and_is_tenant_scoped(client, tenant):
    normal_id = await _make_inquiry(tenant["id"], urgency="normal", category="設備の故障")
    urgent_id = await _make_inquiry(tenant["id"], urgency="urgent", category="緊急(水漏れ)", text="水漏れ")
    try:
        res = await client.get("/api/inquiries", headers=_headers(tenant))
        ids = [i["id"] for i in res.json()["inquiries"]]
        assert ids[:2] == [str(urgent_id), str(normal_id)]

        other = await client.get(f"/api/inquiries/{uuid.uuid4()}", headers=_headers(tenant))
        assert other.status_code == 404
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_staff_reply_pushes_to_line_and_records_message(client, tenant, monkeypatch):
    await _configure_line(tenant["id"])
    pushed = []

    async def fake_push(*, access_token, user_id, text):
        pushed.append((user_id, text))
        return True

    import src.api.routes.inquiries as inquiries_route

    monkeypatch.setattr(inquiries_route, "push_to_line", fake_push)
    inquiry_id = await _make_inquiry(tenant["id"], external_user_id="Ustaff_reply")
    try:
        res = await client.post(f"/api/inquiries/{inquiry_id}/reply", json={"text": "明日伺います。"}, headers=_headers(tenant))
        assert res.status_code == 200
        body = res.json()
        assert pushed == [("Ustaff_reply", "明日伺います。")]
        assert body["messages"][-1]["role"] == "staff"
        assert body["status"] == "in_progress"

        done = await client.patch(f"/api/inquiries/{inquiry_id}", json={"status": "resolved"}, headers=_headers(tenant))
        assert done.json()["status"] == "resolved"
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_web_inquiry_cannot_be_replied_from_screen(client, tenant):
    inquiry_id = await _make_inquiry(tenant["id"], channel="web", external_user_id=None)
    try:
        res = await client.post(f"/api/inquiries/{inquiry_id}/reply", json={"text": "返信"}, headers=_headers(tenant))
        assert res.status_code == 400
        assert (await client.get(f"/api/inquiries/{inquiry_id}", headers=_headers(tenant))).json()["can_reply"] is False
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_draft_reply_uses_ai_without_sending_and_records_cost(client, tenant):
    from src.main import app

    inquiry_id = await _make_inquiry(tenant["id"])
    app.state.llm.queue_text("ご連絡ありがとうございます。担当者が確認のうえご連絡します。")
    try:
        res = await client.post(f"/api/inquiries/{inquiry_id}/draft", headers=_headers(tenant))
        assert res.status_code == 200
        assert "確認のうえ" in res.json()["text"]
        assert "エアコンが壊れた" in app.state.llm.text_calls[-1]["user_message"]
        detail = (await client.get(f"/api/inquiries/{inquiry_id}", headers=_headers(tenant))).json()
        assert all(m["role"] != "staff" for m in detail["messages"])  # 案を作っただけで送っていない
    finally:
        await _cleanup_channels(tenant["id"])


# ---------- 設定API ----------

@_async
async def test_inquiry_settings_and_line_channel_self_service(client, tenant):
    res = await client.put(
        "/api/account/inquiry-settings",
        json={"notify_email": "staff@example.com", "emergency_phone": "03-1234-5678"},
        headers=_headers(tenant),
    )
    assert res.status_code == 200
    assert res.json()["emergency_phone"] == "03-1234-5678"
    assert res.json()["line_webhook_url"].endswith(f"/webhooks/line/{tenant['id']}")

    bad = await client.put("/api/account/inquiry-settings", json={"notify_email": "not-an-email"}, headers=_headers(tenant))
    assert bad.status_code == 422

    line = await client.put(
        "/api/account/line-channel",
        json={"line_channel_secret": "s" * 32, "line_channel_access_token": "t" * 100},
        headers=_headers(tenant),
    )
    assert line.json()["line_configured"] is True
    assert "sss" not in line.text  # キーの中身は返さない
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        assert decrypt_secret(row.line_channel_secret) == "s" * 32

    removed = await client.delete("/api/account/line-channel", headers=_headers(tenant))
    assert removed.json()["line_configured"] is False


# ---------- 保持期間 ----------

@_async
async def test_cleanup_deletes_old_resolved_and_very_old_inquiries(tenant):
    from scripts.cleanup_web_chat_logs import cleanup_web_chat_logs

    old = datetime.utcnow() - timedelta(days=40)
    very_old = datetime.utcnow() - timedelta(days=100)
    keep_open = await _make_inquiry(tenant["id"], status="open", created_at=old, updated_at=old)
    drop_resolved = await _make_inquiry(tenant["id"], status="resolved", created_at=old, updated_at=old)
    drop_very_old = await _make_inquiry(tenant["id"], status="open", created_at=very_old, updated_at=very_old)
    try:
        await cleanup_web_chat_logs()
        remaining = {i.id for i in await _inquiries(tenant["id"])}
        assert keep_open in remaining
        assert drop_resolved not in remaining and drop_very_old not in remaining
    finally:
        await _cleanup_channels(tenant["id"])


@_async
async def test_inquiry_settings_shows_effective_notification_address(client, tenant):
    # 通知先が未入力なら登録済みメール、どちらも無ければNone(画面で警告を出すため)
    await _configure(tenant["id"], email=None, inquiry_notify_email=None)
    assert (await client.get("/api/account/inquiry-settings", headers=_headers(tenant))).json()["effective_notify_email"] is None

    await _configure(tenant["id"], email="owner@example.com")
    assert (await client.get("/api/account/inquiry-settings", headers=_headers(tenant))).json()["effective_notify_email"] == "owner@example.com"

    await client.put("/api/account/inquiry-settings", json={"notify_email": "staff@example.com"}, headers=_headers(tenant))
    assert (await client.get("/api/account/inquiry-settings", headers=_headers(tenant))).json()["effective_notify_email"] == "staff@example.com"
