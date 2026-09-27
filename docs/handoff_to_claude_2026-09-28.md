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
