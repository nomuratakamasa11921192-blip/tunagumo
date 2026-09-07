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

FALLBACK_MODEL="gpt-5.6-sol"

# $1 = codexに渡すプロンプト文字列
run_codex_with_fallback() {
  local prompt="$1"
  local out
  out=$(codex "$prompt" 2>&1)
  local status=$?

  if [ $status -ne 0 ] || echo "$out" | grep -qiE "rate.?limit|usage.?cap|429|quota"; then
    echo "[daily_qa] メインモデルで失敗/制限を検知。${FALLBACK_MODEL} で再実行します。" >&2
    echo "$out"
    out=$(codex --model "$FALLBACK_MODEL" "$prompt" 2>&1)
    status=$?
  fi

  echo "$out"
  return $status
}

echo "=== [1/3] Codexによる日常自動テストとバグ修復 ==="
run_codex_with_fallback "AGENTS.mdとSYSTEM_PROMPT.mdに従い、saas/docker配下で
'docker compose exec api pytest -v' を実行して自動テストを走らせよ。
もしエラーを検知した場合、原因を特定し、自律エージェントモードでファイルを
自動修正してテストが100%通過するまで修復を繰り返せ（最低5回、それでも直ら
なければ人間への報告に留めて停止せよ）。/opt/tsunagumo 配下は絶対に変更する
な。修正内容は CHANGES.log に記録せよ。"

echo "=== [2/3] Codexによる開発途中コードの誤爆チェック ==="
run_codex_with_fallback "本日の自動修正が、TODOコメントや未実装のダミー関数など、人間が意図的に
未完成のまま残している開発途中の機能を誤って削除・書き換えしていないか、
git diff で確認せよ。もし該当する変更があれば、その部分だけ元に戻せ。"

echo "=== [3/3] CodexによるGitコミット＆GitHub保存 ==="
run_codex_with_fallback "本日の日常QAが正常終了した。変更があれば
'[Codex] 定期バグチェックと修復完了' のメッセージでコミットし、
origin/main へpushせよ（これは本番VPSへの反映ではなく、GitHubリポジトリの
更新のみであることを理解した上で実行せよ）。変更が無ければ何もしなくてよい。
完了したら、その旨を qa_execution.log に日本語1行で追記せよ。"

echo "=== [完了] Codex単独・日常自動QAシステム 正常終了 ==="
