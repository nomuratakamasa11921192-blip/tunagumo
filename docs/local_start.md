# ローカルPCでCodex・Claude Codeを始める

2026-09-18のユーザー指定：普段の開発は「事業①/tunagumo」で行う。
CodexとClaude Codeは同じフォルダを順番に担当する。VPSは既存の日次QAと必要時の検証に使う。
共通ルールは `SYSTEM_PROMPT.md`。同期の詳細は `docs/local_vps_workflow.md`。

## ユーザーが行う操作

1. PC側でCodexを開き、作業フォルダに「事業①/tunagumo」を指定する。SSH接続中のVPSのフォルダではなく、PC内のフォルダを選ぶ。
2. 下の開始メッセージを送る。Claude Codeへ交代するときも同じフォルダを開き、先にCodexの作業が終わったことを確認する。

```text
このPCのtunagumoを開発の中心にする。CodexとClaude Codeを順番に使う。
まず作業場所・Git状態・originを確認し、既存変更とENVを保持してGitHubの最新変更を取得して。
取得先は nomuratakamasa11921192-blip/tunagumo。
SYSTEM_PROMPT.md、docs/local_start.md、docs/handoff_2026-09-17.mdを読み、
ローカルの開発環境を準備・検証してから残件を順番に進めて。
ENVはこのPCにある。値は出力もコミットもせず、必要な変数の有無を確認して。
通常作業のYes/No確認は不要。仕様の選択が必要なときだけ聞いて。
本番 /opt/tsunagumo には触れない。完了済みのOpenAI移行はやり直さない。
```

このVPSセッションからPCのアプリ起動・ログイン・ファイル取得は実施できていない。
以下の準備は、PC側のAIが実際の環境を確認して進める。

## PC側のAIが最初に行うこと

1. 現在のディレクトリ、OS、シェル、Gitブランチと差分を確認する。パスをVPSのものと決めつけない。リポジトリの状態確認は次を使う。

   ```bash
   git rev-parse --show-toplevel
   git status --short --branch
   git remote get-url origin
   ```

   リモートURLに認証情報が含まれる場合は伏せて報告する。既存の変更を破棄・無断でstashしない。
   originが上記リポジトリで、mainに未コミット変更がない場合は `git pull --ff-only`。
   別ブランチや差分がある場合はfetchして比較し、作業を保持したまま統合する。
   Git管理されていない既存フォルダを削除・上書きしてcloneしない。

2. `SYSTEM_PROMPT.md`、担当AIの `AGENTS.md` / `CLAUDE.md`、この資料、
   `docs/local_vps_workflow.md`、`docs/handoff_2026-09-17.md` を読む。
   手動作業は `local/<作業名>` ブランチで進める。両AIで同じファイルを同時に編集しない。

3. Git、Python、Docker/Compose、Nodeの導入状況を確認する。
   バックエンドの実行環境はDockerfileのPython 3.12を使い、PGroongaとffmpegを含むCompose構成で検証する。
   Dockerデーモンが起動していることも確認する。Composeが `!reset` を扱えるかは、下記の構成検査で確認する。
   Windowsのシェル差・Apple Siliconのコンテナ対応などは実機で判定し、不足だけを公式手順に従って準備する。
   VPSの設定やインストーラーをそのままPCに流用しない。

4. ENVは値を表示せず、ファイルの場所・変数の設定有無・Git除外を確認する。
   実API用と通常テスト用は分ける。PCの既存ENVに本番DB接続先があっても使用せず、開発用DBを使う。
   `.env*`、`private/`、ローカル生成物をGitへ追加しない。実キーやDBはGitHubから取得されない。

## 自動テスト用の設定を作る

`saas/.env.test` がない場合だけ、`saas/.env.test.example` から作成する。
テンプレートの `TENANT_SECRET_KEY` は空なので、テスト専用の鍵が必要。
次はリポジトリルートで実行するBash用の例。PCのシェルが違う場合は同等のPython処理を使う。
秘密値を標準出力へ表示せず、既存ファイルも上書きしない。

```bash
python3 - <<'PY'
import base64
import os
from pathlib import Path

source = Path('saas/.env.test.example')
target = Path('saas/.env.test')
if target.exists():
    print('既存の .env.test を保持しました。設定の有無を別途確認してください。')
else:
    content = source.read_text(encoding='utf-8')
    key = base64.urlsafe_b64encode(os.urandom(32)).decode('ascii')
    content = content.replace('TENANT_SECRET_KEY=\n', f'TENANT_SECRET_KEY={key}\n')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    print('テスト専用設定を作成しました。値は非表示です。')
PY
```

既存の `.env.test` に鍵がない場合は、既存設定を保持して空欄だけ補う。
実APIキーをテスト設定へ混ぜない。Composeに影響する同名のシェル環境変数も確認し、
実キーを渡さないテスト用プロセスで実行する。

## PCで検証する

リポジトリルートから、次を順番に実行する。
`config --quiet` は展開した設定値を出力せず、構成だけを検査する。

```bash
cd saas/docker
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml config --quiet
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml build api
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml up -d db
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml run --rm api alembic upgrade head
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -v
```

VPSでの直近結果は646件成功。PCで未実行のまま同じ結果として報告しない。
ブラウザの検証は `saas/frontend/tests/README.md`、LINEボットは `line-bot/test/bot_test.mjs` を参照。
ルートの `python3 -m unittest discover -s tests -v` は日次QAの回帰24件で、Bashが使える環境で実行する。
`daily_qa.sh` 自体はCodexの起動・push・通知を含むVPS用ジョブなので、ローカル準備の確認目的では起動しない。

テスト用ComposeはAPIポートを公開せず、スケジューラも常駐させない。
ブラウザからSaaSを使う準備は別途、PCの開発用ENVを確認後に行う。
APIを公開する場合はローカルホストに限定し、実API用設定・ダミーテスト設定・開発DBの接続を混同しない。

## 準備後の残件

1. 文章生成の日本語品質とコスト記録を実APIで確認する。提供元を選べる既存構成を維持する。
2. 画像生成・編集の品質と実コストを確認する。実測後にフォールバック単価を評価する。
3. LINE・メールの設定と実際のポータル受信形式を確認する。外部への送信は対象・内容の明示的な依頼がある場合に限る。
4. 契約・公開前確認は `docs/release_review_2026-09-17.md` に従い、実設定を推測して変更しない。

不動産情報ライブラリの実API確認はVPSで完了済み。ユーザーはキーを再発行しない方針。
PCで使う場合はPC側の既存設定を調べる。VPSの `saas/.env.reinfolib.local` はGit同期されない。

交代時はブランチ、コミット、変更内容、テスト結果、次の作業を記録する。
AIの会話自体は自動で引き継がれない。変更したファイルだけをコミット・pushし、
`CHANGES.log` に `[Codex]` または `[Claude]` を主語として日本語で記録する。
