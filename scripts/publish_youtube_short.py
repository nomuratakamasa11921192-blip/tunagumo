#!/usr/bin/env python3
"""
ツナグモ YouTube Shorts投稿スクリプト(YouTube Data API v3)

YouTubeへの動画アップロードは「自分のチャンネルに、自分が作ったアプリでアップロードする」
だけであれば、Google側の本審査(OAuth同意画面の公開審査)は不要。
Google Cloud Consoleで自分用のOAuthクライアントを作るだけで使える。

前提(初回のみ、ユーザー側の作業):
  1. https://console.cloud.google.com/ で新規プロジェクトを作成
  2. 「APIとサービス」→「ライブラリ」→ "YouTube Data API v3" を有効化
  3. 「APIとサービス」→「OAuth同意画面」
     - User Type: 外部
     - 公開ステータスは「テスト」のままでよい(審査不要)
     - テストユーザーに自分のGoogleアカウント(YouTubeチャンネルのオーナー)を追加
  4. 「認証情報」→「認証情報を作成」→「OAuthクライアントID」
     - アプリケーションの種類: デスクトップアプリ
     - 作成後、JSONをダウンロード
  5. ダウンロードしたJSONを、Gitに含まれない場所に保存
     (例: private/youtube_client_secret.json ※ private/ は既に管理対象外)
  6. .env に以下を設定:
       YOUTUBE_CLIENT_SECRET_PATH=private/youtube_client_secret.json
       YOUTUBE_TOKEN_PATH=private/youtube_token.json

  7. 必要なライブラリをインストール:
       pip install google-auth-oauthlib google-api-python-client

使い方:
  python scripts/publish_youtube_short.py --video website/assets/instagram/reel1_intro.mp4 \
    --title "求人票の作成が、2時間から30分に" \
    --description-file sales/post_captions/reel1.txt \
    --privacy private

  --privacy は private / unlisted / public から選ぶ(デフォルト private)。
  初回実行時はブラウザが開き、Googleアカウントでの認証を求められる
  (「このアプリはGoogleで確認されていません」と出るが、自分のアプリなので
  「詳細」→「安全でないページに移動」で進めて問題ない)。
  2回目以降はトークンが保存されているため、認証は不要。

このスクリプトは確認なしでは絶対に投稿しない(--yes を明示的に渡さない限り、
アップロード前に必ず一時停止してプレビューを表示する)。
"""

import argparse
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


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


def get_authenticated_service(client_secret_path, token_path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_path):
                print(f"[ERROR] クライアントシークレットが見つかりません: {client_secret_path}", file=sys.stderr)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def main():
    parser = argparse.ArgumentParser(description="ツナグモ YouTube Shorts投稿(要確認)")
    parser.add_argument("--video", required=True, help="アップロードする動画ファイルのパス")
    parser.add_argument("--title", required=True, help="動画タイトル")
    parser.add_argument("--description-file", help="概要欄に入れるテキストファイルのパス")
    parser.add_argument("--tags", help="カンマ区切りのタグ(任意)")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private",
                         help="公開設定(デフォルト: private)")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトをスキップして即実行する(通常は使わない)")
    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"[ERROR] 動画ファイルが見つかりません: {args.video}", file=sys.stderr)
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    env = load_env(os.path.join(project_root, ".env"))

    client_secret_path = env.get("YOUTUBE_CLIENT_SECRET_PATH") or os.environ.get("YOUTUBE_CLIENT_SECRET_PATH")
    token_path = env.get("YOUTUBE_TOKEN_PATH") or os.environ.get("YOUTUBE_TOKEN_PATH")
    if not client_secret_path or not token_path:
        print("[ERROR] .env に YOUTUBE_CLIENT_SECRET_PATH と YOUTUBE_TOKEN_PATH を設定してください。",
              file=sys.stderr)
        sys.exit(1)

    description = ""
    if args.description_file:
        if not os.path.exists(args.description_file):
            print(f"[ERROR] 概要欄ファイルが見つかりません: {args.description_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.description_file, "r", encoding="utf-8") as f:
            description = f.read().strip()

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    print("=" * 60)
    print("これから以下の内容でYouTubeにアップロードします:")
    print("-" * 60)
    print(f"動画ファイル: {args.video}")
    print(f"タイトル: {args.title}")
    print(f"公開設定: {args.privacy}")
    print("概要欄:")
    print(description if description else "(なし)")
    print("=" * 60)

    if not args.yes:
        answer = input("この内容で本当にアップロードしますか? (yes と入力すると実行します): ")
        if answer.strip().lower() != "yes":
            print("キャンセルしました。何もアップロードしていません。")
            sys.exit(0)

    youtube = get_authenticated_service(client_secret_path, token_path)

    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": args.title,
            "description": description,
            "tags": tags,
        },
        "status": {
            "privacyStatus": args.privacy,
        },
    }
    media = MediaFileUpload(args.video, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"アップロード中... {int(status.progress() * 100)}%")

    print(f"アップロード完了: https://youtube.com/watch?v={response['id']}")


if __name__ == "__main__":
    main()
