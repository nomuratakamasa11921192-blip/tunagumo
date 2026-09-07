"""構造化ログ(8-5)。

- 1行1JSONで出力する(集計・検索しやすくするため)
- tenant_id/session_idは分かっている場合だけ自動で付与する(contextvarsで受け渡す)
- プロンプトの内容・生成された文書の本文は絶対にログに出さない
  (顧客の依頼内容や成果物はログ基盤に流出させてはいけない個人情報・営業秘密になり得るため)。
  ログを追加するときは、経過や件数やIDだけを書き、goal/instruction/board/system_promptの
  中身を渡さないこと。
"""

import json
import logging
from contextvars import ContextVar

_tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


class RequestContext:
    """`with RequestContext(tenant_id=..., session_id=...):` の間、そのスレッド/タスク内の
    全ログ行にtenant_id/session_idが自動で付与される。"""

    def __init__(self, *, tenant_id: str | None = None, session_id: str | None = None):
        self._tenant_id = tenant_id
        self._session_id = session_id
        self._tenant_token = None
        self._session_token = None

    def __enter__(self) -> "RequestContext":
        self._tenant_token = _tenant_id_var.set(self._tenant_id)
        self._session_token = _session_id_var.set(self._session_id)
        return self

    def __exit__(self, *exc_info) -> None:
        _tenant_id_var.reset(self._tenant_token)
        _session_id_var.reset(self._session_token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.tenant_id = _tenant_id_var.get()
        record.session_id = _session_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        tenant_id = getattr(record, "tenant_id", None)
        session_id = getattr(record, "session_id", None)
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if session_id:
            payload["session_id"] = session_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """アプリ起動時に1回だけ呼ぶ(main.pyのモジュール読み込み時)。"""
    root = logging.getLogger()
    root.setLevel(level)

    # uvicornが自前でハンドラを追加している場合があるので、重複しないよう一度クリアする
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
