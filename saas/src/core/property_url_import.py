"""物件ページのURLから、依頼の下書き(テキスト・画像URL)を抽出する。

顧客が物件ポータルや自社サイトのURLを貼るだけで、依頼内容の入力を省力化する
機能(Phase 2)。他社ポータルの多くは自動的なアクセス(クローリング)を利用規約で
禁止しているため、以下の点で防御的に実装している:
  - robots.txtを尊重し、Disallowされているパスは取得しない
  - bot判定を回避する目的の偽装(User-Agent詐称等)はしない。正直に自社の
    UAを名乗る
  - 顧客が能動的に1件ずつ貼ったときだけ取得する(自動巡回・大量取得はしない)
  - 取得した内容を利用する権利は顧客自身に確認してもらう(呼び出し元のAPI
    レスポンス・UIで注記を表示すること)
"""

import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "tsunagumo-property-import/1.0 (+https://tunagumo.com; AI SaaS for real estate)"
FETCH_TIMEOUT_SECONDS = 15.0
MAX_TEXT_CHARS = 4000
MAX_IMAGE_URLS = 8


class PropertyImportError(Exception):
    pass


async def _is_allowed_by_robots(client: httpx.AsyncClient, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        res = await client.get(robots_url, timeout=FETCH_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        # robots.txtが取得できない場合は、存在しない(=許可)とみなす
        return True
    if res.status_code >= 400:
        return True

    parser = RobotFileParser()
    parser.parse(res.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _extract_text_and_images(html: str, base_url: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    raw_text = soup.get_text(separator="\n")
    lines = [line.strip() for line in raw_text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)[:MAX_TEXT_CHARS]

    image_urls: list[str] = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        absolute = urljoin(base_url, src)
        if absolute in seen:
            continue
        seen.add(absolute)
        image_urls.append(absolute)
        if len(image_urls) >= MAX_IMAGE_URLS:
            break

    return text, image_urls


async def import_property_url(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """物件ページのURLから、本文テキストと画像URL一覧を抽出して返す。
    取得できない/拒否された場合はPropertyImportErrorを送出する。

    transportはテスト用のフック(httpx.MockTransportを渡すと実際のネットワーク
    アクセスをせずに検証できる)。本番では常に未指定(=実際に取得)で呼ぶ。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PropertyImportError("有効なURLを入力してください(http/httpsのみ対応)。")

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=FETCH_TIMEOUT_SECONDS,
        transport=transport,
    ) as client:
        if not await _is_allowed_by_robots(client, url):
            raise PropertyImportError(
                "このサイトはrobots.txtで自動取得を許可していないため、取得できませんでした。"
                "お手数ですが、内容を手動でコピーしてご入力ください。"
            )

        try:
            res = await client.get(url)
        except httpx.HTTPError as e:
            raise PropertyImportError(f"ページの取得に失敗しました: {e}") from e

        if res.status_code >= 400:
            raise PropertyImportError(f"ページの取得に失敗しました(status={res.status_code})。")

        content_type = res.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise PropertyImportError("HTMLページではないため、内容を抽出できませんでした。")

        text, image_urls = _extract_text_and_images(res.text, url)
        if not text:
            raise PropertyImportError("ページから本文を抽出できませんでした。")

        return {"text": text, "image_urls": image_urls}
