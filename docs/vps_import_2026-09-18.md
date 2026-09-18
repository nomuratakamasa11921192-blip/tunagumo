# VPSからPCへの回収状況（2026-09-18）

ユーザーの「VPSで作業した内容全てこっちに持ってきて」という指示に対応する。
**Gitに保存済みの履歴はPCにあるが、VPS専用ファイルの回収は未完了。**
過去の監査は `vps_work_audit_2026-09-18.md`、実施済みの本番反映は `release_2026-09-18.md` を参照する。

## PCで確認・保存したもの

- `git fetch origin` 後、PC作業ブランチと `origin/main` はともに `8d1ef9f`。双方の未統合コミットは0件。
- VPSからGitHubへ保存されたコード、テスト、仕様、CHANGES.log、引き継ぎ記録を保持。
- PC内の `saas/workspace/vps-import-20260918/repository.bundle` に取得済みの全参照・完全なGit履歴を保存し、`git bundle verify` 成功。
- 同じフォルダに、ファイルのSHA-256一覧と、ENVの変数名・設定有無だけの一覧を保存。秘密値は記録していない。このフォルダはGit対象外。
- 既存の `.claude/scheduled_tasks.lock` の削除は変更せず保持。

## 既存の商品・アカウント設定

新規登録を前提にせず、以下の既存設定を引き継ぐ。

| 項目 | 既存設定 |
|---|---|
| 営業LINE | `website/trial.html` の `https://line.me/R/ti/p/%40787dbfal` |
| 営業ボット | `line-bot/wrangler.toml` の Worker `tsunagumo-line-bot` |
| ライト | `price_1UGHevGj5LbAzw5NEfkaCyXF`、月39,800円 |
| スタンダード | `price_1UGHgqGj5LbAzw5NHgFjODed`、月98,000円 |
| プレミアム | `price_1UGHgBGj5LbAzw5NM3fIrpUc`、月198,000円 |

スタンダードの価格IDは、前工程で閲覧したStripeの商品画面と一致する。
ブラウザーの既存アカウントを照合先として進められるため、アカウント選択の回答だけを待つ必要はない。
有効な認証情報と決済設定の確認は未完了。PCの別アカウントのキーを流用しない。
指定された通知先はPC内の回収記録に保存した。本番への設定反映はまだ行っていない。

## 接続後に回収するもの

| 対象 | 保存先・確認方法 | 現状 |
|---|---|---|
| VPS開発cloneの未コミット変更・未push履歴 | `/home/ubuntu/dev/tunagumo` の状態を読み取り、PCの履歴と照合 | 未回収 |
| VPSの別作業clone・リリース用スクリプト | `/home/ubuntu/releases`、`/root/tsunagumo-release-20260918` | 未回収 |
| 開発用設定 | 開発cloneのENVなど。既存PC設定を上書きせず、Gitへ入れずに扱う | 未回収 |
| 日次QA・cron・本番反映の詳細ログ | 実行結果・設定の確認後、秘密情報を含まない形でPCへ保存 | 未回収 |
| プロジェクトに関するAI作業履歴・生成物 | VPS上の所在と対象範囲を確認してから回収 | 未回収 |

SSHの22番・2222番は今回もタイムアウト。さくら管理画面は会員ログイン画面で止まっている。
ユーザーに再ログインを依頼済み。接続できていない範囲を「回収済み」と扱わない。
今回、本番のファイル・DB・サービスは変更していない。
