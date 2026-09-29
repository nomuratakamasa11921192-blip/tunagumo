"""X v2 MP4 upload. Called only after auto_post has checked approval.

Uses a user OAuth2 token with media.write, tweet.read, tweet.write and users.read.
Never obtains tokens, changes permissions, or retries uncertain writes.
Reference: https://docs.x.com/x-api/media/quickstart/media-upload-chunked
"""
import json
import math
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = 'https://api.x.com/2'
EXPECTED_USERNAME = 'tunagumo_com'
CHUNK_BYTES = 4 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024


def validate_video(path):
    path = Path(path)
    if path.suffix.lower() != '.mp4' or not path.is_file():
        raise RuntimeError('X動画にはローカルのMP4ファイルを指定してください。')
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise RuntimeError('X動画は空でなく512MB以下のファイルを指定してください。')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('動画確認用のffprobeがありません。送信はしていません。')
    try:
        result = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
            'format=duration:stream=codec_type,codec_name', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=30, check=True)
        probe = json.loads(result.stdout)
        duration = float(probe['format']['duration'])
        videos = [s for s in probe['streams'] if s['codec_type'] == 'video']
        audio = [s for s in probe['streams'] if s['codec_type'] == 'audio']
        if (not math.isfinite(duration) or not 0 < duration <= 140
                or len(videos) != 1 or videos[0]['codec_name'] != 'h264'
                or any(s['codec_name'] != 'aac' for s in audio)):
            raise ValueError('Unsupported encoding')
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise RuntimeError('X動画は140秒以内のH.264・AAC形式で確認してください。') from exc
    return path.resolve()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def oauth1_header(method, url, creds):
    """OAuth 1.0a の署名。JSONやmultipartの本文は署名に含めず、URLのクエリだけを含める(X API v2の仕様)。"""
    import base64, hashlib, hmac
    quote = lambda v: urllib.parse.quote(str(v), safe='~')
    parsed = urllib.parse.urlsplit(url)
    oauth = {'oauth_consumer_key': creds['X_API_KEY'], 'oauth_nonce': secrets.token_hex(16),
             'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
             'oauth_token': creds['X_ACCESS_TOKEN'], 'oauth_version': '1.0'}
    params = list(oauth.items()) + urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    base_params = '&'.join(f'{quote(k)}={quote(v)}' for k, v in sorted((quote(k), quote(v)) for k, v in params))
    base_url = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'
    base = '&'.join([method.upper(), quote(base_url), quote(base_params)])
    key = f"{quote(creds['X_API_SECRET'])}&{quote(creds['X_ACCESS_TOKEN_SECRET'])}"
    oauth['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(f'{quote(k)}="{quote(v)}"' for k, v in sorted(oauth.items()))


def request(token, method, path, payload=None, content_type='application/json'):
    """tokenはOAuth2のアクセストークン(文字列)か、OAuth1.0aの4つの鍵(dict)。"""
    if not token:
        raise RuntimeError('Xの動画用ユーザー認証が未設定です。送信はしていません。')
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('API path required')
    data = (json.dumps(payload).encode('utf-8')
            if payload is not None and content_type == 'application/json' else payload)
    auth = oauth1_header(method, API_ROOT + path, token) if isinstance(token, dict) else f'Bearer {token}'
    req = urllib.request.Request(API_ROOT + path, method=method, data=data,
        headers={'Authorization': auth, 'Content-Type': content_type})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            raw = response.read()
        result = json.loads(raw.decode('utf-8')) if raw else {}
        if not isinstance(result, dict) or result.get('errors'):
            raise ValueError('Unexpected API response')
        return result
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'X API HTTP {exc.code}。投稿先を確認するまで再送しません。') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError('Xの通信結果を確認できません。投稿先を確認するまで再送しません。') from exc


def media_id_of(result):
    media_id = str(result.get('data', {}).get('id', ''))
    if not media_id.isascii() or not media_id.isdigit():
        raise RuntimeError('Xの動画IDを確認できません。投稿しません。')
    return media_id


def upload(token, path):
    size = path.stat().st_size
    initialized = request(token, 'POST', '/media/upload/initialize', {
        'media_type': 'video/mp4', 'total_bytes': size, 'media_category': 'tweet_video'})
    media_id = media_id_of(initialized)
    with path.open('rb') as stream:
        segment, sent = 0, 0
        while chunk := stream.read(CHUNK_BYTES):
            boundary = secrets.token_hex(24)
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="segment_index"\r\n\r\n{segment}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="chunk.mp4"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode('ascii')
            body += chunk + f'\r\n--{boundary}--\r\n'.encode('ascii')
            request(token, 'POST', f'/media/upload/{media_id}/append', body,
                    f'multipart/form-data; boundary={boundary}')
            segment += 1
            sent += len(chunk)
    if sent != size:
        raise RuntimeError('送信中に動画のサイズが変わりました。投稿しません。')
    finalized = request(token, 'POST', f'/media/upload/{media_id}/finalize')
    if media_id_of(finalized) != media_id:
        raise RuntimeError('動画の完了応答が一致しません。投稿しません。')
    processing = finalized['data'].get('processing_info')
    deadline = time.monotonic() + 120
    polls = 0
    while processing is not None:
        state = processing.get('state')
        if state == 'succeeded':
            break
        if state not in ('pending', 'in_progress'):
            raise RuntimeError('Xで動画を処理できませんでした。投稿しません。')
        delay = processing.get('check_after_secs', 1)
        if (isinstance(delay, bool) or not isinstance(delay, (int, float))
                or not math.isfinite(delay) or delay < 0 or delay > 60
                or time.monotonic() + max(1, delay) > deadline or polls >= 120):
            raise RuntimeError('Xの動画処理が制限時間内に完了しません。投稿しません。')
        time.sleep(max(1, delay))
        polls += 1
        result = request(token, 'GET', '/media/upload?' + urllib.parse.urlencode({
            'command': 'STATUS', 'media_id': media_id}))
        if media_id_of(result) != media_id:
            raise RuntimeError('動画の状態応答が一致しません。投稿しません。')
        processing = result['data'].get('processing_info')
        if processing is None:
            raise RuntimeError('Xの動画処理結果が不明です。投稿しません。')
    return media_id


def publish(token, path, parts):
    # Validate before the first request, including the read-only account lookup.
    path = validate_video(path)
    if not token:
        raise RuntimeError('Xの認証情報が未設定です。動画も本文も送信していません。')
    if not parts or any(not part.strip() for part in parts):
        raise RuntimeError('投稿本文がありません。')
    identity = request(token, 'GET', '/users/me').get('data', {})
    if identity.get('username', '').lower() != EXPECTED_USERNAME:
        raise RuntimeError('Xの接続先がtunagumo_comと一致しません。送信していません。')
    media_id = upload(token, path)
    first_id = reply_to = None
    for index, part in enumerate(parts):
        payload = {'text': part}
        if index == 0:
            payload['media'] = {'media_ids': [media_id]}
        if reply_to:
            payload['reply'] = {'in_reply_to_tweet_id': reply_to}
        result = request(token, 'POST', '/tweets', payload)
        reply_to = media_id_of(result)
        first_id = first_id or reply_to
    return f'https://x.com/i/status/{first_id}'
