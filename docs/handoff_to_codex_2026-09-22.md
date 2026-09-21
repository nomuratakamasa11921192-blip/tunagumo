# Codexへの引き継ぎ（2026-09-22、Claude）

ユーザーの指示「今やってるの全部止めて。Codexでやることにした」により、Claudeが動かしていた
定期実行をすべて**停止（削除ではなく無効化）**した。再開はCodex側で判断すること。

## 停止したもの

### このPCのタスクスケジューラ（すべて Disabled、登録は残っている）

| タスク | 内容 | AI |
|---|---|---|
| tsunagumo-daily-qa | 日次QA（tunagumo-qaクローンでdaily_qa.sh） | Codex |
| tsunagumo-instagram-check | 旧・Notion承認→Buffer投稿 | Codex |
| tsunagumo-notion-sync | Notion「承認済」→ sales/queue へ取り込み | 不使用 |
| tsunagumo-post-x / -instagram / -line / -youtube | sales/queue の1件を投稿 | 不使用 |

再開: `Enable-ScheduledTask -TaskName <タスク名>`

### Claudeのクラウドルーティン（claude.ai、無効化）

- ツナグモ 不動産営業リード発掘（0〜7時JST毎時、Notion「ツナグモ 不動産営業リード」へ追加）
- ツナグモ ローカル自動化ジョブ再設定リマインド（毎週木曜）

管理画面: https://claude.ai/code/routines

## 止めていないもの（意図的）

- **VPSのcron: DBバックアップ(3:00)とディスク監視(8:00)**。本番の安全装置でありAIも使わないため。

## 途中だった作業

- Notion連携: `NOTION_TOKEN` 設定済み、DB共有済み、「チャネル」列と状態「投稿待ち」を追加済み。
  ローカル下書き4件（X3・LINE1）をNotionへ「下書き」として作成済み（未承認）。
- X: APIキー4つ未設定。YouTube: トークンのスコープ不足で要再認証。
- キーの設定は `python scripts/set_env_from_clipboard.py <名前>` を使うと、値を記録に残さずに済む。

仕組みの詳細は `docs/auto_post_2026-09-21.md`。
