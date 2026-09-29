#!/usr/bin/env python3
"""X・Instagram・YouTube・LINE への自動投稿（AIを介さない）。

## 方針（2026-09-21）

**投稿時にAIを動かさない。** Codex も Claude Code も使わない。順番待ちのファイルを
1件取り出して送るだけなので、AIの利用上限・障害・課金に左右されない。

承認済みの原稿を `sales/queue/<チャネル>/` から1件送る。
定期実行は別途有効化が必要。送信前の印と送信結果の記録で、並行送信と
結果が不明な送信の自動再試行を防ぐ。障害時は投稿先の実態を確認する。

## ファイルの書き方

本文だけの場合は、そのまま書く。画像・動画を付ける場合は先頭に `media:` を書き、
`---` で本文と区切る。

    media: https://tsunagumo.pages.dev/assets/instagram/post1.png
    ---
    本文をここに書く。

Xでスレッドにしたい場合は、本文中に `---` だけの行を入れて区切る
（`media:` を使う場合、最初の `---` は区切りとして消費される）。

## チャネルごとの必要な設定（.env）

| チャネル | 必要なもの | 状態(2026-09-21) |
|---|---|---|
| x | X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET | 設定済み(認証確認済み) |
| line | LINE_CHANNEL_ACCESS_TOKEN | 設定済み |
| instagram | BUFFER_API_KEY（Buffer経由）＋ 公開URLのmedia | 設定済み |
| youtube | YOUTUBE_CLIENT_SECRET_PATH / YOUTUBE_TOKEN_PATH | 設定済み(youtube.upload・更新確認済み) |
"""

import argparse
import datetime
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

QUEUE_ROOT = os.path.join(REPO_ROOT, "sales", "queue")
POSTED_ROOT = os.path.join(REPO_ROOT, "sales", "posted")
CHANNELS = ("x", "instagram", "youtube", "line")

# LINEの本文上限(1通)。Xと違い全角も1文字として数える。
LINE_MAX_CHARS = 5000


def load_env():
    import publish_x
    return publish_x.load_env(os.path.join(REPO_ROOT, ".env"))


APPROVAL_MARK = "approved:"


def header_values(raw):
    """Read metadata only before the body; body text cannot grant approval."""
    values = {}
    for line in raw.splitlines():
        key, sep, value = line.strip().partition(":")
        if not sep or key.lower() not in ("approved", "notion", "media"):
            break
        values[key.lower()] = value.strip()
    return values


def approval_of(raw):
    """先頭付近の `approved: <日時>` を返す。無ければ None。

    承認済みの印が無いものは投稿しない(2026-09-21)。キューへ誤って置いた下書きや、
    書きかけのファイルが、確認されないまま世に出るのを防ぐための最後の砦。
    印は scripts/review_posts.py が付ける。手で書いてもよい。
    """
    return header_values(raw).get("approved") or None


def notion_page_of(raw):
    """`notion: <ページID>` があれば返す。Notion由来の投稿かどうかの判別に使う。"""
    return header_values(raw).get("notion") or None


def parse_item(raw):
    """`media:` ヘッダと本文に分ける。ヘッダが無ければ全体が本文。"""
    lines = raw.splitlines()
    media = []
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith(APPROVAL_MARK):
            body_start = i + 1
        elif stripped.lower().startswith("notion:"):
            body_start = i + 1
        elif stripped.lower().startswith("media:"):
            media.append(stripped.split(":", 1)[1].strip())
            body_start = i + 1
        elif stripped == "---" and body_start:
            body_start = i + 1
            break
        else:
            break
    return media, "\n".join(lines[body_start:]).strip()


def next_in_queue(channel):
    d = os.path.join(QUEUE_ROOT, channel)
    if not os.path.isdir(d):
        return None
    files = sorted(
        f for f in os.listdir(d)
        if f.endswith(".txt") and os.path.isfile(os.path.join(d, f))
    )
    return os.path.join(d, files[0]) if files else None


