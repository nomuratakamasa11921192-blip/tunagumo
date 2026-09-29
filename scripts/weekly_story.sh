#!/usr/bin/env bash
# 毎週「開発の裏側・高校生で起業した話」の下書きを1本作り、Notionへ「下書き」で送る(2026-09-29、本人依頼)。
# 機能・案内の投稿が一通り終わるまでは作らない(Xの投稿待ちが3本以上ならスキップ)。
# 公開は本人がNotionで「承認済」にした後、既存の定期タスクが行う。このスクリプト自体は投稿しない。
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
pending=$(ls sales/queue/x/*.txt 2>/dev/null | wc -l)
if [ "$pending" -ge 3 ]; then
  echo "[weekly_story] Xの投稿待ちが${pending}本あるため、今週は見送ります。"
  exit 0
fi
week=$(date +%Y-%m-%d)
out="sales/drafts/x/${week}_story.txt"
if [ -e "$out" ]; then echo "[weekly_story] 作成済み: $out"; exit 0; fi
mkdir -p sales/drafts/x
claude -p "あなたはツナグモ(不動産会社向けAIサービス)の広報担当です。
今週の『開発の裏側・高校生で起業した話』のX投稿の下書きを1本だけ作り、ファイル ${out} に本文だけを書いてください(ファイル以外は変更しない、git操作もしない)。

材料: CHANGES.log の直近7日分、docs/ の直近の記録。実際にあった出来事・改善だけを使う。
条件:
- 運営者は埼玉の高校3年生。一人称は「私」。親しみやすく誠実に、誇張しない。
- 顧客名・個人情報・売上・契約数・未確認の数字は書かない。技術用語は言い換える。
- 本文は全角140字以内。最後に「詳しくはプロフィールのリンクから」。URLとハッシュタグは入れない。
- SYSTEM_PROMPT.md の顧客向け文章ルールに従う。" --model "${CLAUDE_SCHEDULED_MODEL:-opus}" --allowedTools Read Grep Glob Write >/dev/null 2>&1
if [ ! -s "$out" ]; then echo "[weekly_story] 下書きを作れませんでした"; exit 1; fi
python scripts/notion_sync.py --push && echo "[weekly_story] Notionへ下書きを送りました: $out"
