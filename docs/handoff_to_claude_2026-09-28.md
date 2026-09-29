# Claude Codeへの引き継ぎ（2026-09-28）

## 作業場所と開始

- C:/Users/user/事業①/tunagumo-company-plan-20260925
- branch: local/company-plan-20260925
- 実装の最新コミット: 8ac70e7（この引き継ぎ文書のコミットが後続する）。originへpush済み。
- Codexと同じ作業先で同時に編集しない。ユーザーがClaudeへ切り替えた後に開始する。
- CLAUDE.md、SYSTEM_PROMPT.md、必須引き継ぎを先に読む。現在のコード・既存変更を保持。
- 既存の未コミット docs/browser_connection_diagnosis_2026-09-27.md、未追跡 artifacts/ と %SystemDrive%/ を勝手に消さない。
- 全CLIはウィンドウを表示しない起動方式を使う。バックグラウンド補助プロセスも非表示。

## 現在の残件（実施順の目安）

1. Claude in Chrome接続を確認する。このPCのClaude Codeは2.1.280。claude --chromeで起動し、/chromeで状態確認。Claude用拡張・Anthropicプランの本人ログインは未確認。最初にapp.tunagumo.comを実際に開き、表示・クリックが通るか確認。Codexで失敗した事実だけからClaudeでも成功すると断定しない。
2. SRT時刻繰り上がり修正（8ac70e7）をリリース用に確認し、明示的な本番反映指示を得た範囲だけ反映。9/27承認済み11ファイルのリリースは完了済みで、新修正の承認と混同しない。
3. ルームツアー再生成と字幕/音声の最終確認。更新した画面から資料検索・リール・ルームツアー・PDFの生成〜結果確認・再撮影。既存素材を操作録画と誤認させない。
4. 既存メール返信の原文受信画面を録画。自動翻訳が出典・署名を欠落させるため原文表示を確認。追加のメール送信は不要。
5. 顧客LINEの本人ログイン、本人userId/会社設定確認、対象を限定した実送受信。営業LINE @787dbfal の既存Webhookを上書きしない。外部送信の対象・内容の許可を別途確認。
6. InstagramのX/YouTube/LINEプロフィールリンクをスマホで追加・保存確認（未確認）。
7. 各動画・投稿文を本人が確認して承認。その後に対象アカウントと媒体別投稿条件を確認して公開。現在は未承認・未公開、投稿4タスク/Notion同期/Instagram確認は停止維持。X動画のAPI認証、Instagram用公開動画URL等は既存記録を実状態と照合する。
8. 作業ブランチの差分をレビューしmainへ統合する。現時点でorigin/mainと同一ではなく、開発修正・投稿保護等の差分が残る。統合後に適切な全体検証を実施。本番反映とは別。
9. CodexのGitウィンドウ点滅が--no-daemon再開後に解消するか確認。Claudeで作業する間はCodexを終了してよい。通知設定は元に復元済み、features.daemon_auto_start=false。起動中のCodexへの反映と根本解消は未確認。

## 音声の仕組み（2026-09-28 コード照合済み）

- ナレーション: saas/src/video/tts.py がOpenAI /v1/audio/speechをHTTPで呼ぶ。model gpt-4o-mini-tts、voice alloy、出力MP3。
- 字幕の音声認識: saas/src/video/stt.py がOpenAI /v1/audio/transcriptionsを呼ぶ。whisper-1、日本語指定・固有名詞ヒント付き、出力SRT。
- 動画合成: 既存PythonとFFmpeg。開発担当がClaudeになっても同じ仕組みを操作可能。
- 現在の依存注入 saas/src/api/deps.py の get_tts_provider/get_stt_provider は settings.openai_api_key（運営側）を使う。tts.py等の古い「顧客キー」コメントは現状と違うので誤解しない。
- ChatGPTアプリの音声会話を録音したものではない。Claude Codeの契約だけでOpenAI API利用を置き換えるものでもない。既存API接続・利用料が継続する。秘密値はチャットに貼らず、ENV/DBを無断で移さない。
- Claude自身の音声聴感評価能力はこのPCでは未検証。ffprobe/ffmpegでの技術点検と実際に聞いた評価を区別し、最終試聴は本人も行う。

## 完了済みと注意

