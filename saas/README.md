# Phase 1: 起動手順（Docker Desktopが使えるようになったら）

```
cd saas
cp .env.example .env
```

`.env` に `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `ANTHROPIC_MODEL_LIGHT`（Phase 2の分類など軽量処理用）を入れる。`DATABASE_URL` は docker-compose が自動で設定するので空のままでよい。

```
cd docker
docker compose up --build
```

起動したら、別のターミナルでマイグレーションとテスト用テナントの作成：

```
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_dev_tenant
```

表示された開発用APIキー（`dev-local-test-key`）をコピーし、ブラウザで http://localhost:8000 を開いて画面のAPIキー欄に貼り付け、「テスト」と送信する。「受け付けました」と session_id が表示されれば、Phase 1の完了条件を満たしている。

## テストの実行

```
docker compose exec api pytest -v
```

## Phase 2: 統括AI・要件確認・部署・QAグラフ

`src/agent/` にLangGraphベースの組織グラフを実装済み。業種設定は `config/default.yaml`（付録Aのテンプレート）。

- 設定の単体検証: `docker compose exec api python -m src.validate_config config/default.yaml`
- 実際のClaude APIを使った手動スモークテスト(課金が発生するのでpytestには含めない):
  `docker compose exec api python -m scripts.smoke_test_agent`
- グラフ構造は `docs/graph.mmd`（Mermaid）参照

## Phase 5: 永続化とAPI連携

`POST /api/sessions`が実際にグラフを起動し、`POST /api/sessions/{id}/answer`で要件確認への回答を送信できる。
チェックポインタはPostgresSaver(コネクションプール経由)。プロセスを再起動しても要件確認待ち・承認待ちの状態は失われない。

起動から完了までの流れ:
1. `POST /api/sessions` → `202 QUEUED`ですぐ返る。裏でグラフが動き出す
2. `GET /api/sessions/{id}` をポーリング。`status`が`CLARIFYING`なら`result.questions`に質問が入る
3. `POST /api/sessions/{id}/answer` に`{"answer": {...}}`を送ると再開する
4. 最終的に`AWAITING_APPROVAL`（承認要求サマリ）か`COMPLETED`（挨拶等）になる

Anthropic APIへの応答が遅い場合、1回の処理に数十秒〜数分かかることがある(タイムアウトは60秒+リトライ)。

## テナント内ユーザー管理・多段階承認(Phase 13の前提+Phase 13、2026-08-26)

これまで「テナントAPIキーを持つ人全員」が承認可能だったのを、テナント内の個人ユーザーを
識別できるようにした上で、承認段数を複数にできるようにした。**既定(`approval_stages=1`)の
テナントは今まで通りテナントAPIキーだけで承認できる**(後方互換)。

- ユーザー招待: `POST /api/account/users`（`{"email": "...", "role": "member|approver|owner"}`）。
  テナントに誰も居ない状態からの最初の1人は、指定した`role`に関わらず必ず`owner`になる
  (先有卵鶏問題の回避)。2人目以降は、`owner`ロールのTenantUserとして`X-User-Token`ヘッダで
  ログインしていないと呼べない
- 招待されたメールにはパスワード設定用の招待コードが届く(Resend未設定の間は送信スキップ)。
  `POST /api/account/accept-invite`（`{"invite_token": "...", "password": "..."}`）で
  初期パスワードを設定するとアカウントが有効になる
- ログイン: `POST /api/account/login`（テナントAPIキー必須＋`{"email", "password"}`）。
  成功すると`X-User-Token`ヘッダに使う不透明トークンが返る(有効期限12時間)
- ユーザー一覧: `GET /api/account/users`（`owner`のみ）
- 承認段数の設定: `PUT /admin/tenants/{tenant_id}/approval-stages`（`{"approval_stages": 2}`等、
  1〜10）。2以上にする場合、そのテナントに`approver`か`owner`のTenantUserを先に用意しておくこと
  (居ないと誰も承認できなくなる)
- 承認API(`POST /api/sessions/{id}/approve`)は、`approval_stages`が2以上のテナントでは
  `X-User-Token`必須(`approver`/`owner`ロールのみ)。1段階の途中で却下された場合は、
  何段目であってもその場でグラフを差し戻す(REVISEループ)。全段階の承認が揃って初めて
  グラフを先に進める。1段のテナントでも`X-User-Token`を付ければ、誰が承認したかが
  `approvals.approver_user_id`に記録される

## LINEチャネル連携の土台(Phase 15、2026-08-26時点では未稼働)

SaaS本体の顧客対応をLINE公式アカウント経由でも受け付けられるようにする土台
(`src/channels/line.py`)。**実際の顧客のLINE公式アカウント認証情報が無いと動かない**ため、
1社目の顧客がLINE公式アカウントを用意するまでは、以下の管理APIで設定してもテナント側の
Webhookエンドポイント自体はまだ実装していない(署名検証・冪等性チェックの部品のみ実装済み)。

- 設定: `PUT /admin/tenants/{tenant_id}/line-channel`
  （`{"line_channel_secret": "...", "line_channel_access_token": "..."}`、暗号化保存）
- 解除: `DELETE /admin/tenants/{tenant_id}/line-channel`
- 応答ロジック自体はPhase 16のWeb埋め込みチャットと共用(`src/agent/public_responder.py`)。
  実際のWebhookルート・LINEアカウントでの実機テストは、1社目のLINE連携要望が出てから着手する

## 業種別設定・テナント発行

`config/` には業種ごとに設定ファイルがある(`src/api/deps.py`の`INDUSTRIES`が正の一覧)。

- `web_agency.yaml` — Web制作・広告代理店（`default.yaml`と同内容。旧名を互換のため残している）
- `real_estate.yaml` — 不動産（賃貸仲介・売買）
- `recruiting.yaml` — 人材紹介・人材派遣
- `legal.yaml` — 士業（社労士・行政書士）。官公署提出書類の作成・法的助言はAIにやらせない制約を
  `ceo_office`と`qa_auditor`の両方に二重で組み込んでいる

起動時に全業種分を読み込み `app.state.app_configs`（industry名→AppConfigの辞書）に保持する。
どの設定を使うかは`Tenant.industry`列で決まる（`src/api/deps.py`の`get_app_config`）。

- 新規テナント発行: `POST /admin/tenants` に`{"name": "...", "industry": "real_estate", "anthropic_api_key": "sk-ant-..."}`
  （管理者キー必須。レスポンスのAPIキーはこの1回しか平文で見られない）
- 開発用テナントを業種指定で作る: `docker compose exec api python -m scripts.seed_dev_tenant real_estate`
  （`anthropic_api_key`はローカル動作確認用に`.env`のANTHROPIC_API_KEYを流用する）
- プロンプト一括更新も業種ごと: `GET/PUT /admin/config/{industry}`（旧`/admin/config`から変更）

## 事業モデル: AI利用の実費は顧客が自分のAnthropic APIキーで負担する

`POST /admin/tenants`で発行するテナントは、必ず顧客自身のAnthropic APIキー(`anthropic_api_key`)を
持つ。顧客の実セッションはこのキーで直接Anthropicに課金される(`src/api/deps.py`の`get_llm`)。
ツナグモ自身の`.env`のANTHROPIC_API_KEYは、起動時ヘルスチェックと管理画面の回帰テスト
(`POST /admin/regression-test`)専用(`get_internal_llm`)で、顧客セッションには一切使わない。

- キーのローテーション: `PATCH /admin/tenants/{tenant_id}/anthropic-key`（管理画面①ダッシュボードの
  「Anthropicキー更新」ボタンからも操作できる）
- 顧客のAnthropic APIキーはDBに暗号化して保存する(`src/core/crypto.py`、Fernet対称鍵)。
  鍵は`.env`の`TENANT_SECRET_KEY`
  (生成: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)。
  この鍵自体はKMS等では管理していない(ADMIN_API_KEYと同程度の信頼が必要な運用上の前提)

## 通知メール(Resend)

`POST /admin/tenants`にemailを指定するとAPIキー入りのウェルカムメール、承認期限が近づくと
リマインドメールを自動送信する(`src/core/email.py`)。**RESEND_API_KEYを設定するまでは
送信をスキップしてログに残すだけ**なので、未契約でもアプリ全体は問題なく動く。

セットアップ手順(未契約):
1. [resend.com](https://resend.com)でアカウント作成(無料枠: 月3,000通・日100通)
2. 送信元ドメインを追加し、表示されるSPF/DKIMのDNSレコードを、そのドメインを管理している
   ところ(Cloudflare DNS等)に追加して認証を待つ(認証が済むまでは`onboarding@resend.dev`のような
   テスト送信元しか使えない)
3. APIキーを発行し、`.env`に設定:
   - `RESEND_API_KEY=re_...`
   - `RESEND_FROM_EMAIL="ツナグモ <notify@認証したドメイン>"`
   - `SAAS_PUBLIC_URL=`(本番で顧客がログインするURL。メール本文のリンクに使う)

line-bot側(`/admin/activate`)は`POST /admin/tenants`にemailを渡すだけで、実際の送信は
SaaS側のこの仕組みに一本化されている(line-bot側にResendの実装は無い)。

## 管理画面(GUI)

`http://localhost:8000/admin.html` に管理者キーでログインすると、①横断ダッシュボード・
新規テナント発行・②プロンプト一括更新(業種選択式)・⑥回帰テスト一括実行をブラウザから
操作できる。API直叩き(curl等)の代替。管理者キーはブラウザのlocalStorageに保存される
(顧客向け`index.html`のAPIキー保存とはキー名を分けてあるので、同じブラウザで両方
ログインしても混ざらない)。

