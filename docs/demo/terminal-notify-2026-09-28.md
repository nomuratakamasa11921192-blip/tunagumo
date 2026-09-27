# チャットごとのターミナル表示への対策（2026-09-28）

## 確認したこと

CodexがWindows側のプロセスと設定を読み取り。ブラウザ操作やターミナルの可視起動は行っていない。
Windows Terminal 1件、OpenConsole 2件、pwsh 2件のPIDは前回から不変。新規タブ、既存ウィンドウの前面化、Codex内の表示のどれかはユーザー回答待ちで、原因は未確定。

ユーザーの ~/.codex/config.toml の notify は、Computer Useの codex-computer-use.exe turn-ended を直接指定していた。この実行ファイルのPE Subsystemは3（Windows CUI）。公式のnotify仕様ではターン完了時に外部コマンドを実行するため、表示原因の候補として起動経路を修正。

公式仕様: https://learn.chatgpt.com/docs/config-file/config-advanced#notifications

## 適用した変更

- config.tomlのnotifyだけを変更。元の実行ファイルとturn-ended引数は維持し、既存の非表示ランチャーをpythonw.exe経由で使用。
- ランチャー: C:/Users/user/.local/share/tsunagumo/run_scheduled_hidden.py
- --workdir . により元の作業ディレクトリを維持。Codexが末尾へ追加する通知JSONも元のプログラムへそのまま渡す。
- ログ: C:/Users/user/.local/share/tsunagumo/logs/codex-turn-notify.log。通知本文・引数は記録せず、開始/終了コードのみを記録。
- 設定バックアップ: C:/Users/user/.codex/config.before-hidden-notify-20260928.toml。秘密情報を含む可能性があるためGitへ追加しない。
- 戻す場合はバックアップのnotify行だけを現在の設定へ戻す。他の設定を上書きしない。
- 通知プログラムそのものの手動起動や改変は行っていない。

## 検証と限界

- TOML解析でnotify以外が同一、元のコマンド配列が保存されていることを確認。
- 合成の通知JSONを使って、同じpythonw→ランチャー経由の子プロセスでGetConsoleWindow()==0、日本語・引用符を含むJSONの受け渡し、終了コード0を確認。
- Codex 0.157.1のfeatures listが設定を読み込んで終了コード0。
- 次のチャットで実際の通知経路が新設定を使用し、表示が再発しないかは未確認。稼働中セッションの設定再読み込み時期も未確認。原因確定・完全解消として報告しない。
- 子プロセス自身の明示的な画面操作、Codex内ターミナル欄の表示は、この対策の制御対象外。

## 今朝の定期QAは完走を確認

- タスク: tsunagumo-daily-qa、9/28 04:00 JST開始、LastTaskResult=0。
- 非表示ランチャー: 04:00:02〜04:30:43 JST、Exit code 0。
- QAログ: 最終全体687件成功、定期回帰29件成功、正常終了マーカー、日本語の保存完了追記を確認。
- 保存先: QA専用mainの9c8daa2。変更はCHANGES.logとsaas/tests/test_video_quota_route.py。本番反映なし。
- 9/28 09:00の運用点検は確認時刻の06:30頃には未実行。SNS投稿4件、Notion同期、Instagram確認は停止のまま。

## 残件

1. 次のチャット送信でターミナル表示の再発確認。
2. ブラウザ連携復旧後、修正後機能の画面生成・確認・再撮影。
3. 既存メール返信の原文受信画面録画。
4. 顧客LINEの本人ログイン・本人限定送受信テスト。
5. Instagramのスマホでのプロフィールリンク追加・保存確認。
6. 動画・投稿文の本人確認と承認、対象アカウント/投稿条件の確認後のSNS公開。

## ユーザー録画による訂正

後続の録画で、チャット開始直後にGitのコンソールウィンドウが繰り返し表示されることを確認。このページのnotify対策は症状に合わず、notify設定だけ元へ復元した。features.daemon_auto_start=false と --no-daemon での同じ会話の再開を準備。現行プロセスの切替・再発確認は未完了。詳細は offline-finish-2026-09-28.md。
