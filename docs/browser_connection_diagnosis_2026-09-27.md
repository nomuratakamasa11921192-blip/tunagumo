# ブラウザー操作の接続障害の診断（Codex、2026-09-27）

## 確認した事実

- 11:38頃（日本時間）、ブラウザーAPIの `tabs.list()` が `Unable to load browser request-header policy` で失敗した。タブ一覧の取得前であり、受信サイトの画面操作には到達していない。
- 同時期にローカルPCから通常の証明書検証を有効にしたTLS接続を確認。app.tunagumo.com（Let's Encrypt YE1）、chatgpt.com（Google Trust Services WE1）、resend.com（Let's Encrypt YR2）は成功した。
- WinHTTPは直接接続で、プロキシ設定なし。ただし、これはブラウザー・アプリの全プロキシ経路やWebSocketの成功を証明しない。
- WindowsのChatGPTとChromeには9月25日から稼働しているプロセスが残っていた。画面を閉じた操作だけでは、全プロセスが再起動されたとは確認できない。
- Windowsパッケージ OpenAI.Codex は26.917.8451.0、Status=Ok。最新バージョンかどうかは確認していない。
- OpenAIの公開ステータスページは稼働中と表示。ただし個別のブラウザー操作障害がないことを保証するものではない。
- 公式ネットワーク案内にある desktop.chat.openai.com は、このPCのDNSで解決できなかった。今回の操作ツールがこのホストを使っているという証拠はなく、原因とは断定しない。

## 判断と未確定事項

以前の寮Wi-Fiで確認した証明書信頼エラーと、現在のブラウザー操作の接続情報取得エラーは別の観測事象。
現在は通常のHTTPS接続が成功しているため、寮Wi-Fiの証明書問題だけを原因として説明できない。
接続切替後の古いアプリ状態、ブラウザー拡張機能、接続情報を配信する経路のいずれが根本原因かは未確定。
規則的な再発間隔を示すログはなく、「一定時間が経つと切れる」とは断定できない。

操作ツールの内部コード・ブラウザープロファイル・Cookie・認証トークンの読み出し、証明書検証の無効化、セキュリティ設定の変更は行っていない。
アプリ固有の該当エラーログは、確認した標準ログ保存先では得られなかった。ログの広範な探索は行わない。

## 次回の復旧判定

1. 本人が未保存の作業を保存し、ChromeとChatGPT/Codexアプリを完全終了する。ウィンドウを閉じるだけでプロセスが残る場合は、タスクマネージャーの対象アプリから終了する。
2. 安定した同じ回線のままアプリとChromeを開き直す。
3. Codexがプロセス開始時刻を確認し、ブラウザーAPIでタブ一覧と対象ページを取得できることを確認する。単にエラーが出ないだけで復旧扱いにしない。
4. 既存のテスト返信を開いて録画する。追加メールは送信しない。
5. 完全終了後も同じエラーが出る場合は、この診断結果と再現時刻を添えて製品側へ問い合わせる。送信自体は本人の指示後に行う。

## 参照

- https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps
- https://status.openai.com/

アプリ再起動による恒久解決は未検証。受信画面の追加録画も未完了。

## 15時台の追加診断

- 同じ会話でChromeへのタブ作成と一覧取得が再び同じ接続ポリシーエラー。組み込みブラウザは `Browser is not available: iab`。
- 15:09 JSTのプロセス確認でもChrome・ChatGPT・codexに9/25開始プロセスが存在。本人へ完全終了後の再起動を依頼。根本原因とは断定しない。
- CLIの公式診断 `codex doctor --summary --no-color --ascii` は終了コード0。通信先HTTP到達・WebSocket HTTP 101が成功し、認証設定と状態DBは正常。21 ok・1 idle・6 notes・3 warn・0 fail、総合degraded。
- CLI 0.156.1に対して0.157.1、デスクトップに対してbuild 26.924.2738.0の更新が利用可能と表示。インストール済み `OpenAI.Codex` は26.917.8451.0 / Status Ok。
- 診断はデスクトップを「not running」と表示したが、プロセス一覧にはChatGPTが存在する。CLIの検出対象と現在のアプリが一致するかは不明なので、「アプリが動いていない」とは断定しない。
- Defender除外やDev Drive等の一般的な注意も出たが、本件との因果関係は未確認。セキュリティ設定・認証・MCP設定は変更しない。
- 更新による本件の解消は未検証。CLIの更新だけでブラウザが直るとは扱わない。
- `winget list --name Codex --disable-interactivity` は対象なし（終了コード1）。インストール済みAppxは確認できるが、wingetでの更新対象は得られなかった。アプリ更新・終了は未実施。

