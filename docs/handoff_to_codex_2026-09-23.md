# Codexへの引き継ぎ（2026-09-23、Claude）

前回の引き継ぎ `handoff_to_codex_2026-09-22.md`（SNS投稿→営業）はそのまま有効。
この文書はHiggsfieldの追加分だけを書く。Codexの利用上限は 2026-09-23 21:58 に解除される。

## Higgsfield API：実際に生成してみた結果（2026-09-23）

ユーザーがAPI用の米ドル残高をチャージしたので、本人テナントの保存済みキーで実際に生成を試した。
キーはこれまでと同じく、回収した暗号化DBからメモリー内で復号しただけで、画面やファイルには出していない。
結果は `saas/workspace/vps-import-20260918/private/higgsfield-live-test-20260923.json`（Git対象外）。

| 機能 | 今のコードのパス | 結果 |
|---|---|---|
| 画像生成 | `/higgsfield-ai/soul/v2/standard` | **成功**（47秒、画像URLを取得） |
| 動画生成 | `/bytedance/seedance/v1/lite/text-to-video` | **失敗** `model_not_found`（新APIに存在しない） |
| 画像編集 | `/bytedance/seedream/v5/lite/edit`（推測値） | **失敗** `model_not_found` |

画像は2回分（1回目は下記のPC側の通信制限で結果を受け取れなかった）、残高を使った。動画・編集は受付前に拒否されたので残高は使っていない。

### 空の依頼を送ってモデルの有無を確かめる方法（残高を使わない）

中身が空の依頼を送ると、存在しないモデルは404 `model_not_found`、存在するモデルは400/422で
「必須の項目がない」と返る。生成は始まらないので残高は減らない。
スクリプト: `saas/workspace/vps-import-20260918/probe_higgsfield_models.py <候補パスを1行1件で書いたファイル>`

存在を確認できたパス：

- `/bytedance/seedance-2.0/text-to-video`・`/bytedance/seedance-2.5/text-to-video`（必須: `prompt`）
- `/bytedance/seedance-2.0/image-to-video`・`/bytedance/seedance-2.5/image-to-video`（必須: `image_url`）
- `/bytedance/seedance-2.5/reference-to-video`（必須: `audio_urls`。今回の用途では使わない）
- Soulのパスは `/higgsfield-ai/soul/v2/{mode}` の形で、modeは `standard` だけが通る（編集用のmodeはない）

試してすべて404だったもの：Kling・Wan・MiniMax・LTX・PixVerseを思いつく命名で書いたもの、
Seedream・Nano Banana・Qwen・Grok・Recraftの編集系。推測で探すのは効率が悪いので途中でやめた。

## Codexがやること

1. **公式のモデル一覧で正しいパスを確認する。** https://console.higgsfield.ai の
   モデル一覧（モデルごとのAPIドキュメント）を見る。今日はこのPCのDNSが不安定で、
   `docs.higgsfield.ai`・`github.com` の名前解決に失敗して読めなかった
2. **動画のモデルを選び直す。** Seedance 2.0は約$0.93/秒と高い（5秒で約$4.7）。
   安いモデル（Kling 2.5は約$0.042/秒と紹介されている）の正しいパスを確認して既定にする。
   `start_image` は新APIでは `image_url` という項目名になっている（Seedanceの場合）
3. **画像編集（バーチャルステージング）のモデルを確定させる。** 見つからない場合は、
   お客様の画面で「現在ご利用いただけません」と表示されるか確認する
4. 直したら空の依頼で存在を確かめ、そのあと1回だけ実際に生成する（`live_test_higgsfield.py`）。
   `ai_budget.py` の目安額（画像$0.25・動画$2.0）が実際の単価と大きくずれていないかも見る
5. 本番への反映は、通常どおりユーザーの承認を得てから行う

## PCでつまずいた点

- 生成後の状況確認のURL（`status_url`）は `platform.higgsfield.ai` を返す。
  PCの安全制限（通信先の制限）で届かなかったため、テストでは `api.higgsfield.ai/requests/{ID}/status`
  へ向け直した。本番サーバーでは問題にならないので、アプリのコードは変えていない
- Dockerが停止していると、キーの取り出し（`pg_restore`）が失敗する。先にDocker Desktopを起動する
