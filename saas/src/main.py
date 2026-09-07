import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.admin.routes import router as admin_router
from src.agent.checkpointer import CheckpointerLifecycle
from src.agent.config_loader import load_config
from src.agent.llm import StructuredLLM
from src.api.deps import INDUSTRIES
from src.api.routes.account import router as account_router
from src.api.routes.chat import router as chat_router
from src.api.routes.documents import router as documents_router
from src.api.routes.market_data import router as market_data_router
from src.api.routes.property_csv import router as property_csv_router
from src.api.routes.property_url import router as property_url_router
from src.api.routes.reel import router as reel_router
from src.api.routes.room_tour import router as room_tour_router
from src.api.routes.schedules import router as schedules_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.stripe_webhook import router as stripe_webhook_router
from src.core.healthcheck import HealthCheckResult, run_startup_checks
from src.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

_checkpointer_lifecycle = CheckpointerLifecycle()


def exit_if_checks_failed(checks: list[HealthCheckResult]) -> None:
    """8-4: 起動時ヘルスチェックに失敗した項目があれば、リクエストを受け付ける前に
    プロセスを明示的に落とす(失敗したまま起動して後からエラーの山になるのを防ぐ)。"""
    failed = [c for c in checks if not c.ok]
    if not failed:
        return
    for c in failed:
        logger.error("起動時ヘルスチェックに失敗しました: %s (%s)", c.name, c.detail)
    sys.exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    exit_if_checks_failed(await run_startup_checks())

    # 業種ごとに設定を読み込み、テナントのindustryで引く(get_app_config)。
    app.state.app_configs = {
        industry: load_config(f"config/{industry}.yaml") for industry in INDUSTRIES
    }
    # ツナグモ自身のキー(.envのANTHROPIC_API_KEY)。健全性チェック・管理画面の回帰テスト専用。
    # 顧客の実セッションは各テナントのanthropic_api_keyを使う(src/api/deps.py get_llm)。
    app.state.internal_llm = StructuredLLM()
    app.state.checkpointer = await _checkpointer_lifecycle.start()
    yield
    await _checkpointer_lifecycle.stop()


app = FastAPI(title="tsunagumo", lifespan=lifespan)

app.include_router(sessions_router)
app.include_router(account_router)
app.include_router(documents_router)
app.include_router(property_url_router)
app.include_router(property_csv_router)
app.include_router(market_data_router)
app.include_router(reel_router)
app.include_router(room_tour_router)
app.include_router(schedules_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(stripe_webhook_router)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
