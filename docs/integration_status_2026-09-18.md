# 既存連携の確認（2026-09-18、Codex）

VPSのPC移行と並行して、既存のアカウント・商品設定を再確認した。
本番反映済みの内容は `release_2026-09-18.md`、データ移行は `vps_import_2026-09-18.md` を参照する。

## 現在の確認結果

| 対象 | 実確認した結果 | 残る作業 |
|---|---|---|
| 営業LINE | 既存アカウント `@787dbfal` の認証成功。Webhook有効、LINEの接続テストもHTTP200・success=true | 顧客のLINEメッセージ実送信は未実施 |
| 予約・営業管理 | 既存Workerの予約枠APIがHTTP200、79枠を返却。管理画面の認証もHTTP200 | 新規予約・契約作成は未実施 |
| Stripe | 稼働中Workerの3価格IDがリポジトリと一致。既存商品を利用する方針を維持 | 対象アカウントの有効なAPIキー、決済疎通、請求ポータル設定の確認 |
| 通知メール | Resendで `tunagumo.com` がverified | 指定済みの管理者通知先を本番へ反映。既存の顧客宛通知への影響を確認して送信元を設定 |
| Higgsfieldコネクター | 認証成功、Plusプラン・クレジット残高を確認 | アプリ内の別APIによる実生成は未確認 |
| SaaSのLINE・受信メール | 前回の本番検証で登録0件 | 営業LINEの既存Webhookを流用・変更せず、利用するテナントと窓口を設定 |

LINEの検証リクエストはWebhook接続確認用で、顧客へのメッセージを送っていない。
予約取得はPython標準User-Agentでは403だったが、検証用途を示すUser-Agentで正常に取得できた。
秘密値や顧客情報は出力せず、PC内のGit対象外の回収フォルダに検証結果を保存した。

## Stripeの継続条件

- 商品を持つ照合対象は `acct_1TsIT4Gj5LbAzw5N`。PCの既存APIキーは別アカウントのため、そのまま本番に流用しない。
- VPSのプロジェクト作業履歴338ファイルを調べたが、対象アカウントの既存キーは回収できなかった。
- Chromeの既存商品画面はセッション期限切れ。再ログインを依頼済み。
- 新規商品・アカウント・契約は作成していない。

## HiggsfieldのPC側修正

公式の[生成結果取得仕様](https://docs.higgsfield.ai/docs/api-reference/requests/get-request-status)と実装を照合し、画像の `images[].url` と動画の `video.url` が読めない不具合を修正した。
`nsfw` と `canceled` も終了状態として扱う。
[公式の再試行方針](https://docs.higgsfield.ai/docs/concepts/errors)を踏まえ、任意パラメータを外して再送するのは生成受付時のHTTP400/422に限定し、認証・残高・モデル不在・サーバーエラーや、生成受付後の状態取得失敗で新しい生成を重複送信しない。

- 修正前の既存テスト：8件成功。
- 不具合を再現する追加テスト：修正前は10件失敗。
- 修正後：クライアント18件と動画上限・利用額計上5件の計23件成功、警告34件。
- 開発専用Compose `-p tunagumo-dev` とテスト用ENVで実行。外部生成APIへの送信・クレジット消費なし。
- この修正はPC上のみ。本番には未反映。モデル別の未検証パラメータ・画像編集エンドポイントが残るため、実生成の成功は断定しない。

[Higgsfield公式のAPI説明](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-the-higgsfield-api)では、Webサイトの契約とAPI利用は別の請求枠である。コネクターの認証成功だけで、SaaS内APIも契約済みプランの範囲で使えるとは判断しない。
