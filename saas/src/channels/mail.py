"""メール即レス(2026-09-16)。顧客(不動産会社)のメールボックスにIMAPで接続して新着の問い合わせを
確認し、AIが確実に答えられるものだけ顧客自身のアドレスから自動で返信する。

送信の方針(ユーザー決定): 確定しているものだけ自動送信、分からないものは人が承認。
- 緊急(火災・ガス・水漏れ等): 定型の安全案内を自動返信し、担当者へ【緊急】通知
- AIが公開用資料の範囲で答えられる: その回答を自動返信(問い合わせ一覧に「対応済み」で記録)
- それ以外(金額・契約・クレーム等): 「担当者から折り返します」とだけ自動返信し、担当者へ回す
  (本文の返信は担当者が問い合わせ画面で確認してから送る)

誤送信・メールのループを防ぐための安全策:
- メルマガ・システム通知・自動返信・エラー通知(バウンス)には返信しない
- 同じ相手への自動返信は24時間で3回まで、1テナントの自動返信は1日100通まで
- 同じメールには二重に返信しない(Message-IDを記録)
- 既読にしない(読み取り専用でSELECTし、BODY.PEEKで取得する)
- 初回接続時は既存のメールに返信しない(その時点の最新UIDを記録するだけ)
- 接続先はTLS必須の既定ポートのみ、社内アドレスには接続しない(SSRF対策)
"""

import asyncio
import email
import email.policy
import imaplib
import ipaddress
import logging
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.safe_http import is_public_host

logger = logging.getLogger(__name__)

ALLOWED_IMAP_PORTS = {993}
ALLOWED_SMTP_PORTS = {465, 587}
CONNECT_TIMEOUT_SECONDS = 20
MAX_MESSAGES_PER_RUN = 20
MAX_BODY_CHARS = 2000
MAX_AUTO_REPLIES_PER_SENDER_PER_DAY = 3
MAX_AUTO_REPLIES_PER_TENANT_PER_DAY = 100

_NOREPLY_PATTERN = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce)", re.IGNORECASE)
_AUTO_SUBJECT_PATTERN = re.compile(
    r"^(自動返信|自動応答|不在|out of office|automatic reply|auto(matic)?[- ]?reply|delivery status notification|undeliverable|returned mail)",
    re.IGNORECASE,
)
# 返信の引用部分の始まり(ここより後は過去のやり取りなので読まない)
_QUOTE_START_PATTERNS = (
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^-{2,}\s*元のメッセージ\s*-{2,}"),
    re.compile(r"^On .+wrote:$"),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日.*[:：]$"),
    re.compile(r"^From:\s", re.IGNORECASE),
)


class MailConfigError(Exception):
    """接続設定の誤り・接続失敗。メッセージは顧客にそのまま表示してよい文面にする。"""


@dataclass
class ParsedMail:
    message_id: str
    subject: str
    from_address: str
    reply_address: str
    body: str
    references: str
    skip_reason: str | None = None


@dataclass
class MailAccountSettings:
    from_address: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    password: str


@dataclass
class FetchResult:
    uidvalidity: int | None
    last_uid: int | None
    messages: list[tuple[int, bytes]] = field(default_factory=list)


def _header(msg, name: str) -> str:
    value = msg.get(name)
    return str(value).strip() if value is not None else ""


def _extract_body(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        content = BeautifulSoup(content, "html.parser").get_text("\n")

    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if any(p.match(stripped) for p in _QUOTE_START_PATTERNS):
            break
        if stripped.startswith(">"):
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:MAX_BODY_CHARS]


def parse_mail(raw: bytes, *, own_address: str, fallback_id: str) -> ParsedMail:
    """受信メールを読み、自動返信してよいメールかどうかも判定する(skip_reasonがNoneなら対象)。"""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    from_address = parseaddr(_header(msg, "From"))[1].lower()
    reply_to = parseaddr(_header(msg, "Reply-To"))[1].lower()
    reply_address = reply_to or from_address
    subject = _header(msg, "Subject")[:300]
    parsed = ParsedMail(
        message_id=(_header(msg, "Message-ID") or fallback_id)[:300],
        subject=subject,
        from_address=from_address,
        reply_address=reply_address,
        body=_extract_body(msg),
        references=_header(msg, "References"),
    )

    auto_submitted = _header(msg, "Auto-Submitted").lower()
    precedence = _header(msg, "Precedence").lower()
    own = own_address.lower()
    if auto_submitted and auto_submitted != "no":
        parsed.skip_reason = "自動送信されたメール"
    elif precedence in ("bulk", "list", "junk", "auto_reply"):
        parsed.skip_reason = "一斉配信メール"
    elif _header(msg, "List-Id") or _header(msg, "List-Unsubscribe"):
        parsed.skip_reason = "メールマガジン・メーリングリスト"
    elif msg.get_content_type() == "multipart/report" or _header(msg, "X-Failed-Recipients"):
        parsed.skip_reason = "エラー通知(配信不能)"
    elif not reply_address or "@" not in reply_address:
        parsed.skip_reason = "返信先が無い"
    elif reply_address == own or from_address == own:
        parsed.skip_reason = "自社アドレスからのメール"
    elif _NOREPLY_PATTERN.search(reply_address):
        parsed.skip_reason = "返信不要のアドレス(noreply等)"
    elif _AUTO_SUBJECT_PATTERN.match(subject):
        parsed.skip_reason = "自動返信・不在通知"
    elif not parsed.body:
        parsed.skip_reason = "本文が空"
    return parsed


