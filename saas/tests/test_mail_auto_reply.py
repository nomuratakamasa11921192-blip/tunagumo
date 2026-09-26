"""メール即レス(2026-09-16)の検証。実際のメールサーバーには接続せず、フェイクの送受信で確認する。"""

import uuid
from email.message import EmailMessage

import pytest
from sqlalchemy import delete, select

import src.agent.mail_scan as mail_scan_module
import src.api.routes.account as account_module
import src.api.routes.inquiries as inquiries_route
import src.channels.inquiries as inquiries_module
from src.agent.mail_scan import scan_mailboxes
from src.agent.public_responder import PublicResponderResult
from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES
from src.channels import mail as mail_module
from src.channels.mail import MailAccountSettings, MailConfigError, FetchResult, parse_mail, validate_account_settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Inquiry, MailProcessedMessage, Tenant, TenantMailAccount

_async = pytest.mark.asyncio(loop_scope="session")

OWN = "info@fudosan.example"


def _raw(*, subject="内見の予約について", body="土曜日に内見できますか？", sender="customer@example.net", headers=None, msg_id=None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = OWN
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id or f"<{uuid.uuid4().hex}@example.net>"
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    return bytes(msg)


# ---------- 受信メールの判定 ----------

@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"headers": {"List-Unsubscribe": "<mailto:u@example.com>"}}, "メールマガジン"),
        ({"headers": {"Auto-Submitted": "auto-replied"}}, "自動送信"),
        ({"headers": {"Precedence": "bulk"}}, "一斉配信"),
        ({"sender": "no-reply@portal.example"}, "noreply"),
        ({"sender": OWN}, "自社アドレス"),
        ({"subject": "自動返信: 不在のお知らせ"}, "自動返信"),
    ],
)
def test_parse_mail_skips_mail_that_should_not_be_auto_replied(kwargs, reason):
    parsed = parse_mail(_raw(**kwargs), own_address=OWN, fallback_id="<x>")
    assert parsed.skip_reason is not None and reason in parsed.skip_reason


def test_parse_mail_uses_reply_to_and_strips_quoted_history():
    body = "駐車場はありますか？\n\n2026年9月15日(火) 10:00 担当 <info@fudosan.example>:\n> 以前のやり取り"
    parsed = parse_mail(
        _raw(body=body, sender="portal-system@portal.example", headers={"Reply-To": "Taro <taro@example.net>"}),
        own_address=OWN, fallback_id="<x>",
    )
    assert parsed.skip_reason is None
    assert parsed.reply_address == "taro@example.net"
    assert parsed.body == "駐車場はありますか？"


@pytest.mark.parametrize(
    "overrides",
    [{"imap_host": "127.0.0.1"}, {"smtp_host": "localhost"}, {"imap_port": 143}, {"smtp_port": 25}],
)
def test_validate_account_settings_rejects_internal_hosts_and_plaintext_ports(overrides, monkeypatch):
    monkeypatch.setattr(mail_module, "is_public_host", lambda host: host not in ("127.0.0.1", "localhost"))
    values = dict(from_address=OWN, imap_host="imap.example.com", imap_port=993, smtp_host="smtp.example.com", smtp_port=465, username=OWN, password="pw")
    values.update(overrides)
    with pytest.raises(MailConfigError):
        validate_account_settings(MailAccountSettings(**values))


# ---------- 定期確認ジョブ ----------

