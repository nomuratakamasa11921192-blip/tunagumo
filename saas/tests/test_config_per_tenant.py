"""業種ごとに異なるapp_configが解決されることの検証(per-tenant config resolution)。
あわせて、運営(ツナグモ)自身のAnthropic APIキーでllmが解決されること
(2026-09-01、BYOK廃止後の事業モデル)も検証する。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.agent.config_loader import load_config
from src.agent.llm import StructuredLLM
from src.api.deps import DEFAULT_INDUSTRY, get_llm, resolve_app_config
from src.core.config import settings

# モジュール全体にpytestmark = pytest.mark.asyncio(...)を付けると、同ファイル内の
# 同期テストにまで警告が出る。かといって付けないと、DBを使う他ファイルとイベント
# ループがずれてasyncpgの接続が壊れる(既知のpytest-asyncioの癖)。そのため、
# async defのテスト関数にだけ個別にデコレータを付ける。


@pytest.fixture
def real_configs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_LIGHT", "claude-haiku-4-5-20251001")
    return {
        "web_agency": load_config("config/web_agency.yaml"),
        "real_estate": load_config("config/real_estate.yaml"),
        "recruiting": load_config("config/recruiting.yaml"),
        "legal": load_config("config/legal.yaml"),
    }


def _fake_request(app_configs: dict) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(app_configs=app_configs)))


def test_resolve_app_config_returns_the_matching_industry(real_configs):
    request = _fake_request(real_configs)

    resolved = resolve_app_config(request, "real_estate")
    assert "listing_copy_dept" in resolved.departments
    assert resolved.company.business == "賃貸仲介・売買仲介"

    resolved_legal = resolve_app_config(request, "legal")
    assert "client_notice_dept" in resolved_legal.departments
    assert "listing_copy_dept" not in resolved_legal.departments


def test_resolve_app_config_falls_back_to_default_for_unknown_industry(real_configs):
    request = _fake_request(real_configs)
    resolved = resolve_app_config(request, "totally_unknown_industry")
    assert resolved is real_configs[DEFAULT_INDUSTRY]


def test_legal_config_forbids_document_drafting_language_in_prompts(real_configs):
    """士業の最重要制約(業務独占業務をAIにやらせない)がプロンプトに明記されているか。"""
    legal = real_configs["legal"]
    ceo_prompt = legal.departments["ceo_office"].system_prompt
    assert "官公署" in ceo_prompt
    assert "有資格者" in ceo_prompt


@pytest.mark.asyncio(loop_scope="session")
async def test_get_llm_builds_client_from_operators_own_anthropic_key(monkeypatch):
    """2026-09-01、BYOK廃止: AI利用料は運営(ツナグモ)が負担するため、テナントの
    キーではなくsettings.anthropic_api_key(運営自身のキー)からクライアントを作る。"""
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-api03-operatorkey00000000")
    tenant = SimpleNamespace()  # テナント自身のキーはもう参照されない
    llm = await get_llm(tenant=tenant)
    assert isinstance(llm, StructuredLLM)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_llm_rejects_when_operator_key_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    tenant = SimpleNamespace()
    with pytest.raises(HTTPException) as exc_info:
        await get_llm(tenant=tenant)
    assert exc_info.value.status_code == 503
