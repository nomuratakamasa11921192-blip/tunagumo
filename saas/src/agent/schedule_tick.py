"""Phase 14: スケジュール起動。骨格はdocs/reference_impl.md「4. スケジュール起動」に従う
(無人で課金が発生する機能なので、安全弁を最初から入れる)。

- 溜まった分をまとめて実行しない(直近1回だけ。プロセス停止中に過ぎた時刻の分を
  全部流すと、起動時に大量課金される)
- 二重起動をDBで防ぐ((schedule_id, scheduled_for)の複合主キー)
- 3回連続失敗で自動的に無効化する(壊れたまま無人で課金され続けるのを防ぐ)
- スケジュールごとの独立した予算上限(Schedule.max_budget_usd)。共有のAppConfigは
  書き換えず、このセッション専用のコピーを作って上書きする(他テナントに影響しない)
- スケジュール実行でも承認は必須。「無人で走って無人で外に出る」経路は作らない
  (run_new_sessionが既存のグラフを通す時点で、承認待ちを経由することが保証されている)

このtick()はスケジュールを1件ずつ順番に処理する(同時実行しない)。そのため仕様書14-3の
「同時実行数の上限」は、この逐次処理という設計そのものによって不要になっている
(そもそも重ならない)。

仕様書14-3の「月間の総実行回数上限」は、正常に動き続けるスケジュールが際限なく積み重なる
ケースへの安全弁として実装する(Schedule.max_monthly_runs)。具体的な閾値は運用実績が無い
段階では推測にしかならないため、既定値(DEFAULT_MAX_MONTHLY_RUNS)は「毎日1回」を大きく
上回る、明らかな異常系(cron式の誤りで想定より高頻度に発火する等)だけを止める保守的な
値にしてある。月の区切りはUTCの暦月で判定する(課金の精算単位ではなく異常検知の目安の
ため、JSTとのずれは許容する)。
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.agent.config_models import AppConfig
from src.agent.llm import StructuredLLM
from src.agent.runner import run_new_session
from src.core.config import settings
from src.core.db import async_session_factory
from src.core.email import send_email
from src.core.logging_config import RequestContext
from src.core.models import Schedule, ScheduleRun
from src.core.models import Session as SessionModel
from src.core.models import Tenant

logger = logging.getLogger(__name__)

MAX_DELAY_HOURS = 2  # これより遅れて気づいた分は実行しない(大量課金防止)
MAX_CONSECUTIVE_FAILURES = 3
# 14-3: 月間の総実行回数上限の既定値。「毎日1回」(月31回)を明確に上回る、cron式の誤り等の
# 異常系だけを止める保守的な値。頻度がこれを超える正当な用途は作成時にmax_monthly_runsを
# 明示的に指定すること。
DEFAULT_MAX_MONTHLY_RUNS = 60


async def _count_runs_this_month(schedule_id: uuid.UUID, now: datetime) -> int:
    """今月(UTC暦月)に成功した実行回数。異常検知の目安であり課金精算の単位ではないため、
    テナントのtimezoneではなくUTCの月境界で判定する(schedule_tick.pyのモジュールdocstring参照)。"""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with async_session_factory() as db:
        stmt = select(func.count()).where(
            ScheduleRun.schedule_id == schedule_id,
            ScheduleRun.status == "SUCCEEDED",
            ScheduleRun.scheduled_for >= month_start,
        )
        return (await db.execute(stmt)).scalar_one()


def _last_due_time(cron_expr: str, tz_name: str, now: datetime) -> datetime | None:
    """cron式について、nowより前で直近の発火予定時刻を返す(UTC naive datetimeで返す)。
    不正なcron式ならNoneを返す(呼び出し元はスキップする)。
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None

    local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if now.tzinfo is None else now.astimezone(tz)
    try:
        itr = croniter(cron_expr, local_now)
        prev_local = itr.get_prev(datetime)
    except (ValueError, KeyError):
        return None

    return prev_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


async def _claim_run(schedule_id: uuid.UUID, tenant_id: uuid.UUID, scheduled_for: datetime) -> bool:
    async with async_session_factory() as db:
        stmt = (
            pg_insert(ScheduleRun)
            .values(
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
                tenant_id=tenant_id,
                status="CLAIMED",
            )
            .on_conflict_do_nothing(index_elements=["schedule_id", "scheduled_for"])
            .returning(ScheduleRun.schedule_id)
        )
        claimed = (await db.execute(stmt)).scalar_one_or_none()
        await db.commit()
    return claimed is not None


async def _mark_run(schedule_id: uuid.UUID, scheduled_for: datetime, status: str, session_id: uuid.UUID | None) -> None:
    async with async_session_factory() as db:
        row = await db.get(ScheduleRun, {"schedule_id": schedule_id, "scheduled_for": scheduled_for})
        if row is not None:
            row.status = status
            row.session_id = session_id
            await db.commit()


async def _record_failure(schedule_id: uuid.UUID) -> int:
    async with async_session_factory() as db:
        row = await db.get(Schedule, schedule_id)
        if row is None:
            return 0
        row.consecutive_failures += 1
        n = row.consecutive_failures
        if n >= MAX_CONSECUTIVE_FAILURES:
            row.enabled = False
        await db.commit()
        return n


