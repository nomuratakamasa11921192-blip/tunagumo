# ツナグモ ファイル一覧と使い方

**最初にこれを読む。** どのファイルを、いつ、どう使うかの索引。

---

# 1. セットアップ（最初に一度だけ）

## フォルダを作る

```bash
mkdir -p ~/tsunagumo/docs
mkdir -p ~/tsunagumo/website
mkdir -p ~/tsunagumo/saas
mkdir -p ~/tsunagumo/private
```

## ファイルを配置する

```
~/tsunagumo/
├── CLAUDE.md                    ← CLAUDE_common.md をリネームして配置
│
├── docs/                        ← Claude Codeに読ませる仕様書
│   ├── website_spec.md
│   ├── ai_org_spec_master.md
│   ├── prompt_library_by_industry.md
│   └── reference_impl.md
│
├── website/                     ← サイトの作業フォルダ
│   └── CLAUDE.md                ← CLAUDE_website.md をリネームして配置
│
├── saas/                        ← SaaS開発の作業フォルダ
│   └── （CLAUDE.md は Phase 1 着手時に作る）
│
└── private/                     ← 人間が読むもの。Claude Codeには見せない
    ├── tsunagumo_playbook.md
    └── ai_business_domains.md
```

**`CLAUDE_common.md` → `~/tsunagumo/CLAUDE.md` にリネーム**
**`CLAUDE_website.md` → `~/tsunagumo/website/CLAUDE.md` にリネーム**

**ファイル名は必ず大文字の `CLAUDE.md`。** 小文字だと読まれない環境がある。

## 動作確認

```bash
cd ~/tsunagumo/website
claude
```

起動したら打つ：

```
今読み込んでいるCLAUDE.mdの内容を要約して教えて
```

**共通とサイト用の両方が返れば成功。**

---

# 2. ファイル一覧

## 毎日見るもの

### `private/tsunagumo_playbook.md`（1,004行）

**事業設計書。** 迷ったらここに戻る。

| 章 | いつ読む |
|---|---|
| 早見表 | **毎日** |
| ビジョン | 迷ったとき |
| 営業（DM → トライアル → 課金） | **毎日** |
| 優位性・弱点・克服策 | 商談前・不安なとき |
| 懸念点と改善案 | 不安なとき |

**Claude Codeには渡さない。** 人間が読むもの。

---

## Claude Codeに渡すもの

### `docs/website_spec.md`（571行）

**サイト改修の指示書。** 全部渡してよい。

```
../docs/website_spec.md を読んでください。
既存のデザイントーンを維持したまま、まず /trial ページを作成してください。
```

### `docs/ai_org_spec_master.md`（2,610行）

**SaaSの技術仕様。**

**一度に全部渡さない。** この単位で渡す：

```
【1回目】「【重要】この文書の読み替え」＋「共通ルール」＋「Phase 1」
　↓ テストが通る
【2回目】「共通ルール」＋「Phase 2」
　↓
（以降、Phase 6まで繰り返す）
```

**MVPの範囲：** Phase 1〜6 ＋ Phase 10（テナント分離）＋ 管理者画面

### `docs/prompt_library_by_industry.md`（536行）

**業種configの中身。** 4業種分のプロンプトと法令制約。

**使うとき：**
```
../docs/prompt_library_by_industry.md の不動産の部分を、
config/real_estate.yaml として実装してください。
```

### `docs/reference_impl.md`（336行）

**拡張機能の実装骨格。MVPでは不要。**

v2〜v3で Phase 11〜14 を作るときだけ開く。
（ツール実行／外部トリガー／多段承認／定期実行）

**この4つは自由に書かせると事故る**（二重送信・二重起動）ので、骨格を指定する。

---

## 迷ったときに開くもの

### `private/ai_business_domains.md`（589行）

**50領域以上の事業性分析。**

新しい事業アイデアが浮かんだとき、**末尾の「5つの確認」を通せば5分で判断できる。**

```
1. 今の事業で、何件納品した？ → 0件なら判断材料がない
2. その領域に予算はあるか？ 決裁は早いか？
3. 3つ目の変数（高校生／春日部／既存スキル）を掛けられるか？
4. B2CかB2Bか？ → B2Cは1万人、B2Bは10〜20社
5. 今のSaaSで対応できないか？ → できるなら新規開発は不要
```

**同じ検討を繰り返さないための記録。**

---

# 3. 今日やること

```
【1】フォルダを作って、ファイルを配置
【2】cd ~/tsunagumo/website && claude
【3】CLAUDE.mdが読まれているか確認
【4】website_spec.md を渡して /trial ページを作る
【5】DMを20〜40件送る
```

**サイトが先。** DMで「2週間無料トライアル」と書いて、
リンク先にトライアルページがないと信用を失う。

---

# 4. Claude Codeへの安全な指示の書き方

## 調査だけさせるとき

```
このPCの状況を調べて、レポートにまとめてください。

【絶対に守ること】
・今回は調査のみ。ファイルの作成・移動・削除を一切しないでください
・~/.claude/、node_modules/、venv/、.git/ には触れないでください
```

**「何も変更しないで」を必ず入れる。**

## 整理させるとき

```
既存のファイルは【移動ではなくコピー】してください。
```

**コピーなら、失敗しても元がある。**

## 削除させるとき

```
❌ 「いらないファイルを消して」  ← 判断をAIに任せない
⭕ 「~/old/ の中身を一覧にして。サイズと最終更新日も表示して」
　 → 自分で見て、ファイル名を指定して削除させる
```

---

# 5. 全体の流れ（確認用）

```
【今】
サイト改修 ＋ SaaS開発 ＋ DM
　↓
【1.5ヶ月後】MVP完成
　↓
【2ヶ月後】自分で1社受けて、自分のSaaSで納品（ドッグフーディング）
　↓
【3ヶ月後】トライアル開始・販売
　↓
【1年後】20社（月120万円）
　↓
【5年後】150社＋社員5人（年1億超）
```

**現在地：0社。**
