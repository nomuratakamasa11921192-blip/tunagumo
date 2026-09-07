"""追加AI予算購入(Part 4)用の、Stripe REST APIへの薄いクライアント。

Stripe公式SDKは使わず(src/api/routes/stripe_webhook.pyと同じ、依存を増やさない方針)、
Checkoutセッション作成(一回払いモード)だけをhttpxで直接叩く。
"""

import httpx

STRIPE_API_BASE = "https://api.stripe.com/v1"
FETCH_TIMEOUT_SECONDS = 15.0

# 追加AI予算チケット: 1回の購入で$5ぶんのAI予算をaddon_credit_usdに加算する。
# 月額プランの$/円換算(例: light \39,800/$10 ≒ \3,980/$1)より少し高めの単価にして
# あり、含まれる予算を使い切ってからの単発購入は少し割高になる(一般的なSaaSの
# 従量課金オプションと同じ考え方)。
ADDON_PRICE_JPY = 25000
ADDON_CREDIT_USD = 5.0


class StripeClientError(Exception):
    pass


async def create_addon_checkout_session(
    *, customer_id: str, success_url: str, cancel_url: str, api_key: str
) -> str:
    """一回払いのCheckoutセッションを作り、顧客が支払いを完了するためのURLを返す。"""
    if not api_key:
        raise StripeClientError("Stripeのシークレットキーが設定されていません。")

    body = {
        "mode": "payment",
        "customer": customer_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "jpy",
        "line_items[0][price_data][unit_amount]": str(ADDON_PRICE_JPY),
        "line_items[0][price_data][product_data][name]": "ツナグモ 追加AI予算チケット",
    }

    async with httpx.AsyncClient(auth=(api_key, ""), timeout=FETCH_TIMEOUT_SECONDS) as client:
        try:
            res = await client.post(f"{STRIPE_API_BASE}/checkout/sessions", data=body)
        except httpx.HTTPError as e:
            raise StripeClientError(f"Stripeへの接続に失敗しました: {e}") from e

    if res.status_code >= 400:
        raise StripeClientError(f"Stripe Checkoutセッションの作成に失敗しました(status={res.status_code}): {res.text[:300]}")

    data = res.json()
    url = data.get("url")
    if not url:
        raise StripeClientError("Stripeのレスポンスにcheckout URLが含まれていませんでした。")
    return url


async def create_billing_portal_session(*, customer_id: str, return_url: str, api_key: str) -> str:
    """Stripeカスタマーポータルのセッションを作り、顧客が自分で解約・カード変更・
    請求履歴の確認をできるページのURLを返す(セルフサービス解約の実体)。
    ポータル自体の外観・許可する操作はStripeダッシュボード側の設定に従う。
    """
    if not api_key:
        raise StripeClientError("Stripeのシークレットキーが設定されていません。")

    body = {"customer": customer_id, "return_url": return_url}

    async with httpx.AsyncClient(auth=(api_key, ""), timeout=FETCH_TIMEOUT_SECONDS) as client:
        try:
            res = await client.post(f"{STRIPE_API_BASE}/billing_portal/sessions", data=body)
        except httpx.HTTPError as e:
            raise StripeClientError(f"Stripeへの接続に失敗しました: {e}") from e

    if res.status_code >= 400:
        raise StripeClientError(
            f"Stripeカスタマーポータルセッションの作成に失敗しました(status={res.status_code}): {res.text[:300]}"
        )

    data = res.json()
    url = data.get("url")
    if not url:
        raise StripeClientError("Stripeのレスポンスにポータル URLが含まれていませんでした。")
    return url
