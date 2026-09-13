# タスク: SaaSのLLM基盤をAnthropic(Claude)からOpenAIへ移行する

## 背景・目的

現在このSaaSは、文章生成の中核にAnthropic Claudeを使い、音声合成(TTS)・文字起こし(STT)・
埋め込み(embeddings)にOpenAIを使い、画像生成・編集にHiggsfieldを使っている。
契約先が3つに分かれていて、請求・APIキー管理・予算管理が煩雑になっている。

そこで**文章生成をOpenAIに寄せて、AnthropicとHiggsfieldへの依存を減らす**ことを目指す。
これにより、OpenAI 1社への一本化に近づける(Higgsfieldの扱いは本タスクの範囲外。別途判断する)。

## 絶対に守ること

- `/opt/tsunagumo` 配下(本番)には一切触れない(`SYSTEM_PROMPT.md` §6)。
- 秘密情報(APIキー)をコードやconfigに直書きしない。必ず環境変数から読む(`SYSTEM_PROMPT.md` §0.2)。
- 既存のビジネスロジックを壊していないか、必ず `docker compose exec api pytest -v` で確認する。
- 人間が意図的に未完成にしている箇所(TODOコメント、ダミー関数)を「バグ」と誤認して
  削除・書き換えしない(`SYSTEM_PROMPT.md` §5)。
- 変更内容は `CHANGES.log` に `[Codex]` として日本語で記録する。

## 現状の構造(調査済み)

### 中核
- `saas/src/agent/llm.py` — `StructuredLLM` クラス。Anthropic SDK(`AsyncAnthropic`)に直結。
  - `call_structured()`: Anthropicの **tool_use** を使って構造化出力を得ている
    (`tools=[{name, description, input_schema}]` + `tool_choice={"type":"tool","name":...}`)。
  - `generate_text()`: 通常のテキスト生成。
  - `_extract_usage()`: `response.usage` から input/output/cache_write/cache_read のトークン数を取得。
  - リトライ: `_RETRYABLE_ERRORS`(429/5xx/接続)は指数バックオフ、
    `_NO_RETRY_ERRORS`(401/400系)は即 `LLMFatalError`。
  - 構造化出力がスキーマ違反の場合は `MAX_VALIDATION_RETRIES` 回だけ別途リトライ。

### 設定
- `saas/config/*.yaml`(real_estate / recruiting / legal / web_agency)
  - `pricing.models` に `"${ANTHROPIC_MODEL}"` `"${ANTHROPIC_MODEL_LIGHT}"` をキーとした単価表
    (input/output/cache_write/cache_read の per_mtok)。
  - 各部署の `model:` `triage_model:` も同じ環境変数を参照している。
- `saas/src/core/config.py` — `Settings` に `anthropic_api_key` 等。
- コスト計算は `saas/src/agent/cost.py` の `compute_cost_usd`。

### Anthropicに依存しているファイル(22件)
`saas/src/` 配下を `grep -rl "anthropic\|Anthropic\|ANTHROPIC"` で洗い出すこと。
主なもの: `agent/llm.py`, `agent/nodes.py`, `api/deps.py`, `core/config.py`,
`core/models.py`, `core/healthcheck.py`, `api/routes/chat.py`, `agent/deadline_scan.py` 等。

### 既にOpenAIを使っている箇所(参考にできる)
`saas/src/rag/embeddings.py`, `saas/src/video/tts.py`, `saas/src/video/stt.py`
— OpenAIクライアントの初期化・エラーハンドリングの書き方はここを踏襲すると一貫性が保てる。

## やること

### 1. `StructuredLLM` をOpenAI対応にする

`saas/src/agent/llm.py` を書き換える。設計方針:

- **構造化出力**: Anthropicの `tool_use` に相当するものとして、OpenAIの
  **Structured Outputs**(`response_format` に JSON Schema を渡す方式)を使う。
  Pydanticモデルからスキーマを渡し、返ってきたJSONを `output_model.model_validate()` で検証する
  流れは現状と同じに保つ。
  ※ OpenAIのStructured Outputsはスキーマに厳格なので、`MAX_VALIDATION_RETRIES` の
    リトライは残しつつ、実際には発火しにくくなるはず。
