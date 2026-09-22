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

## DBバックアップは一度も動いていなかった（2026-09-22に修正）

VPSのcronは `ubuntu` ユーザーで次の理由により実行できておらず、**本番DBの自動バックアップは
ゼロだった**（手動で取ったデプロイ時のバックアップのみ）。ディスク監視も同様。

1. ログの出力先 `/var/log` に書けず、シェルがコマンド自体を実行しない
2. 保存先 `backups/` がroot所有フォルダ内で作れない
3. 暗号化パスフレーズのある `saas/.env` がroot専用で読めない
4. `backup_db.sh` に実行権限が無い（Windowsからのコミットで落ちていた）

対処:
- バックアップとディスク監視を **root の crontab** へ移し、`bash ./backup_db.sh` の形で呼ぶ
  （実行権限に左右されない）。`ubuntu` 側の動かない登録は削除（バックアップ: `/home/ubuntu/crontab.bak-*`）
- 手動で1回実行し、暗号化済み45,824バイトのバックアップ作成を確認
- Gitでシェルスクリプト全てに実行権限を付与

## 毎日の点検（新設、有効）

`tsunagumo-ops-check`（このPC、毎朝9:00）。**点検はAIを使わない** `scripts/check_vps_health.sh`
で、最新バックアップの鮮度・サイズ・暗号化、ディスク使用率、コンテナ3つ、公開ページ2つを確認する。
異常時のみWindows通知を出し、Codexに**読むだけの**原因調査をさせて `ops_check.log` に残す。
Codexが利用上限で止まっても、点検と通知は動く。

## 止めていないもの（意図的）

- **VPSのcron: DBバックアップ(3:00)とディスク監視(8:00)**。本番の安全装置でありAIも使わないため。

## 途中だった作業

- Notion連携: `NOTION_TOKEN` 設定済み、DB共有済み、「チャネル」列と状態「投稿待ち」を追加済み。
  ローカル下書き4件（X3・LINE1）をNotionへ「下書き」として作成済み（未承認）。
- X: APIキー4つ設定済み・認証確認済み（投稿元は個人アカウント。名義を営業用へ変更予定）。
- YouTube: 再認証は不要だった。トークンは youtube.upload を持ち、更新も成功。
  以前の「スコープ不足で403」は、確認に channels.list（読み取り権限が必要）を使ったための誤判定。
- X APIは無料プランのため、自分のアカウント確認と投稿は可能だが、他ユーザーの参照は402になる。
- キーの設定は `python scripts/set_env_from_clipboard.py <名前>` を使うと、値を記録に残さずに済む。

仕組みの詳細は `docs/auto_post_2026-09-21.md`。

## 投稿の担当（2026-09-22 ユーザー決定）

**投稿の定期実行はCodexが担当する。** Claudeの投稿系タスク（tsunagumo-post-* / tsunagumo-notion-sync）は
停止のまま再開しない。Codexは次のどちらかで回すこと。

- 既存の仕組みを使う: `python scripts/notion_sync.py`（Notionの承認済を取り込む）→
  `python scripts/auto_post.py --channel <x|instagram|youtube|line>`（1件投稿）。どちらもAI不要。
- Codex自身の定期実行から上記を呼ぶ。

承認はNotion『ツナグモ Instagram投稿予定』の「状態」で行う（下書き→承認済）。
承認されていないものは auto_post.py が投稿を拒否する。

## Codexが次に始めること（2026-09-22 ユーザー決定）

### 1. SNS投稿（先にやる）
営業の連絡を受けた人はまずアカウントを見に来るため、**DM・フォーム営業より先に10投稿ほど置く**。
- X（@tunagumo_com、名前「ツナグモ」）: 1日1〜2投稿。Instagram: 週2〜3投稿
- 打ち出しは「不動産特化のAI社長」。文面は `docs/sns_profiles_2026-09-22.md` の方針に合わせる
- 流れ: Notion『ツナグモ Instagram投稿予定』に「下書き」で作る → ユーザーが「承認済」 →
  `python scripts/notion_sync.py` → `python scripts/auto_post.py --channel <x|instagram|youtube|line>`
- Notionの「チャネル」列（x/instagram/youtube/line）を必ず入れる。無いとInstagram扱いになる

### 2. 営業（投稿の後）
リストは Notion『ツナグモ 不動産営業リード』（639社、全件「連絡状況=未」）。
DM文面は 2026-09-22 にサイトの価格（月39,800円〜）へ修正済み（`scripts/fix_lead_dm_prices.py`、
元の文面は `saas/workspace/lead_dm_backup/`）。
- **Instagram DM が主**（リストはInstagramアカウント確認済みの会社。Xアカウントは未調査）
- **問い合わせフォームも併用**（会社のメールに届くため読まれやすい）。公式サイトは「情報源URL」列
- 送信は**人が行う**。フォームの大量自動送信はしない（スパム行為になる）
- 「営業目的のお問い合わせはご遠慮ください」等の記載がある会社には送らない
- InstagramのDMは1日10〜20件程度から（短時間の大量送信は送信制限を受けやすい）
- 送ったら「連絡状況」を「連絡済み」に更新する

### 守ること
- **事実でないことを書かない。** 2026-09-22、Claudeが書いた下書きに「うちの会社では平均40分」
  「知り合いの不動産屋さんの一言」という作り話が入っていた（未公開のうちに修正済み）。
  数字・エピソードは、ユーザーから聞いたものか、確認できるものだけにする
- 価格はサイト（ライト39,800円／スタンダード98,000円／プレミアム198,000円）と必ず一致させる
- 動画制作代行（¥15,000〜）はサイトに載っていないので案内しない
- `SYSTEM_PROMPT.md` の禁止表現（絶対・必ず・100%・国内完結・誰でも使えます）を使わない

### 注意
- Codexは 2026-09-23 21:58 まで利用上限に当たっている（それまで実行できない）