## さくらVPSへのデプロイ手順(2026-08-20時点)

VPS契約・SSH鍵の登録・ドメイン取得は済んでいる前提(`docker/`配下に準備済みのファイルがある)。

1. VPSにSSH接続する(鍵は`C:\Users\user\.ssh\tsunagumo_vps`):
   ```
   ssh -i C:\Users\user\.ssh\tsunagumo_vps root@<VPSのIPアドレス>
   ```
2. `docker/vps_setup.sh` の中身をVPS上にコピーして実行(Docker・Caddyのインストール、
   ファイアウォール設定まで自動でやる):
   ```
   sudo ./vps_setup.sh
   ```
3. `saas/`一式を `/opt/tsunagumo/saas` に転送する(git clone、またはscp)。**転送時に`saas/.env`と
   `saas/docker/.env`の両方(下記4・5参照)を上書き・削除しないこと**。既存デプロイを更新する場合は、
   コード一式(`.env`系を除く)だけを差し替え、この2つの`.env`は旧ディレクトリからコピーして引き継ぐ
4. `docker/.env.production.template` の内容を `/opt/tsunagumo/saas/.env` として配置する。
   `DATABASE_URL`・`ANTHROPIC_API_KEY`(ツナグモ自身の分)・`SAAS_PUBLIC_URL`(実ドメイン)を埋める
