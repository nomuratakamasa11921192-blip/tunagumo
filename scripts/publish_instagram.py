#!/usr/bin/env python3
"""
ツナグモ Instagram自動投稿スクリプト(公式 Instagram Graph API 経由)

前提:
  - tunagumo2026 がプロ(ビジネス)アカウントであること(確認済み)
  - FacebookページとInstagramアカウントが連携済みであること
  - Meta for Developersでアプリを作成し、長期アクセストークンを取得済みであること
  - .env に以下を設定:
      IG_ACCESS_TOKEN=...       # instagram_content_publish 権限を持つ長期トークン
      IG_BUSINESS_ACCOUNT_ID=... # Instagramプロアカウントの Graph API 上のID

使い方:
  1. 投稿したい画像を website/assets/instagram/ に置く
  2. wrangler pages deploy で website フォルダを公開し、画像を公開URLにする
     例: https://tsunagumo.pages.dev/assets/instagram/post1.png
  3. 以下を実行(確認プロンプトが出るので、実際に投稿してよいか目視確認してからYesと答える):
     python scripts/publish_instagram.py --image-url "https://tsunagumo.pages.dev/assets/instagram/post1.png" --caption-file "sales/post_captions/post1.txt"

このスクリプトは確認なしでは絶対に投稿しない(--yes を明示的に渡さない限り、投稿前に必ず一時停止してプレビューを表示する)。
"""

import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
import json

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


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
            env[key.strip()] = value.strip()
    return env


def graph_request(method, path, params=None, data=None):
    url = f"{GRAPH_API_BASE}/{path}"
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET")
    else:
        payload = urllib.parse.urlencode(data or {}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[ERROR] Graph API HTTPError {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="ツナグモ Instagram自動投稿(要確認)")
    parser.add_argument("--image-url", required=True, help="公開されている画像URL(tsunagumo.pages.dev配下推奨)")
    parser.add_argument("--caption-file", required=True, help="キャプションテキストが書かれたファイルパス")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトをスキップして即投稿する(通常は使わない)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    env = load_env(os.path.join(project_root, ".env"))

    access_token = env.get("IG_ACCESS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN")
    ig_user_id = env.get("IG_BUSINESS_ACCOUNT_ID") or os.environ.get("IG_BUSINESS_ACCOUNT_ID")

    if not access_token or not ig_user_id:
        print("[ERROR] .env に IG_ACCESS_TOKEN と IG_BUSINESS_ACCOUNT_ID を設定してください。", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.caption_file):
        print(f"[ERROR] キャプションファイルが見つかりません: {args.caption_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.caption_file, "r", encoding="utf-8") as f:
        caption = f.read().strip()

    print("=" * 60)
    print("これから以下の内容でツナグモ(tunagumo2026)に投稿します:")
    print("-" * 60)
    print(f"画像URL: {args.image_url}")
    print("キャプション:")
    print(caption)
    print("=" * 60)

    if not args.yes:
        answer = input("この内容で本当に投稿しますか? (yes と入力すると投稿します): ")
        if answer.strip().lower() != "yes":
            print("キャンセルしました。何も投稿していません。")
            sys.exit(0)

    print("メディアコンテナを作成中...")
    create_res = graph_request(
        "POST",
        f"{ig_user_id}/media",
        data={
            "image_url": args.image_url,
            "caption": caption,
            "access_token": access_token,
        },
    )
    creation_id = create_res.get("id")
    if not creation_id:
        print(f"[ERROR] メディアコンテナの作成に失敗しました: {create_res}", file=sys.stderr)
        sys.exit(1)
    print(f"メディアコンテナ作成完了: {creation_id}")

    # コンテナの処理完了を待つ(画像取得・エンコードに数秒かかることがある)
    for _ in range(10):
        status_res = graph_request(
            "GET",
            creation_id,
            params={"fields": "status_code", "access_token": access_token},
        )
        status = status_res.get("status_code")
        if status == "FINISHED":
            break
        print(f"  処理中... status={status}")
        time.sleep(2)

    print("投稿を公開中...")
    publish_res = graph_request(
        "POST",
        f"{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
    )
    print(f"公開完了: {publish_res}")


if __name__ == "__main__":
    main()
