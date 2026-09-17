"""Synthetic portal notifications; these are not samples from actual providers."""
from email.message import EmailMessage

import pytest

from src.channels.mail import MAX_BODY_CHARS, MAX_PORTAL_BODY_CHARS, parse_mail, portal_contact

OWN = 'office@example.com'


def parse(body, *, html=False):
    msg = EmailMessage()
    msg['From'] = 'noreply@portal.example'
    msg['To'] = OWN
    msg['Subject'] = '物件のお問い合わせ通知'
    msg.set_content(body, subtype='html' if html else 'plain')
    return parse_mail(bytes(msg), own_address=OWN, fallback_id='<portal-test>')


@pytest.mark.parametrize('body,email,phone', [
    ('サポート: support@portal.example\n【メールアドレス】customer@example.net', 'customer@example.net', None),
    ('メールアドレス:\n\ncustomer@example.net\n運営窓口: help@portal.example', 'customer@example.net', None),
    ('【お客様のメールアドレス】ＣＵＳＴＯＭＥＲ＠ｅｘａｍｐｌｅ．ｎｅｔ\n電話番号：０９０－１２３４－５６７８', 'customer@example.net', '090-1234-5678'),
    ('【E-mail】customer@example.net\n【TEL】03(1234)5678', 'customer@example.net', '03(1234)5678'),
    ('窓口: 03-1111-2222\n【電話番号】090-1234-5678', None, '090-1234-5678'),
    ('メールアドレス: first@example.net\nメールアドレス: second@example.net', None, None),
    ('first@example.net\nsecond@example.net', None, None),
    ('CUSTOMER@example.net\ncustomer@example.net', 'customer@example.net', None),
    ('OFFICE@example.com\nNOREPLY@portal.example\ncustomer@example.net', 'customer@example.net', None),
    ('メールアドレス: 未記入\n運営窓口: help@portal.example', None, None),
    ('メールアドレス: first@example.net / second@example.net', None, None),
    ('メールアドレス:\n運営窓口: help@portal.example', None, None),
    ('電話番号: 01234567890123456789', None, None),
    ('電話番号: 03-123-456\n物件番号: 012345', None, None),
    ('電話番号: 090-1234-5678\n電話番号: 080-1234-5678', None, None),
])
def test_contact_prefers_labels_and_does_not_guess_between_candidates(body, email, phone):
    contact = portal_contact(parse(body), own_address=OWN)
    assert contact == {'email': email, 'phone': phone}


def test_contact_can_follow_long_property_description_without_enlarging_ai_input():
    parsed = parse('物件詳細です。' * 500 + '\n【メールアドレス】customer@example.net\n【電話番号】090-1234-5678')
    assert len(parsed.body) <= MAX_BODY_CHARS
    assert portal_contact(parsed, own_address=OWN) == {'email': 'customer@example.net', 'phone': '090-1234-5678'}


def test_html_table_contact_is_read_from_adjacent_cell():
    parsed = parse('<p>窓口: help@portal.example</p><table><tr><td>メールアドレス</td><td>customer@example.net</td></tr><tr><td>電話番号</td><td>090-1234-5678</td></tr></table>', html=True)
    assert portal_contact(parsed, own_address=OWN) == {'email': 'customer@example.net', 'phone': '090-1234-5678'}


def test_quoted_contact_is_not_selected():
    parsed = parse('メールアドレス: customer@example.net\n-----Original Message-----\nメールアドレス: old@example.net')
    assert portal_contact(parsed, own_address=OWN)['email'] == 'customer@example.net'


def test_truncated_address_at_contact_limit_is_not_selected():
    partial_line = "\nメールアドレス: customer@example.ne"
    parsed = parse("x" * (MAX_PORTAL_BODY_CHARS - len(partial_line)) + partial_line + "t\n")
    assert len(parsed.contact_body) < MAX_PORTAL_BODY_CHARS
    assert portal_contact(parsed, own_address=OWN)["email"] is None


def test_truncated_single_line_does_not_fall_back_to_shortened_ai_input():
    parsed = parse("customer@example.net " + "x" * MAX_PORTAL_BODY_CHARS)
    assert parsed.contact_body == ""
    assert portal_contact(parsed, own_address=OWN)["email"] is None
