"""起動時ヘルスチェックが失敗したらプロセスを落とす(8-4)ことの検証。"""

import pytest

from src.core.healthcheck import HealthCheckResult
from src.main import exit_if_checks_failed


def test_all_checks_ok_does_not_exit():
    exit_if_checks_failed([HealthCheckResult(name="db", ok=True), HealthCheckResult(name="anthropic", ok=True)])


def test_any_failed_check_exits_process():
    checks = [
        HealthCheckResult(name="db", ok=True),
        HealthCheckResult(name="anthropic", ok=False, detail="APIキーが無効です"),
    ]
    with pytest.raises(SystemExit) as exc_info:
        exit_if_checks_failed(checks)
    assert exc_info.value.code == 1
