"""ディスク使用率の監視(8-6)。閾値を超えたら管理者(野村さん)にメールで通知する。
バックアップがディスクを埋めてサービスを止める、という事故を防ぐための最終防衛線。

使い方: docker compose exec api python -m scripts.check_disk_usage
cron例(README.md参照): 0 8 * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.check_disk_usage >> /var/log/tsunagumo_scan.log 2>&1
"""

import asyncio
import shutil

from src.core.config import settings
from src.core.email import send_email

DISK_USAGE_WARNING_THRESHOLD_PERCENT = 85


def disk_usage_percent(path: str = "/") -> float:
    total, used, _free = shutil.disk_usage(path)
    return used / total * 100


async def check_and_notify(*, used_percent: float, admin_email: str) -> bool:
    """閾値を超えていたら通知メールを送る。戻り値は「通知を試みたか」(送信成功/失敗は問わない)。"""
    if used_percent < DISK_USAGE_WARNING_THRESHOLD_PERCENT:
        return False

    if not admin_email:
        return False

    await send_email(
        to=admin_email,
        subject="【ツナグモ】VPSのディスク使用率が高くなっています",
        html=(
            f"<p>ディスク使用率が {used_percent:.1f}% になっています"
            f"(閾値: {DISK_USAGE_WARNING_THRESHOLD_PERCENT}%)。</p>"
            "<p>古いバックアップやログが溜まっていないか確認してください。</p>"
        ),
    )
    return True


async def main() -> None:
    used_percent = disk_usage_percent()
    print(f"ディスク使用率: {used_percent:.1f}%")

    if used_percent < DISK_USAGE_WARNING_THRESHOLD_PERCENT:
        return

    print(f"警告: ディスク使用率が閾値({DISK_USAGE_WARNING_THRESHOLD_PERCENT}%)を超えています")

    if not settings.admin_notification_email:
        print("ADMIN_NOTIFICATION_EMAIL未設定のため、通知メールは送れません")
        return

    await check_and_notify(used_percent=used_percent, admin_email=settings.admin_notification_email)


if __name__ == "__main__":
    asyncio.run(main())
