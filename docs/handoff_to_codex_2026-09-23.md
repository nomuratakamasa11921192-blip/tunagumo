# Codexへの引き継ぎ（2026-09-23、Claude）

前回の引き継ぎ `handoff_to_codex_2026-09-22.md`（SNS投稿→営業）はそのまま有効。
この文書はHiggsfieldの追加分だけを書く。Codexの利用上限は 2026-09-23 21:58 に解除される。

## Higgsfield API：実際に生成してみた結果（2026-09-23）

**前提（訂正）**：画像の生成・編集は 2026-09-15 にOpenAIへ移行済み（`openai_image_client.py`）。
アプリがHiggsfieldを使うのは**動画生成だけ**（`sessions.py` の `generate_video`）。
下の画像・画像編集の行は、使われていない古い処理を試しただけなので、直す必要はない。

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

## 動画生成の修正（同日、Claudeが完了）

公式ドキュメント（docs.higgsfield.ai/docs/models）で接続先を確認し、`higgsfield_client.py` の
`generate_video` を次のように切り替えた。費用は顧客負担なので、安いモデルを選んでいる。

| 条件 | モデル（パス） | 長さ | 目安 |
|---|---|---|---|
| 物件写真あり・通常 | `/kling-video/v2.5-turbo/standard/image-to-video` | 5秒 | 約$0.21 |
| 物件写真なし・通常 | `/minimax/hailuo-2.3/standard/text-to-video` | 6秒 | 約$0.28 |
| 高画質（写真あり／なし） | `/kling-video/v2.5-turbo/pro/image-to-video`・`.../pro/text-to-video` | 5秒 | Pro単価 |

- 写真URLを受け付けない場合（422など）は、同じ画質の文章から作る動画で作り直す
- Seedance 2.0（約$0.93/秒）は高いため使わない。単価は公開記事の値。正確な単価はコンソールで確認する
- テスト667件成功。実生成も2本成功（写真なし83秒、写真あり137秒）。結果は
  `saas/workspace/vps-import-20260918/private/higgsfield-video-test-20260923.json`
- **2026-09-23 16:05頃 本番反映済み**（下記）

## SaaSの文章生成をGPT-6 Solへ（同日、ユーザー指示。いったんLunaにした後Solへ変更）

- `config/*.yaml` の単価表に `gpt-6-sol`（入力$2・出力$10・キャッシュ読込$0.2・書込$2.5）と `gpt-6-luna`（入力$0.10・出力$0.50）を追加（OpenAI公式）
- `.env.example` を `LLM_PROVIDER=openai`、`LLM_MODEL`・`LLM_MODEL_LIGHT`・`OPENAI_MODEL`・`OPENAI_MODEL_LIGHT` を `gpt-6-sol` に変更
- **本番反映済み**（下記）

## PCでつまずいた点

- 生成後の状況確認のURL（`status_url`）は `platform.higgsfield.ai` を返す。
  PCの安全制限（通信先の制限）で届かなかったため、テストでは `api.higgsfield.ai/requests/{ID}/status`
  へ向け直した。本番サーバーでは問題にならないので、アプリのコードは変えていない
- Dockerが停止していると、キーの取り出し（`pg_restore`）が失敗する。先にDocker Desktopを起動する

## Codexのモデル（同日、ユーザー指示）

Codexの既定モデルは `gpt-6-astra`（プロジェクトとユーザーの `config.toml`）。いったんSolにしたが、Astraに戻した。

## 本番反映（2026-09-23、ユーザー承認・ユーザーがrootで実行）

`scripts/deploy_20260923.sh` を `sudo` で実行。反映コード `5d12187`。api・schedulerのみ作り直し、dbは維持。
確認結果: 稼働中の文章生成モデル `gpt-6-sol gpt-6-sol`、Klingのコードを反映済み、直近2分のERROR/Traceback 0件、
app.tunagumo.com/index.html と tunagumo.com が200。
退避先は `/root/tsunagumo-release-20260923`（更新前コード・ENV・DBバックアップ）。切り戻し用イメージは
`tsunagumo-rollback-api:20260923` と `tsunagumo-rollback-scheduler:20260923`。
