"""旧仕様のプラン別動画本数制限(将来用として保持、現在のAPIからは未使用)。

現行の文章・画像・音声等は運営負担で、ai_budget.pyの月間予算で管理する。
Higgsfieldによる動画生成は顧客自身の契約・APIキーで費用を負担する。
このモジュールの20本/60本という上限は有効化されていない。業務仕様の決定前に
APIへ接続したり、顧客向けの現行制限として表示したりしないこと。
"""

from datetime import datetime, timezone

# プランID: 月あたりの動画生成上限本数(None = 無制限)。
# 各プランの月額・本数は事業判断の対象なので、変更する場合はここだけを直す。
VIDEO_GENERATION_LIMITS: dict[str, int | None] = {
    "light": 20,
    "standard": 60,
    "unlimited": None,
}
DEFAULT_PLAN = "light"


class VideoQuotaExceededError(Exception):
    def __init__(self, plan: str, limit: int):
        self.plan = plan
        self.limit = limit
        super().__init__(
            f"今月の動画生成本数の上限({limit}本、{plan}プラン)に達しました。"
            "プランのアップグレードをご検討いただくか、運営にお問い合わせください。"
        )


def _current_period_key(dt: datetime) -> tuple[int, int]:
    return (dt.year, dt.month)


def _reset_period_if_needed(tenant, now: datetime) -> None:
    """月が変わっていれば(video_period_started_atと現在で年月が違えば)カウントを
    0にリセットする(月次バッチ不要の「参照時リセット」方式)。"""
    period_started_at = tenant.video_period_started_at
    if period_started_at.tzinfo is None:
        period_started_at = period_started_at.replace(tzinfo=timezone.utc)
    if _current_period_key(period_started_at) != _current_period_key(now):
        tenant.video_generations_this_period = 0
        tenant.video_period_started_at = now


def ensure_video_quota_available(tenant, *, now: datetime | None = None) -> None:
    """tenantの今月の動画生成が上限に達していないかチェックする(カウントは変更しない)。
    上限超過ならVideoQuotaExceededErrorを送出する。実際の生成呼び出しの「前」に呼ぶ。
    """
    now = now or datetime.now(timezone.utc)
    _reset_period_if_needed(tenant, now)
    limit = VIDEO_GENERATION_LIMITS.get(tenant.plan, VIDEO_GENERATION_LIMITS[DEFAULT_PLAN])
    if limit is not None and tenant.video_generations_this_period >= limit:
        raise VideoQuotaExceededError(tenant.plan, limit)


def effective_video_count(tenant, *, now: datetime | None = None) -> int:
    """表示用: 月が変わっていれば0扱いで今月の動画生成本数を返す(tenantは変更しない)。"""
    now = now or datetime.now(timezone.utc)
    period_started_at = tenant.video_period_started_at
    if period_started_at.tzinfo is None:
        period_started_at = period_started_at.replace(tzinfo=timezone.utc)
    if _current_period_key(period_started_at) != _current_period_key(now):
        return 0
    return tenant.video_generations_this_period


def consume_video_quota(tenant, *, now: datetime | None = None) -> None:
    """実際に動画を1本生成できた後に呼び、カウントを1つ進める(失敗した生成は消費しない)。"""
    now = now or datetime.now(timezone.utc)
    _reset_period_if_needed(tenant, now)
    tenant.video_generations_this_period += 1
