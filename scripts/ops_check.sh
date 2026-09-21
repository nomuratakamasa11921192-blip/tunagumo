#!/bin/bash
# ===========================================================================
# ops_check.sh - 本番VPSの毎日の点検（異常時だけCodexが調べる）
#
# 2026-09-22作成。二段構えにしている:
#   1. 点検そのもの(check_vps_health.sh)はAIを使わない。Codexが利用上限で
#      止まっていても、点検と異常の通知は必ず行われる。
#   2. 異常があったときだけ、Windowsの通知を出したうえで、Codexに原因を
#      調べさせて報告を ops_check.log に残す。Codexは読むだけで、本番は
#      変更させない（直すかどうかは人が判断する）。
#
# 定期実行: Windowsタスク tsunagumo-ops-check（毎朝9:00。3時のバックアップ・
# 8時のディスク監視より後）
# ===========================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

LOG="ops_check.log"
stamp() { date "+%Y-%m-%d %H:%M"; }

result=$(bash scripts/check_vps_health.sh 2>&1)
status=$?
{
  echo "===== $(stamp) 点検 ====="
  echo "$result"
} >> "$LOG"

if [ $status -eq 0 ]; then
  exit 0
fi

# --- 異常あり -------------------------------------------------------------
summary=$(echo "$result" | grep "^\[NG\]" | head -3 | sed 's/^\[NG\] //' | tr '\n' ' ')

# Windowsの通知。Codexが動かなくても、異常に気付けるようにする。
powershell -NoProfile -Command "
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > \$null
  \$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  \$t = \$x.GetElementsByTagName('text')
  \$null = \$t.Item(0).AppendChild(\$x.CreateTextNode('ツナグモ本番に異常があります'))
  \$null = \$t.Item(1).AppendChild(\$x.CreateTextNode('$summary'))
  \$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\$app).Show([Windows.UI.Notifications.ToastNotification]::new(\$x))
" >/dev/null 2>&1

# Codexに原因を調べさせる（読むだけ・変更禁止）。上限等で失敗しても点検結果と通知は残っている。
report=$(codex exec -s danger-full-access "あなたはツナグモ本番VPSの点検担当です。作業ディレクトリはこのリポジトリのルート。
本日の自動点検で次の異常が見つかりました:

$result

SSH(ホスト名 tsunagumo-vps、ユーザー ubuntu)で原因を**調べるだけ**にしてください。
- 本番のファイル・設定・コンテナ・DBは一切変更しない（再起動・削除・書き換え禁止）
- sudo は使わない
- 手がかり: バックアップは root の crontab で毎日3:00に /opt/tsunagumo/saas/docker で bash ./backup_db.sh を実行し、ログは /var/log/tsunagumo_backup.log
- docs/security_review_2026-09-20.md、docs/handoff_to_codex_2026-09-22.md に経緯あり

最後に、日本語で次の3点を簡潔に書いてください: 1) 何が起きているか 2) 考えられる原因 3) 人がやるべき対処（コマンドがあれば示す）" 2>&1)
codex_status=$?

{
  echo "----- Codexの調査 (終了コード ${codex_status}) -----"
  echo "$report" | tail -40
  echo
} >> "$LOG"

exit 1
