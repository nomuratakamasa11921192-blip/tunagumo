"""起動時ヘルスチェック(4-4)。デプロイ時・定期監視から呼ぶ想定。
失敗があれば非ゼロ終了する(呼び出し元のsystemd/cron側で異常を検知できるようにする)。

使い方: docker compose exec api python -m scripts.health_check
"""

import asyncio
import sys

from src.core.healthcheck import run_startup_checks


async def main() -> int:
    results = await run_startup_checks()
    ok = True
    for r in results:
        status = "OK" if r.ok else "FAILED"
        print(f"[{status}] {r.name}" + (f": {r.detail}" if r.detail else ""))
        if not r.ok:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