class FakeTransport:
    def __init__(self, messages=None, *, fail_send=False):
        self.messages = list(messages or [])
        self.sent = []
        self.fail_send = fail_send
        self.fetch_calls = []

    def fetch_new(self, account, *, last_uid, uidvalidity):
        self.fetch_calls.append(last_uid)
        if last_uid is None:
            return FetchResult(uidvalidity=1, last_uid=100)  # 初回は既存メールに返信しない
        batch = [(last_uid + i + 1, raw) for i, raw in enumerate(self.messages)]
        self.messages = []
        return FetchResult(uidvalidity=1, last_uid=batch[-1][0] if batch else last_uid, messages=batch)

    def send(self, account, *, to, subject, body, in_reply_to, references, auto):
        if self.fail_send:
            raise MailConfigError("送信失敗")
        self.sent.append({"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to, "auto": auto})

    def test_connection(self, account):
        pass


@pytest.fixture
def sent_notifications(monkeypatch):
    sent = []

    async def fake_send_email(*, to, subject, html):
        sent.append(subject)
        return True

    monkeypatch.setattr(inquiries_module, "send_email", fake_send_email)
    return sent


@pytest.fixture(autouse=True)
def _fake_ai(monkeypatch):
    from src.core.config import settings
    from tests.fakes import FakeEmbeddingProvider

    monkeypatch.setattr(settings, "openai_api_key", "sk-test-dummy")
    monkeypatch.setattr(mail_scan_module, "OpenAIEmbeddingProvider", lambda api_key: FakeEmbeddingProvider())

    async def fake_search(*args, **kwargs):
        return []

    monkeypatch.setattr(mail_scan_module, "hybrid_search", fake_search)


def _ai(monkeypatch, *, escalated: bool, reply: str = "土曜日も内見を承っております。", company_names=None):
    async def fake_respond(**kwargs):
        if company_names is not None:
            company_names.append(kwargs['company_name'])
        return PublicResponderResult(
            reply="担当者から折り返しご連絡いたします。少々お待ちください。" if escalated else reply,
            escalated=escalated, reason="test", usage={}, category="内見・物件",
        )

    monkeypatch.setattr(mail_scan_module, "respond", fake_respond)


async def _setup_account(tenant_id, *, last_uid=100, **extra):
    async with async_session_factory() as db:
        db.add(
            TenantMailAccount(
                tenant_id=tenant_id, from_address=OWN, imap_host="imap.example.com", imap_port=993,
                smtp_host="smtp.example.com", smtp_port=465, username=OWN, password_encrypted=encrypt_secret("pw"),
                last_uid=last_uid, uidvalidity=1, **extra,
            )
        )
        await db.commit()


async def _cleanup(tenant_id):
    async with async_session_factory() as db:
        for model in (Inquiry, MailProcessedMessage, TenantMailAccount):
            await db.execute(delete(model).where(model.tenant_id == tenant_id))
        await db.commit()


async def _scan(config, transport):
    return await scan_mailboxes(
        app_configs={i: config for i in INDUSTRIES}, default_industry=DEFAULT_INDUSTRY,
        llm_factory=lambda: None, transport=transport,
    )


async def _inquiries(tenant_id):
    async with async_session_factory() as db:
        return (await db.execute(select(Inquiry).where(Inquiry.tenant_id == tenant_id))).scalars().all()


@_async
async def test_first_connection_does_not_reply_to_existing_mail(config, tenant, monkeypatch):
    await _setup_account(tenant["id"], last_uid=None)
    transport = FakeTransport([_raw()])
    try:
        await _scan(config, transport)
        assert transport.sent == []
        async with async_session_factory() as db:
            assert (await db.get(TenantMailAccount, tenant["id"])).last_uid == 100
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_answerable_mail_is_auto_replied_and_recorded_as_resolved(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    _ai(monkeypatch, escalated=False)
    raw = _raw(msg_id="<question-1@example.net>")
    transport = FakeTransport([raw])
    try:
        result = await _scan(config, transport)
        assert result["auto_replied"] == 1
        assert len(transport.sent) == 1
        sent = transport.sent[0]
        assert sent["to"] == "customer@example.net"
        assert sent["subject"] == "Re: 内見の予約について"
        assert sent["in_reply_to"] == "<question-1@example.net>"
        assert sent["auto"] is True
        assert "土曜日も内見を承っております。" in sent["body"] and "自動応答" in sent["body"]

        rows = await _inquiries(tenant["id"])
        assert len(rows) == 1 and rows[0].status == "resolved" and rows[0].channel == "email"
        assert sent_notifications == []

        # 同じメールをもう一度受け取っても二重に返信しない
        transport.messages = [raw]
        await _scan(config, transport)
        assert len(transport.sent) == 1
    finally:
        await _cleanup(tenant["id"])


@_async
@pytest.mark.parametrize('company_name', ['ツナグモ', '別会社の不動産'])
async def test_mail_uses_registered_tenant_name_for_ai_and_signature(config, tenant, monkeypatch, sent_notifications, company_name):
    assert config.company.name != company_name
    async with async_session_factory() as db:
        (await db.get(Tenant, tenant['id'])).name = company_name
        await db.commit()
    await _setup_account(tenant['id'])
    company_names = []
    _ai(monkeypatch, escalated=False, company_names=company_names)
    transport = FakeTransport([_raw()])
    try:
        await _scan(config, transport)
        assert len(transport.sent) == 1
        assert f'このメールは{company_name}の自動応答です。' in transport.sent[0]['body']
        assert config.company.name not in transport.sent[0]['body']
        assert company_names == [company_name]
    finally:
        await _cleanup(tenant['id'])


@_async
async def test_unanswerable_mail_gets_acknowledgement_and_goes_to_staff(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    await _set_email(tenant["id"])
    _ai(monkeypatch, escalated=True)
    transport = FakeTransport([_raw(subject="更新料の金額について", body="更新料はいくらですか")])
    try:
        result = await _scan(config, transport)
        assert result["escalated"] == 1
        assert "担当者から折り返し" in transport.sent[0]["body"]
        rows = await _inquiries(tenant["id"])
        assert rows[0].status == "open" and rows[0].email_subject == "更新料の金額について"
        assert len(sent_notifications) == 1
    finally:
        await _cleanup(tenant["id"])


async def _set_email(tenant_id):
    from src.core.models import Tenant

    async with async_session_factory() as db:
        (await db.get(Tenant, tenant_id)).email = "owner@example.com"
        await db.commit()


@_async
async def test_emergency_mail_gets_safety_guidance_without_ai(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    await _set_email(tenant["id"])

    async def must_not_call(**kwargs):
        raise AssertionError("緊急時にAIを呼んではいけない")

    monkeypatch.setattr(mail_scan_module, "respond", must_not_call)
    transport = FakeTransport([_raw(subject="至急", body="キッチンでガスの臭いがします")])
    try:
        await _scan(config, transport)
        assert "火を使わず" in transport.sent[0]["body"]
        rows = await _inquiries(tenant["id"])
        assert rows[0].urgency == "urgent"
        assert sent_notifications[0].startswith("【緊急】")
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_newsletters_are_ignored(config, tenant, monkeypatch):
    await _setup_account(tenant["id"])
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([_raw(headers={"List-Unsubscribe": "<mailto:x@example.com>"}), _raw(sender="noreply@system.example")])
    try:
        result = await _scan(config, transport)
        assert result["skipped"] == 2
        assert transport.sent == [] and await _inquiries(tenant["id"]) == []
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_auto_replies_to_same_sender_are_capped_to_prevent_mail_loops(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([_raw(body=f"質問{i}です") for i in range(5)])
    try:
        await _scan(config, transport)
        assert len(transport.sent) == mail_module.MAX_AUTO_REPLIES_PER_SENDER_PER_DAY
        open_rows = [i for i in await _inquiries(tenant["id"]) if i.status == "open"]
        assert open_rows and "上限" in open_rows[0].reason
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_send_failure_goes_to_staff(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([_raw()], fail_send=True)
    try:
        result = await _scan(config, transport)
        assert result["escalated"] == 1
        rows = await _inquiries(tenant["id"])
        assert rows[0].status == "open" and "送信に失敗" in rows[0].reason
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_connection_error_is_saved_for_settings_screen(config, tenant):
    await _setup_account(tenant["id"])

    class Broken(FakeTransport):
        def fetch_new(self, account, *, last_uid, uidvalidity):
            raise MailConfigError("受信サーバーにログインできませんでした。")

    try:
        await _scan(config, Broken())
        async with async_session_factory() as db:
            row = await db.get(TenantMailAccount, tenant["id"])
            assert "ログインできません" in row.last_error
            assert row.last_uid == 100  # エラー時は位置を進めない
    finally:
        await _cleanup(tenant["id"])


# ---------- 設定API・担当者の返信 ----------

@_async
async def test_mail_account_api_tests_connection_and_hides_password(client, tenant, monkeypatch):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    transport = FakeTransport()
    monkeypatch.setattr(account_module, "mail_transport", lambda: transport)
    body = {
        "from_address": OWN, "username": OWN, "password": "app-password-123",
        "imap_host": "imap.example.com", "smtp_host": "smtp.example.com", "smtp_port": 587,
    }
    try:
        res = await client.put("/api/account/mail-account", json=body, headers=headers)
        assert res.status_code == 200
        assert res.json()["configured"] is True and "app-password" not in res.text
        async with async_session_factory() as db:
            row = await db.get(TenantMailAccount, tenant["id"])
            assert decrypt_secret(row.password_encrypted) == "app-password-123" and row.last_uid is None

        # パスワード空欄の更新は、保存済みのパスワードを使う
        res2 = await client.put("/api/account/mail-account", json={**body, "password": "", "enabled": False}, headers=headers)
        assert res2.json()["enabled"] is False

        class Failing(FakeTransport):
            def test_connection(self, account):
                raise MailConfigError("送信サーバーにログインできませんでした。")

        monkeypatch.setattr(account_module, "mail_transport", lambda: Failing())
        bad = await client.put("/api/account/mail-account", json=body, headers=headers)
        assert bad.status_code == 400 and "ログイン" in bad.json()["detail"]

        removed = await client.delete("/api/account/mail-account", headers=headers)
        assert removed.json()["configured"] is False
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_staff_reply_to_email_inquiry_is_sent_in_same_thread(client, tenant, monkeypatch):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    await _setup_account(tenant["id"])
    transport = FakeTransport()
    monkeypatch.setattr(inquiries_route, "mail_transport", lambda: transport)
    async with async_session_factory() as db:
        row = Inquiry(
            tenant_id=tenant["id"], channel="email", external_user_id="customer@example.net",
            email_subject="更新料について", email_message_id="<orig@example.net>",
            messages=[{"role": "user", "content": "更新料は？", "at": "2026-09-16T00:00:00Z"}],
        )
        db.add(row)
        await db.commit()
        inquiry_id = row.id
    try:
        res = await client.post(f"/api/inquiries/{inquiry_id}/reply", json={"text": "更新料は賃料1か月分です。"}, headers=headers)
        assert res.status_code == 200 and res.json()["can_reply"] is True
        assert transport.sent == [{
            "to": "customer@example.net", "subject": "Re: 更新料について", "body": "更新料は賃料1か月分です。",
            "in_reply_to": "<orig@example.net>", "auto": False,
        }]
    finally:
        await _cleanup(tenant["id"])


# ---------- 実際のIMAP/SMTP処理(ライブラリを模したフェイクで、コマンドの流れを確認) ----------

class _FakeSock:
    def __init__(self, ip="8.8.8.8"):
        self.ip = ip

    def getpeername(self):
        return (self.ip, 993)


class _FakeIMAP:
    instances = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.sock = _FakeSock()
        self.commands = []
        _FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.commands.append(("login", user))

    def select(self, mailbox, readonly=False):
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"555"]

    def uid(self, command, *args):
        self.commands.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b"99 100 101 102"]
        return "OK", [(b"1 (UID %s BODY[] {10}" % args[0].encode(), _raw(body=f"uid{args[0]}")), b")"]

    def logout(self):
        self.commands.append(("logout",))


@pytest.fixture
def real_transport(monkeypatch):
    _FakeIMAP.instances = []
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", _FakeIMAP)
    monkeypatch.setattr(mail_module, "is_public_host", lambda host: True)
    return mail_module.ImapSmtpTransport()


def _account():
    return MailAccountSettings(
        from_address=OWN, imap_host="imap.example.com", imap_port=993, smtp_host="smtp.example.com",
        smtp_port=465, username=OWN, password="pw",
    )


def test_imap_fetch_reads_only_new_mail_without_marking_as_read(real_transport):
    result = real_transport.fetch_new(_account(), last_uid=100, uidvalidity=555)
    assert [uid for uid, _ in result.messages] == [101, 102]
    assert result.last_uid == 102 and result.uidvalidity == 555
    commands = _FakeIMAP.instances[-1].commands
    assert ("select", "INBOX", True) in commands  # 読み取り専用
    assert all(c[3] == "(BODY.PEEK[])" for c in commands if c[:2] == ("uid", "FETCH"))  # 既読にしない
    assert commands[-1] == ("logout",)


def test_imap_first_run_and_uidvalidity_change_only_record_position(real_transport):
    assert real_transport.fetch_new(_account(), last_uid=None, uidvalidity=None).messages == []
    changed = real_transport.fetch_new(_account(), last_uid=50, uidvalidity=1)  # サーバー側でUIDが振り直された
    assert changed.messages == [] and changed.last_uid == 102


def test_subject_scope_does_not_fetch_private_bodies(real_transport, monkeypatch):
    prefix = "[TSUNAGUMO-TEST]"

    class ScopedIMAP(_FakeIMAP):
        def uid(self, command, *args):
            self.commands.append(("uid", command) + args)
            if command == "SEARCH":
                return "OK", [b"100 101 102 103" if args == (None, "ALL") else b"102 103"]
            uid = int(args[0])
            subject = prefix + " 内見" if uid == 102 else "個人 " + prefix
            if "HEADER.FIELDS" in args[1]:
                raw = _raw(subject=subject).split(b"\n\n", 1)[0] + b"\n\n"
            else:
                assert uid == 102, "対象外メール本文を取得しない"
                raw = _raw(subject=subject)
            return "OK", [(b"header", raw)]

    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", ScopedIMAP)
    account = _account()
    account.subject_prefix = prefix
    result = real_transport.fetch_new(account, last_uid=100, uidvalidity=555)
    assert [uid for uid, _ in result.messages] == [102]
    assert result.last_uid == 103
    commands = _FakeIMAP.instances[-1].commands
    assert ("uid", "SEARCH", None, "UID", "101:*", "HEADER", "Subject", '"[TSUNAGUMO-TEST]"') in commands
    assert not any(c[:3] == ("uid", "FETCH", "101") for c in commands)


def test_subject_search_failure_does_not_fall_back_to_all_mail(real_transport, monkeypatch):
    class FailedSearch(_FakeIMAP):
        def uid(self, command, *args):
            if command == "SEARCH" and "HEADER" in args:
                return "NO", [b"failed"]
            assert command != "FETCH"
            return super().uid(command, *args)

    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", FailedSearch)
    account = _account()
    account.subject_prefix = "[TSUNAGUMO-TEST]"
    with pytest.raises(MailConfigError, match="検索"):
        real_transport.fetch_new(account, last_uid=100, uidvalidity=555)


@_async
async def test_subject_scope_skips_recording_and_notifications(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"], subject_prefix="[TSUNAGUMO-TEST]")
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([
        _raw(subject="私用のメール", msg_id="<private@example.net>"),
        _raw(subject="[TSUNAGUMO-TEST] 内見", msg_id="<test@example.net>"),
    ])
    try:
        result = await _scan(config, transport)
        assert result["skipped"] == 1 and result["auto_replied"] == 1
        assert len(transport.sent) == 1
        assert len(await _inquiries(tenant["id"])) == 1
        async with async_session_factory() as db:
            assert await db.get(MailProcessedMessage, (tenant["id"], "<private@example.net>")) is None
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_mail_scope_defaults_preserves_and_resets_on_change(client, tenant, monkeypatch):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    monkeypatch.setattr(account_module, "mail_transport", lambda: FakeTransport())
    body = {"from_address": OWN, "username": OWN, "password": "test-app-password",
            "imap_host": "imap.example.com", "smtp_host": "smtp.example.com"}
    try:
        response = await client.put("/api/account/mail-account", json=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["subject_prefix"] == "[TSUNAGUMO-TEST]"
        for update in ({"subject_prefix": "[ANOTHER-TEST]"}, {"username": "business@example.net"}, {"enabled": False}):
            async with async_session_factory() as db:
                row = await db.get(TenantMailAccount, tenant["id"])
                row.last_uid, row.uidvalidity = 200, 555
                await db.commit()
            body.update(update)
            response = await client.put("/api/account/mail-account", json=body, headers=headers)
            assert response.status_code == 200
            async with async_session_factory() as db:
                row = await db.get(TenantMailAccount, tenant["id"])
                assert row.last_uid is None and row.uidvalidity is None
        body.pop("subject_prefix")
        response = await client.put("/api/account/mail-account", json=body, headers=headers)
        assert response.json()["subject_prefix"] == "[ANOTHER-TEST]"
        body["subject_prefix"] = 'bad"\r\nALL'
        assert (await client.put("/api/account/mail-account", json=body, headers=headers)).status_code == 422
    finally:
        await _cleanup(tenant["id"])


def test_imap_refuses_private_peer_address(real_transport, monkeypatch):
    class PrivatePeer(_FakeIMAP):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sock = _FakeSock("10.0.0.5")

    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", PrivatePeer)
    with pytest.raises(MailConfigError):
        real_transport.fetch_new(_account(), last_uid=100, uidvalidity=555)


def test_smtp_send_sets_threading_and_auto_submitted_headers(real_transport, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, context=None, timeout=None):
            self.sock = _FakeSock()

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent.append(msg)

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mail_module.smtplib, "SMTP_SSL", FakeSMTP)
    real_transport.send(
        _account(), to="customer@example.net", subject="Re: 質問", body="回答です",
        in_reply_to="<orig@example.net>", references="<older@example.net>", auto=True,
    )
    msg = sent[0]
    assert msg["In-Reply-To"] == "<orig@example.net>"
    assert msg["References"] == "<older@example.net> <orig@example.net>"
    assert msg["Auto-Submitted"] == "auto-replied"
    assert msg["From"] == OWN


# ---------- ポータルサイトの反響通知 ----------

PORTAL_BODY = """お問い合わせがありました。

【物件名】サンプルハイツ101
【お名前】山田 太郎
【メールアドレス】taro-portal@example.net
【電話番号】090-1234-5678
【ご希望】土日に内見を希望します。

このメールは送信専用アドレスから配信されています。
"""


@pytest.mark.parametrize("portal_body,expected_email", [
    (PORTAL_BODY, "taro-portal@example.net"),
    ("窓口: support@portal.example\n" + PORTAL_BODY, "taro-portal@example.net"),
    ("物件の詳細説明です。" * 300 + "\n" + PORTAL_BODY, "taro-portal@example.net"),
    (PORTAL_BODY + "\nメールアドレス: another@example.net", None),
])
@_async
async def test_portal_inquiry_is_recorded_without_auto_reply(config, tenant, monkeypatch, sent_notifications, portal_body, expected_email):
    await _setup_account(tenant["id"])
    await _set_email(tenant["id"])
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([_raw(subject="【SUUMO】お問い合わせがありました", body=portal_body, sender="noreply@suumo.example")])
    try:
        result = await _scan(config, transport)
        assert result["escalated"] == 1
        assert transport.sent == []  # 返信不可アドレスへ自動返信しない
        rows = await _inquiries(tenant["id"])
        assert len(rows) == 1
        assert rows[0].category == "内見・物件(ポータル反響)"
        assert rows[0].external_user_id == expected_email  # 曖昧な候補は返信先にしない
        assert "090-1234-5678" in rows[0].messages[0]["content"]
        assert len(sent_notifications) == 1
    finally:
        await _cleanup(tenant["id"])


@_async
async def test_ordinary_noreply_notification_is_still_ignored(config, tenant, monkeypatch, sent_notifications):
    await _setup_account(tenant["id"])
    _ai(monkeypatch, escalated=False)
    transport = FakeTransport([_raw(subject="サーバー月次メンテナンスのお知らせ", body="定期メンテナンスを実施します。", sender="noreply@infra.example")])
    try:
        result = await _scan(config, transport)
        assert result["skipped"] == 1
        assert await _inquiries(tenant["id"]) == [] and sent_notifications == []
    finally:
        await _cleanup(tenant["id"])
