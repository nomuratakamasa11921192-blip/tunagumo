"""Phase 12: 承認後にのみ実行される、不可逆な外部アクション(メール送信等)の実行機構。
骨格はdocs/reference_impl.md「2. 冪等な外部アクション実行」に従う
(このシステムで最も事故りやすい箇所、と明記されているため)。

**順序が命**: `INSERT(CLAIMED)` → 実行 → `state`更新。
実行してから記録すると、実行直後にプロセスが落ちたとき記録が残らず、
再起動後にもう一度送信される。

ActionPlanの action_type は src/tools/registry.py に IRREVERSIBLE として登録された
ツール名と同じレジストリを指す(READ/REVERSIBLE_WRITEは部署ノードから直接実行できるが、
IRREVERSIBLEはノードからは呼べず、必ずこの executor 経由・承認後にのみ実行される)。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.config import settings
from src.core.db import async_session_factory
from src.core.email import send_email
from src.core.logging_config import RequestContext
from src.core.models import AuditLog, ExecutedAction
from src.core.runtime_flags import actions_enabled
from src.tools.registry import get as get_tool_spec

logger = logging.getLogger(__name__)


class ActionPlan(BaseModel):
    """QAを通過した成果物から組み立てる、実行待ちの不可逆アクション1件分の計画(12-2)。
    承認画面にはpreviewを全文表示する(「送信します」ではなく宛先・件名・本文まで)。
    reversible=Falseのアクション(メール送信・公開投稿等)は、承認画面に警告を明示すること。
    """

    idempotency_key: str
    action_type: str
    params: dict
    preview: str = ""
    reversible: bool = False


class UnknownActionTypeError(Exception):
    pass


@dataclass
class ActionResult:
    status: Literal["OK", "DUPLICATE", "DRY_RUN", "BLOCKED", "FAILED"]
    result: dict | None = None
    error: str | None = None

    @classmethod
    def ok(cls, result: dict) -> "ActionResult":
        return cls(status="OK", result=result)

    @classmethod
    def duplicate(cls) -> "ActionResult":
        return cls(status="DUPLICATE")

    @classmethod
    def dry_run(cls, preview: str) -> "ActionResult":
        return cls(status="DRY_RUN", result={"preview": preview})

    @classmethod
    def blocked(cls) -> "ActionResult":
        return cls(status="BLOCKED")

    @classmethod
    def failed(cls, error: Exception | str) -> "ActionResult":
        return cls(status="FAILED", error=str(error))


async def _notify_admin_of_failure(plan: ActionPlan, error: Exception) -> None:
    if not settings.admin_notification_email:
        return
    await send_email(
        to=settings.admin_notification_email,
        subject="【ツナグモ】外部アクションの実行に失敗しました",
        html=(
            f"<p>action_type: {plan.action_type}</p>"
            f"<p>idempotency_key: {plan.idempotency_key}</p>"
            f"<p>エラー: {error}</p>"
            "<p>送信済みかどうか不明なため、自動リトライはしていません。手動で確認してください。</p>"
        ),
    )


async def execute_action(
    plan: ActionPlan,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    approval_id: uuid.UUID | None,
    dry_run: bool = False,
) -> ActionResult:
    spec = get_tool_spec(plan.action_type)
    if spec.kind != "IRREVERSIBLE":
        raise UnknownActionTypeError(
            f"{plan.action_type} はIRREVERSIBLEとして登録されたツールではありません"
        )

    with RequestContext(tenant_id=str(tenant_id), session_id=str(session_id)):
        # ① 先に「実行する」と記録する(ON CONFLICT DO NOTHINGがUNIQUE制約=idempotency_keyで
        # 二重実行そのものを防ぐ本体)
        async with async_session_factory() as db:
            stmt = (
                pg_insert(ExecutedAction)
                .values(
                    idempotency_key=plan.idempotency_key,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    approval_id=approval_id,
                    action_type=plan.action_type,
                    params=plan.params,
                    state="CLAIMED",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(ExecutedAction.idempotency_key)
            )
            claimed = (await db.execute(stmt)).scalar_one_or_none()
            await db.commit()

        if claimed is None:
            logger.info("重複実行を防ぎました: idempotency_key=%s", plan.idempotency_key)
            return ActionResult.duplicate()

        # ② ドライラン
        if dry_run:
            await _finish(plan.idempotency_key, "SUCCEEDED", {"dry_run": True})
            return ActionResult.dry_run(plan.preview)

        # ③ 緊急停止フラグ(再デプロイなしで止められること)
        if not await actions_enabled():
            await _finish(plan.idempotency_key, "FAILED", {"reason": "actions_disabled"})
            logger.warning(
                "actions_enabledフラグが無効のため実行をブロックしました: action_type=%s", plan.action_type
            )
            return ActionResult.blocked()

        # ④ 実行
        try:
            args = spec.args_schema.model_validate(plan.params)
            output = await spec.handler(args)
            await _finish(plan.idempotency_key, "SUCCEEDED", {"output": output})
            await _audit_log(
                tenant_id=tenant_id,
                session_id=session_id,
                action="ACTION_EXECUTED",
                actor=f"system:{plan.action_type}",
                comment=plan.idempotency_key,
            )
            logger.info(
                "アクションを実行しました: action_type=%s, idempotency_key=%s",
                plan.action_type,
                plan.idempotency_key,
            )
            return ActionResult.ok({"output": output})
        except Exception as e:
            # 送信済みか不明な失敗は絶対にリトライしない(FAILEDのまま固定し、人間が判断する)
            await _finish(plan.idempotency_key, "FAILED", {"error": str(e)})
            await _audit_log(
                tenant_id=tenant_id,
                session_id=session_id,
                action="ACTION_FAILED",
                actor=f"system:{plan.action_type}",
                comment=str(e)[:500],
            )
            logger.exception("アクションの実行に失敗しました: action_type=%s", plan.action_type)
            await _notify_admin_of_failure(plan, e)
            return ActionResult.failed(e)


async def _finish(idempotency_key: str, state: str, result: dict) -> None:
    async with async_session_factory() as db:
        row = await db.get(ExecutedAction, idempotency_key)
        if row is not None:
            row.state = state
            row.result = result
            row.finished_at = datetime.utcnow()
            await db.commit()


async def _audit_log(*, tenant_id: uuid.UUID, session_id: uuid.UUID, action: str, actor: str, comment: str) -> None:
    async with async_session_factory() as db:
        db.add(AuditLog(tenant_id=tenant_id, session_id=session_id, action=action, actor=actor, comment=comment))
        await db.commit()
