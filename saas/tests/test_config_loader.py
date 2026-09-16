import os

import pytest

from src.agent.config_loader import ConfigError, ConfigSecurityError, load_config

CONFIG_PATH = "config/default.yaml"


def test_default_config_loads_successfully(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "test-model-light")
    config = load_config(CONFIG_PATH)
    assert config.company.name == "サンプル・クリエイティブ株式会社"
    assert "ceo_office" in config.departments
    assert "qa_auditor" in config.departments
    assert config.departments["ceo_office"].model == "test-model"
    assert config.departments["ceo_office"].triage_model == "test-model-light"


@pytest.mark.parametrize(
    "industry", ["web_agency", "real_estate", "recruiting", "legal"]
)
def test_all_industry_configs_load_successfully(industry, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "test-model-light")
    config = load_config(f"config/{industry}.yaml")
    assert "ceo_office" in config.departments
    assert "qa_auditor" in config.departments
    assert config.departments["ceo_office"].model == "test-model"
    assert config.departments["ceo_office"].triage_model == "test-model-light"


def test_missing_env_var_raises(monkeypatch):
    # config/*.yamlはLLM_MODEL/LLM_MODEL_LIGHTを参照する(2026-09-16、提供元の切り替え対応)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_LIGHT", raising=False)
    with pytest.raises(ConfigError):
        load_config(CONFIG_PATH)


def test_hardcoded_secret_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_MODEL_LIGHT", "gpt-5.6-luna")
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        """
company:
  name: "テスト"
  business: "テスト"
limits:
  max_budget_usd: 1.0
  max_total_steps: 10
  max_retries_per_dept: 1
  max_rejections: 1
  max_clarify_rounds: 1
  approval_deadline_hours: 24
  max_parallel_depts: 1
pricing:
  updated_at: "2026-08-18"
  models:
    "${OPENAI_MODEL}":
      input_per_mtok: 3.0
      output_per_mtok: 15.0
compliance:
  forbidden_words: []
departments:
  ceo_office:
    role: "統括"
    model: "sk-ant-api03-abcdefghijklmnop"
    depends_on: []
    system_prompt: "test"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigSecurityError):
        load_config(bad_config)


def test_nonexistent_depends_on_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    bad_config = tmp_path / "bad_deps.yaml"
    bad_config.write_text(
        """
company:
  name: "テスト"
  business: "テスト"
limits:
  max_budget_usd: 1.0
  max_total_steps: 10
  max_retries_per_dept: 1
  max_rejections: 1
  max_clarify_rounds: 1
  approval_deadline_hours: 24
  max_parallel_depts: 1
pricing:
  updated_at: "2026-08-18"
  models:
    "${OPENAI_MODEL}":
      input_per_mtok: 3.0
      output_per_mtok: 15.0
compliance:
  forbidden_words: []
departments:
  ceo_office:
    role: "統括"
    model: "${OPENAI_MODEL}"
    depends_on: []
    system_prompt: "test"
  qa_auditor:
    role: "品質保証"
    model: "${OPENAI_MODEL}"
    depends_on: [nonexistent_dept]
    system_prompt: "test"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="存在しない部署ID"):
        load_config(bad_config)


def test_circular_dependency_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    bad_config = tmp_path / "circular.yaml"
    bad_config.write_text(
        """
company:
  name: "テスト"
  business: "テスト"
limits:
  max_budget_usd: 1.0
  max_total_steps: 10
  max_retries_per_dept: 1
  max_rejections: 1
  max_clarify_rounds: 1
  approval_deadline_hours: 24
  max_parallel_depts: 1
pricing:
  updated_at: "2026-08-18"
  models:
    "${OPENAI_MODEL}":
      input_per_mtok: 3.0
      output_per_mtok: 15.0
compliance:
  forbidden_words: []
departments:
  ceo_office:
    role: "統括"
    model: "${OPENAI_MODEL}"
    depends_on: []
    system_prompt: "test"
  dept_a:
    role: "a"
    model: "${OPENAI_MODEL}"
    depends_on: [dept_b]
    system_prompt: "test"
  dept_b:
    role: "b"
    model: "${OPENAI_MODEL}"
    depends_on: [dept_a]
    system_prompt: "test"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="循環参照"):
        load_config(bad_config)


def test_blank_system_prompt_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    bad_config = tmp_path / "blank_prompt.yaml"
    bad_config.write_text(
        """
company:
  name: "テスト"
  business: "テスト"
limits:
  max_budget_usd: 1.0
  max_total_steps: 10
  max_retries_per_dept: 1
  max_rejections: 1
  max_clarify_rounds: 1
  approval_deadline_hours: 24
  max_parallel_depts: 1
pricing:
  updated_at: "2026-08-18"
  models:
    "${OPENAI_MODEL}":
      input_per_mtok: 3.0
      output_per_mtok: 15.0
compliance:
  forbidden_words: []
departments:
  ceo_office:
    role: "統括"
    model: "${OPENAI_MODEL}"
    depends_on: []
    system_prompt: "   "
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_config(bad_config)


def test_unknown_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    bad_config = tmp_path / "unknown_key.yaml"
    bad_config.write_text(
        """
company:
  name: "テスト"
  business: "テスト"
limits:
  max_budget_usd: 1.0
  max_total_steps: 10
  max_retries_per_dept: 1
  max_rejections: 1
  max_clarify_rounds: 1
  approval_deadline_hours: 24
  max_parallel_depts: 1
pricing:
  updated_at: "2026-08-18"
  models:
    "${OPENAI_MODEL}":
      input_per_mtok: 3.0
      output_per_mtok: 15.0
compliance:
  forbidden_words: []
departments:
  ceo_office:
    role: "統括"
    model: "${OPENAI_MODEL}"
    depends_on: []
    system_prompt: "test"
    totally_unknown_key: "value"
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_config(bad_config)
