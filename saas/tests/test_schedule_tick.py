"""Phase 14: スケジュール起動tickの検証。reference_impl.md「4. スケジュール起動」の
骨格通り、二重起動防止・大幅遅延のスキップ・3回連続失敗での自動無効化を確認する。
"""

import uuid
from datetime import datetime, timedelta

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete, select

from src.agent.schedule_tick import tick
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.api.deps import DEFAULT_INDUSTRY, INDUSTRIES
from src.core.db import async_session_factory
from src.core.models import Approval, Schedule
from src.core.models import Session as SessionModel
from src.core.models import ScheduleRun
from tests.conftest import config, tenant  # noqa: F401  (fixtures)
from tests.fakes import FakeLLM

asyncio_test = pytest.mark.asyncio(loop_scope="session")


async def _make_schedule(
    tenant_id: uuid.UUID,
    *,
    cron: str = "* * * * *",
    goal: str = "定期実行のテスト",
    max_budget_usd: float | None = None,
    max_monthly_runs: int | None = None,
) -> Schedule:
    async with async_session_factory() as db:
        row = Schedule(
            tenant_id=tenant_id,
            cron=cron,
            timezone="Asia/Tokyo",
            goal=goal,
            max_budget_usd=max_budget_usd,
            max_monthly_runs=max_monthly_runs,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _seed_succeeded_runs(schedule_id: uuid.UUID, tenant_id: uuid.UUID, count: int, around: datetime) -> None:
    """今月(aroundが属する暦月)の「成功した実行」をcount件だけ作る(月間上限テスト用のダミー実績)。
    月初からの経過時間で置くため、月をまたいで前月分としてカウントされる心配がない。"""
    month_start = around.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with async_session_factory() as db:
        for i in range(count):
            db.add(
                ScheduleRun(
                    schedule_id=schedule_id,
                    scheduled_for=month_start + timedelta(minutes=i),
                    tenant_id=tenant_id,
                    status="SUCCEEDED",
                )
            )
        await db.commit()


async def _cleanup(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        schedule_ids = (await db.execute(select(Schedule.id).where(Schedule.tenant_id == tenant_id))).scalars().all()
        if schedule_ids:
            await db.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id.in_(schedule_ids)))
        await db.execute(delete(Schedule).where(Schedule.tenant_id == tenant_id))
        # ApprovalはSession.idへの外部キーを持つため、Sessionより先に消す必要がある
        await db.execute(delete(Approval).where(Approval.tenant_id == tenant_id))
        await db.execute(delete(SessionModel).where(SessionModel.tenant_id == tenant_id))
        await db.commit()


def _queue_greeting(llm: FakeLLM) -> None:
    # ceo_officeの分類だけで完了する経路(最短でグラフを終わらせる)
    llm.queue_structured(Triage(intent="GREETING", reply="定期実行を受け付けました", reason="テスト用"))


def _queue_full_approval_flow(llm: FakeLLM) -> None:
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="週次レポート作成", reason="業務依頼"))
    llm.queue_structured(
        SupervisorDecision(verdict="ASSIGN", target_depts=["copy_dept"], instruction="x", reason="x")
    )
    llm.queue_text("## 週次レポート")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="完了", qa_result="合格"))


