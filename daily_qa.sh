#!/bin/bash
# ===========================================================================
# daily_qa.sh - Codex主導・日常バグチェック＆GitHub自動同期スクリプト
#
# 実行場所: VPS上の開発用clone（例: ~/dev/tsunagumo）専用。
# /opt/tsunagumo（本番）には一切触れない。このスクリプト自体もそこに置かない。
#
# モデルのフォールバック: config.toml の [model] にはメインモデル(gpt-6-astra)
# しか宣言できない（Codex CLIに宣言的なフォールバック設定は無い）。そのため
# ここでは、メインモデルの呼び出しが失敗した場合、gpt-5.6-sol で同じ指示を
# 再実行するラッパー関数を用意する。
# ===========================================================================
set -uo pipefail
cd "$(dirname "$0")"

# .env にSLACK_WEBHOOK_URL等の秘密情報を置く場合はここで読み込む(.envはgit管理外)
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

FALLBACK_MODEL="gpt-5.6-luna"

# $1 = Slackに送るメッセージ本文。SLACK_WEBHOOK_URL未設定なら何もしない(黙って続行)
notify_slack() {
  local text="$1"
  if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
    curl -s -X POST -H 'Content-type: application/json' \
      --data "{\"text\": $(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
      "$SLACK_WEBHOOK_URL" >/dev/null 2>&1
  fi
}

# $1 = codexに渡すプロンプト文字列
run_codex_with_fallback() {
  local prompt="$1"
  local out
  # codex exec = 非対話(cron)実行専用のサブコマンド。素の `codex "..."` は
  # TUI起動を試みてしまい、端末が無いcron環境では "stdin is not a terminal" で
  # 失敗する(2026-09-08 instagram_check.shのcron初回実行で発覚、同じ構造の
  # このスクリプトも同様に失敗していた可能性が高い)。
  # `-a`/`--ask-for-approval` は exec には存在しない(codexコマンド本体のみの
  # オプションだった)。exec では `-s/--sandbox danger-full-access` を使う
  # (2026-09-10、`codex exec --help`で確認)。
  out=$(codex exec -s danger-full-access "$prompt" 2>&1)
  local status=$?

  if [ $status -ne 0 ] || echo "$out" | grep -qiE "rate.?limit|usage.?cap|429|quota"; then
    echo "[daily_qa] メインモデルで失敗/制限を検知。${FALLBACK_MODEL} で再実行します。" >&2
    echo "$out"
    out=$(codex exec -s danger-full-access --model "$FALLBACK_MODEL" "$prompt" 2>&1)
    status=$?
  fi

  echo "$out"
  return $status
}

# ---------------------------------------------------------------------------
# 本番との分離(最重要)
#
# 本番は /opt/tsunagumo/saas/docker で docker compose を動かしている。
# Docker Composeは「プロジェクト名」を既定でディレクトリ名から決めるため、
# 開発用cloneのディレクトリも "docker" だと本番と同じプロジェクト名になり、
# `docker compose exec api ...` が本番コンテナに入ってしまう。
# 実際、2026-09-15にこの状態が発覚した(daily_qa.shのpytestが本番コンテナを
# 参照していた)。必ず専用のプロジェクト名を明示して本番と分離する。
# ---------------------------------------------------------------------------
export COMPOSE_PROJECT_NAME="tunagumo-dev"

# テスト用のcompose実行をまとめる。-p でプロジェクトを明示し、
# docker-compose.test.yml で env_file を ../.env.test に差し替える
# (本番の .env・本番のポート8000・本番のコンテナには一切触れない)。
dc() {
  ( cd saas/docker && docker compose -p "$COMPOSE_PROJECT_NAME" \
      -f docker-compose.yml -f docker-compose.test.yml "$@" )
}

echo "=== [0/4] 最新コードの取得とテスト用イメージの再ビルド ==="
# apiコンテナはソースをイメージに焼き込む構成(ボリュームマウントしていない)ため、
# 再ビルドしないと git pull した修正がコンテナに反映されず、テストも古いままになる。
# 2026-09-15、イメージが11日間再ビルドされておらず tests/ がコンテナ内に存在せず、
# pytestが0件で「成功」扱いになっていたのを発見したため追加。
git pull --ff-only 2>&1 || echo "[daily_qa] git pullに失敗しました(ローカル変更がある可能性)" >&2

if [ ! -f saas/.env.test ]; then
  echo "[daily_qa] saas/.env.test がありません。テストを実行できないため中止します。" >&2
  echo "[daily_qa] saas/.env.example を元に .env.test を作成してください。" >&2
  exit 1
fi

dc build api 2>&1 | tail -5
dc up -d db api 2>&1 | tail -3

echo "=== [1/4] Codexによる日常自動テストとバグ修復 ==="
run_codex_with_fallback "AGENTS.mdとSYSTEM_PROMPT.mdに従い、開発用のテストを実行せよ。

【テストの実行方法】saas/docker に移動し、必ず次の形で実行すること:
  docker compose -p tunagumo-dev -f docker-compose.yml -f docker-compose.test.yml exec -T api pytest -v

【絶対に守ること】-p tunagumo-dev と -f docker-compose.test.yml を省略してはならない。
省略すると本番(/opt/tsunagumo/saas/docker)のコンテナに接続し、本番の .env を読み、
本番のポート8000と衝突する。2026-09-15に実際にこの事故が起きている。
詳細は SYSTEM_PROMPT.md §6.1 を読むこと。

【再ビルドが必要】apiコンテナはソースをイメージに焼き込む構成なので、ファイルを修正した
後は必ず次で再ビルドしてからpytestを実行し直すこと。再ビルドしないと修正が反映されず、
古いコードをテストし続けることになる:
  docker compose -p tunagumo-dev -f docker-compose.yml -f docker-compose.test.yml build api
  docker compose -p tunagumo-dev -f docker-compose.yml -f docker-compose.test.yml up -d api

【0件を成功と見なすな】pytestが『0件で成功』のような結果になった場合、それは成功ではなく
テストが発見できていないことを意味する。成功と報告せず、原因を調査せよ。

もしエラーを検知した場合、原因を特定し、自律エージェントモードでファイルを
自動修正してテストが100%通過するまで修復を繰り返せ（最低5回、それでも直ら
なければ人間への報告に留めて停止せよ）。/opt/tsunagumo 配下は絶対に変更する
な。修正内容は CHANGES.log に記録せよ。"

echo "=== [2/4] Codexによる開発途中コードの誤爆チェック ==="
run_codex_with_fallback "本日の自動修正が、TODOコメントや未実装のダミー関数など、人間が意図的に
未完成のまま残している開発途中の機能を誤って削除・書き換えしていないか、
git diff で確認せよ。もし該当する変更があれば、その部分だけ元に戻せ。"

echo "=== [3/4] CodexによるGitコミット＆GitHub保存 ==="
run_codex_with_fallback "本日の日常QAが正常終了した。変更があれば
'[Codex] 定期バグチェックと修復完了' のメッセージでコミットし、
origin/main へpushせよ（これは本番VPSへの反映ではなく、GitHubリポジトリの
更新のみであることを理解した上で実行せよ）。変更が無ければ何もしなくてよい。
完了したら、その旨を qa_execution.log に日本語1行で追記せよ。"

echo "=== [4/4] Slackへの完了通知 ==="
notify_slack "【定期QA】ツナグモ 本日の自動バグチェック完了。詳細は qa_execution.log / GitHub を確認してください。"

echo "=== [完了] Codex単独・日常自動QAシステム 正常終了 ==="
