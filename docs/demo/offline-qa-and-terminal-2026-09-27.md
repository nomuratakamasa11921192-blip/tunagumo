# ブラウザを使わないQA復旧と非表示起動（2026-09-27）

## 実施範囲

CodexがローカルPCで実施。ブラウザ、本番、外部メッセージ送信、SNS公開は操作していない。
作業ブランチは local/company-plan-20260925。既存の診断メモの未コミット変更を保持。

## 定期QAの停止原因と復旧

- 9/27 04:00の定期QAはGitHubへの443接続失敗で、テスト開始前に終了コード1で停止していた。
- 今回はGitHubへの照会とfetchが成功。QA専用clone C:/Users/user/事業①/tunagumo-qa はmain・作業ツリーがクリーンであることを確認し、d41fe5fからorigin/mainのa5e7346へfast-forwardした。
- タスクと同じGit BashでDocker context desktop-linux、Compose v5.4.0、Python、Codexの実行ファイル解決を確認。
- 開発用Compose -p tunagumo-dev、saas/.env.test、docker-compose.test.ymlでビルド、DB更新、全体pytestと定期QA回帰を実行。ログは artifacts/offline-qa-20260927/verify-qa.log。
- 定期ジョブのテスト工程の手動検証。Codexの自動修復・自動保存・Slack通知を含むジョブ全体は起動していない。定刻の正常完走は未確認。
- QAのmainと機能修正済み作業ブランチは別。過去の作業ブランチ695件成功を今回のmainの結果と混同しない。

## ターミナル表示対策

定期QAと運用点検のタスクは、対話ユーザーでbash.exeを直接起動していた。表示が発生し得る構成だが、質問ごとの大量表示との因果関係は未確定。

scripts/run_scheduled_hidden.py を追加。PythonのGUI版 pythonw.exe から子プロセスを CREATE_NO_WINDOW で起動するよう、この2タスクのActionsのみ変更した。

- インストール先: C:/Users/user/.local/share/tsunagumo/run_scheduled_hidden.py
- ログ: C:/Users/user/.local/share/tsunagumo/logs/タスク名.log
- 元の実行コマンド、作業ディレクトリ、環境変数指定を保持。子プロセスの終了コードをそのまま返す。
- トリガー、Settings、PrincipalsのXMLが変更されていないことを照合済み。
- 元のタスクXML: artifacts/offline-qa-20260927/tsunagumo-daily-qa.before.xml と tsunagumo-ops-check.before.xml。
- 日本語・空白付きパス、標準出力/標準エラー、終了コード7、実行ファイル不存在、ログ追記を3件のテストで検証。
- 実機でpythonw→Git Bashの終了コード7とログ保存を確認。
- 非表示コマンド実行前後で、WindowsTerminal 1件、OpenConsole 2件、pwsh 2件のPIDが不変。画面上のタブ数やCodex内部タブを確認した意味ではない。
- 子プロセスが明示的に新しいウィンドウを開く場合やCodex内のターミナル表示は、このラッパーだけでは制御しない。

次回予定はQA 9/28 04:00、運用点検9/28 09:00（JST）。SNS投稿4件、Notion同期、InstagramチェックはDisabledを維持。

## 検証方法と切り戻し

`python -m unittest discover -s scripts/tests -p test_scheduled_hidden.py -v`

元に戻す場合は、保存したbefore.xmlのActions/ExecにあるCommand、Arguments、WorkingDirectoryからNew-ScheduledTaskActionを作成し、該当タスクだけSet-ScheduledTask -Actionで戻す。投稿系の有効化やタスク全体の再登録は不要。

## 最終結果

- QA用main a5e7346: ビルド・DB更新・687件成功、失敗0。警告2067件は残る。
- 同じGit Bash/開発Composeから定期QA回帰29件成功。工程全体の終了コード0。
- 非表示起動の単体検証3件成功。配置したランチャーとGit管理ソースのSHA-256一致。
- QA専用cloneの作業ツリーはクリーン。定刻実行のLastTaskResultは未更新（9/27の失敗1のまま）であり、今回の手動検証を定刻成功として扱わない。
