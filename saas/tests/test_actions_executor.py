"""Phase 12: 冪等な外部アクション実行の検証。reference_impl.md「2.」の骨格通り、
CLAIMED記録→実行→state更新の順序と、二重実行防止・緊急停止フラグ・失敗時の
非リトライを確認する。"""

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import delete, select

from src.actions.executor import ActionPlan, UnknownActionTypeError, execute_action
from src.core.db import async_session_factory
from src.core.models import AuditLog, ExecutedAction
from src.core.models import Session as SessionModel
from src.core.runtime_flags import set_actions_enabled
from src.tools.registry import ToolSpec, register, reset_registry_for_tests
from tests.conftest import tenant  # noqa: F401  (fixture)

asyncio_test = pytest.mark.asyncio(loop_scope="session")


async def _make_session(tenant_id: uuid.UUID) -> uuid.UUID:
    """AuditLog.session_idはsessions.idへの実FKなので、監査ログを書くテストでは
    実在するSession行を用意する。"""
    async with async_session_factory() as db:
        row = SessionModel(tenant_id=tenant_id, request_text="アクション実行テスト用", status="APPROVED")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


class SendEmailArgs(BaseModel):
    to: str
    body: str


_send_calls: list[dict] = []


async def _handle_send_email(args: SendEmailArgs) -> str:
    _send_calls.append({"to": args.to, "body": args.body})
    return "送信しました"


async def _handle_send_email_fails(args: SendEmailArgs) -> str:
    raise RuntimeError("SMTP接続エラー(テスト用)")


def _register_send_email_tool(handler=_handle_send_email) -> None:
    register(
        ToolSpec(
            name="send_email",
            kind="IRREVERSIBLE",
            description="メールを送信する",
            args_schema=SendEmailArgs,
            handler=handler,
        )
    )


def _register_read_tool() -> None:
    class ReadArgs(BaseModel):
        x: str

    async def handle_read(args: ReadArgs) -> str:
        return "ok"

    register(ToolSpec(name="just_read", kind="READ", description="test", args_schema=ReadArgs, handler=handle_read))


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry_for_tests()
    _send_calls.clear()
    yield
    reset_registry_for_tests()


@pytest.fixture(autouse=True)
async def _reset_actions_enabled():
    await set_actions_enabled(False)
    yield
    await set_actions_enabled(False)


async def _cleanup_executed_actions(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(ExecutedAction).where(ExecutedAction.tenant_id == tenant_id))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await db.execute(delete(SessionModel).where(SessionModel.tenant_id == tenant_id))
        await db.commit()


@asyncio_test
async def test_non_irreversible_tool_is_rejected(tenant):
    _register_read_tool()
    plan = ActionPlan(idempotency_key=str(uuid.uuid4()), action_type="just_read", params={"x": "y"})

    with pytest.raises(UnknownActionTypeError):
        await execute_action(plan, tenant_id=tenant["id"], session_id=uuid.uuid4(), approval_id=None)


@asyncio_test
async def test_dry_run_does_not_call_handler_and_marks_succeeded(tenant):
    _register_send_email_tool()
    session_id = uuid.uuid4()
    plan = ActionPlan(
        idempotency_key=str(uuid.uuid4()),
        action_type="send_email",
        params={"to": "customer@example.com", "body": "hi"},
        preview="customer@example.com へ「hi」を送信します",
    )

    try:
        result = await execute_action(
            plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None, dry_run=True
        )

        assert result.status == "DRY_RUN"
        assert _send_calls == []  # 実際には送信していない

        async with async_session_factory() as db:
            row = await db.get(ExecutedAction, plan.idempotency_key)
            assert row.state == "SUCCEEDED"
            assert row.result == {"dry_run": True}
    finally:
        await _cleanup_executed_actions(tenant["id"])


@asyncio_test
async def test_execution_blocked_when_actions_disabled(tenant):
    _register_send_email_tool()
    await set_actions_enabled(False)
    session_id = uuid.uuid4()
    plan = ActionPlan(
        idempotency_key=str(uuid.uuid4()),
        action_type="send_email",
        params={"to": "customer@example.com", "body": "hi"},
    )

    try:
        result = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)

        assert result.status == "BLOCKED"
        assert _send_calls == []

        async with async_session_factory() as db:
            row = await db.get(ExecutedAction, plan.idempotency_key)
            assert row.state == "FAILED"
    finally:
        await _cleanup_executed_actions(tenant["id"])


@asyncio_test
async def test_successful_execution_records_succeeded_and_audit_log(tenant):
    _register_send_email_tool()
    await set_actions_enabled(True)
    session_id = await _make_session(tenant["id"])
    plan = ActionPlan(
        idempotency_key=str(uuid.uuid4()),
        action_type="send_email",
        params={"to": "customer@example.com", "body": "承認された内容です"},
    )

    try:
        result = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)

        assert result.status == "OK"
        assert len(_send_calls) == 1
        assert _send_calls[0]["to"] == "customer@example.com"

        async with async_session_factory() as db:
            row = await db.get(ExecutedAction, plan.idempotency_key)
            assert row.state == "SUCCEEDED"

            audit = (
                await db.execute(select(AuditLog).where(AuditLog.session_id == session_id))
            ).scalar_one()
            assert audit.action == "ACTION_EXECUTED"
    finally:
        await _cleanup_executed_actions(tenant["id"])


@asyncio_test
async def test_duplicate_idempotency_key_is_not_executed_twice(tenant):
    _register_send_email_tool()
    await set_actions_enabled(True)
    session_id = await _make_session(tenant["id"])
    plan = ActionPlan(
        idempotency_key=str(uuid.uuid4()),
        action_type="send_email",
        params={"to": "customer@example.com", "body": "重複防止テスト"},
    )

    try:
        result1 = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)
        result2 = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)

        assert result1.status == "OK"
        assert result2.status == "DUPLICATE"
        assert len(_send_calls) == 1  # ハンドラは1回しか呼ばれていない(二重送信していない)
    finally:
        await _cleanup_executed_actions(tenant["id"])


@asyncio_test
async def test_handler_failure_marks_failed_and_does_not_retry(tenant):
    _register_send_email_tool(handler=_handle_send_email_fails)
    await set_actions_enabled(True)
    session_id = await _make_session(tenant["id"])
    plan = ActionPlan(
        idempotency_key=str(uuid.uuid4()),
        action_type="send_email",
        params={"to": "customer@example.com", "body": "失敗するはず"},
    )

    try:
        result = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)
        assert result.status == "FAILED"
        assert "SMTP接続エラー" in result.error

        async with async_session_factory() as db:
            row = await db.get(ExecutedAction, plan.idempotency_key)
            assert row.state == "FAILED"

        # 同じidempotency_keyで再試行しても、二重実行防止のロジックにより
        # 新たに実行されることはない(既にCLAIMED済みのキーなので重複扱いになる)
        result2 = await execute_action(plan, tenant_id=tenant["id"], session_id=session_id, approval_id=None)
        assert result2.status == "DUPLICATE"
    finally:
        await _cleanup_executed_actions(tenant["id"])
