# 定期実行モデルと本番点検（2026-09-25）

ユーザーの「順に進めたい」「定期実行はGPT6sol」に対応。

## 定期実行

- 日次QA: `tsunagumo-daily-qa`、毎日04:00 JST、専用clone `C:\Users\user\事業①\tunagumo-qa` のmainで実行。初回予定は2026-09-26 04:00。
- 本番点検: `tsunagumo-ops-check`、毎日09:00 JST。通常点検はAI不要、異常時の読み取り専用調査だけCodexを使う。
- Windowsの両タスクに `CODEX_SCHEDULED_MODEL=gpt-6-sol` を明示。スクリプトも同変数を読み、未設定時は同モデルを使う。QAの修復・監査・保存・再試行、および異常調査のすべてが対象。
- 対話作業の `.codex/config.toml` のAstra設定は維持。
- PCでユーザーがログオンしていること、ネットワーク、Codex認証、QAにはDocker Desktopの稼働が必要。PC電源オフ中は実行されない。
- 投稿・Notion同期・旧Instagramチェックは停止を維持。投稿内容の承認と画像公開準備が済んでから、別工程で再開する。
- タスク変更前のXMLは `%TEMP%\tsunagumo-schedule-20260925\` に保存。

## Windowsで判明した実行不具合

Git Bashの `python3` はMicrosoft Storeの別名に解決され、終了コード1だった。
シェル回帰テストは開発用ComposeのAPIコンテナで実行するよう変更。必要なテストとシェル2ファイルだけを読み取り専用でマウントし、`MSYS_NO_PATHCONV=1` でWindows側のパス変換を防止する。
Slack本文のJSON化にはWindowsでは実体のある `python`、Linuxでは `python3` を使う。

変更前24件成功。変更後29件成功。外部API・SSH・AI・Slackを偽物に置き換えた回帰テストであり、夜間QAの本実行や実AI生成の成功を意味しない。

## 本番の読み取り専用点検

2026-09-25 19時台JSTに `scripts/check_vps_health.sh` とSSHで確認。

- 公開サイトとアプリはHTTP 200、API・scheduler・DBの3コンテナはrunning、ディスク使用率26%。
- 最新の暗号化DBバックアップは `tsunagumo_20260923_155853.sql.gz.enc`、確認時点で約51時間経過。点検はこの鮮度違反で終了コード1。
- cronサービスはactive。ubuntuのcrontabには旧AIジョブのコメントだけがある。
- `/var/log/tsunagumo_backup.log` は存在せず、閲覧できたcron journalにも9/23以降のバックアップ実行記録がない。
- rootのcrontabは `sudo -n crontab -l` がパスワード要求で失敗し、未確認。「登録がない」とはまだ断定できない。
- 本番のファイル、コンテナ、設定、DBは変更していない。

### 次の確認（VPSのrootコンソールで読むだけ）

```bash
date -Is
crontab -l
ls -l /var/log/tsunagumo_backup.log
journalctl -u cron --since '2026-09-23 00:00' --no-pager | grep -E 'backup_db|tsunagumo|tunagumo' | tail -n 30
```

登録があれば時刻・作業ディレクトリ・ログ出力先を確認し、失敗ログから対処を決める。登録がなければ既存crontabを保存したうえで復旧案を作る。
本番の変更は `SYSTEM_PROMPT.md` §6に従い、具体的な対象と戻し方を提示してから明示的な指示を受ける。

## この後の順序

1. バックアップの実行停止原因を確定し、承認された対処後に新しい暗号化ファイルと次回定期実行を確認。
2. 追加デモ撮影・動作確認。
3. SNS投稿候補の確認、公開用画像の配置とNotionのURL整備、承認済み投稿だけの配信再開。
4. Instagram相互リンクはスマホアプリで設定。
