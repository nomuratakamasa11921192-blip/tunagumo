"""Stripe Webhook受信(2026-09-01追加)。

サブスクの解約・失効を検知して、該当テナントの`subscription_active`をFalseにする
(get_current_tenantがこれを見てアクセスを拒否する)。トライアル終了後に未払いのまま
使われ続ける、解約後もAPIキーが生きているせいで使い続けられる、といった抜け穴を防ぐ。

Stripe公式SDKは使わず、署名検証(HMAC-SHA256)を自前で行う薄い実装にしている
(higgsfield_client.pyと同じ、依存を増やさない方針)。
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.db import get_db
from src.core.models import Tenant
from src.core.stripe_client import ADDON_CREDIT_USD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe", tags=["stripe-webhook"])

# 署名のタイムスタンプがこれより古い/新しい場合はリプレイ攻撃とみなして拒否する。
SIGNATURE_TOLERANCE_SECONDS = 300

# サブスクをsubscription_active=Falseにする対象のStripeサブスクステータス。
INACTIVE_STATUSES = {"canceled", "unpaid", "incomplete_expired", "paused"}
# アクティブとみなす(Falseから戻す)ステータス。
ACTIVE_STATUSES = {"active", "trialing"}


def _verify_signature(payload: bytes, signature_header: str, secret: str) -> None:
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise HTTPException(status_code=400, detail="Stripe-Signatureヘッダの形式が不正です")

    if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="署名のタイムスタンプが古すぎます")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="署名が一致しません")


async def _add_addon_credit(db: AsyncSession, customer_id: str, credit_usd: float) -> None:
    """追加AI予算チケットの購入完了(Part 4)。addon_credit_usdに加算する
    (月次リセットされない、src/core/ai_budget.py参照)。"""
    result = await db.execute(select(Tenant).where(Tenant.stripe_customer_id == customer_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        logger.info("stripe webhook: no tenant for customer_id=%s (addon credit ignored)", customer_id)
        return
    tenant.addon_credit_usd += credit_usd
    await db.commit()
    logger.info(
        "stripe webhook: tenant_id=%s addon_credit_usd += %.2f (customer_id=%s)",
        tenant.id,
        credit_usd,
        customer_id,
    )


async def _set_subscription_active(db: AsyncSession, customer_id: str, active: bool) -> None:
    result = await db.execute(select(Tenant).where(Tenant.stripe_customer_id == customer_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        # このStripe顧客に紐づくテナントがまだ無い(トライアル申込直後でSaaSテナント未発行、
        # 等)場合は静かに無視する。エラーにはしない(Stripe側がリトライを繰り返すだけになる)。
        logger.info("stripe webhook: no tenant for customer_id=%s (ignored)", customer_id)
        return
    tenant.subscription_active = active
    await db.commit()
    logger.info(
        "stripe webhook: tenant_id=%s subscription_active=%s (customer_id=%s)",
        tenant.id,
        active,
        customer_id,
    )


@router.post("", status_code=200)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=401, detail="Stripe Webhookが設定されていません")

    signature_header = request.headers.get("stripe-signature")
    if not signature_header:
        raise HTTPException(status_code=400, detail="Stripe-Signatureヘッダがありません")

    raw_body = await request.body()
    _verify_signature(raw_body, signature_header, settings.stripe_webhook_secret)

    event = json.loads(raw_body)
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        customer_id = data_object.get("customer")
        # payment_status == "paid"の場合のみ加算する(無料トライアルmodeの
        # checkoutなど、支払いが伴わないセッションでは加算しない)。
        if customer_id and data_object.get("payment_status") == "paid":
            await _add_addon_credit(db, customer_id, ADDON_CREDIT_USD)
    elif event_type == "customer.subscription.deleted":
        customer_id = data_object.get("customer")
        if customer_id:
            await _set_subscription_active(db, customer_id, active=False)
    elif event_type == "customer.subscription.updated":
        customer_id = data_object.get("customer")
        status = data_object.get("status")
        if customer_id and status in INACTIVE_STATUSES:
            await _set_subscription_active(db, customer_id, active=False)
        elif customer_id and status in ACTIVE_STATUSES:
            await _set_subscription_active(db, customer_id, active=True)
    else:
        logger.info("stripe webhook: unhandled event type=%s (ignored)", event_type)

    return {"received": True}
