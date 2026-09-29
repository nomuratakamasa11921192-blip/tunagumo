"""ブラウザ等でAPIを使わずに投稿した1件を、auto_post.pyと同じ形で「投稿済み」に記録する。

投稿待ち(sales/queue/<チャネル>/)から投稿済み(sales/posted/<チャネル>/)へ移し、送信記録を残し、
Notion由来ならNotionの状態も「投稿済」にする。これで二重投稿防止(auto_post.find_posted_duplicate)が効く。

使い方: python scripts/record_manual_post.py --channel x --file sales/queue/x/xxx.txt --url https://x.com/...
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_post  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="手動投稿した1件を投稿済みに記録する")
    parser.add_argument("--channel", required=True, choices=auto_post.CHANNELS)
    parser.add_argument("--file", required=True)
    parser.add_argument("--url", default="", help="投稿先のURL(分かれば)")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        raw = f.read()
    duplicate = auto_post.find_posted_duplicate(args.channel, raw, args.file)
    if duplicate:
        print(f"同じ内容が投稿済みとして記録されています: {os.path.basename(duplicate)}", file=sys.stderr)
        return 1

    dest_dir = os.path.join(auto_post.POSTED_ROOT, args.channel)
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(args.file)
    receipt = {"channel": args.channel, "result": args.url or "manual", "method": "manual(browser)",
               "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "posted"}
    with open(os.path.join(dest_dir, name + ".receipt.json"), "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    os.replace(args.file, os.path.join(dest_dir, name))
    print(f"投稿済みに記録しました: {name}")

    page_id = auto_post.notion_page_of(raw)
    if page_id:
        import notion_sync
        token = auto_post.load_env().get("NOTION_TOKEN") or os.environ.get("NOTION_TOKEN")
        if token:
            page = notion_sync.api(token, "GET", f"/pages/{page_id}")
            props = page.get("properties", {})
            state_name = next(n for n in (notion_sync.STATE_PROP, "Status", "ステータス") if n in props)
            notion_sync.set_state(token, page_id, notion_sync.STATE_POSTED, props[state_name]["type"], state_name)
            print("Notionを投稿済へ更新しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
