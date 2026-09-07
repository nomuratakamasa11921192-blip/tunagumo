import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete, select

from src.agent.config_loader import load_config
from src.api.deps import INDUSTRIES, get_llm, hash_api_key
from src.core.crypto import encrypt_secret
from src.core.db import async_session_factory
from src.core.models import Approval, AuditLog, Chunk, Document
from src.core.models import Schedule, ScheduleRun
from src.core.models import Session as SessionModel
from src.core.models import LineWebhookEvent, Tenant, TenantUser, TenantUserToken, WebChatRequestLog, WebChatSession
from src.main import app
from tests.fakes import FakeLLM


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_LIGHT", "claude-haiku-4-5-20251001")
    return load_config("config/default.yaml")


@pytest.fixture
async def tenant():
    api_key = f"test-key-{uuid.uuid4()}"
    async with async_session_factory() as db:
        t = Tenant(
            name="pytest-tenant",
            api_key_hash=hash_api_key(api_key),
            # get_llmの「未設定なら402」チェックを通すためのダミー値。実際の中身は
            # clientフィクスチャのdependency_overridesでFakeLLMに差し替わるので使われないが、
            # src/agent/deadline_scan.pyはDBから直接読んでdecrypt_secretするため、
            # 本物の暗号化済み値でないとCryptoConfigErrorになる。
            anthropic_api_key=encrypt_secret("sk-ant-test00000000000000000000"),
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)

    yield {"id": t.id, "api_key": api_key}

    async with async_session_factory() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == t.id))
        await db.execute(delete(Approval).where(Approval.tenant_id == t.id))
        await db.execute(delete(SessionModel).where(SessionModel.tenant_id == t.id))
        # Chunk/DocumentはTenantへの外部キーを持つため、先に消さないと
        # 削除時にForeignKeyViolationErrorになる(Phase 7 RAG)。
        # ChunkはDocumentへの外部キーも持つため、Document側より先に消す必要がある。
        await db.execute(delete(Chunk).where(Chunk.tenant_id == t.id))
        await db.execute(delete(Document).where(Document.tenant_id == t.id))
        # ScheduleRunはScheduleへの外部キーを持つため、Schedule側より先に消す(Phase 14)
        schedule_ids = (await db.execute(select(Schedule.id).where(Schedule.tenant_id == t.id))).scalars().all()
        if schedule_ids:
            await db.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id.in_(schedule_ids)))
        await db.execute(delete(Schedule).where(Schedule.tenant_id == t.id))
        # Phase 16
        await db.execute(delete(WebChatRequestLog).where(WebChatRequestLog.tenant_id == t.id))
        await db.execute(delete(WebChatSession).where(WebChatSession.tenant_id == t.id))
        # Phase 13の前提: TenantUserTokenはTenantUserへの外部キーを持つため先に消す
        await db.execute(delete(TenantUserToken).where(TenantUserToken.tenant_id == t.id))
        await db.execute(delete(TenantUser).where(TenantUser.tenant_id == t.id))
        # Phase 15の土台
        await db.execute(delete(LineWebhookEvent).where(LineWebhookEvent.tenant_id == t.id))
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest.fixture
async def client(config):
    # lifespanはASGITransport経由では自動実行されないため、本番のlifespanと
    # 同じ内容を明示的に用意する(app.state.app_config等)。
    # チェックポインタはMemorySaver(プロセス内)で十分。PostgresSaverでの
    # 再起動耐性はtest_agent_durability.pyで別途検証する。
    # 業種ごとに別ファイルを持つが、テストは部署ID(copy_dept等)がdefault.yaml/
    # web_agency.yaml準拠であることだけに依存するので、全業種に同じconfigを充てて
    # 差し替えられるようにしておく(get_app_configはテナントのindustryで引く)。
    app.state.app_configs = {industry: config for industry in INDUSTRIES}
    # 本番はテナントごとに別のStructuredLLM(顧客自身のAnthropic APIキー)を組み立てるが、
    # テストでは全員同じFakeLLMを共有し、既存テストの`app.state.llm.queue_structured(...)`
    # という呼び方をそのまま使えるようにする(get_llm/get_internal_llmの両方をこれに向ける)。
    fake_llm = FakeLLM()
    app.state.llm = fake_llm
    app.state.internal_llm = fake_llm
    app.state.checkpointer = MemorySaver()
    app.dependency_overrides[get_llm] = lambda: app.state.llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_llm, None)
