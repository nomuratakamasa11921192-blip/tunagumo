"""通知メール送信(Resend https://resend.com )。

RESEND_API_KEYが未設定の間は送信をスキップしてログに残すだけにする(呼び出し元を
落とさない)。開発中・Resend未契約の間もアプリ全体は問題なく動く。

本文にはプロンプトや生成された文書の中身を含めないこと
(src/core/logging_config.pyの「プロンプト・成果物本文はログに出さない」方針と同じ理由:
メール本文も、経由するサービスに顧客の依頼内容を流出させる経路になり得るため)。
"""

import logging

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


async def send_email(*, to: str, subject: str, html: str) -> bool:
    """送信できたらTrue、未設定またはResend側のエラーでスキップ・失敗したらFalseを返す。
    通知メールの送否は本流の処理(承認期限スキャン等)を止める理由にしないため、例外は
    投げずFalseで呼び出し元に伝える。
    """
    if not settings.resend_api_key or not settings.resend_from_email:
        logger.warning("RESEND_API_KEY/RESEND_FROM_EMAIL未設定のため、メール送信をスキップします")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.resend_from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
        if res.status_code >= 400:
            logger.error("メール送信に失敗しました(status=%s)", res.status_code)
            return False
        return True
    except httpx.HTTPError as e:
        logger.error("メール送信中に通信エラーが発生しました: %s", e)
        return False
