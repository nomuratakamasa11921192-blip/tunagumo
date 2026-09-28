#!/usr/bin/env bash
# Claude Code等からCodexの画像生成(ChatGPT月額プランの利用枠)を呼び出す。APIキー不要・従量課金なし。
# 使い方: bash scripts/codex_image.sh <保存先.png> "<作りたい画像の説明>"
# 前提: `codex login status` が "Logged in using ChatGPT"。1枚あたり数分かかる。
set -euo pipefail

out="${1:?保存先のPNGパスを指定してください}"
prompt="${2:?画像の説明を指定してください}"
dir="$(cd "$(dirname "$out")" && pwd)"
name="$(basename "$out")"

codex login status 2>&1 | grep -q "ChatGPT" || { echo "CodexがChatGPTでログインしていません(codex login)"; exit 1; }

(cd "$dir" && codex exec --skip-git-repo-check --sandbox workspace-write -c model_reasoning_effort=low \
  "画像生成ツールで次の画像を1枚作り、このフォルダに ${name} として保存してください。保存したファイル名だけ最後に答えてください。
${prompt}" </dev/null)

[ -s "$dir/$name" ] || { echo "画像が保存されませんでした: $dir/$name"; exit 1; }
echo "保存しました: $dir/$name"
