from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # 2026-09-01: BYOK(顧客自身のAPIキー)を廃止し、運営(ツナグモ)自身のキーで
    # AI利用料を負担する方式に切り替えた(src/api/deps.pyのget_llm等参照)。
    # 月間予算上限(src/core/ai_budget.py)がAnthropic Console側の顧客自身の
    # 利用上限に代わる主防御層になる。
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    higgsfield_api_key_id: str = ""
    higgsfield_api_key_secret: str = ""
    anthropic_model: str = ""
    anthropic_model_light: str = ""
    admin_api_key: str = ""
    # 顧客のAnthropic APIキーなどをDBに保存する際の暗号化鍵(Fernet)。
    # 生成方法: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    tenant_secret_key: str = ""
    # 通知メール送信(Resend)。未設定の間はsrc/core/email.pyが送信をスキップする。
    resend_api_key: str = ""
    resend_from_email: str = ""
    # 顧客がログインするSaaS本体のURL(ウェルカムメール・リマインドメールの文中で使う)。
    # 未設定の間はメール文中でリンクを省略する。
    saas_public_url: str = ""
    # 運営(野村さん)への通知先(ディスク使用率警告等、顧客向けではない運用アラート用)。
    # 未設定の間はscripts/check_disk_usage.pyがログに残すだけになる。
    admin_notification_email: str = ""
    # Stripe Webhookの署名検証用シークレット(https://dashboard.stripe.com/webhooks で
    # エンドポイント登録後に発行される whsec_... )。未設定の間はsrc/api/routes/stripe_webhook.py
    # が全リクエストを401で拒否する(検証をスキップして受け付けることは絶対にしない)。
    stripe_webhook_secret: str = ""
    # 2026-09-01: 追加AI予算購入(Part 4)のStripe Checkoutセッション作成に使う
    # シークレットキー(https://dashboard.stripe.com/apikeys)。未設定の間は
    # src/api/routes/account.pyの追加購入エンドポイントが503を返す。
    stripe_secret_key: str = ""
    # AI査定(5位)・空室対策レポート(10位)・エリア人口動態分析(11位)で使う、国交省
    # 「不動産情報ライブラリ(reinfolib)」のAPIキー(無料・公共データ、ツナグモ自身が
    # https://www.reinfolib.mlit.go.jp/ で取得する。顧客ごとのキーではない)。
    # 未設定の間はsrc/api/routes/market_data.pyが「準備中」エラーを返す。
    reinfolib_api_key: str = ""
    env: str = "development"


settings = Settings()
