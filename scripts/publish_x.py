#!/usr/bin/env python3
"""X(旧Twitter)への自動投稿。

設計の前提(2026-09-21):
- **Codexを介さない。** 投稿時にAIは動かない。順番待ちのテキストを1件取り出して
  送るだけなので、AIの利用上限や障害に左右されない。
- **Bufferを介さない。** X APIへ直接送る。外部サービスの接続切れで止まらない。
- 公式SDKは使わず、OAuth 1.0a の署名も標準ライブラリだけで組む
  (src/core/stripe_client.py と同じ、依存を増やさない方針)。

## 使い方

1. 投稿したい文章を sales/x_queue/ に .txt で置く(ファイル名順に投稿される)。
   スレッドにしたい場合は、本文中に --- だけの行を入れて区切る。
2. python scripts/publish_x.py
   → 最も古い1件を投稿し、sales/x_posted/ へ移す。
3. 定期実行(Windowsタスクスケジューラ)から呼べば、置いておくだけで投稿される。

--dry-run を付けると、送信せずに「何が投稿されるか」だけ表示する。

## 必要な設定(.env)

X の開発者ポータルでアプリを作り、次の4つを .env に置く。
アプリの権限は **Read and Write** にすること(Readのみだと投稿できない)。

  X_API_KEY=...
  X_API_SECRET=...
  X_ACCESS_TOKEN=...
  X_ACCESS_TOKEN_SECRET=...
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_DIR = os.path.join(REPO_ROOT, "sales", "x_queue")
POSTED_DIR = os.path.join(REPO_ROOT, "sales", "x_posted")
API_URL = "https://api.x.com/2/tweets"

# Xの本文上限。日本語などの全角は2文字として数えられるため、全角のみなら140字。
MAX_WEIGHTED_LENGTH = 280
REQUIRED_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


def load_env(env_path):
    env = {}
    if not os.path.exists(env_path):
        return env
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            # KEY="値" と引用符付きで書かれることがある。外さずに使うと認証が通らない。
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key.strip()] = value
    return env


def weighted_length(text):
    """Xの文字数の数え方に合わせる。半角英数記号は1、それ以外(日本語等)は2。"""
    total = 0
    for ch in text:
        total += 1 if ord(ch) < 0x1100 else 2
    return total


def _quote(value):
    return urllib.parse.quote(str(value), safe="~")


def _oauth_header(method, url, creds, body_is_json=True):
    """OAuth 1.0a の Authorization ヘッダを組み立てる。

    本文がJSONの場合、署名対象に本文は含めない(oauth_* のみを署名する)のが
    X API v2 の仕様。フォーム送信と混同しないこと。
    """
    params = {
        "oauth_consumer_key": creds["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    if not body_is_json:
        raise ValueError("このスクリプトはJSON本文のみを想定している")

    base_parts = "&".join(f"{_quote(k)}={_quote(params[k])}" for k in sorted(params))
    base_string = "&".join([method.upper(), _quote(url), _quote(base_parts)])
    signing_key = f"{_quote(creds['X_API_SECRET'])}&{_quote(creds['X_ACCESS_TOKEN_SECRET'])}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    params["oauth_signature"] = signature
    return "OAuth " + ", ".join(f'{_quote(k)}="{_quote(params[k])}"' for k in sorted(params))


def post_tweet(creds, text, reply_to=None):
    payload = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": _oauth_header("POST", API_URL, creds),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))["data"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        print(f"[ERROR] X API HTTPError {e.code}: {detail}", file=sys.stderr)
        if e.code in (401, 403):
            print("[HINT] アプリの権限が Read and Write か、4つのキーが正しいか確認してください。",
                  file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERROR] Xへ接続できませんでした: {e.reason}", file=sys.stderr)
        sys.exit(1)


def next_in_queue():
    if not os.path.isdir(QUEUE_DIR):
        return None
    files = sorted(
        f for f in os.listdir(QUEUE_DIR)
        if f.endswith(".txt") and os.path.isfile(os.path.join(QUEUE_DIR, f))
    )
    return os.path.join(QUEUE_DIR, files[0]) if files else None


def split_thread(raw):
    """--- だけの行でスレッドに分割する。空の断片は捨てる。"""
    parts, current = [], []
    for line in raw.splitlines():
        if line.strip() == "---":
            parts.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    parts.append("\n".join(current).strip())
    return [p for p in parts if p]


def main():
    parser = argparse.ArgumentParser(description="順番待ちの文章をXへ1件投稿する")
    parser.add_argument("--dry-run", action="store_true", help="送信せず内容だけ表示する")
    parser.add_argument("--file", help="順番待ちではなく、指定したファイルを投稿する")
    args = parser.parse_args()

    path = args.file or next_in_queue()
    if not path:
        # 投稿するものが無いのは異常ではない。定期実行を失敗扱いにしない。
        print("[publish_x] 順番待ちの投稿はありません。")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        parts = split_thread(f.read())
    if not parts:
        print(f"[ERROR] 中身が空です: {path}", file=sys.stderr)
        return 1

    for i, part in enumerate(parts, 1):
        length = weighted_length(part)
        if length > MAX_WEIGHTED_LENGTH:
            print(f"[ERROR] {i}件目が長すぎます({length}/{MAX_WEIGHTED_LENGTH})。"
                  f"分割するか短くしてください: {path}", file=sys.stderr)
            print("[HINT] --- だけの行で区切るとスレッドとして投稿できます。", file=sys.stderr)
            return 1

    if args.dry_run:
        print(f"[dry-run] {os.path.basename(path)} / {len(parts)}件")
        for i, part in enumerate(parts, 1):
            print(f"--- {i}件目 ({weighted_length(part)}/{MAX_WEIGHTED_LENGTH}) ---")
            print(part)
        return 0

    env = load_env(os.path.join(REPO_ROOT, ".env"))
    creds = {}
    missing = []
    for key in REQUIRED_KEYS:
        value = env.get(key) or os.environ.get(key)
        if not value:
            missing.append(key)
        creds[key] = value
    if missing:
        print(f"[ERROR] .env に次の設定がありません: {', '.join(missing)}", file=sys.stderr)
        print("[HINT] Xの開発者ポータルでアプリを作り、Read and Write 権限で発行してください。",
              file=sys.stderr)
        return 1

    reply_to = None
    first_id = None
    for i, part in enumerate(parts, 1):
        data = post_tweet(creds, part, reply_to=reply_to)
        reply_to = data["id"]
        first_id = first_id or data["id"]
        print(f"[publish_x] {i}/{len(parts)} 投稿しました (id={data['id']})")

    # 投稿済みを別フォルダへ移し、二重投稿を防ぐ。
    if not args.file:
        os.makedirs(POSTED_DIR, exist_ok=True)
        os.replace(path, os.path.join(POSTED_DIR, os.path.basename(path)))
        print(f"[publish_x] 投稿済みへ移動: {os.path.basename(path)}")

    print(f"[publish_x] 完了: https://x.com/i/status/{first_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
