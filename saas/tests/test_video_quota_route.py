import asyncio

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.api.deps import get_higgsfield_client
from src.core.db import async_session_factory
from src.core.models import Tenant as TenantModel
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeHiggsfieldClient:
    async def generate_video(self, prompt, quality="draft", reference_image_url=None):
        return "https://cdn.example.com/video.mp4"


@pytest.fixture
def with_fake_higgsfield():
    app.dependency_overrides[get_higgsfield_client] = lambda: _FakeHiggsfieldClient()
    yield
    app.dependency_overrides.pop(get_higgsfield_client, None)


async def _create_awaiting_approval_session(client, headers) -> str:
    llm = app.state.llm
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="物件紹介文を作ってほしい", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="物件紹介文", reason="要件が揃った")
    )
    llm.queue_text("## 物件紹介文\nテスト物件の紹介文です。")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="物件紹介文が完成しました", qa_result="合格"))

    created = await client.post("/api/sessions", json={"text": "物件紹介文を作って"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "AWAITING_APPROVAL":
            return session_id
        await asyncio.sleep(0.02)
    raise TimeoutError("承認待ちまで進みませんでした")


async def test_generate_video_succeeds_under_quota(client, tenant, with_fake_higgsfield):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers)

    res = await client.post(f"/api/sessions/{session_id}/generate-video", json={"quality": "draft"}, headers=headers)

    assert res.status_code == 201


async def test_generate_video_blocked_at_budget(client, tenant, with_fake_higgsfield):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    # 先にセッションを作ってから予算を使い切らせる(セッション作成自体にも同じ予算チェックが
    # あるため、先に予算切れにすると依頼作成の時点で429になってしまう)。
    session_id = await _create_awaiting_approval_session(client, headers)

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        row.plan = "light"
        row.ai_cost_this_period_usd = 10.0  # lightプランの月間予算($10)を使い切った状態
        await db.commit()

    res = await client.post(f"/api/sessions/{session_id}/generate-video", json={"quality": "draft"}, headers=headers)

    assert res.status_code == 429


async def test_generate_video_records_cost_only_on_success(client, tenant, with_fake_higgsfield):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    session_id = await _create_awaiting_approval_session(client, headers)

    await client.post(f"/api/sessions/{session_id}/generate-video", json={"quality": "draft"}, headers=headers)

    async with async_session_factory() as db:
        row = await db.get(TenantModel, tenant["id"])
        assert row.ai_cost_this_period_usd > 0


async def test_admin_can_set_tenant_plan(client, tenant):
    from src.core.config import settings

    headers = {"Authorization": f"Bearer {settings.admin_api_key}"}
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/plan", json={"plan": "standard"}, headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "standard"
    assert body["monthly_ai_budget_usd"] == 40.0


async def test_admin_rejects_unknown_plan(client, tenant):
    from src.core.config import settings

    headers = {"Authorization": f"Bearer {settings.admin_api_key}"}
    res = await client.put(
        f"/admin/tenants/{tenant['id']}/plan", json={"plan": "does-not-exist"}, headers=headers
    )
    assert res.status_code == 400
