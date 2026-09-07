import asyncio

import pytest

from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.core.config import settings
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_api_key}"}


async def test_dashboard_requires_admin_auth(client):
    res = await client.get("/admin/dashboard")
    assert res.status_code == 401


async def test_dashboard_rejects_wrong_key(client):
    res = await client.get("/admin/dashboard", headers={"Authorization": "Bearer wrong-key"})
    assert res.status_code == 401


async def test_dashboard_reflects_tenant_activity(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    app.state.llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))

    created = await client.post("/api/sessions", json={"text": "こんにちは"}, headers=headers)
    session_id = created.json()["session_id"]

    for _ in range(100):
        res = await client.get(f"/api/sessions/{session_id}", headers=headers)
        if res.json()["status"] == "COMPLETED":
            break
        await asyncio.sleep(0.02)

    dash = await client.get("/admin/dashboard", headers=admin_headers())
    assert dash.status_code == 200
    body = dash.json()
    row = next(t for t in body["tenants"] if t["tenant_id"] == str(tenant["id"]))
    assert row["session_count"] >= 1
    assert row["error_count"] == 0


async def test_list_industries(client):
    res = await client.get("/admin/industries", headers=admin_headers())
    assert res.status_code == 200
    assert set(res.json()["industries"]) == {"real_estate"}


async def test_get_config_returns_current_yaml(client):
    res = await client.get("/admin/config/real_estate", headers=admin_headers())
    assert res.status_code == 200
    assert "departments:" in res.json()["yaml_text"]


async def test_get_config_rejects_unknown_industry(client):
    res = await client.get("/admin/config/not_a_real_industry", headers=admin_headers())
    assert res.status_code == 404


