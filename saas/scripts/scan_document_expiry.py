"""社内資料(RAG)の有効期限スキャン(7-7-2-4)。1日1回程度のcron/systemd timerから呼ぶ想定。
Phase 8でworker/schedulerサービスを組むまでは、手動または一時的なcronで実行する。

使い方: docker compose exec api python -m scripts.scan_document_expiry
"""

import asyncio

from src.rag.expiry_scan import scan_document_expiry


async def main() -> None:
    result = await scan_document_expiry()
    print(f"期限接近の通知: {result['warned']}件, 期限切れによるEXPIRED化: {result['expired']}件")


if __name__ == "__main__":
    asyncio.run(main())
