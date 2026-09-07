"""ローカル開発用に、固定APIキーのテナントを1件作成する。
本番では使わない。

使い方:
    python -m scripts.seed_dev_tenant                # web_agency(既定)
    python -m scripts.seed_dev_tenant real_estate     # 業種を指定
"""
import asyncio
import os
import sys

from sqlalchemy import select

from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES, hash_api_key
from src.core.crypto import encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Tenant

DEV_API_KEY = "dev-local-test-key"


async def main() -> None:
    industry = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INDUSTRY
    if industry not in INDUSTRIES:
        print(f"未知の業種です: {industry}（利用可能: {INDUSTRIES}）")
        return

    # 開発用テナントは、テナント自身のAnthropic APIキーの代わりにツナグモ自身の
    # .envのANTHROPIC_API_KEYを流用する(ローカル動作確認のためだけの措置。本番の
    # テナント発行は必ず顧客自身のキーを使う。POST /admin/tenants 参照)。
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    async with async_session_factory() as db:
        existing = await db.execute(
            select(Tenant).where(Tenant.api_key_hash == hash_api_key(DEV_API_KEY))
        )
        found = existing.scalar_one_or_none()
        if found:
            print("既に存在します。APIキー:", DEV_API_KEY, "/ 業種:", found.industry)
            return

        tenant = Tenant(
            name="開発用テナント",
            industry=industry,
            api_key_hash=hash_api_key(DEV_API_KEY),
            anthropic_api_key=encrypt_secret(anthropic_api_key) if anthropic_api_key else None,
        )
        db.add(tenant)
        await db.commit()
        print("作成しました。APIキー:", DEV_API_KEY, "/ 業種:", industry)


if __name__ == "__main__":
    asyncio.run(main())
