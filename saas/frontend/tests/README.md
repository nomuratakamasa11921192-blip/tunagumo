# フロントエンドの画面遷移テスト

リポジトリのルートから実行します。テストはChromiumで実際のHTMLを開き、通信の応答を意図的に遅らせ、画面移動後に入力内容が消えないことを検証します。HTTP通信はすべてテスト内で差し替えるため、APIキー・DB・開発サーバーは不要です。

```bash
python -m pip install -r saas/frontend/tests/requirements.txt
python -m playwright install chromium
python -m unittest discover -s saas/frontend/tests -v
```

LinuxではChromiumの実行に必要な共有ライブラリも必要です。バックエンドの `cd saas && pytest -v` は従来の `tests/` のみを実行し、ブラウザテストは上記コマンドで実行します。
