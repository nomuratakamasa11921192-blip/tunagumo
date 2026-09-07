"""Phase 14: 定期実行(スケジュール)のセルフサービスAPI。顧客が自分のテナント分だけ
登録・一覧・有効無効切替・削除できる(src/admin/routes.pyの管理者版は、顧客からキーを
聞き出さずに済ませたい代理サポート用として引き続き残す)。
"""

import uuid

from croniter import CroniterBadCronError, croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.api.deps import get_current_tenant, get_scoped_db
from src.core.models import Schedule, Tenant

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _validate_cron_and_timezone(cron_expr: str, tz_name: str) -> None:
    try:
        croniter(cron_expr)
    except (CroniterBadCronError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"cron式が不正です: {e}") from e
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"未知のタイムゾーンです: {tz_name}") from e


class ScheduleCreateRequest(BaseModel):
    cron: str
    timezone: str = "Asia/Tokyo"
    goal: str


class ScheduleResponse(BaseModel):
    id: uuid.UUID
    cron: str
    timezone: str
    goal: str
    enabled: bool
    consecutive_failures: int


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]


class ScheduleUpdateRequest(BaseModel):
    enabled: bool


def _to_response(s: Schedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=s.id,
        cron=s.cron,
        timezone=s.timezone,
        goal=s.goal,
        enabled=s.enabled,
        consecutive_failures=s.consecutive_failures,
    )


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    req: ScheduleCreateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> ScheduleResponse:
    """顧客自身の定期実行を登録する。予算・月間実行回数の上限は、悪用防止のため
    顧客からは指定させず、schedule_tick.py側の既定値をそのまま使う。"""
    _validate_cron_and_timezone(req.cron, req.timezone)

    schedule = Schedule(tenant_id=tenant.id, cron=req.cron, timezone=req.timezone, goal=req.goal)
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return _to_response(schedule)


@router.get("", response_model=ScheduleListResponse)
async def list_schedules(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> ScheduleListResponse:
    result = await db.execute(select(Schedule).where(Schedule.tenant_id == tenant.id))
    return ScheduleListResponse(schedules=[_to_response(s) for s in result.scalars().all()])


async def _get_own_schedule(db: AsyncSession, tenant: Tenant, schedule_id: uuid.UUID) -> Schedule:
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None or schedule.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="スケジュールが見つかりません")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: uuid.UUID,
    req: ScheduleUpdateRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> ScheduleResponse:
    """有効/無効の切り替え。3回連続失敗で自動的に無効化された後、原因を直してから
    再度有効化する場合もここを使う(再有効化時はconsecutive_failuresを0に戻す)。"""
    schedule = await _get_own_schedule(db, tenant, schedule_id)
    schedule.enabled = req.enabled
    if req.enabled:
        schedule.consecutive_failures = 0
    await db.commit()
    await db.refresh(schedule)
    return _to_response(schedule)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: uuid.UUID,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_scoped_db),
) -> None:
    schedule = await _get_own_schedule(db, tenant, schedule_id)
    await db.delete(schedule)
    await db.commit()
