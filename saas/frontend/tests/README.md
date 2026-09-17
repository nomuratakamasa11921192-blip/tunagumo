# フロントエンドの画面遷移テスト

リポジトリのルートから実行します。テストはChromiumで実際のHTMLを開き、通信の応答を意図的に遅らせ、画面移動後に入力内容が消えないことを検証します。HTTP通信はすべてテスト内で差し替えるため、APIキー・DB・開発サーバーは不要です。

```bash
python -m pip install -r saas/frontend/tests/requirements.txt
python -m playwright install chromium
python -m unittest discover -s saas/frontend/tests -v
```

LinuxではChromiumの実行に必要な共有ライブラリも必要です。バックエンドの `cd saas && pytest -v` は従来の `tests/` のみを実行し、ブラウザテストは上記コマンドで実行します。

既存のChromiumを使う場合は `PLAYWRIGHT_CHROMIUM_EXECUTABLE` に実行ファイルの絶対パスを指定できます。
未指定なら従来どおりPlaywright付属版を使います。任意の版との互換性は保証されないため、
既存版で確認したときはブラウザのバージョンを作業記録に残してください。
PowerShellでは `$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE = 'C:\path\to\chrome-headless-shell.exe'` のように設定します。
