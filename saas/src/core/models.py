import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# OpenAI text-embedding-3-smallの次元数(Phase 7 RAG)。モデルを変える場合、
# 次元数が変わるならマイグレーションで列を作り直し、全チャンクの再埋め込みが必要になる。
EMBEDDING_DIM = 1536


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # config/{industry}.yaml を選択するキー。src.api.deps.INDUSTRIES の許可リストで検証する
    industry: Mapped[str] = mapped_column(String(50), default="real_estate", server_default="real_estate")
    # 顧客自身のAnthropic APIキー(事業モデル: AI利用の実費は顧客が自分で契約・負担する)。
    # このSaaSはこれを使ってその顧客のセッションのAI呼び出しを行う。ツナグモ自身の
    # ANTHROPIC_API_KEY(.env)は、健全性チェックや管理者の回帰テストにのみ使う。
    # src/core/crypto.pyで暗号化した値を保存する(平文よりかなり長くなるためText型)。
    # ログには絶対に出力しないこと(src/core/logging_config.pyの方針と同じ)。
    anthropic_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 顧客自身のOpenAI APIキー(Phase 7 RAGの埋め込みに使う。Anthropicと同じ理由で
    # 顧客自身の契約・負担とする。src/core/crypto.pyで暗号化して保存)。
    # RAG機能を使わない顧客は未設定のままでよい。
    openai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 顧客自身のHiggsfield APIキー(画像生成。Anthropic/OpenAIと同じ理由で顧客自身の
    # 契約・負担とする。src/core/crypto.pyで暗号化して保存)。Higgsfieldの認証は
    # key_id:key_secretの組で1つのAuthorizationヘッダになるため2列に分けて持つ。
    # 画像生成機能を使わない顧客は未設定のままでよい。
    higgsfield_api_key_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    higgsfield_api_key_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 承認期限リマインドなどの通知先(src/core/email.py)。未設定なら通知はスキップされる。
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Phase 16: Web埋め込みチャットウィジェット用の公開鍵(ブラウザに晒してよい。
    # Anthropic/OpenAIキーとは別物で、これ単体では何もできない)。未設定ならウィジェット無効。
    web_widget_public_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # ウィジェットの埋め込みを許可するオリジン(例: "https://example.com")。
    # これ以外のOriginからの/api/chatは拒否する(16-3-1)。
    web_widget_allowed_origin: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Phase 13: 承認に必要な段数。既定の1は今までの動作(テナントAPIキーを持つ全員が
    # 1回承認すれば確定)と完全互換。2以上にすると、その回数ぶん別々のTenantUser
    # (role=approver/owner)の承認が揃うまで次に進まない(src/api/routes/sessions.py参照)。
    approval_stages: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Phase 15: LINE公式アカウントのチャネル認証情報(暗号化済み、src/core/crypto.py)。
    # 両方揃って初めてそのテナントのLINE連携が有効になる(src/channels/line.py参照)。
    line_channel_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_channel_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 2026-09-15: 問い合わせ一次受け。担当者へのエスカレーション通知の送り先(未設定ならemailを使う)と、
    # 緊急時の自動返信に載せる緊急連絡先の電話番号(src/agent/inquiry_triage.py)。
    inquiry_notify_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 2026-09-16: 物件提案・追客メール(src/agent/proposal_scan.py)。広告宣伝メールは特定電子メール法により
    # 送信者の名称・住所・問い合わせ先の表示が必要なため、これらが揃うまで送信しない。
    marketing_sender_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    marketing_sender_address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    marketing_sender_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Trueなら、ひな形どおりの提案メール(物件情報はDBの値のみ)を承認なしで自動送信する。既定は承認制
    proposal_auto_send: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    follow_up_interval_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    follow_up_max: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    # 2026-09-01: Stripeサブスクの状態と連動してアクセスを止めるための列。
    # stripe_customer_idが未設定(=Stripeと紐付いていない、管理者が手動発行したテナント等)の
    # 場合はsubscription_activeのデフォルトTrueのまま何もチェックしない。Stripe Webhookが
    # customer.subscription.deleted/updated(status=canceled等)を受け取った時だけFalseにする
    # (src/api/routes/stripe_webhook.py)。get_current_tenant(src/api/deps.py)がこれを見て
    # 403で拒否する。
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    subscription_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # 2026-09-01: プラン別の動画生成本数制限(src/core/plan_limits.py参照)。このSaaSは
    # 文章・画像等は会社の月間AI共有枠で管理する。Higgsfield動画は顧客負担。
    plan: Mapped[str] = mapped_column(String(20), default="light", server_default="'light'")
    enterprise_ai_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_generations_this_period: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # この値の年月と現在の年月が異なれば、動画生成カウントを0にリセットしてから判定する
    # (月次バッチを別途動かす必要がない、参照時リセット方式)。
    video_period_started_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # 2026-09-01: BYOK廃止に伴う月間AI予算上限(src/core/ai_budget.py参照)。
    # video_generations_this_period/video_period_started_atと同じ「参照時リセット」方式。
    ai_cost_this_period_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    ai_cost_period_started_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # 追加購入分(Stripeの一回払いで購入したAI予算の上乗せ)。月次リセットされない。
    addon_credit_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # idempotency_keyが送られた場合のみ、テナント内で一意にする
        Index(
            "ix_sessions_tenant_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    request_text: Mapped[str] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 管理者ダッシュボード(横断的な利用状況・コスト表示)のための実測コスト。
    # グラフ実行完了のたびに、その時点の累計cost_usdで上書きする(4-1)。
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Approval(Base):
    """承認要求の記録(6-1)。1セッションに対して複数回作られうる
    (却下→REVISE→再度SUMMARIZEのたびに新しい承認要求になる)。"""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING/APPROVED/REJECTED/EXPIRED
    # Phase 13: 何段階目の承認要求か(1始まり)。tenant.approval_stages段が全てAPPROVEDに
    # なって初めてグラフを先に進める。1段のテナント(既定)では常に1のみ発生する。
    stage: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deadline_at: Mapped[datetime] = mapped_column()
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Phase 13: 実際に承認したTenantUser(個人ユーザーの識別が無い既存テナントではNULLのまま、
    # decided_byの「テナントID文字列」が引き続き唯一の記録になる)。
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant_users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class AuditLog(Base):
    """承認・却下の追記専用ログ(6-3-3)。UPDATE/DELETEはアプリケーションコードから行わない。"""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))  # APPROVE / REJECT / ACTION_EXECUTED / ACTION_FAILED
    actor: Mapped[str] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Document(Base):
    """Phase 7(RAG): アップロードされた社内資料1件。中身そのものはchunksに分割して
    持つ(Document自体はライフサイクル管理用のメタデータ)。"""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # ACTIVE / SUPERSEDED / EXPIRED / ARCHIVED / FAILED(取り込み失敗)
    status: Mapped[str] = mapped_column(String(20), default="PROCESSING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_until: Mapped[date | None] = mapped_column(nullable=True)
    # 新版のdocument_id(自分がSUPERSEDEDになった場合、差し替え先を指す)
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # internal(社内向け)/ public(Phase 15/16の社外チャネル向け、将来用)
    index_scope: Mapped[str] = mapped_column(String(20), default="internal")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until_notified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Chunk(Base):
    """Document 1件を分割した断片。埋め込みベクトルとハイブリッド検索(pgroonga)の
    両方の対象になる。"""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    # 検索時にJOIN無しでテナント絞り込みできるよう非正規化して持つ
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ExecutedAction(Base):
    """Phase 12: 承認後に実行された不可逆アクション(メール送信等)の記録。
    idempotency_keyのUNIQUE制約(主キー)そのものが二重実行防止の本体。
    「先にCLAIMEDとして記録してから実行する」順序が重要(src/actions/executor.py参照)。"""

    __tablename__ = "executed_actions"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(index=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action_type: Mapped[str] = mapped_column(String(100))
    params: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="CLAIMED")  # CLAIMED/SUCCEEDED/FAILED
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RuntimeFlag(Base):
    """再デプロイなしで機能を止められる緊急停止フラグ(Phase 12)。
    テナント横断の運用設定なのでtenant_idは持たない。"""

    __tablename__ = "runtime_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Schedule(Base):
    """Phase 14: 定期実行の定義。「毎週月曜9時にSNS投稿案を作る」等。
    業種共通のconfig YAMLとは違い、テナントごとに内容が異なるためDBに持つ。"""

    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    cron: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Tokyo")
    goal: Mapped[str] = mapped_column(Text)
    max_budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    # 14-3: 月間の総実行回数上限。正常に動き続けるスケジュールが際限なく積み重なることへの
    # 安全弁(1回ごとのmax_budget_usdとは別物)。既定値はDEFAULT_MAX_MONTHLY_RUNS
    # (schedule_tick.py)で、テナントが多い頻度で使いたい場合は作成時に明示的に上書きできる
    max_monthly_runs: Mapped[int | None] = mapped_column(nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 3回連続失敗で自動的にFalseへ倒す(壊れたまま無人で課金され続けるのを防ぐ、14-2)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class ScheduleRun(Base):
    """1回分の起動記録。(schedule_id, scheduled_for)の複合主キーが二重起動防止の本体
    (同じ時刻分は1回しかCLAIMEDにならない)。"""

    __tablename__ = "schedule_runs"

    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedules.id"), primary_key=True)
    scheduled_for: Mapped[datetime] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="CLAIMED")  # CLAIMED/SUCCEEDED/FAILED/SKIPPED_CAP/SKIPPED_BUDGET
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class WebChatSession(Base):
    """Phase 16: Web埋め込みチャットの会話1件。社内グラフ(OrgState/sessions)とは
    完全に別物(fan-outしない単一ノードの応答、承認フローも通らない)。
    ブラウザ側はsessionStorageでこのidを保持する(localStorageは使わない、16-4)。"""

    __tablename__ = "web_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    # [{"role": "user"|"assistant", "content": "..."}]の配列。個人情報が入りうるため
    # 保持期間を区切って自動削除する(16-4: 24時間、7-5-7と同じ考え方)
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    message_count: Mapped[int] = mapped_column(Integer, default=0)  # 往復数(16-3-3の上限判定用)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    # 2026-09-15: LINEの会話も同じ仕組みで持つ。channel="line"の場合、external_user_idにLINEのuserIdが入る
    # (webは匿名の訪問者なので常にNULL)。保持期間(24時間)はwebと同じ。
    channel: Mapped[str] = mapped_column(String(10), default="web", server_default="web")
    external_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class TenantMailAccount(Base):
    """2026-09-16: メール即レス。顧客(不動産会社)が普段使っているメールボックスにIMAPで接続して
    新着メールを確認し、SMTPで顧客自身のアドレスから返信する(src/channels/mail.py)。
    パスワードは暗号化して保存する(src/core/crypto.py)。1テナント1アカウント。"""

    __tablename__ = "tenant_mail_accounts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    from_address: Mapped[str] = mapped_column(String(320))
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    username: Mapped[str] = mapped_column(String(320))
    password_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 次回はこのUIDより後のメールだけを見る。NULLは「まだ一度も接続していない」(初回は既存の
    # メールに返信しないよう、その時点の最新UIDを記録するだけにする)
    last_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class MailProcessedMessage(Base):
    """処理済みメールの記録。(tenant_id, message_id)の主キーで同じメールに二重返信しないようにし、
    sender_addressで「同じ相手への自動返信は24時間で3回まで」(メールのループ防止)を数える。"""

    __tablename__ = "mail_processed_messages"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    sender_address: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    auto_replied: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)


class Inquiry(Base):
    """2026-09-15: AIが担当者へ回した問い合わせ(エスカレーション)。担当者が対応を終えるまで
    会話を残し、状態を管理する。web_chat_sessionsは24時間で消えるため、別テーブルにしている。
    個人情報を含みうるので、対応済みは30日、未対応でも90日で自動削除する
    (scripts/cleanup_web_chat_logs.py)。"""

    __tablename__ = "inquiries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(10))  # web / line / email
    # 担当者が画面から返信する時の宛先。LINEはuserId、メールは返信先メールアドレス。webはNULL
    external_user_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # メールの場合の件名とMessage-ID(担当者の返信を同じスレッドにつなげるため)
    email_subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    web_chat_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    urgency: Mapped[str] = mapped_column(String(10), default="normal")  # urgent / normal
    category: Mapped[str] = mapped_column(String(50), default="その他")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / in_progress / resolved
    reason: Mapped[str] = mapped_column(Text, default="")
    # [{"role": "user"|"assistant"|"staff", "content": "...", "at": ISO8601}]
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Property(Base):
    """2026-09-16: 提案に使う物件(顧客自身が登録した在庫)。"""

    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    deal_type: Mapped[str] = mapped_column(String(10))  # rent / sale
    location: Mapped[str] = mapped_column(String(300), default="")  # 所在地(市区町村〜町名)
    station: Mapped[str] = mapped_column(String(100), default="")
    walk_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_yen: Mapped[int] = mapped_column(Integer)  # 賃貸は月額賃料、売買は価格
    layout: Mapped[str] = mapped_column(String(20), default="")  # 1K, 2LDK など
    floor_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    built_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="available")  # available / closed
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Lead(Base):
    """2026-09-16: 見込み客と希望条件。広告宣伝メールの事前同意(consent_at)が無い相手には送らない
    (特定電子メール法)。配信停止されたらstatus=unsubscribedにし、以後は一切送らない。"""

    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320), index=True)
    consent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    consent_source: Mapped[str] = mapped_column(String(300), default="")  # 同意を得た経緯(例: 来店時の申込書)
    deal_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    areas: Mapped[list] = mapped_column(JSONB, default=list)  # 希望エリア・駅名(部分一致)
    max_price_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layouts: Mapped[list] = mapped_column(JSONB, default=list)
    max_walk_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_floor_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / paused / unsubscribed
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(nullable=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


class Proposal(Base):
    """2026-09-16: 見込み客への物件提案・追客メール。bodyは本文だけを持ち、送信者情報と配信停止リンク
    (法定表示)は送信時に必ず付ける(編集で消されないようにするため)。"""

    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # proposal / follow_up
    property_ids: Mapped[list] = mapped_column(JSONB, default=list)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / sent / discarded / failed
    # AIで文面を書き換えた提案は、自動送信の対象にしない(人が確認してから送る)
    ai_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WebChatRequestLog(Base):
    """Phase 16: IPベースのレート制限(16-3-2)・テナントごとの日次コスト上限(16-3-4)を
    判定するための生ログ。古い行は定期的に削除する(scripts/cleanup_web_chat_logs.py)。"""

    __tablename__ = "web_chat_request_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)


class TenantUser(Base):
    """Phase 13の前提: テナント内の個人ユーザー識別。既存の「テナントAPIキー1本」認証
    (依頼の作成・閲覧、通常の承認)はそのまま残し、これは「多段階承認で誰が承認したか」
    を区別するための追加レイヤーとして新設する(src/api/deps.pyのget_current_tenantとは別物)。

    role: member(依頼者。承認はできない) / approver(承認できる) / owner(承認に加え、
    ユーザー招待・削除ができるテナント管理者)。
    password_hashは招待を受けてパスワードを設定するまでNULL(is_activeもFalseのまま)。
    """

    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_users_tenant_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    login_locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # member/approver/owner
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    # 招待リンクのトークンはハッシュ化して保存する(APIキーと同じ方式、src/api/deps.pyのhash_api_key)。
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class LineWebhookEvent(Base):
    """Phase 15の土台: LINE Webhookイベントの冪等性処理。(tenant_id, event_id)の複合主キーが
    二重処理防止の本体(ExecutedAction・ScheduleRunと同じ考え方: UNIQUE制約そのものが実体)。
    LINEは同じイベントを再送してくることがある(deliveryContext.isRedelivery)ため必要。"""

    __tablename__ = "line_webhook_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class StripeWebhookEvent(Base):
    """Stripe Webhookイベントの冪等性処理(2026-09-15)。Stripeは同じイベントを複数回
    送ることがある(公式ドキュメントで明言)ため、処理済みのevent_idを記録し、主キーの
    UNIQUE制約で二重処理(追加AI予算の二重加算等)を防ぐ。LineWebhookEventと同じ考え方。
    テナントに紐づかないイベントもあるためtenant_idは持たない(管理用のget_db接続で扱う)。"""

    __tablename__ = "stripe_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class TenantUserToken(Base):
    """TenantUserのログインセッション(不透明トークン、テナントAPIキーと同じくハッシュ照合)。
    複数端末からの同時ログインを許すため、TenantUser 1人につき複数行持てる。"""

    __tablename__ = "tenant_user_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RLS・JOIN無しでのテナント絞り込みのため非正規化して持つ(Chunk.tenant_idと同じ考え方)。
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    tenant_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column()
