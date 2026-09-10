#!/bin/bash
# ===========================================================================
# instagram_check.sh - Codex主導・Instagram投稿承認チェック＆自動投稿スクリプト
#
# 実行場所: VPS上の開発用clone（~/dev/tunagumo）専用。
# 元々Claude Code側の定期実行で行っていた処理をCodexに移管したもの。
# 30分おきにNotionを確認し、「承認済」の行だけBuffer経由でInstagramに投稿する。
# ===========================================================================
set -uo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

FALLBACK_MODEL="gpt-5.6-luna"

notify_slack() {
  local text="$1"
  if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
    curl -s -X POST -H 'Content-type: application/json' \
      --data "{\"text\": $(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
      "$SLACK_WEBHOOK_URL" >/dev/null 2>&1
  fi
}

run_codex_with_fallback() {
  local prompt="$1"
  local out
  # codex exec = 非対話(cron)実行専用のサブコマンド。素の `codex "..."` は
  # TUI起動を試みてしまい、端末が無いcron環境では "stdin is not a terminal" で
  # 失敗する(2026-09-08 cron初回実行で発覚)。
  # `-a`/`--ask-for-approval` は exec には存在しない(codexコマンド本体のみの
  # オプションだった)。exec では `-s/--sandbox danger-full-access` を使う
  # (2026-09-10、`codex exec --help`で確認)。
  out=$(codex exec -s danger-full-access "$prompt" 2>&1)
  local status=$?

  if [ $status -ne 0 ] || echo "$out" | grep -qiE "rate.?limit|usage.?cap|429|quota"; then
    echo "[instagram_check] メインモデルで失敗/制限を検知。${FALLBACK_MODEL} で再実行します。" >&2
    echo "$out"
    out=$(codex exec -s danger-full-access --model "$FALLBACK_MODEL" "$prompt" 2>&1)
    status=$?
  fi

  echo "$out"
  return $status
}

run_codex_with_fallback "あなたはツナグモのInstagram投稿承認チェック担当です。作業ディレクトリはこのリポジトリのルート。
YouTube投稿は廃止済み。Instagram(Buffer)投稿のみ行ってください。

### 1. 承認待ちの確認
Notion MCPで、データベース『ツナグモ Instagram投稿予定』のこのビューを確認せよ:
mode: view, view_url: https://app.notion.com/p/83b85a552c124499b1e9ba5bd19f7acd?v=d93c4dc8-f67d-4c3a-b61f-bd64bdb184dd
返る全行から、『状態』が『承認済』の行だけを自分で読んで探せ(『下書き』『投稿済』は対象外)。

### 2. 該当行があれば、各行について以下を実行
a. その行の『投稿タイトル』『キャプション』『画像URL』を取得する。
b. 動画判定: 『投稿タイトル』が『[動画] 』で始まる、または『画像URL』の拡張子が.mp4/.mov等なら動画として扱う。それ以外は画像。
c. キャプションを sales/ai_tips_drafts/notion_approved_<ページID下8桁>.txt に保存する。
d. python scripts/publish_via_buffer.py --media-url \"<画像URL>\" --caption-file \"<cのファイルパス>\" --yes (動画なら --video を追加) を実行してInstagramに投稿する。BUFFER_API_KEYは.envから読まれる。
e. 成功したら、そのNotionページの『状態』を『投稿済』にNotion MCPで更新する(可能なら『投稿後のパーマリンク』も埋める)。
f. 失敗した場合は『状態』を変更せず、エラー内容を記録する(次回再試行)。

### 3. 該当行が無い場合
何もせず終了せよ。

### 4. 完了後
実際に投稿した行があれば、そのタイトル・画像/動画の別・投稿結果を instagram_check.log に日本語で1行追記せよ。無ければ何もしなくてよい。"

echo "=== [完了] Codex Instagram承認チェック 正常終了 ==="