- 9/27に本人承認済み11ファイルを本番へ反映、ソース一致・公開200・起動ログ確認済み。バックアップ等は docs/demo/fixes-and-release-2026-09-27.md。
- 本番で実AI生成した字幕ツナグモ・24.021秒の物件紹介動画を保存済み。画面からの更新後の一連操作の検証は未完了。
- 9/28定期QAは04:00開始〜04:30終了コード0。全体687件・回帰29件と保存工程成功。定期QAの完走は残件ではない。
- 自動QAは現在もCodexを実行する設定。手動作業をClaudeへ移すだけでは定期処理は移行しない。全面移管が必要なら別途ジョブ設計・モデル設定・利用条件の確認が必要。現状は勝手に変更しない。
- QA専用cloneはC:/Users/user/事業①/tunagumo-qaのmain。手動作業と同じ開発Dockerプロジェクトを使うのでテストを同時実行しない。
- 最新の局所検証：QA修正取込前後5件ずつ成功。字幕修正前6件、修正後14件成功。最新全体695件/画面24件は9/27の字幕時刻修正前の結果。最新コードの全体検証は次の統合・リリース時に行う。
- サイト画像3枚・Stripeキー等の古い未完了メモを、そのまま現在の残件へ戻さない。完了記録を優先し、外部サービスの現在の認証・残高は必要時に確認する。

## 成果物

- artifacts/offline-finish-20260928/review-package.zip：既存動画2本・SNS別原稿・検査結果。未承認。字幕時刻修正後の再生成版ではない。
- artifacts/fixes-20260927/social-ready/index.html：2本の確認ページ。
- docs/demo/social-captions-2026-09-27.md：元の6機能の文面。
- docs/demo/offline-finish-2026-09-28.md：最新の字幕修正・端末診断。
- docs/demo/browser-resume-2026-09-27.md：ブラウザ再開順と写真選択失敗の注意。

公式Claude Chrome連携: https://code.claude.com/docs/en/chrome

## 2026-09-28 Claude実施分

- Claude in Chromeは接続成功。本番アプリで架空物件の写真4枚からルームツアーを生成し、寝室・キッチンの映像に「リビング」等の字幕が出る不具合を確認（写真は均等割り、字幕は文字数比で別々に切替していた）。
- 修正ffea4e8：台本を写真1枚=1シーンで依頼し、シーンの時間割から写真ごとの秒数を決める。あいさつ・締めが別シーンなら最初・最後の写真へまとめる。全709件成功。
- 本番反映：`scripts/deploy_roomtour_align_20260928.py`（字幕時刻修正8ac70e7も含む2ファイル）。退避先 `/home/ubuntu/deploy-roomtour-20260928/backup-20260928_175046`、切戻しタグ `tsunagumo-roomtour-rollback-{api,scheduler}:20260928_175046`。本番と最新mainの実行コード差分はこれで解消（mail_scan.pyは改行コードのみの差）。
- VPSの `/home/ubuntu/tsunagumo-release-stage-20260919/.git` はroot所有でubuntuからpull不可。
- Higgsfield（MCP/Web月額）は9/28時点で `free`・0クレジット。Codexが9/24に月額クレジットを使い切った後、プランが無料に戻っている。
- 反映後の本番で再生成（25秒指定）：`ダウンロード/room_tour (2).mp4`、24.9秒・1280×720・音声あり。1秒ごとの全コマでリビング→寝室→キッチン→洗面所の字幕と写真が一致。残りは本人の試聴と画面録画。

## 2026-09-28 夜 Claude実施分（本人「全て許可する」）

- 本番反映4件（すべて `scripts/deploy_files.py` + `scripts/make_release.py`、事前ハッシュ照合・自動切戻し、ENV/DB不変、ERROR 0件）：
  1. ffea4e8 ルームツアーの写真切替を字幕に同期（+字幕時刻修正8ac70e7）
  2. 2c976d7 品質検査に部署が参照した社内資料本文を渡す（正しいRAG回答が「原文未照合」で差し戻し上限になっていた）
  3. e87e377 マイソクPDFの題名を本文のキャッチコピーに（承認画面用見出し「…：承認依頼」が出ていた）、補正後画像をPDFに使用
  4. a356f3f 社内資料の意味検索にコサイン距離0.5の上限（本番実測：関係あり0.33〜0.43、無関係0.51〜0.64）。無関係な資料の出典を書かされる/出典不足で差し戻される問題を解消