async def test_update_config_rejects_invalid_yaml(client):
    res = await client.put(
        "/admin/config/real_estate",
        json={"yaml_text": "not: valid\n  - this is broken: yaml: :"},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_update_config_validates_and_hot_reloads(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_LIGHT", "claude-haiku-4-5-20251001")

    original = await client.get("/admin/config/real_estate", headers=admin_headers())
    original_text = original.json()["yaml_text"]

    updated_text = original_text.replace(
        'name: "サンプル不動産株式会社"', 'name: "更新後の会社名株式会社"'
    )
    assert updated_text != original_text

    try:
        res = await client.put(
            "/admin/config/real_estate", json={"yaml_text": updated_text}, headers=admin_headers()
        )
        assert res.status_code == 200
        assert res.json()["company_name"] == "更新後の会社名株式会社"
        # ②: 再デプロイなしで即座に反映されること(この業種のみ)
        assert app.state.app_configs["real_estate"].company.name == "更新後の会社名株式会社"
    finally:
        # 他のテストに影響しないよう元に戻す
        await client.put(
            "/admin/config/real_estate", json={"yaml_text": original_text}, headers=admin_headers()
        )


async def test_regression_test_endpoint_runs_golden_cases_with_fake_llm(client):
    llm = app.state.llm
    # ケース1: 挨拶
    llm.queue_structured(Triage(intent="GREETING", reply="こんにちは", reason="挨拶"))
    # ケース2: コピー作成 -> ASSIGN -> QA合格 -> SUMMARIZE -> 承認待ち
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="LPの見出しを作る", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## 見出し案")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))

    res = await client.post("/admin/regression-test", headers=admin_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["all_passed"] is True
    assert len(body["results"]) == 2


VALID_ANTHROPIC_KEY = "sk-ant-api03-testkeyfortestsonly000000"


async def test_create_tenant_issues_api_key_and_sets_industry(client):
    res = await client.post(
        "/admin/tenants",
        json={
            "name": "テスト工務店株式会社",
            "industry": "real_estate",
            "anthropic_api_key": VALID_ANTHROPIC_KEY,
        },
        headers=admin_headers(),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["industry"] == "real_estate"
    assert body["api_key"]
    # 顧客のAnthropic APIキーはレスポンスに含まれないこと(必要ないので漏らさない)
    assert "anthropic_api_key" not in body
    # emailを渡していないので、ウェルカムメールは送っていない
    assert body["email_sent"] is False

    # 発行したAPIキーでそのままログインできること
    headers = {"Authorization": f"Bearer {body['api_key']}"}
    res2 = await client.get("/api/sessions", headers=headers)
    assert res2.status_code == 200


async def test_create_tenant_without_openai_key_succeeds(client):
    """openai_api_keyは任意(RAGを使わない顧客は未入力でよい)。"""
    res = await client.post(
        "/admin/tenants",
        json={"name": "RAG未使用テナント", "industry": "real_estate", "anthropic_api_key": VALID_ANTHROPIC_KEY},
        headers=admin_headers(),
    )
    assert res.status_code == 201


async def test_create_tenant_with_openai_key_validates_format(client):
    res = await client.post(
        "/admin/tenants",
        json={
            "name": "不正なOpenAIキー",
            "industry": "real_estate",
            "anthropic_api_key": VALID_ANTHROPIC_KEY,
            "openai_api_key": "not-a-valid-key",
        },
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_create_tenant_with_valid_openai_key_succeeds(client):
    res = await client.post(
        "/admin/tenants",
        json={
            "name": "RAG使用テナント",
            "industry": "real_estate",
            "anthropic_api_key": VALID_ANTHROPIC_KEY,
            "openai_api_key": "sk-test-openai-key-000000000000",
        },
        headers=admin_headers(),
    )
    assert res.status_code == 201
    assert "openai_api_key" not in res.json()


async def test_create_tenant_with_email_but_resend_unconfigured_skips_email(client):
    """テスト環境ではRESEND_API_KEYが設定されていないので、emailを渡しても送信されない
    (テナント発行自体は成功する)ことを確認する。"""
    res = await client.post(
        "/admin/tenants",
        json={
            "name": "メール未設定確認",
            "industry": "real_estate",
            "anthropic_api_key": VALID_ANTHROPIC_KEY,
            "email": "customer@example.com",
        },
        headers=admin_headers(),
    )
    assert res.status_code == 201
    assert res.json()["email_sent"] is False


async def test_create_tenant_sends_welcome_email_when_resend_configured(client, monkeypatch):
    """src.admin.routes.send_emailを直接差し替える。httpx.AsyncClient.postを
    グローバルに差し替えると、テストクライアント自身(ASGITransport経由)のPOSTまで
    横取りしてしまう(実際に踏んだ)ため、この粒度で止める。"""
    sent = {}

    async def fake_send_email(*, to, subject, html):
        sent["to"] = to
        sent["subject"] = subject
        sent["html"] = html
        return True

    monkeypatch.setattr("src.admin.routes.send_email", fake_send_email)

    res = await client.post(
        "/admin/tenants",
        json={
            "name": "メール送信確認",
            "industry": "real_estate",
            "anthropic_api_key": VALID_ANTHROPIC_KEY,
            "email": "customer@example.com",
        },
        headers=admin_headers(),
    )
    assert res.status_code == 201
    assert res.json()["email_sent"] is True
    assert sent["to"] == "customer@example.com"
    # メール本文にはSaaSログイン用のAPIキー(tsg_...)だけを載せ、顧客のAnthropic
    # APIキー(sk-ant-...)は絶対に含めないこと
    assert "sk-ant" not in sent["html"]


async def test_create_tenant_rejects_unknown_industry(client):
    res = await client.post(
        "/admin/tenants",
        json={"name": "x", "industry": "not_a_real_industry", "anthropic_api_key": VALID_ANTHROPIC_KEY},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_create_tenant_rejects_malformed_anthropic_key(client):
    res = await client.post(
        "/admin/tenants",
        json={"name": "x", "industry": "real_estate", "anthropic_api_key": "not-a-real-key"},
        headers=admin_headers(),
    )
    assert res.status_code == 400


async def test_create_tenant_requires_admin_auth(client):
    res = await client.post("/admin/tenants", json={"name": "x"})
    assert res.status_code == 401


async def test_update_tenant_anthropic_key_rotates_key(client):
    created = await client.post(
        "/admin/tenants",
        json={"name": "ローテ対象", "industry": "real_estate", "anthropic_api_key": VALID_ANTHROPIC_KEY},
        headers=admin_headers(),
    )
    tenant_id = created.json()["tenant_id"]

    res = await client.patch(
        f"/admin/tenants/{tenant_id}/anthropic-key",
        json={"anthropic_api_key": "sk-ant-api03-rotatedkey0000000000000"},
        headers=admin_headers(),
    )
    assert res.status_code == 204


async def test_update_tenant_anthropic_key_rejects_unknown_tenant(client):
    res = await client.patch(
        "/admin/tenants/00000000-0000-0000-0000-000000000000/anthropic-key",
        json={"anthropic_api_key": VALID_ANTHROPIC_KEY},
        headers=admin_headers(),
    )
    assert res.status_code == 404