参照：[OpenAI公式・WindowsのCodex Doctor](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)。診断コマンドの存在と引数は、このPCの`codex --help`と`codex doctor --help`でも確認。

## 本人の再起動完了連絡後（16:14 JST以降）

- 本人の「やったよ」を受け、Chromeでアプリを開く操作を再試行したが、同じ接続ポリシー取得エラーでページ作成前に失敗。
- 操作ツールのセッションを初期化してから再試行しても、約31秒でタイムアウト。画面取得・追加録画・投稿は未実施。
- 16:14のプロセス一覧ではChrome 09/25 01:22、ChatGPT 09/25 01:29、codex 09/25 01:30開始のプロセスを観測。本人の再起動操作を否定する根拠とはせず、どの範囲が再起動されたかは未確定。
- OpenAI.Codexは26.917.8451.0 / Status Ok。本人へアプリ設定のComputer Use内でChromeがManage・接続要求・項目なしのどれかを質問中。
- 公式ブラウザ拡張手順を再確認。Manage表示・拡張があるプロファイル・アプリ更新を確認し、再起動で直らない場合は設定経由の拡張再接続、なお失敗する場合は/feedbackとチャットID付きサポート連絡が案内されている。連絡は未送信。

参照：[公式ブラウザ拡張の復旧手順](https://learn.chatgpt.com/docs/chrome-extension#troubleshooting)（9/27再確認）。

- 後続の `winget list --name ChatGPT --disable-interactivity` でMicrosoft Storeの `ChatGPT / 9PLM9XGG6VKS / 26.917.8451.0` を検出。「Codex名で対象なし」と「更新経路がない」を同一視しない。更新操作はまだ行っていない。
- 本人の16:17の画像はChromeの新しいタブ。アプリ側のComputer Use設定は映っていないため、タスクバーのChatGPTから設定を開きChromeの接続表示を確認する手順を案内した。画像だけで操作接続復旧とは扱わない。

## 16:22の設定画面と本人の訂正

- 本人の画像でGoogle Chrome「インストール済み」、デフォルトのウェブ閲覧・ダウンロード・アップロード「常に許可」を確認した。
- この状態の `cua.getState()` も `apps: []`, `browsers: []`、同じrequest-header-policyエラー。画面操作には到達していない。
- 本人は拡張機能の再インストールをすでに実施済みと明言した。Codexが同じ操作を再依頼したのは誤り。次回も未実施扱いして再依頼しない。
- `winget upgrade --id 9PLM9XGG6VKS --source msstore --disable-interactivity` は「利用可能なアップグレードが見つかりませんでした」で終了コード1。更新は適用されていない。先のdoctorの更新通知とStoreの配信状況が一致しない理由は未特定。
- 権限不足・SNS側障害・拡張未インストールとは断定しない。原因と確実な修復方法は未特定。通常の再起動・再インストールの反復を求めず、公式サポートへの診断情報提供を次の経路とする。問い合わせは未送信。

### 問い合わせ用本文（未送信）

WindowsのChatGPT/CodexからChromeを操作できません。2026年9月27日（JST）に再現しています。
Computer UseのGoogle Chromeは「インストール済み」、ウェブ閲覧は「常に許可」です。本人がアプリ・ブラウザ再起動と拡張再インストールを実施した後も失敗します。
`cua.getState()` はブラウザ一覧取得時に `Unable to load browser request-header policy. Retry the browser command.` を返し、`apps: []`, `browsers: []` となります。タブ作成もページが開く前に失敗します。
インストール済みOpenAI.Codexは26.917.8451.0です。doctorはデスクトップbuild 26.924.2738.0への更新を通知しましたが、Microsoft StoreのChatGPT（9PLM9XGG6VKS）をwingetで更新すると新しいパッケージなしでした。doctorのHTTP接続とWebSocket HTTP101は成功しました。
接続ポリシー取得失敗の調査と、この環境で利用できる正式な更新・修復方法をお願いします。
送信時はアプリの `/feedback` から会話IDを添付してください。Cookie・認証トークン等を添付する必要はありません。
