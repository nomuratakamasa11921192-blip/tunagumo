#!/usr/bin/env python3
"""下書きを1件ずつ確認して、承認したものだけを投稿待ちへ移す。

## 考え方（2026-09-21）

置いただけでは投稿されない。**野村さんが目で見て承認したものだけ**が世に出る。

    sales/drafts/<チャネル>/   … 下書き。ここに置いても投稿されない
        ↓ このスクリプトで承認
    sales/queue/<チャネル>/    … 投稿待ち。定期実行が1件ずつ送る
        ↓ 投稿されると自動で移動
    sales/posted/<チャネル>/   … 投稿済み

承認すると、ファイルの先頭に `approved: <日時>` が書き込まれる。
`scripts/auto_post.py` はこの印が無いものを投稿しないので、うっかり下書きを
投稿待ちへ置いてしまっても送られない。

## 使い方

    python scripts/review_posts.py              … 全チャネルの下書きを確認
    python scripts/review_posts.py --channel x  … Xの下書きだけ確認

各件について次を選ぶ。

    y … 承認する（投稿待ちへ移す）
    n … 却下する（sales/rejected/ へ移す）
    s … 保留（下書きのまま。次回また聞かれる）
    q … 中断する
"""

import argparse
import datetime
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

# Windowsの既定コンソールはcp932で、扱えない文字があると出力時に例外になる。
# 投稿の可否を決める道具が文字化けで落ちないよう、置換して出力を続ける。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

import auto_post  # noqa: E402  (REPO_ROOT をパスへ入れてから読み込む)

DRAFTS_ROOT = os.path.join(REPO_ROOT, "sales", "drafts")
REJECTED_ROOT = os.path.join(REPO_ROOT, "sales", "rejected")

# チャネルごとの、投稿前に人が知っておくべきこと。
WARNINGS = {
    "line": "【注意】友だち全員へ一斉に届きます。取り消せません。"
            "無料プランは月200通までで、1回の配信が友だちの人数分として数えられます。",
    "x": "【注意】公開されます。全角140字(半角280字)を超えると投稿されません。",
    "instagram": "【注意】公開されます。media: に公開URLが必要です。",
    "youtube": "【注意】公開されます。media: に動画ファイルのパスが必要です。",
}


def drafts_of(channel):
    d = os.path.join(DRAFTS_ROOT, channel)
    if not os.path.isdir(d):
        return []
    return [
        os.path.join(d, f)
        for f in sorted(os.listdir(d))
        if f.endswith(".txt") and os.path.isfile(os.path.join(d, f))
    ]


def show(channel, path, raw):
    media, body = auto_post.parse_item(raw)
    print("=" * 66)
    print(f"[{channel}] {os.path.basename(path)}")
    if WARNINGS.get(channel):
        print(WARNINGS[channel])
    print("-" * 66)
    for url in media:
        print(f"media: {url}")
    print(body)
    print("-" * 66)
    if channel == "x":
        import publish_x
        for i, part in enumerate(publish_x.split_thread(body), 1):
            n = publish_x.weighted_length(part)
            flag = "  ← 長すぎます" if n > publish_x.MAX_WEIGHTED_LENGTH else ""
            print(f"  {i}件目: {n}/{publish_x.MAX_WEIGHTED_LENGTH}{flag}")
    else:
        print(f"  {len(body)} 文字")


def approve(channel, path, raw):
    """承認印を付けて投稿待ちへ移す。"""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    dest_dir = os.path.join(auto_post.QUEUE_ROOT, channel)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    with open(dest, "w", encoding="utf-8") as f:
        f.write(f"approved: {stamp}\n{raw.lstrip()}")
    os.remove(path)
    return stamp


def reject(channel, path):
    dest_dir = os.path.join(REJECTED_ROOT, channel)
    os.makedirs(dest_dir, exist_ok=True)
    os.replace(path, os.path.join(dest_dir, os.path.basename(path)))


def main():
    parser = argparse.ArgumentParser(description="下書きを確認して承認する")
    parser.add_argument("--channel", choices=auto_post.CHANNELS, help="指定すると1チャネルのみ")
    args = parser.parse_args()

    channels = [args.channel] if args.channel else list(auto_post.CHANNELS)
    pending = [(ch, p) for ch in channels for p in drafts_of(ch)]
    if not pending:
        print("確認待ちの下書きはありません。")
        return 0

    print(f"確認待ち: {len(pending)}件")
    approved = rejected = held = 0

    for channel, path in pending:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        show(channel, path, raw)

        while True:
            try:
                choice = input("承認しますか? [y=承認 / n=却下 / s=保留 / q=中断] ").strip().lower()
            except EOFError:
                # 対話できない環境(定期実行等)で誤って承認しないよう、保留にして抜ける
                print("\n入力を受け取れないため中断します。承認は行っていません。")
                return 1
            if choice in ("y", "n", "s", "q"):
                break
            print("y / n / s / q のいずれかを入力してください。")

        if choice == "q":
            print("中断しました。")
            break
        if choice == "y":
            stamp = approve(channel, path, raw)
            approved += 1
            print(f"→ 承認しました（{stamp}）。投稿待ちへ移しました。")
        elif choice == "n":
            reject(channel, path)
            rejected += 1
            print("→ 却下しました。sales/rejected/ へ移しました。")
        else:
            held += 1
            print("→ 保留。下書きのままにします。")

    print("=" * 66)
    print(f"承認 {approved}件 / 却下 {rejected}件 / 保留 {held}件")
    if approved:
        print("承認したものは、次の定期実行の時刻に投稿されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
