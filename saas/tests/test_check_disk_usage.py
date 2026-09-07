import pytest

from scripts.check_disk_usage import DISK_USAGE_WARNING_THRESHOLD_PERCENT, check_and_notify

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_no_notification_below_threshold(monkeypatch):
    async def fake_send_email(**kwargs):
        raise AssertionError("閾値未満なのでメールを送るべきではない")

    monkeypatch.setattr("scripts.check_disk_usage.send_email", fake_send_email)

    notified = await check_and_notify(
        used_percent=DISK_USAGE_WARNING_THRESHOLD_PERCENT - 1, admin_email="admin@example.com"
    )
    assert notified is False


async def test_notification_sent_at_or_above_threshold(monkeypatch):
    sent = []

    async def fake_send_email(*, to, subject, html):
        sent.append(to)
        return True

    monkeypatch.setattr("scripts.check_disk_usage.send_email", fake_send_email)

    notified = await check_and_notify(
        used_percent=DISK_USAGE_WARNING_THRESHOLD_PERCENT, admin_email="admin@example.com"
    )
    assert notified is True
    assert sent == ["admin@example.com"]


async def test_no_notification_when_admin_email_unset(monkeypatch):
    async def fake_send_email(**kwargs):
        raise AssertionError("宛先未設定なのでメールを送るべきではない")

    monkeypatch.setattr("scripts.check_disk_usage.send_email", fake_send_email)

    notified = await check_and_notify(used_percent=99.0, admin_email="")
    assert notified is False
