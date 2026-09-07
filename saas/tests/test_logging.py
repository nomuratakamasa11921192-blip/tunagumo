"""構造化ログ(8-5)の検証。JSON形式で出力され、tenant_id/session_idが自動付与されること。"""

import io
import json
import logging

from src.core.logging_config import RequestContext, _ContextFilter, _JsonFormatter


def _make_logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger("test_logging_structured")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter())
    logger.addHandler(handler)
    return logger


def test_log_line_is_valid_json_with_message_and_level():
    stream = io.StringIO()
    logger = _make_logger(stream)

    logger.info("テストメッセージ")

    line = json.loads(stream.getvalue().strip())
    assert line["message"] == "テストメッセージ"
    assert line["level"] == "INFO"
    assert "timestamp" in line


def test_tenant_and_session_id_are_attached_within_request_context():
    stream = io.StringIO()
    logger = _make_logger(stream)

    with RequestContext(tenant_id="tenant-123", session_id="session-456"):
        logger.info("処理中")

    line = json.loads(stream.getvalue().strip())
    assert line["tenant_id"] == "tenant-123"
    assert line["session_id"] == "session-456"


def test_tenant_and_session_id_absent_outside_request_context():
    stream = io.StringIO()
    logger = _make_logger(stream)

    logger.info("コンテキスト外のログ")

    line = json.loads(stream.getvalue().strip())
    assert "tenant_id" not in line
    assert "session_id" not in line


def test_context_does_not_leak_across_calls():
    stream = io.StringIO()
    logger = _make_logger(stream)

    with RequestContext(tenant_id="tenant-a", session_id="session-a"):
        logger.info("1件目")
    logger.info("2件目")

    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines()]
    assert lines[0]["tenant_id"] == "tenant-a"
    assert "tenant_id" not in lines[1]
