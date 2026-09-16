"""物件提案・追客メール(2026-09-16)の検証。実際のメールは送らず、フェイクの送信で確認する。"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

import src.agent.proposal_scan as proposal_scan_module
import src.api.routes.leads as leads_route
from src.agent.proposal_scan import generate_for_tenant, scan_proposals, within_send_hours
from src.channels.mail import MailConfigError
from src.core.crypto import encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Lead, Property, Proposal, Tenant, TenantMailAccount
from src.core.proposals import format_price, is_match

_async = pytest.mark.asyncio(loop_scope="session")

# 日本時間の昼(送信可能な時間帯)と深夜
NOON_JST = datetime(2026, 9, 16, 3, 0)  # UTC 3時 = JST 12時
NIGHT_JST = datetime(2026, 9, 16, 15, 0)  # UTC 15時 = JST 0時


class FakeTransport:
    def __init__(self, *, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, account, *, to, subject, body, in_reply_to, references, auto, extra_headers=None):
        if self.fail:
            raise MailConfigError("送信サーバーにログインできませんでした。")
        self.sent.append({"to": to, "subject": subject, "body": body, "headers": extra_headers or {}})


def _prop(**kw) -> Property:
    base = dict(
        name="サンプルハイツ101", deal_type="rent", location="埼玉県春日部市中央", station="春日部駅", walk_minutes=8,
        price_yen=65000, layout="1LDK", floor_area_sqm=40.5, built_year=2015, features="バストイレ別", url="https://example.com/1",
        status="available",
    )
    base.update(kw)
    return Property(**base)


def _lead(**kw) -> Lead:
    base = dict(
        name="山田太郎", email="taro@example.net", consent_at=datetime(2026, 9, 1), deal_type="rent", areas=["春日部"],
        max_price_yen=70000, layouts=["1LDK", "2DK"], max_walk_minutes=10, min_floor_area_sqm=35, status="active",
        unsubscribe_token=uuid.uuid4().hex, follow_up_count=0,
    )
    base.update(kw)
    return Lead(**base)


# ---------- 照合・表示 ----------

@pytest.mark.parametrize(
    "prop_kw,expected",
    [
        ({}, True),
        ({"price_yen": 75000}, False),
        ({"layout": "3LDK"}, False),
        ({"walk_minutes": 15}, False),
        ({"walk_minutes": None}, False),
        ({"location": "東京都新宿区", "station": "新宿駅"}, False),
        ({"deal_type": "sale"}, False),
        ({"status": "closed"}, False),
        ({"layout": "1ldk"}, True),
    ],
)
def test_is_match(prop_kw, expected):
    assert is_match(_prop(**prop_kw), _lead()) is expected


def test_format_price_and_send_hours():
    assert format_price(_prop(price_yen=65000)) == "賃料 6.5万円/月"
    assert format_price(_prop(deal_type="sale", price_yen=29_800_000)) == "価格 2,980万円"
    assert within_send_hours(NOON_JST) is True
    assert within_send_hours(NIGHT_JST) is False


# ---------- DBを使う検証 ----------

async def _setup(tenant_id, *, auto_send=False, sender=True, mail=True, lead_kw=None, props=None):
    async with async_session_factory() as db:
        t = await db.get(Tenant, tenant_id)
        t.proposal_auto_send = auto_send
        t.follow_up_interval_days = 7
        t.follow_up_max = 3
        if sender:
            t.marketing_sender_name = "春日部不動産"
            t.marketing_sender_address = "埼玉県春日部市中央1-1-1"
            t.marketing_sender_contact = "048-000-0000"
        if mail:
            db.add(TenantMailAccount(
                tenant_id=tenant_id, from_address="info@fudosan.example", imap_host="imap.example.com", imap_port=993,
                smtp_host="smtp.example.com", smtp_port=465, username="info@fudosan.example", password_encrypted=encrypt_secret("pw"),
            ))
        lead = _lead(tenant_id=tenant_id, **(lead_kw or {}))
        db.add(lead)
        for p in props if props is not None else [_prop(tenant_id=tenant_id)]:
            db.add(p)
        await db.commit()
        return lead.id


async def _cleanup(tenant_id):
    async with async_session_factory() as db:
        for model in (Proposal, Lead, Property, TenantMailAccount):
            await db.execute(delete(model).where(model.tenant_id == tenant_id))
        await db.commit()


async def _proposals(tenant_id):
    async with async_session_factory() as db:
        return (await db.execute(select(Proposal).where(Proposal.tenant_id == tenant_id).order_by(Proposal.created_at))).scalars().all()


async def _generate(tenant_id, *, now, transport):
    async with async_session_factory() as db:
        tenant = await db.get(Tenant, tenant_id)
        return await generate_for_tenant(db, tenant, now=now, transport=transport)


@_async
async def test_matching_property_creates_pending_proposal_by_default(tenant, monkeypatch):
    await _setup(tenant["id"])
    transport = FakeTransport()
    try:
        result = await _generate(tenant["id"], now=NOON_JST, transport=transport)
        assert result == {"created": 1, "sent": 0, "pending": 1}
        rows = await _proposals(tenant["id"])
        assert rows[0].status == "pending" and rows[0].kind == "proposal"
        assert "サンプルハイツ101" in rows[0].body and "賃料 6.5万円/月" in rows[0].body
        assert transport.sent == []  # 既定は承認制

        # 承認待ちがある間は同じ見込み客に重ねて作らない
        assert (await _generate(tenant["id"], now=NOON_JST, transport=transport))["created"] == 0
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_auto_send_includes_legal_footer_and_unsubscribe_header(tenant):
    lead_id = await _setup(tenant["id"], auto_send=True)
    transport = FakeTransport()
    try:
        result = await _generate(tenant["id"], now=NOON_JST, transport=transport)
        assert result["sent"] == 1
        mail = transport.sent[0]
        assert mail["to"] == "taro@example.net"
        assert "春日部不動産" in mail["body"] and "埼玉県春日部市中央1-1-1" in mail["body"] and "048-000-0000" in mail["body"]
        assert "/api/unsubscribe/" in mail["body"]
        assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

        async with async_session_factory() as db:
            lead = await db.get(Lead, lead_id)
            assert lead.last_sent_at == NOON_JST

        # 同じ物件は二度提案しない
        assert (await _generate(tenant["id"], now=NOON_JST + timedelta(days=4), transport=transport))["created"] == 0
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_auto_send_waits_for_daytime_then_sends(tenant):
    await _setup(tenant["id"], auto_send=True)
    transport = FakeTransport()
    try:
        night = await _generate(tenant["id"], now=NIGHT_JST, transport=transport)
        assert night["pending"] == 1 and transport.sent == []
        morning = await _generate(tenant["id"], now=NIGHT_JST + timedelta(hours=10), transport=transport)  # JST 10時
        assert morning["sent"] == 1 and len(transport.sent) == 1
    finally:
        await _cleanup(tenant["id"])


@pytest.mark.parametrize(
    "setup_kw,expected_error",
    [
        ({"sender": False}, "送信者情報"),
        ({"mail": False}, "メールアドレスが未連携"),
    ],
)
@_async
async def test_auto_send_is_blocked_without_legal_sender_info_or_mail_account(tenant, setup_kw, expected_error):
    await _setup(tenant["id"], auto_send=True, **setup_kw)
    transport = FakeTransport()
    try:
        result = await _generate(tenant["id"], now=NOON_JST, transport=transport)
        assert result["sent"] == 0 and transport.sent == []
        assert expected_error in (await _proposals(tenant["id"]))[0].error
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_leads_without_consent_or_unsubscribed_get_nothing(tenant):
    await _setup(tenant["id"], auto_send=True, lead_kw={"consent_at": None})
    try:
        assert (await _generate(tenant["id"], now=NOON_JST, transport=FakeTransport()))["created"] == 0
    finally:
        await _cleanup(tenant["id"])
    await _setup(tenant["id"], auto_send=True, lead_kw={"status": "unsubscribed"})
    try:
        assert (await _generate(tenant["id"], now=NOON_JST, transport=FakeTransport()))["created"] == 0
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_follow_up_after_interval_stops_on_reply_and_max(tenant):
    last_sent = NOON_JST - timedelta(days=8)
    lead_id = await _setup(tenant["id"], auto_send=True, props=[], lead_kw={"last_sent_at": last_sent})
    transport = FakeTransport()
    try:
        result = await _generate(tenant["id"], now=NOON_JST, transport=transport)
        assert result["sent"] == 1
        rows = await _proposals(tenant["id"])
        assert rows[0].kind == "follow_up"
        async with async_session_factory() as db:
            lead = await db.get(Lead, lead_id)
            assert lead.follow_up_count == 1
            lead.last_reply_at = NOON_JST + timedelta(hours=1)  # 見込み客から返信があった
            await db.commit()
        assert (await _generate(tenant["id"], now=NOON_JST + timedelta(days=10), transport=transport))["created"] == 0

        async with async_session_factory() as db:
            lead = await db.get(Lead, lead_id)
            lead.last_reply_at = None
            lead.follow_up_count = 3  # 上限到達
            await db.commit()
        assert (await _generate(tenant["id"], now=NOON_JST + timedelta(days=20), transport=transport))["created"] == 0
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_scan_notifies_staff_about_pending_proposals(tenant, monkeypatch):
    await _setup(tenant["id"])
    async with async_session_factory() as db:
        (await db.get(Tenant, tenant["id"])).email = "owner@example.com"
        await db.commit()
    notified = []

    async def fake_send_email(*, to, subject, html):
        notified.append(subject)
        return True

    monkeypatch.setattr(proposal_scan_module, "send_email", fake_send_email)
    try:
        await scan_proposals(transport=FakeTransport(), now=NOON_JST)
        assert any("承認待ち" in s for s in notified)
    finally:
        await _cleanup(tenant["id"])


# ---------- 画面用API ----------

def _h(tenant):
    return {"Authorization": f"Bearer {tenant['api_key']}"}


@_async
async def test_api_crud_generate_edit_and_send(client, tenant, monkeypatch):
    transport = FakeTransport()
    monkeypatch.setattr(leads_route, "mail_transport", lambda: transport)
    await _setup(tenant["id"], props=[], lead_kw={"email": "unused@example.net", "status": "paused"})
    try:
        prop = await client.post("/api/properties", json={
            "name": "駅前マンション", "deal_type": "rent", "location": "春日部市", "station": "春日部駅",
            "walk_minutes": 5, "price_yen": 80000, "layout": "2DK", "floor_area_sqm": 45,
        }, headers=_h(tenant))
        assert prop.status_code == 201

        lead = await client.post("/api/leads", json={
            "name": "佐藤花子", "email": "Hanako@Example.net", "consent": True, "consent_source": "来店時の申込書",
            "deal_type": "rent", "areas": ["春日部"], "max_price_yen": 90000, "layouts": ["2DK"],
        }, headers=_h(tenant))
        assert lead.status_code == 201 and lead.json()["email"] == "hanako@example.net" and lead.json()["consent"] is True

        gen = await client.post("/api/proposals/generate", headers=_h(tenant))
        assert gen.json()["created"] == 1
        pending = (await client.get("/api/proposals?status=pending", headers=_h(tenant))).json()
        assert len(pending) == 1 and "駅前マンション" in pending[0]["body"]
        assert "配信を停止" in pending[0]["footer"]

        edited = await client.put(f"/api/proposals/{pending[0]['id']}", json={
            "subject": pending[0]["subject"], "body": pending[0]["body"] + "\n\n週末の内見もご相談ください。",
        }, headers=_h(tenant))
        assert edited.status_code == 200

        sent = await client.post(f"/api/proposals/{pending[0]['id']}/send", headers=_h(tenant))
        assert sent.status_code == 200 and sent.json()["status"] == "sent"
        assert "週末の内見" in transport.sent[0]["body"] and "配信を停止" in transport.sent[0]["body"]

        again = await client.post(f"/api/proposals/{pending[0]['id']}/send", headers=_h(tenant))
        assert again.status_code == 409
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_api_send_without_consent_is_rejected(client, tenant, monkeypatch):
    monkeypatch.setattr(leads_route, "mail_transport", lambda: FakeTransport())
    lead_id = await _setup(tenant["id"])
    try:
        await _generate(tenant["id"], now=NOON_JST, transport=FakeTransport())
        proposal = (await _proposals(tenant["id"]))[0]
        async with async_session_factory() as db:
            (await db.get(Lead, lead_id)).consent_at = None
            await db.commit()
        res = await client.post(f"/api/proposals/{proposal.id}/send", headers=_h(tenant))
        assert res.status_code == 400 and "同意" in res.json()["detail"]
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_polish_marks_ai_edited_and_keeps_pending(client, tenant):
    from src.main import app

    await _setup(tenant["id"])
    try:
        await _generate(tenant["id"], now=NOON_JST, transport=FakeTransport())
        proposal = (await _proposals(tenant["id"]))[0]
        app.state.llm.queue_text("山田太郎様\n\nご希望に合う物件をご紹介します。\n■ サンプルハイツ101 ...")
        res = await client.post(f"/api/proposals/{proposal.id}/polish", headers=_h(tenant))
        assert res.status_code == 200
        body = res.json()
        assert body["ai_edited"] is True and body["status"] == "pending"
        assert "一字一句変えず" in app.state.llm.text_calls[-1]["system_prompt"]
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_unsubscribe_requires_button_and_stops_future_mail(client, tenant):
    lead_id = await _setup(tenant["id"])
    try:
        await _generate(tenant["id"], now=NOON_JST, transport=FakeTransport())
        async with async_session_factory() as db:
            token = (await db.get(Lead, lead_id)).unsubscribe_token

        page = await client.get(f"/api/unsubscribe/{token}")
        assert page.status_code == 200 and "配信を停止する" in page.text
        async with async_session_factory() as db:
            assert (await db.get(Lead, lead_id)).status == "active"  # 開いただけでは停止しない

        done = await client.post(f"/api/unsubscribe/{token}")
        assert "配信を停止しました" in done.text
        async with async_session_factory() as db:
            assert (await db.get(Lead, lead_id)).status == "unsubscribed"
        assert (await _proposals(tenant["id"]))[0].status == "discarded"

        # 画面から見込み客を更新しても、配信停止は勝手に解除されない
        lead = (await client.get("/api/leads", headers=_h(tenant))).json()[0]
        await client.put(f"/api/leads/{lead_id}", json={**{k: lead[k] for k in ("name", "email", "areas", "layouts")}, "consent": True, "status": "active"}, headers=_h(tenant))
        async with async_session_factory() as db:
            assert (await db.get(Lead, lead_id)).status == "unsubscribed"

        assert (await client.get("/api/unsubscribe/invalid-token")).status_code == 404
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_proposal_settings_api(client, tenant):
    res = await client.put("/api/proposal-settings", json={
        "sender_name": "春日部不動産", "sender_address": "", "sender_contact": "048-000-0000", "auto_send": True,
        "follow_up_interval_days": 5, "follow_up_max": 2,
    }, headers=_h(tenant))
    assert res.status_code == 200
    assert res.json()["missing"] == ["住所"]
    bad = await client.put("/api/proposal-settings", json={"follow_up_interval_days": 1}, headers=_h(tenant))
    assert bad.status_code == 422


@_async
async def test_other_tenants_data_is_not_accessible(client, tenant):
    await _setup(tenant["id"])
    try:
        async with async_session_factory() as db:
            prop = (await db.execute(select(Property).where(Property.tenant_id == tenant["id"]))).scalars().first()
        # 存在しないIDと同じく404(他テナントのIDでも見えない)
        assert (await client.delete(f"/api/properties/{uuid.uuid4()}", headers=_h(tenant))).status_code == 404
        assert len((await client.get("/api/properties", headers=_h(tenant))).json()) == 1
        assert prop is not None
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_long_condition_values_are_trimmed(client, tenant):
    res = await client.post("/api/leads", json={
        "name": "長い条件さん", "email": "long@example.net", "areas": ["あ" * 100], "layouts": ["1LDK" * 20],
    }, headers=_h(tenant))
    assert res.status_code == 201
    body = res.json()
    assert len(body["areas"][0]) == 50 and len(body["layouts"][0]) == 20