async def _reset_failures(schedule_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        row = await db.get(Schedule, schedule_id)
        if row is not None and row.consecutive_failures != 0:
            row.consecutive_failures = 0
            await db.commit()


async def _notify_admin_disabled(schedule: Schedule) -> None:
    if not settings.admin_notification_email:
        return
    await send_email(
        to=settings.admin_notification_email,
        subject="【ツナグモ】スケジュールを自動停止しました",
        html=(
            f"<p>スケジュール(id: {schedule.id}, tenant_id: {schedule.tenant_id})が"
            f"{MAX_CONSECUTIVE_FAILURES}回連続で失敗したため、自動的に停止しました。</p>"
            "<p>原因を確認し、必要なら管理画面から再度有効化してください。</p>"
        ),
    )


async def _notify_admin_monthly_cap(schedule: Schedule, limit: int) -> None:
    if not settings.admin_notification_email:
        return
    await send_email(
        to=settings.admin_notification_email,
        subject="【ツナグモ】スケジュールが月間の実行回数上限に達しました",
        html=(
            f"<p>スケジュール(id: {schedule.id}, tenant_id: {schedule.tenant_id})が、"
            f"今月の実行回数上限({limit}回)に達したため、今月分はこれ以上起動しません。</p>"
            "<p>意図した頻度であれば、管理画面からmax_monthly_runsを引き上げてください。"
            "意図しない頻度で発火している場合は、cron式に誤りがないか確認してください。</p>"
        ),
    )


async def tick(
    *,
    now: datetime,
    app_configs: dict[str, AppConfig],
    default_industry: str,
    llm_factory: Callable[[], StructuredLLM],
    checkpointer,
) -> dict:
    started = 0
    skipped_late = 0
    skipped_disabled = 0
    skipped_monthly_cap = 0

    async with async_session_factory() as db:
        schedules = list((await db.execute(select(Schedule).where(Schedule.enabled.is_(True)))).scalars().all())
        tenants = await db.execute(select(Tenant.id, Tenant.industry))
        tenant_info = {row[0]: row[1] for row in tenants.all()}

    for sch in schedules:
        due = _last_due_time(sch.cron, sch.timezone, now)
        if due is None:
            logger.error("不正なcron式またはtimezoneのためスキップします: schedule_id=%s", sch.id)
            continue

        limit = sch.max_monthly_runs if sch.max_monthly_runs is not None else DEFAULT_MAX_MONTHLY_RUNS
        month_count = await _count_runs_this_month(sch.id, now)
        if month_count >= limit:
            # (schedule_id, due)を「上限到達によりスキップ済み」として即座に確保する。
            # これにより、次のcron発火時刻(due)が来るまでは同じ回に対して毎分再通知しない
            # (claimできなければ、このdueは前回のtickで既に処理済みという意味)
            if not await _claim_run(sch.id, sch.tenant_id, due):
                continue
            await _mark_run(sch.id, due, "SKIPPED_CAP", None)
            skipped_monthly_cap += 1
            await _notify_admin_monthly_cap(sch, limit)
            continue

        if now - due > timedelta(hours=MAX_DELAY_HOURS):
            skipped_late += 1
            continue

        if not await _claim_run(sch.id, sch.tenant_id, due):
            continue  # 既に他のプロセス/前回のtickが処理済み

        with RequestContext(tenant_id=str(sch.tenant_id)):
            industry = tenant_info.get(sch.tenant_id, default_industry)
            app_config = app_configs.get(industry, app_configs[default_industry])
            llm = llm_factory()
            session_id = uuid.uuid4()

            # 14-3: スケジュールごとの独立した予算上限。共有のapp_configを直接書き換えず
            # (他テナント・他セッションに影響するため)、このセッション専用のコピーを作る
            run_config = app_config
            if sch.max_budget_usd is not None:
                run_config = app_config.model_copy(
                    update={"limits": app_config.limits.model_copy(update={"max_budget_usd": sch.max_budget_usd})}
                )

            try:
                async with async_session_factory() as db:
                    db.add(
                        SessionModel(
                            id=session_id,
                            tenant_id=sch.tenant_id,
                            request_text=sch.goal,
                            status="QUEUED",
                        )
                    )
                    await db.commit()

                # スケジュール実行でも承認は必須(run_new_sessionは既存のグラフを通すため、
                # 必ずAWAITING_APPROVALを経由する。「無人で外に出る」経路は作らない)
                await run_new_session(
                    tenant_id=str(sch.tenant_id),
                    session_id=str(session_id),
                    requester_id=str(sch.tenant_id),
                    raw_message=sch.goal,
                    app_config=run_config,
                    llm=llm,
                    checkpointer=checkpointer,
                )
                await _mark_run(sch.id, due, "SUCCEEDED", session_id)
                await _reset_failures(sch.id)
                started += 1
            except Exception:
                logger.exception("スケジュール実行に失敗しました: schedule_id=%s", sch.id)
                await _mark_run(sch.id, due, "FAILED", session_id)
                n = await _record_failure(sch.id)
                if n >= MAX_CONSECUTIVE_FAILURES:
                    async with async_session_factory() as db:
                        row = await db.get(Schedule, sch.id)
                    if row is not None:
                        await _notify_admin_disabled(row)

    return {
        "started": started,
        "skipped_late": skipped_late,
        "skipped_disabled": skipped_disabled,
        "skipped_monthly_cap": skipped_monthly_cap,
    }
