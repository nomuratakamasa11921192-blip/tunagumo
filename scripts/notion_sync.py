#!/usr/bin/env python3
"""Notionで「承認済」にした行を、投稿待ちへ取り込む。

## 考え方（2026-09-21）

**Notionが承認画面になる。** スマホからでも承認でき、承認したものだけが投稿される。
取り込みも投稿もAIを使わないので、AIの利用上限や障害に左右されない。

    Notion 『状態』= 下書き    … 何も起きない
              ↓ 野村さんが「承認済」にする
    Notion 『状態』= 承認済    … このスクリプトが投稿待ちへ取り込む
              ↓ 取り込むと自動で
    Notion 『状態』= 投稿待ち  … auto_post.py が投稿する
              ↓ 投稿に成功すると自動で
    Notion 『状態』= 投稿済

「承認済」の行だけを見るので、下書きのまま置いてある行が誤って出ることはない。

## 使い方

    python scripts/notion_sync.py            … 承認済を取り込む
    python scripts/notion_sync.py --dry-run  … 取り込まず、何が対象かだけ表示

## 必要な設定（.env）

Notionの「インテグレーション」を作り、対象データベースをそのインテグレーションに
共有したうえで、トークンを置く。

    NOTION_TOKEN=ntn_...
    NOTION_DATABASE_ID=83b85a552c124499b1e9ba5bd19f7acd

## 列の対応

| Notionの列 | 使い道 |
|---|---|
| 状態（選択） | 下書き / 承認済 / 投稿待ち / 投稿済 |
| 投稿タイトル（タイトル） | ファイル名に使う。`[動画] ` で始まると動画として扱う |
| キャプション（テキスト） | 本文 |
| 画像URL（URL/テキスト） | media。Instagram・YouTubeでは必須 |
| チャネル（選択） | x / instagram / youtube / line。無ければ instagram |
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

import auto_post  # noqa: E402

API_ROOT = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

STATE_PROP = "状態"
STATE_APPROVED = "承認済"
STATE_QUEUED = "投稿待ち"
STATE_POSTED = "投稿済"

DEFAULT_DATABASE_ID = "83b85a552c124499b1e9ba5bd19f7acd"
DEFAULT_CHANNEL = "instagram"


def api(token, method, path, payload=None):
    req = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Notion API {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Notionへ接続できませんでした: {e.reason}")


def plain_text(prop):
    """Notionのプロパティから、型を問わず素のテキストを取り出す。"""
    if not prop:
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in prop.get(kind, []))
    if kind == "url":
        return prop.get("url") or ""
    if kind == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if kind == "status":
        sel = prop.get("status")
        return sel.get("name", "") if sel else ""
    if kind == "files":
        files = prop.get("files") or []
        if files:
            f = files[0]
            return (f.get("external") or {}).get("url") or (f.get("file") or {}).get("url") or ""
    return ""


def find_prop(props, *names):
    """列名は変わりうるので、候補のいずれかに一致するものを返す。"""
    for name in names:
        if name in props:
            return props[name]
    return None


def database_schema(token, db_id):
    """DBの列名と型を返す。列構成は人が変えるので、決め打ちにしない。"""
    db = api(token, "GET", f"/databases/{db_id}")
    return {name: prop.get("type") for name, prop in db.get("properties", {}).items()}


def build_property(kind, value):
    """列の型に合わせた値を組み立てる。対応していない型なら None。"""
    if kind == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if kind == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if kind == "url":
        return {"url": value or None}
    if kind in ("select", "status"):
        return {kind: {"name": value}}
    return None


def create_draft(token, db_id, schema, title, caption, media, channel):
    """ローカルの下書きをNotionへ「下書き」として作る。

    Notionで一覧・承認できるよう、置き場所をNotionへ寄せるための方向
    (2026-09-21追加)。承認は人がNotion上で行う。
    """
    wanted = [
        (("投稿タイトル", "Name", "名前"), title),
        (("キャプション", "本文", "Caption"), caption),
        (("画像URL", "メディア", "Media", "URL"), media),
        (("チャネル", "Channel"), channel),
        ((STATE_PROP, "Status", "ステータス"), "下書き"),
    ]
    props = {}
    for names, value in wanted:
        if value in (None, ""):
            continue
        for name in names:
            if name in schema:
                built = build_property(schema[name], value)
                if built is not None:
                    props[name] = built
                break
    if not props:
        raise RuntimeError("書き込める列が見つかりません。列名を確認してください。")
    page = api(token, "POST", "/pages",
               {"parent": {"database_id": db_id}, "properties": props})
    return page["id"].replace("-", "")


def set_state(token, page_id, state, state_type):
    value = {"name": state}
    api(token, "PATCH", f"/pages/{page_id}",
        {"properties": {STATE_PROP: {state_type: value}}})


def safe_filename(title, page_id):
    base = re.sub(r"[^\w぀-ヿ一-鿿-]+", "_", title).strip("_")[:40]
    return f"{datetime.datetime.now():%Y-%m-%d}_{base or 'notion'}_{page_id[:8]}.txt"


def push_drafts(token, db_id, dry_run):
    """sales/drafts/ の下書きをNotionへ「下書き」として送る。

    ローカルに置いた下書きもNotionの一覧に出るようにして、承認する場所を
    Notionへ一本化するための処理(2026-09-21)。送った下書きは
    sales/drafts/_sent/ へ移し、同じものを二度作らないようにする。
    """
    drafts_root = os.path.join(REPO_ROOT, "sales", "drafts")
    sent_root = os.path.join(drafts_root, "_sent")

    items = []
    for channel in auto_post.CHANNELS:
        d = os.path.join(drafts_root, channel)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".txt") and os.path.isfile(os.path.join(d, name)):
                items.append((channel, os.path.join(d, name)))

    if not items:
        print("[notion_sync] Notionへ送る下書きはありません。")
        return 0

    try:
        schema = database_schema(token, db_id)
    except RuntimeError as e:
        print(f"[notion_sync] {e}", file=sys.stderr)
        return 1
    print(f"[notion_sync] Notionの列: {', '.join(sorted(schema))}")

    sent = 0
    for channel, path in items:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        media, body = auto_post.parse_item(raw)
        title = os.path.splitext(os.path.basename(path))[0]

        if dry_run:
            print(f"  - [{channel}] {title} ({len(body)}文字) を下書きとして作成予定")
            continue

        try:
            page_id = create_draft(token, db_id, schema, title, body,
                                   media[0] if media else "", channel)
        except RuntimeError as e:
            print(f"  - [{channel}] {title}: 作成できませんでした（{e}）", file=sys.stderr)
            continue

        os.makedirs(os.path.join(sent_root, channel), exist_ok=True)
        os.replace(path, os.path.join(sent_root, channel, os.path.basename(path)))
        sent += 1
        print(f"  - [{channel}] {title} → Notionに下書きとして作成しました")

    if not dry_run:
        print(f"[notion_sync] {sent}件を送りました。Notionで内容を確認し、"
              "「承認済」にすると投稿されます。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Notionの承認済を投稿待ちへ取り込む")
    parser.add_argument("--dry-run", action="store_true", help="変更せず対象だけ表示する")
    parser.add_argument("--push", action="store_true",
                        help="ローカルの下書きをNotionへ送る(Notionで一覧・承認できるように)")
    args = parser.parse_args()

    env = auto_post.load_env()
    token = env.get("NOTION_TOKEN") or os.environ.get("NOTION_TOKEN")
    db_id = env.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DATABASE_ID") or DEFAULT_DATABASE_ID
    if not token:
        print("[notion_sync] .env に NOTION_TOKEN がありません。", file=sys.stderr)
        print("[notion_sync] Notionでインテグレーションを作り、対象DBを共有してください。",
              file=sys.stderr)
        return 1

    if args.push:
        return push_drafts(token, db_id, args.dry_run)

    try:
        result = api(token, "POST", f"/databases/{db_id}/query", {"page_size": 50})
    except RuntimeError as e:
        print(f"[notion_sync] {e}", file=sys.stderr)
        return 1

    rows = result.get("results", [])
    targets = []
    for page in rows:
        props = page.get("properties", {})
        state_prop = find_prop(props, STATE_PROP, "Status", "ステータス")
        if plain_text(state_prop) != STATE_APPROVED:
            continue
        targets.append((page, props, (state_prop or {}).get("type", "select")))

    if not targets:
        # 承認済が無いのは異常ではない。定期実行を失敗扱いにしない。
        print(f"[notion_sync] 承認済の行はありません（全{len(rows)}行を確認）。")
        return 0

    print(f"[notion_sync] 承認済: {len(targets)}件")
    taken = 0
    for page, props, state_type in targets:
        page_id = page["id"].replace("-", "")
        title = plain_text(find_prop(props, "投稿タイトル", "Name", "名前"))
        caption = plain_text(find_prop(props, "キャプション", "本文", "Caption"))
        media = plain_text(find_prop(props, "画像URL", "メディア", "Media", "URL"))
        channel = plain_text(find_prop(props, "チャネル", "Channel")) or DEFAULT_CHANNEL

        if channel not in auto_post.CHANNELS:
            print(f"  - {title}: 未対応のチャネル「{channel}」のため取り込みません", file=sys.stderr)
            continue
        if not caption:
            print(f"  - {title}: キャプションが空のため取り込みません", file=sys.stderr)
            continue
        if channel in ("instagram", "youtube") and not media:
            print(f"  - {title}: {channel}には画像URLが必要なため取り込みません", file=sys.stderr)
            continue

        if args.dry_run:
            print(f"  - [{channel}] {title} ({len(caption)}文字" +
                  (f", media={media}" if media else "") + ")")
            continue

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"approved: {stamp} (Notion)", f"notion: {page_id}"]
        if media:
            lines.append(f"media: {media}")
        lines.append("---")
        lines.append(caption)

        dest_dir = os.path.join(auto_post.QUEUE_ROOT, channel)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, safe_filename(title, page_id))
        with open(dest, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        # 取り込み済みにして、次回の実行で二重に取り込まないようにする
        try:
            set_state(token, page_id, STATE_QUEUED, state_type)
        except RuntimeError as e:
            os.remove(dest)
            print(f"  - {title}: 状態を更新できなかったため取り消しました（{e}）", file=sys.stderr)
            continue

        taken += 1
        print(f"  - [{channel}] {title} → 投稿待ちへ取り込みました")

    if not args.dry_run:
        print(f"[notion_sync] {taken}件を取り込みました。次の定期実行の時刻に投稿されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
