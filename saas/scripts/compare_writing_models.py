"""文章の質を見比べるための比較スクリプト(2026-09-16)。

同じ指示・同じ依頼文を、OpenAI(現行)とAnthropic(Claude)の両方に投げて、出力を並べて表示する。
実際に使っている部署プロンプト(config/real_estate.yaml)をそのまま使うので、本番と同じ条件で比べられる。

使い方(VPSの開発用cloneで):
    cd ~/dev/tunagumo/saas
    export OPENAI_API_KEY=...        # 画面には貼らず、ここで入力する
    export ANTHROPIC_API_KEY=...
    python -m scripts.compare_writing_models          # 3種類ぜんぶ
    python -m scripts.compare_writing_models 1        # 1つだけ(番号指定)

モデルは環境変数で変えられる(既定: gpt-5.6-terra / claude-sonnet-5)。
    export COMPARE_OPENAI_MODEL=gpt-5.6-sol
    export COMPARE_ANTHROPIC_MODEL=claude-opus-5

注意: 実際にAPIを呼ぶので、わずかに費用がかかる(3種類でおよそ数円〜十数円)。
"""

import asyncio
import os
import sys
import time

import httpx
import yaml

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 1200

# 実際に顧客が送りそうな依頼文(本番のテンプレートに沿ったもの)
CASES = [
    {
        "title": "物件紹介文(ポータル掲載用)",
        "dept": "listing_copy_dept",
        "request": (
            "ポータル掲載用の賃貸物件の紹介文を作ってください。\n"
            "物件名: サンハイツ春日部 203号室\n"
            "所在地: 埼玉県春日部市中央3丁目\n"
            "最寄り: 東武スカイツリーライン 春日部駅 徒歩8分\n"
            "賃料: 6.8万円(管理費3,000円)\n"
            "間取り: 1LDK / 42.5㎡ / 2016年築 / 2階\n"
            "設備: 独立洗面台、追い焚き、宅配ボックス、インターネット無料、南向き\n"
            "備考: ペット相談可(小型犬1匹まで)"
        ),
    },
    {
        "title": "反響対応メール",
        "dept": "client_comm_dept",
        "request": (
            "ポータル経由の反響に返信するメールを作ってください。\n"
            "お客様: 30代ご夫婦、来月から入居希望\n"
            "問い合わせ内容: 「サンハイツ春日部203号室は、来週土曜に内見できますか。駐車場も空いていますか」\n"
            "こちらの状況: 土曜は10時〜16時で内見可能。駐車場は現在1台空き(月額8,000円)"
        ),
    },
    {
        "title": "オーナー様への月次報告",
        "dept": "owner_report_dept",
        "request": (
            "オーナー様向けの月次募集状況報告を作ってください。\n"
            "物件: サンハイツ春日部(全12戸、現在2戸空室)\n"
            "今月の反響: 問い合わせ9件、内見4件、申込1件(審査中)\n"
            "実施した施策: ポータル写真の差し替え、賃料を7.2万円から6.8万円へ調整\n"
            "来月の方針: 早期決定を狙ってフリーレント1ヶ月の導入を提案したい"
        ),
    },
]


def load_dept_prompt(dept_id: str) -> str:
    with open("config/real_estate.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    depts = config.get("departments", config)
    for key, value in depts.items():
        if key == dept_id:
            return value["system_prompt"]
    raise SystemExit(f"部署 {dept_id} が config/real_estate.yaml に見つかりません")


async def ask_openai(client: httpx.AsyncClient, model: str, system_prompt: str, request: str) -> tuple[str, float]:
    started = time.time()
    res = await client.post(
        OPENAI_URL,
        headers={"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={
            "model": model,
            "max_completion_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": request}],
        },
    )
    if res.status_code >= 400:
        return f"(エラー {res.status_code}: {res.text[:300]})", time.time() - started
    return res.json()["choices"][0]["message"]["content"], time.time() - started


async def ask_anthropic(client: httpx.AsyncClient, model: str, system_prompt: str, request: str) -> tuple[str, float]:
    started = time.time()
    res = await client.post(
        ANTHROPIC_URL,
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": request}],
        },
    )
    if res.status_code >= 400:
        return f"(エラー {res.status_code}: {res.text[:300]})", time.time() - started
    blocks = res.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return text, time.time() - started


async def main() -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(key):
            raise SystemExit(f"{key} が未設定です。export {key}=... を実行してから起動してください")

    openai_model = os.environ.get("COMPARE_OPENAI_MODEL", "gpt-5.6-terra")
    anthropic_model = os.environ.get("COMPARE_ANTHROPIC_MODEL", "claude-sonnet-5")
    selected = [int(a) for a in sys.argv[1:] if a.isdigit()]
    cases = [CASES[i - 1] for i in selected] if selected else CASES

    async with httpx.AsyncClient(timeout=120.0) as client:
        for index, case in enumerate(cases, start=1):
            system_prompt = load_dept_prompt(case["dept"])
            print("\n" + "=" * 78)
            print(f"【{index}. {case['title']}】")
            print("=" * 78)
            openai_text, openai_sec = await ask_openai(client, openai_model, system_prompt, case["request"])
            anthropic_text, anthropic_sec = await ask_anthropic(client, anthropic_model, system_prompt, case["request"])

            print(f"\n----- A: {openai_model} ({openai_sec:.1f}秒) -----\n")
            print(openai_text.strip())
            print(f"\n----- B: {anthropic_model} ({anthropic_sec:.1f}秒) -----\n")
            print(anthropic_text.strip())

    print("\n" + "=" * 78)
    print("どちらが良かったかを、次の点で見てください:")
    print("- 指定した形式(見出し・字数)を守れているか")
    print("- 提供していない情報を作っていないか(【要確認：〇〇】と書けているか)")
    print("- 「絶対」「最高」など、使ってはいけない表現が無いか")
    print("- そのまま出せる日本語か(手直しの量)")


if __name__ == "__main__":
    asyncio.run(main())
