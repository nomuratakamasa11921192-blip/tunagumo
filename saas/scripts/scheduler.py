"""定期ジョブの常駐スケジューラ(Phase 8-2)。承認期限スキャン・社内資料の有効期限スキャン・
Phase 14の顧客スケジュール起動を、プロセス内で定期実行し続ける。
docker-compose の `scheduler` サービスとして起動する想定。

同じジョブをcronから個別に呼び出したい場合は scripts/scan_approval_deadlines.py と
scripts/scan_document_expiry.py と scripts/scan_schedules.py をそれぞれ使う
(README.mdのVPS手順書はそちらを使う想定で書いている)。どちらの方式でも二重実行は
しないこと(cronとこのスケジューラを同時に有効にしない)。

使い方: docker compose up -d scheduler
"""

import asyncio
import logging
from datetime import datetime

from anthropic import AsyncAnthropic

from src.agent.checkpointer import CheckpointerLifecycle
from src.agent.config_loader import load_config
from src.agent.deadline_scan import scan_approval_deadlines
from src.agent.llm import DEFAULT_TIMEOUT_SECONDS, StructuredLLM
from src.agent.schedule_tick import tick as schedule_tick
from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES
from src.core.config import settings
from src.core.healthcheck import run_startup_checks
from src.core.logging_config import setup_logging
from src.rag.expiry_scan import scan_document_expiry
from scripts.cleanup_web_chat_logs import cleanup_web_chat_logs

setup_logging()
logger = logging.getLogger(__name__)

APPROVAL_SCAN_INTERVAL_SECONDS = 5 * 60
DOCUMENT_EXPIRY_SCAN_INTERVAL_SECONDS = 24 * 60 * 60
SCHEDULE_TICK_INTERVAL_SECONDS = 60  # cronの分単位の粒度に合わせる
WEB_CHAT_CLEANUP_INTERVAL_SECONDS = 60 * 60  # Phase 16-4: 1時間ごとに24時間超の会話を削除


def _llm_factory() -> StructuredLLM:
    # 運営(ツナグモ)自身のAnthropic APIキーでクライアントを作る(2026-09-01、BYOK廃止)。
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=DEFAULT_TIMEOUT_SECONDS)
    return StructuredLLM(client=client)


async def _approval_scan_loop(app_configs: dict, checkpointer) -> None:
    while True:
        try:
            result = await scan_approval_deadlines(
                app_configs=app_configs,
                default_industry=DEFAULT_INDUSTRY,
                llm_factory=_llm_factory,
                checkpointer=checkpointer,
            )
            logger.info(
                "承認期限スキャン完了: リマインド%d件, 期限切れ%d件", result["reminded"], result["expired"]
            )
        except Exception:
            # 1回の失敗でスケジューラ全体を止めない(次の周期でリトライする)
            logger.exception("承認期限スキャンに失敗しました")
        await asyncio.sleep(APPROVAL_SCAN_INTERVAL_SECONDS)


async def _document_expiry_scan_loop() -> None:
    while True:
        try:
            result = await scan_document_expiry()
            logger.info(
                "社内資料の有効期限スキャン完了: 通知%d件, 期限切れ化%d件", result["warned"], result["expired"]
            )
        except Exception:
            logger.exception("社内資料の有効期限スキャンに失敗しました")
        await asyncio.sleep(DOCUMENT_EXPIRY_SCAN_INTERVAL_SECONDS)


async def _schedule_tick_loop(app_configs: dict, checkpointer) -> None:
    while True:
        try:
            result = await schedule_tick(
                now=datetime.utcnow(),
                app_configs=app_configs,
                default_industry=DEFAULT_INDUSTRY,
                llm_factory=_llm_factory,
                checkpointer=checkpointer,
            )
            if result["started"] or result["skipped_late"] or result["skipped_disabled"] or result["skipped_monthly_cap"]:
                logger.info(
                    "スケジュール起動tick完了: 起動%d件, 遅延スキップ%d件, 未設定スキップ%d件, 月間上限スキップ%d件",
                    result["started"],
                    result["skipped_late"],
                    result["skipped_disabled"],
                    result["skipped_monthly_cap"],
                )
        except Exception:
            logger.exception("スケジュール起動tickに失敗しました")
        await asyncio.sleep(SCHEDULE_TICK_INTERVAL_SECONDS)


async def _web_chat_cleanup_loop() -> None:
    while True:
        try:
            result = await cleanup_web_chat_logs()
            if result["deleted_sessions"] or result["deleted_request_logs"]:
                logger.info(
                    "Web埋め込みチャットの保持期間クリーンアップ完了: 会話%d件, ログ%d件削除",
                    result["deleted_sessions"],
                    result["deleted_request_logs"],
                )
        except Exception:
            logger.exception("Web埋め込みチャットのクリーンアップに失敗しました")
        await asyncio.sleep(WEB_CHAT_CLEANUP_INTERVAL_SECONDS)


async def main() -> None:
    # 8-4と同じ考え方: 必須の接続先が壊れたまま無言で動き続けない
    failed = [c for c in await run_startup_checks() if not c.ok]
    if failed:
        for c in failed:
            logger.error("起動時ヘルスチェックに失敗しました: %s (%s)", c.name, c.detail)
        raise SystemExit(1)

    app_configs = {industry: load_config(f"config/{industry}.yaml") for industry in INDUSTRIES}
    lifecycle = CheckpointerLifecycle()
    checkpointer = await lifecycle.start()

    logger.info(
        "スケジューラを起動しました(承認期限スキャン: %d秒間隔, 資料期限スキャン: %d秒間隔, "
        "顧客スケジュール起動: %d秒間隔)",
        APPROVAL_SCAN_INTERVAL_SECONDS,
        DOCUMENT_EXPIRY_SCAN_INTERVAL_SECONDS,
        SCHEDULE_TICK_INTERVAL_SECONDS,
    )
    try:
        await asyncio.gather(
            _approval_scan_loop(app_configs, checkpointer),
            _document_expiry_scan_loop(),
            _schedule_tick_loop(app_configs, checkpointer),
            _web_chat_cleanup_loop(),
        )
    finally:
        await lifecycle.stop()


if __name__ == "__main__":
    asyncio.run(main())
