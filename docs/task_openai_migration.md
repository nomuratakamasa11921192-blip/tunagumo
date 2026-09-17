# タスク: SaaSのAI基盤をOpenAIに一本化する

> **2026-09-18 引き継ぎ注記**：以下は当初の移行計画。Part A/Bの実装後、文章生成はAnthropic/OpenAIを選べる構成に更新済み。画像はOpenAI、動画はHiggsfieldのまま。実APIによる文章・画像の品質/コスト確認が残る。完了済み実装のやり直しやAnthropic対応の削除は行わず、`docs/handoff_2026-09-17.md` と `docs/local_start.md` の最新状況から再開する。テストには本資料の旧Compose例ではなく、引き継ぎ資料の開発用Compose手順を使う。

## 背景・目的

現在このSaaSは、文章生成にAnthropic Claude、画像生成・編集にHiggsfield、
音声合成(TTS)・文字起こし(STT)・埋め込み(embeddings)にOpenAIを使っている。
契約先が3つに分かれていて、請求・APIキー管理・予算管理が煩雑になっている。

そこで**文章生成と画像処理をOpenAIに寄せ、AnthropicとHiggsfieldへの依存を無くす**。
これにより契約先が1社(OpenAI)になり、運用が大幅に単純化される。

本タスクは2部構成:
- **Part A**: 文章生成を Anthropic → OpenAI へ移行
- **Part B**: 画像生成・編集を Higgsfield → OpenAI Images API へ移行

Part A だけでも独立して価値があるので、**Part A を先に完成させてテストを通してから Part B に進む**こと。

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

## Part A: 文章生成の移行(こちらを先に完成させる)

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

**Part A がここまで完了し、テストが全て通ったら、一度コミットして人間に報告すること。**
その後 Part B に進む。

---

## Part B: 画像生成・編集の移行(Part A 完了後)

### 背景と費用負担の方針(2026-09-15決定)

費用負担は機能ごとに分ける:

| 機能 | 提供元 | 費用負担 |
|---|---|---|
| 文章生成・音声合成・文字起こし・埋め込み | OpenAI | **運営負担**(月額に含む) |
| 画像編集(曇り空→青空、バーチャルステージング) | **OpenAI へ移行** | **運営負担** |
| 動画生成 | **Higgsfield のまま維持** | **顧客負担**(顧客自身のAPIキー) |

**動画だけ顧客負担にする理由**: 1本あたりの単価が高くコストが読みにくいため、
運営が被るとプラン設計が破綻する。単価が十分下がったら運営負担への切り替えを再検討する。

**すでに実施済みの変更**(このタスクの前提。再度やる必要はない):
- `saas/src/api/deps.py` の `get_higgsfield_client` を、運営キーではなく
  **テナント自身のキー**(`tenant.higgsfield_api_key_id/_secret` を復号)を使うように戻した。
- `saas/src/api/routes/sessions.py` の Higgsfield 画像・動画生成後の `record_cost` 呼び出しを
  削除した(顧客が自分のキーで払うため、運営予算から二重に引かない)。
- 顧客がキーを登録するエンドポイント `PATCH /api/account/higgsfield-key` は元から存在する。

**このタスクでやること**:
- `generate_image()` / `edit_image()` を **OpenAI Images API に移行**する(運営負担なので
  運営キーを使う)。
- `generate_video()` は **Higgsfield のまま残す**(顧客負担)。移行しない。

**`edit_image()` について**: 現状 **「未検証機能」** とコメントされており、Higgsfield APIの
フィールド名を2通り推測して試す実装になっている(仕様が不明なまま書かれた)。つまり
**現時点で動作保証が無い**。OpenAIへ移行することで、公式ドキュメントのある確実なAPIになる。

### やること

