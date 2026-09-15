"""顧客が入力したURLを取りに行くときの共通の安全対策(SSRF対策、2026-09-15)。

顧客が入力したURLをサーバーからそのまま取得すると、localhostや社内ネットワーク、
クラウドのメタデータ(169.254.169.254)など、外部から本来届かない場所の中身を読み出されて
しまう。そこで次の3段階で「公開IPアドレスにしか接続しない」ことを確認する。
1. 接続前に、ホスト名の解決先が全て公開IPかを確認する(is_public_host)
2. 実際に接続した相手のIPを、本文を読む前に再確認する(connected_to_public_address)。
   1と2の間にDNSの向き先を社内アドレスへ変える手口(DNSリバインディング)を防ぐ。
3. リダイレクトは自動では追わず、行き先ごとに1からやり直す(safe_get)
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx


class UnsafeURLError(Exception):
    """取得してはいけないURL(社内アドレス等)、または取得できなかった場合の例外。
    メッセージは顧客にそのまま表示してよい文面にする。"""


def is_public_host(host: str) -> bool:
    """ホスト名の解決先が全て公開IPならTrue。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False
    return bool(infos)


def connected_to_public_address(response: httpx.Response) -> bool:
    """実際に接続した相手のIPが公開IPならTrue。接続先が取れない場合(テスト用の
    MockTransport等)は事前確認(is_public_host)の結果に任せてTrueを返す。"""
    stream = response.extensions.get("network_stream")
    addr = stream.get_extra_info("server_addr") if stream is not None else None
    if not addr:
        return True
    return ipaddress.ip_address(addr[0].split("%")[0]).is_global


@dataclass
class SafeResponse:
    url: str  # リダイレクト後の最終URL
    status_code: int
    headers: httpx.Headers
    content: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        # contentは展開済みなので、Content-Encodingを外してから文字コードだけ解釈させる
        headers = [(k, v) for k, v in self.headers.multi_items() if k.lower() != "content-encoding"]
        return httpx.Response(self.status_code, headers=headers, content=self.content).text


async def safe_get(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    allowed_schemes: tuple[str, ...] = ("https",),
    max_redirects: int = 5,
    headers: dict | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SafeResponse:
    """公開IPアドレスにだけ接続してGETする。本文はmax_bytesまで(超えたらUnsafeURLError)。
    4xx/5xxはそのまま返すので、呼び出し元でstatus_codeを確認すること。"""
    # 環境変数のプロキシ設定を使うと接続先IPの再確認ができないため、trust_env=False。
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(url)
            if parsed.scheme not in allowed_schemes or not parsed.hostname:
                raise UnsafeURLError("このURLからは読み込めません。")
            if not await asyncio.to_thread(is_public_host, parsed.hostname):
                raise UnsafeURLError("このURLからは読み込めません。")
            try:
                async with client.stream("GET", url) as res:
                    if not connected_to_public_address(res):
                        raise UnsafeURLError("このURLからは読み込めません。")
                    if res.is_redirect and res.headers.get("location"):
                        url = urljoin(url, res.headers["location"])
                        continue
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in res.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise UnsafeURLError("データのサイズが大きすぎるため、読み込めませんでした。")
                        chunks.append(chunk)
                    return SafeResponse(url=url, status_code=res.status_code, headers=res.headers, content=b"".join(chunks))
            except httpx.HTTPError as e:
                raise UnsafeURLError("読み込みに失敗しました。URLをご確認ください。") from e
    raise UnsafeURLError("転送(リダイレクト)が多すぎるため、読み込めませんでした。")
