import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Coroutine

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from src.agent.config_models import AppConfig
from src.agent.graph import build_graph
from src.agent.llm import StructuredLLM
from src.agent.state import new_state
from src.core.ai_budget import record_cost
from src.core.db import async_session_factory
from src.core.logging_config import RequestContext
from src.core.models import Approval
from src.core.models import Session as SessionModel
from src.core.models import Tenant
from src.rag.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# asyncio.create_task()が返すTaskへの参照をどこにも保持しないと、
# ガーベジコレクションで実行中のタスクが消える場合がある(asyncio公式ドキュメントに
# 明記されている既知の落とし穴)。モジュールレベルのsetで強参照を保持し、
# 完了時にdiscardする。
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro: Coroutine, *, tenant_id: str, session_id: str) -> None:
    """グラフ実行をAPIレスポンスと切り離して裏で走らせる(3秒ルール)。

    FastAPIのBackgroundTasksは、ASGIアプリのレスポンス送出そのものが完了するまで
    バックグラウンドタスクの終了を待つため(テスト用のASGITransportでは特に顕著)、
    「即座に返す」ことを検証するテストと矛盾する。asyncio.create_taskで
    イベントループに乗せるだけにする。

    例外はログに残すだけでなく、セッションをERROR状態にして画面に伝える
    (握りつぶしてセッションが無言のまま止まって見えることを防ぐ)。
    """

    async def _run() -> None:
        with RequestContext(tenant_id=tenant_id, session_id=session_id):
            try:
                logger.info("グラフ実行を開始します")
                await coro
                logger.info("グラフ実行が完了しました")
            except Exception as e:
                logger.exception("バックグラウンドのグラフ実行に失敗しました")
                try:
                    async with async_session_factory() as db:
                        row = await db.get(SessionModel, uuid.UUID(session_id))
                        if row is not None and str(row.tenant_id) == tenant_id:
                            row.status = "ERROR"
                            row.result = {"error_message": f"内部エラーが発生しました: {e}"}
                            await db.commit()
                except Exception:
                    logger.exception("エラー状態の記録にも失敗しました")

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _thread_id(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:{session_id}"


def _result_for_status(status: str, org_state: dict) -> dict:
    if status == "COMPLETED":
        return {"reply": org_state.get("approval_summary", {}).get("reply", "")}
    if status == "CLARIFYING":
        return {"questions": org_state.get("pending_questions", [])}
    if status in ("AWAITING_APPROVAL", "APPROVED"):
        # 承認画面では要約だけでなく、実際に生成された成果物本文も見えないと
        # 顧客が中身を確認しようがない。boardをそのまま含める。
        return {
            "approval_summary": org_state.get("approval_summary", {}),
            "board": org_state.get("board", {}),
        }
    if status in ("ERROR", "HALTED"):
        return {
            "error_message": org_state.get("error_message", ""),
            "rejection_history": org_state.get("rejection_history", []),
            "board": org_state.get("board", {}),
        }
    return {}


async def _persist(
    tenant_id: str, session_id: str, status: str, org_state: dict, *, app_config: AppConfig
) -> None:
    async with async_session_factory() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        if row is None or str(row.tenant_id) != tenant_id:
            return
        previous_cost_usd = row.cost_usd or 0.0
        new_cost_usd = org_state.get("cost_usd", 0.0) or 0.0

        row.status = status
        row.result = _result_for_status(status, org_state)
        row.cost_usd = new_cost_usd

        # 2026-09-01: BYOK廃止に伴い、運営負担の月間予算からこの回ぶんの増分だけを
        # 消費する(cost_usdはセッション開始からの累計なので、差分だけ加算する。
        # _persistは同じセッションに対して複数回呼ばれうる=CLARIFYING→AWAITING_APPROVAL→
        # COMPLETED等、そのたびに累計を丸ごと加算すると二重計上になるため)。
        cost_delta = new_cost_usd - previous_cost_usd
        if cost_delta > 0:
            tenant_row = await db.get(Tenant, uuid.UUID(tenant_id))
            if tenant_row is not None:
                record_cost(tenant_row, cost_delta)

        if status == "AWAITING_APPROVAL":
            # 6-1: 承認要求のレコードを作成する(承認/却下の対象、期限管理の対象になる)
            deadline = datetime.utcnow() + timedelta(hours=app_config.limits.approval_deadline_hours)
            db.add(
                Approval(
                    tenant_id=uuid.UUID(tenant_id),
                    session_id=uuid.UUID(session_id),
                    payload=org_state.get("approval_summary", {}),
                    status="PENDING",
                    deadline_at=deadline,
                )
            )

        await db.commit()


async def run_new_session(
    *,
    tenant_id: str,
    session_id: str,
    requester_id: str,
    raw_message: str,
    app_config: AppConfig,
    llm: StructuredLLM,
    checkpointer: AsyncPostgresSaver,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """新規セッションのグラフ実行をバックグラウンドで開始する。

    POST /api/sessions が202を返した直後にこれをBackgroundTasksから呼ぶ想定
    (3秒ルール: APIは待たせない)。
    """
    graph = build_graph(app_config, llm=llm, checkpointer=checkpointer, embedding_provider=embedding_provider)
    cfg = {"configurable": {"thread_id": _thread_id(tenant_id, session_id)}}
    state = new_state(tenant_id, session_id, requester_id, raw_message)

    result = await graph.ainvoke(state, config=cfg)
    await _persist(tenant_id, session_id, result["status"], result, app_config=app_config)


async def submit_clarify_answer(
    *,
    tenant_id: str,
    session_id: str,
    answer: dict | str,
    app_config: AppConfig,
    llm: StructuredLLM,
    checkpointer: AsyncPostgresSaver,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """要件確認(clarify)への回答を受け取り、停止していたグラフを再開する。"""
    graph = build_graph(app_config, llm=llm, checkpointer=checkpointer, embedding_provider=embedding_provider)
    cfg = {"configurable": {"thread_id": _thread_id(tenant_id, session_id)}}

    result = await graph.ainvoke(Command(resume=answer), config=cfg)
    await _persist(tenant_id, session_id, result["status"], result, app_config=app_config)


async def submit_approval_decision(
    *,
    tenant_id: str,
    session_id: str,
    decision: str,
    comment: str | None,
    app_config: AppConfig,
    llm: StructuredLLM,
    checkpointer: AsyncPostgresSaver,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """承認・却下の決定を受け取り、停止していたグラフを再開する(6-2)。

    承認/却下そのものの記録(approvals・audit_logsテーブル)はAPIルート側で
    レスポンスを返す前に確定させる(二重押下防止のため)。ここでは単にグラフを進める。
    """
    graph = build_graph(app_config, llm=llm, checkpointer=checkpointer, embedding_provider=embedding_provider)
    cfg = {"configurable": {"thread_id": _thread_id(tenant_id, session_id)}}

    result = await graph.ainvoke(
        Command(resume={"decision": decision, "comment": comment or ""}), config=cfg
    )
    await _persist(tenant_id, session_id, result["status"], result, app_config=app_config)
