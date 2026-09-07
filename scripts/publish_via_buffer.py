#!/usr/bin/env python3
"""
ツナグモ Instagram自動投稿スクリプト(Buffer API経由)

Meta社のInstagram Graph API直接連携は、instagram_content_publish権限の
App Review(審査)が必須で回避できない。Bufferは自社アプリで審査済みのため、
Buffer側にInstagramを連携しておけば、こちらで新たに審査を通す必要がない。

前提:
  1. Buffer(https://buffer.com)のアカウントを作成
  2. Bufferの管理画面から、ツナグモのInstagramプロアカウント(tunagumo2026)を連携
  3. https://publish.buffer.com/settings/api で個人用APIキーを発行
  4. .env に以下を設定:
       BUFFER_API_KEY=...

使い方:
  1. 投稿したい画像/動画を website/assets/instagram/ に置く
  2. wrangler pages deploy で website フォルダを公開し、公開URLにする
     例: https://tsunagumo.pages.dev/assets/instagram/reel1_intro.mp4
  3. 以下を実行(確認プロンプトが出るので、内容を確認してからYesと答える):
     python scripts/publish_via_buffer.py --media-url "https://tsunagumo.pages.dev/assets/instagram/reel1_intro.mp4" --video --caption-file "sales/post_captions/reel1.txt"

     即時投稿ではなく予約したい場合:
     python scripts/publish_via_buffer.py --media-url "..." --caption-file "..." --schedule "2026-08-28T10:00:00+09:00"

このスクリプトは確認なしでは絶対に投稿しない(--yes を明示的に渡さない限り、投稿前に必ず一時停止してプレビューを表示する)。
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

API_ENDPOINT = "https://api.buffer.com"


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


def gql(api_key, query):
    body = {"query": query}
    req = urllib.request.Request(
        API_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Buffer API HTTPError {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    if "errors" in data and data["errors"]:
        print(f"[ERROR] Buffer API GraphQLエラー: {data['errors']}", file=sys.stderr)
        sys.exit(1)
    return data["data"]


def find_instagram_channel(api_key):
    orgs = gql(api_key, "query { account { organizations { id } } }")
    org_list = orgs["account"]["organizations"]
    if not org_list:
        print("[ERROR] Bufferの組織(organization)が見つかりません。", file=sys.stderr)
        sys.exit(1)
    org_id = org_list[0]["id"]

    channels = gql(
        api_key,
        f"""
        query GetChannels {{
          channels(input: {{ organizationId: {json.dumps(org_id)} }}) {{
            id
            name
            displayName
            service
          }}
        }}
        """,
    )["channels"]

    ig_channels = [c for c in channels if c.get("service", "").lower() == "instagram"]
    if not ig_channels:
        print("[ERROR] Bufferに連携されたInstagramチャンネルが見つかりません。"
              "先にBufferの管理画面でtunagumo2026を連携してください。", file=sys.stderr)
        sys.exit(1)
    if len(ig_channels) > 1:
        print("[WARN] Instagramチャンネルが複数見つかりました。最初のものを使います:")
        for c in ig_channels:
            print(f"  - {c['displayName']} ({c['id']})")
    return ig_channels[0]


def main():
    parser = argparse.ArgumentParser(description="ツナグモ Instagram自動投稿(Buffer経由・要確認)")
    parser.add_argument("--media-url", required=True, help="公開されている画像/動画URL")
    parser.add_argument("--video", action="store_true", help="--media-url が動画の場合に指定")
    parser.add_argument("--caption-file", required=True, help="キャプションテキストが書かれたファイルパス")
    parser.add_argument("--schedule", help="予約投稿する日時(ISO8601形式 例: 2026-08-28T10:00:00+09:00)。省略時はBufferのキューに追加")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトをスキップして即実行する(通常は使わない)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    env = load_env(os.path.join(project_root, ".env"))

    api_key = env.get("BUFFER_API_KEY") or os.environ.get("BUFFER_API_KEY")
    if not api_key:
        print("[ERROR] .env に BUFFER_API_KEY を設定してください。"
              "https://publish.buffer.com/settings/api で発行できます。", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.caption_file):
        print(f"[ERROR] キャプションファイルが見つかりません: {args.caption_file}", file=sys.stderr)
        sys.exit(1)
    with open(args.caption_file, "r", encoding="utf-8") as f:
        caption = f.read().strip()

    channel = find_instagram_channel(api_key)

    print("=" * 60)
    print(f"これから以下の内容でツナグモ({channel['displayName']})に投稿します:")
    print("-" * 60)
    print(f"メディアURL: {args.media_url} ({'動画' if args.video else '画像'})")
    print(f"予約日時: {args.schedule if args.schedule else '(Bufferのキューに追加・次の空き枠)'}")
    print("キャプション:")
    print(caption)
    print("=" * 60)

    if not args.yes:
        answer = input("この内容で本当に投稿しますか? (yes と入力すると投稿します): ")
        if answer.strip().lower() != "yes":
            print("キャンセルしました。何も投稿していません。")
            sys.exit(0)

    asset_key = "video" if args.video else "image"
    fields = [
        f"text: {json.dumps(caption)}",
        f"channelId: {json.dumps(channel['id'])}",
        "schedulingType: automatic",
        f"assets: [{{ {asset_key}: {{ url: {json.dumps(args.media_url)} }} }}]",
    ]
    if args.schedule:
        fields.append("mode: customScheduled")
        fields.append(f"dueAt: {json.dumps(args.schedule)}")
    else:
        fields.append("mode: addToQueue")

    result = gql(
        api_key,
        f"""
        mutation CreatePost {{
          createPost(input: {{ {', '.join(fields)} }}) {{
            ... on PostActionSuccess {{
              post {{ id text }}
            }}
            ... on MutationError {{
              message
            }}
          }}
        }}
        """,
    )

    outcome = result["createPost"]
    if "message" in outcome:
        print(f"[ERROR] 投稿に失敗しました: {outcome['message']}", file=sys.stderr)
        sys.exit(1)
    print(f"投稿を作成しました: post_id={outcome['post']['id']}")


if __name__ == "__main__":
    main()