5. **(重要)** `saas/docker/.env` を新規に作成し、`POSTGRES_PASSWORD=<実際のDB用パスワード>` の
   1行だけを書く(4の`../.env`とは別ファイル。`docker-compose.prod.yml`の`${POSTGRES_PASSWORD}`
   変数展開専用で、`docker compose`がカレントディレクトリの`.env`を自動で読む仕組みを利用している)。
   このファイルが無い、または`-f docker-compose.prod.yml`を付け忘れると、`docker-compose.yml`本体に
   直書きされた開発用ダミーパスワード(`tsunagumo_dev`)・`ENV=development`のまま起動してしまい、
   本番DBへの認証が失敗する(2026-08-28に実際に発生・復旧した実例あり。DB自体は名前付きボリューム
   `db_data`にあるため消えはしないが、正しい起動には必ずこの2ファイル+`-f`両方が必要)
6. 起動(**`-f`を2つとも指定すること**):
   ```
   cd /opt/tsunagumo/saas/docker
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
   docker compose exec api alembic upgrade head
   ```
7. **(2026-08-26変更)** 営業サイト(`website/`一式)とSaaS本体は別ドメインに分離する
   (同じトップドメインだとルートパスが衝突するため)。
   - 営業サイト一式を `/opt/tsunagumo/website` に配置する(scp等)
   - SaaS本体用に `app.<ドメイン>` のAレコードを追加(VPSと同じIP)
   - `docker/Caddyfile.example` の内容を `/etc/caddy/Caddyfile` に配置し、
     `sudo systemctl reload caddy` でHTTPS化(Let's Encryptの証明書取得は自動)。
     トップドメイン(`tunagumo.com`)は営業サイトの静的配信、`app.tunagumo.com`が
     SaaS本体になる
   - `.env`の`SAAS_PUBLIC_URL`は`https://app.tunagumo.com`にする
     (`docker/.env.production.template`参照)
8. 承認期限スキャン・社内資料の有効期限スキャン・顧客スケジュール起動(Phase 14)・
   Web埋め込みチャットの保持期間クリーンアップ(Phase 16)は、`docker compose up -d`で
   `scheduler`サービスが自動的に常駐実行する(承認期限: 5分間隔、資料期限: 1日間隔、
   顧客スケジュール: 1分間隔、Webチャットクリーンアップ: 1時間間隔。
   `scripts/scheduler.py`参照)。**cronへの二重登録はしないこと**(同じスキャンが
   二重に走ってしまう)。cronの方が都合が良い場合は、`docker-compose.yml`から
   `scheduler`サービスを止めた上で、代わりに以下を登録する:
   ```
   crontab -e
   */5 * * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.scan_approval_deadlines >> /var/log/tsunagumo_scan.log 2>&1
   0 9 * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.scan_document_expiry >> /var/log/tsunagumo_scan.log 2>&1
   * * * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.scan_schedules >> /var/log/tsunagumo_scan.log 2>&1
   0 * * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.cleanup_web_chat_logs >> /var/log/tsunagumo_scan.log 2>&1
   ```
   顧客ごとのスケジュール(「毎週月曜9時にSNS投稿案を作成」等)は管理画面からではなく
   `POST /admin/tenants/{tenant_id}/schedules`で登録する(cron式・タイムゾーン・依頼内容・
   予算上限を指定)。3回連続で失敗すると自動的に無効化され、`ADMIN_NOTIFICATION_EMAIL`に
   通知が届く。スケジュール実行でも承認は必須で、無人で外部に出る経路はない。
   Web埋め込みチャット(Phase 16)は`PUT /admin/tenants/{tenant_id}/web-widget`で
   有効化する(埋め込み先ドメインを指定すると公開鍵が発行される)。顧客サイトに
   `<script src="https://.../widget.js" data-key="発行された公開鍵"></script>`を
   1行貼るだけで動く。対応不可と判断した会話は
   `GET /admin/tenants/{tenant_id}/web-chat/escalated`で確認できる。
9. 日次バックアップとディスク使用率監視をcronに登録(こちらは`scheduler`サービスでは
   カバーしていないので、必ず登録する):
   ```
   crontab -e
   0 3 * * * cd /opt/tsunagumo/saas/docker && ./backup_db.sh >> /var/log/tsunagumo_backup.log 2>&1
   0 8 * * * cd /opt/tsunagumo/saas/docker && docker compose exec -T api python -m scripts.check_disk_usage >> /var/log/tsunagumo_scan.log 2>&1
   ```
   バックアップは`docker/backups/`に暗号化して保存される(`BACKUP_ENCRYPTION_PASSPHRASE`が
   `.env`に必要、`docker/.env.production.template`参照)。保存後は、VPS以外の場所
   (別クラウドのオブジェクトストレージ等)へrclone等で同期すること。復元は
   `docker/restore_db.sh <バックアップファイル>`(デフォルトでは安全のため
   `tsunagumo_restore_test`という別名データベースに復元する。実測時間は
   `restore_db.sh`冒頭のコメント参照)。
10. line-bot(Cloudflare Worker)側の`SAAS_BASE_URL`を、実際のドメインに更新して再デプロイ:
   ```
   cd line-bot
   # wrangler.tomlのSAAS_BASE_URLを "https://app.tunagumo.com" (SaaS本体のサブドメイン)に書き換えてから
   npx wrangler deploy
   ```