- **エラー型**: `openai.RateLimitError` / `APIConnectionError` / `APITimeoutError` / `InternalServerError`
  をリトライ対象、`AuthenticationError` / `PermissionDeniedError` / `BadRequestError` /
  `NotFoundError` / `ConflictError` / `UnprocessableEntityError` をリトライ不可とする。
  `LLMOutputError` / `LLMFatalError` の外部インターフェースは**変えない**(呼び出し元を壊さないため)。
- **usage抽出**: OpenAIは `response.usage.prompt_tokens` / `completion_tokens`、
  キャッシュは `prompt_tokens_details.cached_tokens`。
  `_extract_usage()` の**返り値のキー名(`input_tokens`/`output_tokens`/`cache_write_tokens`/
  `cache_read_tokens`)は変えない**。`cost.py` が依存しているため。
  OpenAIにはAnthropicのような「キャッシュ書き込み」課金が無いので、`cache_write_tokens` は 0 とし、
  その旨をコメントで明記する。
- `StructuredLLM.__init__` が受け取るクライアント型を `AsyncOpenAI` に変える。
  テストがフェイクを注入している箇所(`tests/conftest.py` 等)も併せて修正する。

### 2. 設定・環境変数を移行する

- `saas/src/core/config.py`: `openai_api_key` は既にあるはずなので確認し、
  文章生成にもこれを使う。`anthropic_api_key` は**削除せず残す**
  (将来切り戻す可能性・ヘルスチェック用)。
- 新しい環境変数名を決める: `OPENAI_MODEL` / `OPENAI_MODEL_LIGHT`
  (既存の `ANTHROPIC_MODEL` / `ANTHROPIC_MODEL_LIGHT` に対応)。
  `saas/.env.example` と `saas/docker/.env.production.template` を更新する。
- `saas/config/*.yaml` 4ファイルの `${ANTHROPIC_MODEL}` → `${OPENAI_MODEL}`、
  `${ANTHROPIC_MODEL_LIGHT}` → `${OPENAI_MODEL_LIGHT}` に置換する
  (`pricing.models` のキーと、各部署の `model:` / `triage_model:` の両方)。

### 3. 単価表を更新する

`saas/config/*.yaml` の `pricing.models` を、**実際のOpenAI公式価格ページで確認した値**に
更新する。`updated_at` も確認日に更新し、参照したURLをコメントに残す(現状のコメントと同じ形式)。

**重要**: 価格は推測で書かない。必ずWeb検索で公式の料金ページを確認し、
確認できなければその旨を報告して人間の判断を仰ぐこと。

### 4. テストを通す

`saas/docker` 配下で `docker compose exec api pytest -v` を実行し、全テストが通る状態にする。
モックの注入経路(`app.state.llm`、`app.dependency_overrides`)がOpenAI方式でも
同じように機能することを確認する。失敗したら原因を特定して修正し、100%通過するまで繰り返す
(最低5回試み、それでも直らなければ人間に報告して停止する)。

### 5. 動作確認

テストが通ったら、実際に1セッション分の文章生成を走らせて、
- 出力の日本語が破綻していないか
- `Session.cost_usd` に妥当な値が記録されるか
を確認する。`SYSTEM_PROMPT.md` §0.1 の顧客向け文章ルール(「絶対」「必ず」「100%」を使わない等)に
沿った出力が維持されているかも併せて見ること。

## 完了したら

- `CHANGES.log` に `[Codex]` として変更内容を日本語で記録する。
- `[Codex] SaaSのLLM基盤をOpenAIへ移行` のメッセージでコミットし、`origin/main` へpushする
  (これはGitHubの更新のみで、本番VPSへの反映ではない)。
- 人間への報告として、以下をまとめる:
  - 変更したファイル一覧
  - 設定が必要な新しい環境変数(`OPENAI_MODEL` 等)と、推奨する値
  - 単価表に入れた価格と、その根拠URL
  - 積み残し・懸念点(あれば)
