#!/usr/bin/env python3
"""クリップボードの値を .env へ書き込む（値を画面に出さない）。

## なぜこれを使うか（2026-09-21）

APIキーをチャットやコマンドに貼ると、その文字列が会話の記録に残る。実際に
Stripeのキーでそれが起きた。コピーした値を**人の目にも記録にも触れさせずに**
設定へ入れるため、クリップボードから直接読んで書き込む。

## 使い方

1. 発行画面でキーのコピーボタンを押す
2. `python scripts/set_env_from_clipboard.py NOTION_TOKEN`
3. 「設定しました」と出れば完了。値は表示されない

確認だけしたい場合は `--check` を付ける（長さと末尾4文字だけ出す）。
末尾4文字は発行画面でも表示される部分なので、取り違えの確認に使える。
"""

import argparse
import os
import re
import subprocess
import sys

# Windowsの既定コンソールはcp932で、扱えない文字があると出力時に例外になる。
# 貼り間違いを知らせる道具が、その貼り間違いで落ちては意味がない。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")

# キーらしい見た目かどうかの目安。明らかな貼り間違い（URLや日本語混入）を弾く。
SUSPICIOUS = re.compile(r"[\s　-鿿]|^https?://")


def read_clipboard():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[ERROR] クリップボードを読めませんでした: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print("[ERROR] クリップボードを読めませんでした。", file=sys.stderr)
        return None
    return out.stdout.decode("utf-8", errors="replace").strip()


def upsert(name, value):
    """既にあれば置き換え、無ければ追記する。他の行には触れない。"""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{name}="):
            lines[i] = f"{name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{name}={value}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return replaced


def main():
    parser = argparse.ArgumentParser(description="クリップボードの値を.envへ入れる")
    parser.add_argument("name", help="設定名（例: NOTION_TOKEN）")
    parser.add_argument("--check", action="store_true", help="書き込まず、形だけ確認する")
    args = parser.parse_args()

    value = read_clipboard()
    if not value:
        print("[ERROR] クリップボードが空です。コピーしてから実行してください。", file=sys.stderr)
        return 1
    if "\n" in value:
        print("[ERROR] 複数行がコピーされています。キーだけをコピーしてください。", file=sys.stderr)
        return 1

    # 値そのものは出さない。取り違えの確認に使える情報だけ示す。
    if SUSPICIOUS.search(value):
        print("[ERROR] キーらしくない値です（空白・日本語・URLが含まれています）。",
              file=sys.stderr)
        print("[HINT] 発行画面のコピーボタンで、キーだけをコピーしてください。", file=sys.stderr)
        return 1

    tail = value[-4:] if len(value) >= 4 else "?"
    print(f"クリップボード: {len(value)}文字 / 末尾 ...{tail}")

    if args.check:
        print("（--check のため書き込んでいません）")
        return 0

    replaced = upsert(args.name, value)
    print(f"{args.name} を.envへ{'更新' if replaced else '追加'}しました。値は表示していません。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