@asyncio_test
async def test_due_schedule_starts_a_session_and_reaches_approval(config, tenant):
    from src.agent.schedule_tick import _last_due_time

    schedule = await _make_schedule(tenant["id"])
    llm = FakeLLM()
    _queue_full_approval_flow(llm)
    app_configs = {industry: config for industry in INDUSTRIES}
    now = datetime.utcnow()

    try:
        result = await tick(
            now=now,
            app_configs=app_configs,
            default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm,
            checkpointer=MemorySaver(),
        )

        assert result["started"] == 1

        due = _last_due_time(schedule.cron, schedule.timezone, now)
        async with async_session_factory() as db:
            run = await db.get(ScheduleRun, {"schedule_id": schedule.id, "scheduled_for": due})
            assert run.status == "SUCCEEDED"
            assert run.session_id is not None

            session = await db.get(SessionModel, run.session_id)
            assert session.status == "AWAITING_APPROVAL"
            # 無人で外に出ていない(承認待ちで止まっている)ことの確認
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_second_tick_for_same_due_time_does_not_start_again(config, tenant):
    await _make_schedule(tenant["id"])
    llm = FakeLLM()
    _queue_greeting(llm)
    app_configs = {industry: config for industry in INDUSTRIES}
    now = datetime.utcnow()

    try:
        result1 = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result1["started"] == 1

        # 同じnow(=同じ発火時刻)で再度tickしても、二重起動しない
        result2 = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result2["started"] == 0
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_disabled_schedule_is_never_ticked(config, tenant):
    schedule = await _make_schedule(tenant["id"])
    async with async_session_factory() as db:
        row = await db.get(Schedule, schedule.id)
        row.enabled = False
        await db.commit()

    llm = FakeLLM()  # 何もqueueしない: 呼ばれたら即エラーになるので、呼ばれていないことの証明になる
    app_configs = {industry: config for industry in INDUSTRIES}

    try:
        result = await tick(
            now=datetime.utcnow(), app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result["started"] == 0
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_far_overdue_schedule_is_skipped_not_run(config, tenant):
    # 毎日0時起動のスケジュールを、今日の正午にtickする -> 遅延12時間 > 上限2時間 -> スキップ
    schedule = await _make_schedule(tenant["id"], cron="0 0 * * *")
    llm = FakeLLM()  # 呼ばれたらエラーになる(queue未設定)。呼ばれないことの証明。
    app_configs = {industry: config for industry in INDUSTRIES}
    noon_today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)

    try:
        result = await tick(
            now=noon_today, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result["started"] == 0
        assert result["skipped_late"] == 1
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_three_consecutive_failures_auto_disables_schedule(config, tenant):
    schedule = await _make_schedule(tenant["id"])
    llm = FakeLLM()  # queueを空のままにして、呼ばれるたびに失敗させる
    app_configs = {industry: config for industry in INDUSTRIES}
    base = datetime.utcnow().replace(second=0, microsecond=0)

    try:
        for i in range(3):
            await tick(
                now=base + timedelta(minutes=i),
                app_configs=app_configs,
                default_industry=DEFAULT_INDUSTRY,
                llm_factory=lambda: llm,
                checkpointer=MemorySaver(),
            )

        async with async_session_factory() as db:
            row = await db.get(Schedule, schedule.id)
            assert row.consecutive_failures == 3
            assert row.enabled is False
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_success_resets_failure_count(config, tenant):
    schedule = await _make_schedule(tenant["id"])
    app_configs = {industry: config for industry in INDUSTRIES}
    base = datetime.utcnow().replace(second=0, microsecond=0)

    try:
        # 1回失敗させる
        failing_llm = FakeLLM()
        await tick(
            now=base, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: failing_llm, checkpointer=MemorySaver(),
        )
        async with async_session_factory() as db:
            row = await db.get(Schedule, schedule.id)
            assert row.consecutive_failures == 1

        # 次の分は成功させる
        ok_llm = FakeLLM()
        _queue_greeting(ok_llm)
        await tick(
            now=base + timedelta(minutes=1), app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: ok_llm, checkpointer=MemorySaver(),
        )
        async with async_session_factory() as db:
            row = await db.get(Schedule, schedule.id)
            assert row.consecutive_failures == 0
            assert row.enabled is True
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_per_schedule_budget_override_halts_before_any_llm_call(config, tenant):
    """14-3: スケジュールごとの独立した予算上限。max_budget_usd=0.0を指定すると、
    最初のノード呼び出し前の安全弁チェックで即座にHALTEDになり、LLMは一切呼ばれない
    (FakeLLMに何もqueueしなくても失敗しないことで、実際に呼ばれていないことを証明する)。
    共有のapp_configがこの上書きで汚染されていないことも確認する。
    """
    schedule = await _make_schedule(tenant["id"], max_budget_usd=0.0)
    llm = FakeLLM()  # 何もqueueしない
    app_configs = {industry: config for industry in INDUSTRIES}
    original_budget = config.limits.max_budget_usd

    try:
        result = await tick(
            now=datetime.utcnow(), app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )

        assert result["started"] == 1  # HALTEDも「起動した」扱い(実行自体は成功している)

        async with async_session_factory() as db:
            runs = (
                await db.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id))
            ).scalars().all()
            session_id = runs[0].session_id
            session = await db.get(SessionModel, session_id)
            assert session.status == "HALTED"
            assert "予算上限" in session.result["error_message"]

        # 共有のAppConfigインスタンス自体は書き換えられていないこと(他テナントへの影響なし)
        assert config.limits.max_budget_usd == original_budget
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_monthly_run_cap_blocks_further_runs_without_calling_llm(config, tenant):
    """14-3: 今月すでにmax_monthly_runs回成功しているスケジュールは、それ以上起動しない。
    LLMを一切呼ばずにスキップされることを、FakeLLMに何もqueueしないことで証明する。"""
    schedule = await _make_schedule(tenant["id"], max_monthly_runs=2)
    now = datetime.utcnow()
    await _seed_succeeded_runs(schedule.id, tenant["id"], count=2, around=now)

    llm = FakeLLM()  # 何もqueueしない: 呼ばれたら即エラーになる
    app_configs = {industry: config for industry in INDUSTRIES}

    try:
        result = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result["started"] == 0
        assert result["skipped_monthly_cap"] == 1
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_monthly_run_cap_does_not_reblock_same_due_slot_twice(config, tenant):
    """同じ発火時刻(due)に対しては、上限到達の記録は1回だけ確保される
    (毎分のtickのたびに何度も同じ理由でスキップ処理が走らないこと)。"""
    from src.agent.schedule_tick import _last_due_time

    schedule = await _make_schedule(tenant["id"], max_monthly_runs=1)
    now = datetime.utcnow()
    await _seed_succeeded_runs(schedule.id, tenant["id"], count=1, around=now)

    llm = FakeLLM()
    app_configs = {industry: config for industry in INDUSTRIES}

    try:
        result1 = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result1["skipped_monthly_cap"] == 1

        # 同じnow(=同じdue)で再度tickしても、二重にSKIPPED_CAP行を作ろうとしない
        result2 = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result2["skipped_monthly_cap"] == 0

        due = _last_due_time(schedule.cron, schedule.timezone, now)
        async with async_session_factory() as db:
            run = await db.get(ScheduleRun, {"schedule_id": schedule.id, "scheduled_for": due})
            assert run.status == "SKIPPED_CAP"
    finally:
        await _cleanup(tenant["id"])


@asyncio_test
async def test_default_monthly_cap_applies_when_unset(config, tenant):
    """max_monthly_runsを指定しなかった場合、DEFAULT_MAX_MONTHLY_RUNSが適用される。"""
    from src.agent import schedule_tick as schedule_tick_module

    schedule = await _make_schedule(tenant["id"])  # max_monthly_runs未指定
    now = datetime.utcnow()
    # 既定値ちょうどの数だけ実績を作る
    await _seed_succeeded_runs(schedule.id, tenant["id"], count=schedule_tick_module.DEFAULT_MAX_MONTHLY_RUNS, around=now)

    llm = FakeLLM()  # 何もqueueしない
    app_configs = {industry: config for industry in INDUSTRIES}

    try:
        result = await tick(
            now=now, app_configs=app_configs, default_industry=DEFAULT_INDUSTRY,
            llm_factory=lambda: llm, checkpointer=MemorySaver(),
        )
        assert result["started"] == 0
        assert result["skipped_monthly_cap"] == 1
    finally:
        await _cleanup(tenant["id"])