def _media_key(ref):
    """ローカルの動画・画像は中身のハッシュ、URLはURLそのもので比べる(作り直した同名ファイルは別物)。"""
    local = ref if os.path.isabs(ref) else os.path.join(REPO_ROOT, ref)
    if os.path.isfile(local):
        import hashlib
        with open(local, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    return ref


def content_key(raw):
    """同じ投稿かどうかの判定用。添付があれば添付の中身、無ければ本文(承認印等のヘッダ除く)で比べる。"""
    media, body = parse_item(raw)
    if media:
        return ("media", tuple(sorted(_media_key(m) for m in media)))
    return ("text", "\n".join(line.strip() for line in body.splitlines() if line.strip()))


def find_posted_duplicate(channel, raw, path):
    """同じチャネルで同じ内容を投稿済みなら、そのファイルを返す(2026-09-29)。
    ClaudeとCodexの両方が投稿を扱っても、二重投稿にならないようにするための最後の確認。"""
    key = content_key(raw)
    posted_dir = os.path.join(POSTED_ROOT, channel)
    if not os.path.isdir(posted_dir):
        return None
    for name in os.listdir(posted_dir):
        p = os.path.join(posted_dir, name)
        if not name.endswith(".txt") or os.path.abspath(p) == os.path.abspath(path):
            continue
        with open(p, "r", encoding="utf-8") as f:
            if content_key(f.read()) == key:
                return p
    return None


def mark_posted(channel, path):
    dest_dir = os.path.join(POSTED_ROOT, channel)
    os.makedirs(dest_dir, exist_ok=True)
    os.replace(path, os.path.join(dest_dir, os.path.basename(path)))


# --------------------------------------------------------------------------
# チャネルごとの送信処理。返り値は人が読める結果の文字列。
# 送れない状態(設定不足等)は例外ではなく RuntimeError で理由を返す。
# --------------------------------------------------------------------------

def send_x(env, media, body, dry_run):
    import publish_x
    parts = publish_x.split_thread(body)
    for i, part in enumerate(parts, 1):
        n = publish_x.weighted_length(part)
        if n > publish_x.MAX_WEIGHTED_LENGTH:
            raise RuntimeError(
                f"{i}件目が長すぎます({n}/{publish_x.MAX_WEIGHTED_LENGTH})。"
                "--- だけの行で区切るとスレッドにできます。"
            )
    if media:
        import publish_x_video
        if len(media) != 1:
            raise RuntimeError("Xの動画投稿はMP4を1本だけ指定してください。")
        video = media[0] if os.path.isabs(media[0]) else os.path.join(REPO_ROOT, media[0])
        video = publish_x_video.validate_video(video)
        if dry_run:
            return f"MP4動画1本を最初の投稿へ添付予定（{len(parts)}件）。認証・公開は未実施"
        # OAuth2が設定されていればそれを使い(期限前に自動更新)、無ければ従来のOAuth1.0aの4つの鍵で送る(2026-09-29)。
        if env.get("X_OAUTH2_CLIENT_ID") or os.environ.get("X_OAUTH2_CLIENT_ID"):
            import x_oauth2_login
            try:
                token = x_oauth2_login.refresh_if_needed()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"Xの認証を更新できませんでした: {e}")
        else:
            missing = [k for k in publish_x.REQUIRED_KEYS if not (env.get(k) or os.environ.get(k))]
            if missing:
                raise RuntimeError(f".env に設定がありません: {', '.join(missing)}")
            token = {k: env.get(k) or os.environ.get(k) for k in publish_x.REQUIRED_KEYS}
        return publish_x_video.publish(token, video, parts)
    if dry_run:
        return f"{len(parts)}件のスレッドとして投稿予定"

    creds = {}
    missing = [k for k in publish_x.REQUIRED_KEYS if not (env.get(k) or os.environ.get(k))]
    if missing:
        raise RuntimeError(f".env に設定がありません: {', '.join(missing)}")
    for k in publish_x.REQUIRED_KEYS:
        creds[k] = env.get(k) or os.environ.get(k)

    reply_to = first_id = None
    for part in parts:
        data = publish_x.post_tweet(creds, part, reply_to=reply_to)
        reply_to = data["id"]
        first_id = first_id or data["id"]
    return f"https://x.com/i/status/{first_id}"


