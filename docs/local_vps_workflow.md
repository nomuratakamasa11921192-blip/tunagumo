# ローカル中心の開発とVPSの併用手順

2026-09-18、ユーザーの「ほぼローカル」「CodexとClaude Codeをローカルで使う」という最新方針に対応。
初回の準備は `docs/local_start.md` を参照。
共通ルールは `SYSTEM_PROMPT.md` §1.1。Codex・Claude Codeとも着手前に読む。

## 作業場所と担当

| 場所 | 主な作業 | 設定・注意点 |
|---|---|---|
| ローカルPCの「事業①/tunagumo」 | 日常開発、画面確認、関連テスト、文章・画像の実API検証、LINE・メールの実機確認 | PCに保存された開発用ENVを使う。絶対パス・OS・Dockerの動作はPC側で確認する。 |
| VPSの `/home/ubuntu/dev/tunagumo` | 既存の日次QA、必要時の全体テスト・継続作業 | 日次QAはmain専用。同じcloneで手動エージェントと日次QAを同時に動かさない。 |
| VPSの `/opt/tsunagumo` | 稼働中の本番 | 今回の開発・同期の対象外。本番反映には別途明示的な指示が必要。 |

VPSに接続した会話からPCのファイルは自動では読めない。PC側のCodex/Claude CodeはPCのcloneを開いて作業する。
「開いているファイル」が曖昧な場合、AIは自分の作業ディレクトリとブランチを確認し、他方のENVが見えると推測しない。

## PCの電源を切った後の定期実行

実行する場所で異なる。アプリから設定したことだけでは、実行場所は判断できない。

| 実行場所 | PCの電源オフ時 | アクセスするデータ |
|---|---|---|
| ローカルPC | ローカルの処理は動かない。PCとアプリの起動が必要 | PCのファイル・ENV・DB |
| Web/クラウド | PC側の電源に依存しない | アップロード済みの資料や接続先。PCのフォルダを直接操作するものではない |
| VPS | VPSで起動しているジョブはPCの電源に依存しない | VPSの開発用ファイル・ENV・DB |