- 本番で確認済み：ルームツアー（字幕一致）、社内資料検索（品質検査合格）、返信文案、物件紹介文、画像生成・補正（明るさ200→217、形状維持）、マイソクPDF、CSV（UTF-8 BOM、13項目）。追客文・オーナー報告は修正前の結果で、修正後は検索が無関係資料を拾わないことをサーバー内で確認（画面での再生成は拡張切断で未実施）。
- SNS：YouTubeリンク順（トライアル→HP→X→Instagram）とXの紹介文を変更済み。Xの非公開解除はClaude Codeの安全判定で拒否されたため本人操作待ち。Instagramリンク・Threads作成はMetaの「エラーが発生しました」で本人操作待ち。
- 画像生成：`scripts/codex_image.sh`（Codex CLI経由、ChatGPT月額枠）。
- 本人操作待ち：Xの「ポストを非公開にする」解除／Instagramリンク（ツナグモのHP・LINE追加）／Threads作成／ルームツアー試聴と画面録画／投稿12本（撮影セット/投稿動画_確認用・投稿原稿）の承認／顧客LINEのログインと送受信試験／メール原文画面の録画／委託契約・データ処理条件の確認。

## 2026-09-29 未明 Claude実施分（本人「全部許可する」）

- 本番反映5件目：5d18ca8 お客様向け応答（メール・Webチャット・LINE共通）から社内用の出典document_idを除去。メール自動返信本文に「出典：document_id …」が載っていたため。
- メール実送受信：Resendで `[TSUNAGUMO-TEST] 架空物件の設備確認 20260929-01` を本人Gmail宛てに1通（Idempotency-Key付き、送信ID 01a0e894-1bf6-755f-a89e-7e535428e4ea）。自動返信は出典IDなし・署名あり。原文表示（翻訳なし）の受信画面を `artifacts/mail-demo-20260929/メール自動返信_原文表示_20260929.jpg` に保存。ChromeのGIF録画機能はこのタブで動作せず、連続録画は未作成。
- プライバシーポリシー：外国の委託先への提供（米国、米国の制度、OpenAI API学習不使用・最長30日保存、Anthropic法人条件の学習不使用）を追記し本番反映（4d4710c、公開ページSHA-256一致、旧版は /home/ubuntu/deploy-privacy-20260929/privacy.html.bak）。法務の最終確認は専門家推奨。
- 顧客LINEの本人限定試験：LINE Developers・LINE公式アカウント管理画面とも本人ログインが必要。本人ログイン後に、本人userIdの確認 → 試験用会社へのチャネル登録 → Worker試験用2設定 → 本人スマホから `[TSUNAGUMO-TEST]` 付きで送信、の順（手順は docs/customer_channel_test_2026-09-26.md）。
- 追記（9/29 01:50）：Xの「投稿を保護する」を解除（本人指示）、プロフィールの鍵表示消失を確認。
- ルームツアー操作デモ：ChromeのGIF録画が使えないため、ffmpeg gdigrab（画面1366×768、Chrome最大化）で実操作を録画。写真選択は録画前、文字入力・長さ25秒・作成ボタンは録画中に実行。サーバー生成は約1分、低速回線でブラウザへの受け取りに約10分。録画44秒＋つなぎ3秒＋完成動画20.7秒＝67.7秒を `撮影セット/動画/ルームツアー_操作デモ_20260929.mp4`（ダウンロードにも複製）。完成動画は字幕と写真一致を確認。
- 顧客LINE試験：LINE Developersで本人userIdを確認。試験用会社 db30da56（ツナグモ・不動産）に@787dbfalのチャネル情報を暗号化登録（読み戻し一致）。Workerに SAAS_TEST_TENANT_ID / SAAS_TEST_LINE_USER_ID を設定（新版6f8f9afa、切戻し元e3a576c0）。Webhook送り先・有効状態は不変、無効署名401、予約ページ200。本人スマホから `[TSUNAGUMO-TEST]` 付きの送信待ち。試験後は2設定を削除する。
- 追記（9/29 15:20）：LINE本人試験が無応答だった原因はWorkerの `redirect:"error"`（Cloudflare非対応）。manualに修正・反映（8971159）。本人の `[TSUNAGUMO-TEST] ツナグモハイツ203号室に宅配ボックスはありますか？` に資料ベースのAI回答がLINE返信200で届いたことを確認。試験後にWorkerの試験用2設定を削除。
- 表記統一（9ba78ee）：顧客に見える表記をtunagumoへ（試験件名 `[TUNAGUMO-TEST]`、外部User-Agent、API名、ウィジェットのログ、サイトのCSS名）。DBのメール件名条件1件も更新。アプリ7ファイル・Worker（版581727e1）・サイトindexを反映。互換性のため維持：ログイン情報の保存名（tsunagumo_api_key等）、Stripeメタデータ名、/opt/tsunagumo、Worker名とURL、Composeのプロジェクト名、定期タスク名。
- ローカルの作業フォルダ名は本人判断で `事業①/tsunagumo` のまま（改名しない）。
