# サイトとInstagram画像の公開準備（2026-09-25）

## 反映候補

- サイトの検索結果・SNS共有用説明文に、映像の新規生成には別契約・別費用が必要なことを明記。
- 本文の確認・承認の説明を、文章や資料の利用と、設定済み窓口の自動一次返信を区別した表現へ修正。
- Instagram投稿準備用の画像3枚（サービス紹介・紹介文作成手順・ルームツアー）を公開URLへ配置。

対象は以下の4ファイルだけ。アプリ、DB、ENV、コンテナの更新・再起動は不要。SNS投稿は別の承認対象。

| ファイル | SHA-256 |
|---|---|
| website/index.html | 3edae82da292f02c5f11e1736572126c6b19dc5f9cf65aa1152c19cc2c0952d5 |
| website/assets/instagram/launch_20260923_01.jpg | d8b97d0f0d56be9d34d633aa0d42f438047dda9c497f946e2705e181df89037e |
| website/assets/instagram/launch_20260923_02.jpg | d04864aeec284a6a6e3ce17544b02aac402f8c2037b114b896194866373eba14 |
| website/assets/instagram/launch_20260923_03.jpg | 7f636fc3e1cbb41a361f8b0ce883980e370f138f452fdb56ffe1c3ceae61e143 |

## 実施済みの事前確認

既存配布tar.gzは4ファイルだけを含み、各ファイルの内容・サイズ・SHA-256がmanifestと現在の編集内容に一致。画像は3枚とも1080×1350、デコードと文字の表示を確認済み。
本番index.htmlのSHA-256は `498b4b1df6aa7b9112947b276d3fbb0b2f499c1f322e9fa6f3a0a83de616f061`。元のmain 5057063の内容と一致した。対象画像3枚は本番に未配置。
PCからのSCPはConnection closedで失敗したため、本番へファイルは転送していない。専用Git作業ブランチ `local/site-launch-20260925` に保存し、承認後にVPSの開発用環境でそのコミットを取得する経路を使用する。

## 承認後の反映手順

1. VPSの本番外の専用作業ディレクトリで承認対象コミットを取得し、上記4ファイルのハッシュを確認。
2. 本番index.htmlのハッシュが上記のままか、画像3枚が未配置のままか再確認。違っていたら上書き前に差分を確認。
3. 本番index.htmlを `/root/website-before-sns-<日時>.tar.gz` に保存。
4. 画像3枚を配置後、index.htmlを反映。各ファイルを一時名から置換し、読込途中のファイルを配信しない。
5. 公開4URLのHTTP応答と配信内容のハッシュ、サイトの表示を確認。
6. 画像URLをNotionの対応する3下書きへ追記し、「下書き」のまま保存・読み直し。承認や投稿は行わない。

取り消しは保存した旧index.htmlを戻し、追加した画像は別の退避ディレクトリへ移動して非公開に戻す。NotionのURLも変更前の値（今回は空欄）へ戻す。

## 投稿状況の再確認

Notionの全24行を読み取り、新規13件（X10・Instagram3）がすべて下書きであることを確認。承認済み0件、4チャネルのローカル送信待ちも0件。
Instagram3件の画像URLは未設定。公開後の更新対応表はPCのGit対象外 `saas/workspace/sns-launch-20260923/notion-image-update-plan-20260925.json` に用意した。
本書作成時点では本番反映・Notion更新・SNS送信を行っていない。

## VPS上の準備完了

`5e46d47f136052b5cdbf434191d9eeb5cbcd9379` を `/home/ubuntu/site-launch-review-20260925` に取得済み。本番外で4ファイルのハッシュを照合した。HTMLはWindowsのCRLFからGit管理のLFへ正規化されるため、上記ハッシュはLF版を記載。正規化済みの配布物とmanifestをPCのGit対象外ディレクトリに `website-sns-20260925.tar.gz` と `website-manifest-20260925.json` として用意した。本番への反映はまだ行っていない。