def send_line(env, media, body, dry_run):
    """LINE公式アカウントの友だち全員へ配信する。

    **取り消せない。** 送信前に必ず内容を確認すること。
    無料プランは月200通までで、配信1回が「友だち人数分」として数えられる。
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    if len(body) > LINE_MAX_CHARS:
        raise RuntimeError(f"本文が長すぎます({len(body)}/{LINE_MAX_CHARS})")
    if any(urllib.parse.urlparse(url).path.lower().endswith((".mp4", ".mov")) for url in media):
        raise RuntimeError("このLINE配信処理は動画添付に未対応です。画像として誤送信しません。")
    token = env.get("LINE_CHANNEL_ACCESS_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(".env に LINE_CHANNEL_ACCESS_TOKEN がありません")
    if dry_run:
        return f"友だち全員へ配信予定({len(body)}文字)"

    messages = [{"type": "text", "text": body}]
    for url in media:
        messages.append({"type": "image", "originalContentUrl": url, "previewImageUrl": url})

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": messages}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            res.read()
        return "友だち全員へ配信しました"
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LINE API {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")


def send_instagram(env, media, body, dry_run):
    """Buffer経由で投稿する。Instagram Graph APIの直接投稿は
    instagram_content_publish 権限の審査が要るため、Bufferを使う（既存方針）。"""
    if not media:
        raise RuntimeError("Instagramは画像か動画が必要です。先頭に media: を書いてください。")
    from urllib.parse import urlparse
    if len(media) != 1 or urlparse(media[0]).scheme != "https" or not urlparse(media[0]).hostname:
        raise RuntimeError("Instagramには確認済みの公開HTTPSメディアURLを1件指定してください。")
    if dry_run:
        return f"Buffer経由で投稿予定(media={media[0]})"

    import publish_via_buffer as B
    key = env.get("BUFFER_API_KEY") or os.environ.get("BUFFER_API_KEY")
    if not key:
        raise RuntimeError(".env に BUFFER_API_KEY がありません")
    channel = B.find_instagram_channel(key)
    is_video = urlparse(media[0]).path.lower().endswith((".mp4", ".mov"))
    post_id = B.create_post(key, channel["id"], media[0], body, is_video)
    return f"Bufferへ予約登録しました({channel['displayName']}, id={post_id})"


def send_youtube(env, media, body, dry_run):
    """動画をアップロードする。

    本文の1行目をタイトル、残りを概要欄として扱う。公開設定は既定でunlisted
    (リンクを知っている人のみ)。自動で全世界に公開されると取り返しがつかないため、
    公開したい場合は本文の先頭に `privacy: public` を書いて明示する。
    """
    if not media:
        raise RuntimeError("YouTubeは動画ファイルのパスが必要です。先頭に media: を書いてください。")

    privacy = "unlisted"
    lines = body.splitlines()
    if lines and lines[0].strip().lower().startswith("privacy:"):
        privacy = lines[0].split(":", 1)[1].strip().lower()
        lines = lines[1:]
        if privacy not in ("public", "unlisted", "private"):
            raise RuntimeError(f"公開設定が不正です: {privacy}（public/unlisted/private）")
    if not lines:
        raise RuntimeError("本文が空です。1行目をタイトルにします。")

    title, description = lines[0].strip(), chr(10).join(lines[1:]).strip()
    video = media[0]
    if not os.path.isabs(video):
        video = os.path.join(REPO_ROOT, video)

    if dry_run:
        exists = "あり" if os.path.isfile(video) else "★ファイルが見つかりません"
        return f"アップロード予定(title={title!r}, 公開={privacy}, 動画={exists})"

    if not os.path.isfile(video):
        raise RuntimeError(f"動画ファイルが見つかりません: {video}")

    import publish_youtube_short as Y
    secret = env.get("YOUTUBE_CLIENT_SECRET_PATH") or os.environ.get("YOUTUBE_CLIENT_SECRET_PATH")
    token = env.get("YOUTUBE_TOKEN_PATH") or os.environ.get("YOUTUBE_TOKEN_PATH")
    if not secret or not token:
        raise RuntimeError(".env に YOUTUBE_CLIENT_SECRET_PATH と YOUTUBE_TOKEN_PATH が必要です")

    try:
        video_id = Y.upload_video(
            os.path.join(REPO_ROOT, secret) if not os.path.isabs(secret) else secret,
            os.path.join(REPO_ROOT, token) if not os.path.isabs(token) else token,
            video, title, description=description, privacy=privacy,
        )
    except Exception as e:  # noqa: BLE001
        message = str(e)
        if "insufficient" in message or "403" in message:
            raise RuntimeError(
                "アップロード権限がありません。python scripts/publish_youtube_short.py で"
                "再認証してください（保存済みトークンのスコープ不足）。"
            )
        raise RuntimeError(f"アップロードに失敗しました: {message[:200]}")
    return f"https://youtube.com/watch?v={video_id}"


SENDERS = {
    "x": send_x,
    "line": send_line,
    "instagram": send_instagram,
    "youtube": send_youtube,
}


def main():
    parser = argparse.ArgumentParser(description="順番待ちを1件、指定チャネルへ投稿する")
    parser.add_argument("--channel", required=True, choices=CHANNELS)
    parser.add_argument("--dry-run", action="store_true", help="送信せず内容だけ表示する")
    parser.add_argument("--file", help="順番待ちではなく指定ファイルを投稿する")
    args = parser.parse_args()

    tag = f"[auto_post:{args.channel}]"
    path = args.file or next_in_queue(args.channel)
    if not path:
        # 投稿が無い日を失敗扱いにしない(定期実行が毎回エラー通知を出さないため)
        print(f"{tag} 順番待ちの投稿はありません。")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    approved = approval_of(raw)
    if not approved and not args.dry_run:
        print(f"{tag} 承認されていないため投稿しません: {os.path.basename(path)}", file=sys.stderr)
        print(f"{tag} python scripts/review_posts.py で内容を確認して承認してください。",
              file=sys.stderr)
        return 1
    media, body = parse_item(raw)
    if not body and not media:
        print(f"{tag} 中身が空です: {path}", file=sys.stderr)
        return 1
    duplicate = find_posted_duplicate(args.channel, raw, path)
    if duplicate:
        print(f"{tag} 同じ内容を投稿済みのため投稿しません: {os.path.basename(path)}"
              f"（投稿済み: {os.path.basename(duplicate)}）", file=sys.stderr)
        if not args.file and not args.dry_run:
            # 列の先頭に残すと以降の投稿が止まるため、重複は脇によけて次回は次の投稿へ進む。
            skipped = os.path.join(POSTED_ROOT, args.channel, "_duplicates")
            os.makedirs(skipped, exist_ok=True)
            os.replace(path, os.path.join(skipped, os.path.basename(path)))
        return 1

    # Atomic claim prevents concurrent workers and retries after an uncertain delivery.
    # Keep the marker on any failure: a timeout can mean the platform accepted it.
    marker = path + ".sending"
    if not args.dry_run:
        try:
            with open(marker, "x", encoding="utf-8") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
        except FileExistsError:
            print(f"{tag} 送信中または送信結果未確認です。再送前に投稿先を確認してください: {path}", file=sys.stderr)
            return 1

    try:
        result = SENDERS[args.channel](load_env(), media, body, args.dry_run)
    except RuntimeError as e:
        print(f"{tag} 投稿できませんでした: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"{tag} [dry-run] {os.path.basename(path)} -> {result}")
        print("--- 本文 ---")
        print(body)
        return 0

    print(f"{tag} {os.path.basename(path)} (承認 {approved}) -> {result}")
    # Save delivery evidence and remove from the queue before any remote bookkeeping.
    receipt = {"channel": args.channel, "result": result,
               "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "status": "buffer_queued" if args.channel == "instagram" else "posted"}
    receipt_path = path + ".receipt.json"
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    if not args.file:
        mark_posted(args.channel, path)
        os.replace(receipt_path, os.path.join(POSTED_ROOT, args.channel, os.path.basename(receipt_path)))
        os.remove(marker)
        print(f"{tag} 送信記録を保存し、処理済みへ移動しました。")
    # --file keeps the claim so repeating the same command cannot publish twice.
    page_id = notion_page_of(raw)
    if page_id:
        # Notionの見た目と実態がずれないよう、投稿できたことを書き戻す。
        # ここで失敗しても投稿自体は済んでいるので、警告に留めて続ける。
        try:
            import notion_sync
            env = load_env()
            token = env.get("NOTION_TOKEN") or os.environ.get("NOTION_TOKEN")
            if token:
                page = notion_sync.api(token, "GET", f"/pages/{page_id}")
                props = page.get("properties", {})
                state_name = next(name for name in (notion_sync.STATE_PROP, "Status", "ステータス") if name in props)
                state = notion_sync.STATE_BUFFER_QUEUED if args.channel == "instagram" else notion_sync.STATE_POSTED
                notion_sync.set_state(token, page_id, state, props[state_name]["type"], state_name)
                print(f"{tag} Notionを{state}へ更新しました。")
        except Exception as e:  # noqa: BLE001
            print(f"{tag} Notionの更新に失敗しました（送信先への登録は完了しています）: {e}",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
