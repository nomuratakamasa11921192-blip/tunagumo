"""X(旧Twitter)の動画投稿用に、OAuth2のユーザー認証を取得・更新する(2026-09-29)。

初回(本人が1回だけ実行): python scripts/x_oauth2_login.py
  ブラウザでXの許可画面が開くので「許可する」を押す。受け取ったトークンを .env に保存する。
更新(自動): python scripts/x_oauth2_login.py --refresh
  アクセストークンは約2時間で切れるため、auto_post.py が投稿前に refresh_if_needed() を呼ぶ。

必要な .env:
  X_OAUTH2_CLIENT_ID      … X Developer Console のアプリの「OAuth 2.0 Client ID」
  X_OAUTH2_CLIENT_SECRET  … 同「Client Secret」(公開クライアントなら空でよい)
アプリ側のコールバックURLに http://127.0.0.1:8723/callback を登録しておくこと。
トークンの値は画面にもログにも表示しない。
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import webbrowser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO_ROOT, ".env")
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:8723/callback"
SCOPES = "tweet.read tweet.write users.read media.write offline.access"


def read_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def write_env(updates):
    """既存の行を保ったまま、指定したキーだけ書き換える(無ければ末尾に追加)。"""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def token_request(env, data):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    client_id = env["X_OAUTH2_CLIENT_ID"]
    secret = env.get("X_OAUTH2_CLIENT_SECRET", "")
    if secret:
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    else:
        data = {**data, "client_id": client_id}
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        # Xの返す理由(error, error_description)だけを表示する。トークンや秘密値は含まれない。
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Xのトークン取得に失敗しました(HTTP {e.code}): {detail}") from None


def save_tokens(result):
    updates = {
        "X_OAUTH2_USER_ACCESS_TOKEN": result["access_token"],
        "X_OAUTH2_EXPIRES_AT": str(int(time.time()) + int(result.get("expires_in", 7200))),
    }
    if result.get("refresh_token"):
        updates["X_OAUTH2_REFRESH_TOKEN"] = result["refresh_token"]
    write_env(updates)


def refresh_if_needed(margin_seconds=300):
    """期限が近ければ更新し、使えるアクセストークンを返す。更新できなければ例外。"""
    env = read_env()
    token = env.get("X_OAUTH2_USER_ACCESS_TOKEN", "")
    expires = int(env.get("X_OAUTH2_EXPIRES_AT", "0") or 0)
    if token and time.time() < expires - margin_seconds:
        return token
    if not env.get("X_OAUTH2_REFRESH_TOKEN") or not env.get("X_OAUTH2_CLIENT_ID"):
        raise RuntimeError("Xの認証が切れています。python scripts/x_oauth2_login.py を実行してください。")
    result = token_request(env, {"grant_type": "refresh_token", "refresh_token": env["X_OAUTH2_REFRESH_TOKEN"]})
    save_tokens(result)
    return result["access_token"]


def login():
    env = read_env()
    if not env.get("X_OAUTH2_CLIENT_ID"):
        print(".env に X_OAUTH2_CLIENT_ID を設定してから実行してください。")
        return 1
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": env["X_OAUTH2_CLIENT_ID"], "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>ツナグモ: Xの認証を受け取りました。この画面は閉じて大丈夫です。</p>".encode("utf-8"))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8723), Handler)
    print("ブラウザでXの許可画面を開きます。「アプリにアクセスを許可」を押してください。")
    webbrowser.open(url)
    # ブラウザはfavicon等の別リクエストも送るため、認可コードかエラーが届くまで待つ(最大5分)。
    server.timeout = 5
    deadline = time.time() + 300
    while time.time() < deadline and "code" not in received and "error" not in received:
        server.handle_request()
    if received.get("state") != state or "code" not in received:
        print(f"認証を受け取れませんでした: {received.get('error', '不明')}")
        return 1
    result = token_request(env, {"grant_type": "authorization_code", "code": received["code"],
                                 "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
    save_tokens(result)
    print("Xの認証を .env に保存しました(値は表示しません)。")
    return 0


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        refresh_if_needed(margin_seconds=10**9)
        print("Xの認証を更新しました。")
        sys.exit(0)
    sys.exit(login())