1. **新規モジュール `saas/src/core/openai_image_client.py` を作る**
   - `generate_image(prompt, quality) -> str` と `edit_image(image_url, instruction) -> str`
     の2メソッドを、`HiggsfieldClient` と**同じ引数・同じ戻り値の型**で実装する。
     動画は含めない(Higgsfield側に残すため)。
   - `generate_image`: OpenAI の画像生成APIを使う。
   - `edit_image`: OpenAI の **`images.edit`** エンドポイントを使う。
     - 入力画像URLを取得してバイト列にし、プロンプトと共に送る。
     - マスクの扱い: 現状の `edit_image(image_url, instruction)` はマスクを受け取らない。
       まずはマスク無し(画像全体を対象)で実装し、「曇り空を青空に」「空室に家具を配置」が
       実用に足る品質で出るか検証する。品質が不十分な場合のみ、マスク対応の引数追加を
       人間に提案する(勝手にインターフェースを変えない)。
     - **注意**: OpenAI公式ドキュメントに「マスクはプロンプトベースのガイダンスであり、
       形状に完全に従うとは限らない」と明記されている。UIの注記
       「※画像編集は試験提供中の機能です。実際の内観と細部が異なる場合があります。」は
       そのまま維持すること。
   - エラー型・リトライの方針は `saas/src/agent/llm.py`(Part Aで書き換えたもの)に合わせる。

2. **`saas/src/api/deps.py` に画像用の依存関数を追加する**
   - `get_image_client`(名前は任意)を新設し、**運営のOpenAIキー**で
     `OpenAIImageClient` を返す。
   - `get_higgsfield_client` は**そのまま残す**(動画生成で顧客キーを使うため)。
   - `saas/src/api/routes/sessions.py` の画像生成・画像編集のエンドポイントを、
     新しい依存関数を使うように差し替える。**動画生成のエンドポイントは触らない**。

3. **画像のコストを運営予算に組み込む**
   - 画像はOpenAI(運営負担)になるので、`sessions.py` の画像生成・編集の後に
     `record_cost(tenant_row, <画像1枚あたりのコスト>)` を入れる。
     ※ Higgsfield時代の `record_cost` は顧客負担化に伴い削除済みなので、
       OpenAI移行後に改めて入れ直す形になる。
   - 画像の単価は**必ずOpenAI公式の料金ページで確認**し、推測で書かない。
     確認できなければ人間に報告して判断を仰ぐ。
   - `ai_budget.py` に `ESTIMATED_OPENAI_IMAGE_COST_USD` のような定数を新設する
     (既存の `ESTIMATED_HIGGSFIELD_*` は動画の参考値として残す。削除しない)。

4. **設定とUIの整理**
   - `Settings` の `higgsfield_api_key_id` / `higgsfield_api_key_secret`(運営キー)は
     **読まれなくなる**が、削除しないで残す。
   - `Tenant` の `higgsfield_api_key_id` / `_secret`(顧客キー)は**引き続き使う**。削除厳禁。
   - フロントエンドのHiggsfieldキー入力欄は**残す**。ただし説明文を
     「動画生成をご利用の場合に登録してください(動画の生成費用はお客様のHiggsfield
     アカウントに課金されます)」という趣旨に更新する。
     文面は `SYSTEM_PROMPT.md` §0.1 の顧客向け文章ルールに従うこと。

5. **テストを通す**
   - Part A と同様、`docker compose exec api pytest -v` が全て通る状態にする。
   - 既存の `tests/` でHiggsfieldをモックしている箇所のうち、**画像**に関するものは
     OpenAI方式に合わせて修正する。**動画**のモックはHiggsfieldのまま残す。
   - `deps.py` の変更(顧客キーを復号して使う形に戻した)により既存テストが落ちる場合は、
     テナントに暗号化済みキーを持たせるfixtureを用意して対応する
     (`tests/test_account.py` に `decrypt_secret` を使った既存パターンがあるので参考にすること)。

6. **実際に1枚、画像編集を試す**
   - 物件写真を想定した画像で「曇り空を青空にする」を実行し、出力を確認する。
   - 実用品質かどうかを人間に報告する(品質が不十分なら、その事実を正直に報告すること。
     動いたことにしない)。

---

## 完了したら

- `CHANGES.log` に `[Codex]` として変更内容を日本語で記録する。
- コミットして `origin/main` へpushする
  (これはGitHubの更新のみで、本番VPSへの反映ではない)。
  - Part A: `[Codex] SaaSの文章生成をOpenAIへ移行`
  - Part B: `[Codex] SaaSの画像生成・編集をOpenAIへ移行しHiggsfield依存を解消`
- 人間への報告として、以下をまとめる:
  - 変更したファイル一覧
  - 設定が必要な新しい環境変数(`OPENAI_MODEL` 等)と、推奨する値
  - 単価表に入れた価格(文章・画像とも)と、その根拠URL
  - 画像編集の実際の出力品質の所見
  - 積み残し・懸念点(あれば)