def validate_account_settings(account: MailAccountSettings) -> None:
    """保存・接続の前に確認する。社内アドレスや暗号化されないポートへは接続しない。"""
    if account.imap_port not in ALLOWED_IMAP_PORTS:
        raise MailConfigError("受信(IMAP)のポートは993(SSL/TLS)を指定してください。")
    if account.smtp_port not in ALLOWED_SMTP_PORTS:
        raise MailConfigError("送信(SMTP)のポートは465(SSL/TLS)または587(STARTTLS)を指定してください。")
    for host in (account.imap_host, account.smtp_host):
        if not host or not is_public_host(host):
            raise MailConfigError(f"メールサーバー「{host}」に接続できません。サーバー名をご確認ください。")


def _ensure_public_peer(sock) -> None:
    peer = sock.getpeername()[0]
    if not ipaddress.ip_address(peer.split("%")[0]).is_global:
        raise MailConfigError("このメールサーバーには接続できません。")


class ImapSmtpTransport:
    """実際のIMAP/SMTP通信(同期処理なので、呼び出し側でasyncio.to_threadから使う)。"""

    def fetch_new(self, account: MailAccountSettings, *, last_uid: int | None, uidvalidity: int | None) -> FetchResult:
        validate_account_settings(account)
        context = ssl.create_default_context()
        try:
            conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, ssl_context=context, timeout=CONNECT_TIMEOUT_SECONDS)
        except (OSError, imaplib.IMAP4.error) as e:
            raise MailConfigError("受信サーバーに接続できませんでした。サーバー名とポートをご確認ください。") from e
        try:
            _ensure_public_peer(conn.sock)
            try:
                conn.login(account.username, account.password)
            except imaplib.IMAP4.error as e:
                raise MailConfigError("受信サーバーにログインできませんでした。ユーザー名とパスワードをご確認ください。") from e
            typ, _ = conn.select("INBOX", readonly=True)
            if typ != "OK":
                raise MailConfigError("受信トレイを開けませんでした。")
            _, data = conn.response("UIDVALIDITY")
            current_validity = int(data[0]) if data and data[0] else None

            _, data = conn.uid("SEARCH", None, "ALL")
            all_uids = [int(u) for u in (data[0] or b"").split()]
            max_uid = max(all_uids) if all_uids else 0

            if last_uid is None or uidvalidity != current_validity:
                # 初回(またはサーバー側でUIDが振り直された)は、既存のメールに返信しない
                return FetchResult(uidvalidity=current_validity, last_uid=max_uid)

            new_uids = sorted(u for u in all_uids if u > last_uid)[:MAX_MESSAGES_PER_RUN]
            messages: list[tuple[int, bytes]] = []
            for uid in new_uids:
                typ, fetched = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if typ == "OK":
                    raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
                    if raw:
                        messages.append((uid, raw))
            return FetchResult(
                uidvalidity=current_validity, last_uid=new_uids[-1] if new_uids else last_uid, messages=messages
            )
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _smtp(self, account: MailAccountSettings):
        validate_account_settings(account)
        context = ssl.create_default_context()
        try:
            if account.smtp_port == 465:
                server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, context=context, timeout=CONNECT_TIMEOUT_SECONDS)
            else:
                server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=CONNECT_TIMEOUT_SECONDS)
                server.starttls(context=context)
        except (OSError, smtplib.SMTPException) as e:
            raise MailConfigError("送信サーバーに接続できませんでした。サーバー名とポートをご確認ください。") from e
        try:
            _ensure_public_peer(server.sock)
            server.login(account.username, account.password)
        except smtplib.SMTPException as e:
            server.close()
            raise MailConfigError("送信サーバーにログインできませんでした。ユーザー名とパスワードをご確認ください。") from e
        except MailConfigError:
            server.close()
            raise
        return server

    def send(
        self,
        account: MailAccountSettings,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None,
        references: str | None,
        auto: bool,
    ) -> None:
        msg = EmailMessage()
        msg["From"] = account.from_address
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=account.from_address.split("@")[-1])
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
        if auto:
            # 相手側の自動返信がこのメールに反応してループしないよう、自動送信であることを示す(RFC 3834)
            msg["Auto-Submitted"] = "auto-replied"
        msg.set_content(body)
        server = self._smtp(account)
        try:
            server.send_message(msg)
        except smtplib.SMTPException as e:
            raise MailConfigError("メールを送信できませんでした。") from e
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass

    def test_connection(self, account: MailAccountSettings) -> None:
        self.fetch_new(account, last_uid=None, uidvalidity=None)
        server = self._smtp(account)
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def reply_subject(subject: str) -> str:
    return subject if re.match(r"^\s*re\s*:", subject, re.IGNORECASE) else f"Re: {subject or 'お問い合わせ'}"


def compose_auto_reply(reply: str, *, company_name: str) -> str:
    return (
        f"{reply}\n\n"
        "――――――――――\n"
        f"このメールは{company_name}の自動応答です。内容によっては、担当者から改めてご連絡いたします。"
    )


async def count_recent_auto_replies(db: AsyncSession, *, tenant_id: uuid.UUID, sender: str | None = None) -> int:
    from src.core.models import MailProcessedMessage

    query = select(func.count()).where(
        MailProcessedMessage.tenant_id == tenant_id,
        MailProcessedMessage.auto_replied.is_(True),
        MailProcessedMessage.processed_at >= datetime.utcnow() - timedelta(hours=24),
    )
    if sender is not None:
        query = query.where(MailProcessedMessage.sender_address == sender)
    return (await db.execute(query)).scalar_one()


async def claim_message(db: AsyncSession, *, tenant_id: uuid.UUID, message_id: str, sender: str | None):
    """同じメールを二重に処理しないよう、先に記録する。既に処理済みならNoneを返す。"""
    from src.core.models import MailProcessedMessage

    row = MailProcessedMessage(tenant_id=tenant_id, message_id=message_id, sender_address=sender)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None
    return row


async def run_in_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)