[OpenAI公式の定期実行説明](https://learn.chatgpt.com/docs/automations?surface=app)（2026-09-18確認）。
普段の開発はローカル中心でよい。VPSには既存のDocker/PGroongaを使う日次QAを残す。
クラウド側で同等のテスト環境が動くことを確認できたら、定期処理の移行も選べる。
現時点では、ユーザーのルーティーンの実行場所やクラウドのDocker可否は未確認。定期処理の移行・二重登録は行っていない。

## GitHubで共有するもの

- 共有する：ソース、テスト、仕様、作業記録、コミット・ブランチ。
- 各環境で管理する：ENV、APIキー、DB、Dockerボリューム、生成ファイル、未コミットの変更、AIの会話履歴。
- `.env` と `.env.*` はGit対象外。テンプレートの `.env.example` 等には実キーを書かない。
- `git pull` だけではENVも会話も転送されない。実API確認は、その環境で必要な設定が揃っている場合に実行する。

## PC側の初回開始（PC側のAIが行う）

既存の「事業①/tunagumo」を開き、次を確認する。

```bash
git rev-parse --show-toplevel
git status --short --branch
git remote -v
```

`origin` が `nomuratakamasa11921192-blip/tunagumo` であることを確認する。
既存の変更があれば内容を確認して保持する。自動でreset・clean・stash・再cloneを行わない。
別リポジトリならそのままpullせず、既存ファイルを維持した別cloneへの移行方法を決める。
作業ツリーが空でmainにいる場合は `git pull --ff-only` で最新化する。

その後、`SYSTEM_PROMPT.md`、この資料、`docs/handoff_2026-09-17.md`、
`docs/release_review_2026-09-17.md` を読み、ENVは値を表示せず設定の有無を調べる。

## 両方で作業する場合のブランチ

- ローカルの手動開発：`local/<作業名>`。VPSの独立した手動開発：`vps/<作業名>`。
- VPSの日次QA用cloneはmainに固定する。同時に手動作業が必要なら、別clone/worktreeを使う。
- 独立したタスクは別ブランチに分ける。同じタスクを別端末へ引き継ぐ場合は、元の作業を停止してから引き継ぐ。
- 同じ作業ディレクトリでは、CodexとClaude Codeを順番に使う。役割は従来どおりCodexが日常開発、Claude Codeが必要時の確認・仕上げ。

開始例（作業ツリーが空であることを確認後。作業名は実際の内容へ置き換える）：

```bash
git fetch origin
git switch -c local/image-quality origin/main
```

完了時は変更したファイルを明示して追加し、`[Codex]` または `[Claude]` で始まるメッセージでコミットする。
初回pushは `git push -u origin HEAD`。作業途中でも他方に引き継ぐ必要がある場合は、未完了部分を明記した作業ブランチへ保存する。
秘密情報を含むファイルや、無関係な既存変更をコミットしない。

mainへ統合する前に最新の `origin/main` を作業ブランチへ取り込み、競合を解消して関連テストを行う。
AIはレビュー可能な差分・検証結果を用意し、通常作業のYes/No確認は繰り返さない。
pushが拒否されたらfetchして差分を確認し、force pushで上書きしない。
VPSの日次QAは従来どおりmainへ直接コミット・pushする。

## PCからVPS、VPSからPCへの引き継ぎ

元の環境で変更をコミット・pushし、次の情報を作業記録へ残す。

- 作業ブランチと最終コミット
- 完了した内容、次に行う作業
- テスト結果と実施した環境（実APIかフェイクかも区別）
- 必要なENVの変数名と保存先（値は書かない）

受け取る側は `git fetch origin` 後に該当ブランチを確認する。
初めて取得するブランチは、例えば `git switch --track origin/local/image-quality` で開く。
既存ブランチの場合は、変更を保持したうえで `git pull --ff-only` を使う。
日次QA用cloneで手動作業用ブランチへ切り替えない。VPSで同時に動くテスト同士も同じ開発DBを共有しうるため、順番に実行する。

## テスト

バックエンドの標準手順は `docs/handoff_2026-09-17.md` §4。PCにもDockerが必要。
両方の環境で開発用Composeを使い、次の指定を省略しない。

```bash
cd saas/docker
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml build api
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml up -d db
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml run --rm api alembic upgrade head
docker compose -p tunagumo-dev --env-file ../.env.test -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -v
```

`.env.test` が無い場合は `docs/local_start.md` の手順でテンプレートから作り、テスト専用の `TENANT_SECRET_KEY` を生成する。既存のファイルには上書きしない。
通常のpytestにはダミー設定を使い、実API用のENVを混ぜない。
PCのCPU/OSでDocker起動に問題があれば、その差を調査し、必要な検証をVPSに引き継ぐ。
日次QAは開始時にmain・作業ツリーに変更がないことを確認し、不一致ならpullやエージェント起動前に停止する。
これは開始時の検査であり、実行途中の手動操作を排他制御する仕組みではない。

## 現在の引き継ぎ先

- **PC側で進める**：手元のENVを用いた文章・画像の品質と実コスト確認、テスト用LINE・メールの設定確認。送信先を指定した実送受信や契約設定の照合は、それぞれの実行条件が揃ってから行う。
- **VPS側で完了済み**：バックエンド646件成功、不動産情報ライブラリの3 APIと画面用要約の実データ確認。
- VPSの不動産キーは `saas/.env.reinfolib.local` にのみ保存。ユーザーは再発行しない方針を明示済み。同じ確認を繰り返さない。
- 2026-09-18時点で、PCのファイル・Git状態・実API設定はこのVPSセッションから確認できていない。PCのセットアップ完了や、ENVの同期完了として報告しない。

PC側のCodex/Claude Codeへの開始メッセージ：

> このローカルのtunagumoを作業先にする。まずGitの状態を確認し、既存変更を保持してGitHubの最新変更を取得して。
> SYSTEM_PROMPT.md、docs/local_vps_workflow.md、docs/handoff_2026-09-17.mdを読んで続きを進めて。
> ENVはこのPCにあるものを値非表示で確認し、文章・画像の実API検証から始めて。
> VPSとは作業ブランチを分け、通常作業のYes/No確認は不要。本番には触れない。
