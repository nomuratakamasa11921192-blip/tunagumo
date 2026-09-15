"""マイソク(募集図面・チラシ)PDFの自動生成(13位)。

物件情報のテキストと画像URLをHTMLテンプレートに流し込み、PDF化する。凝ったテンプレート
エンジン(Jinja2等)は使わず、単純な文字列置換で十分なため新規依存を増やさない。
PDF化にはWeasyPrint(Pure Python、HTML+CSS→PDF)を使う。
"""

import html
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import weasyprint

from src.core.openai_image_client import (
    GENERATED_IMAGE_URL_PREFIX,
    connected_to_public_address,
    generated_image_path,
    is_public_host,
)

MAX_FLYER_IMAGES = 6
MAX_FLYER_IMAGE_BYTES = 20 * 1024 * 1024
# 相対URL(/api/generated-images/...)を解決するための内部専用のbase_url。
# 実在しないドメイン(.invalid)なので、外部へ通信することはない。
_INTERNAL_BASE_URL = "https://tsunagumo.invalid"


class FlyerGenerationError(Exception):
    pass


@dataclass
class FlyerData:
    title: str
    body_text: str
    image_urls: list[str] = field(default_factory=list)


_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{ font-family: "Noto Sans CJK JP", "Noto Sans JP", sans-serif; color: #1a1a1a; }}
  h1 {{ font-size: 20px; border-bottom: 3px solid #2b6cb0; padding-bottom: 6px; margin-bottom: 12px; }}
  /* flexだとWeasyPrintが画像を描画しない(63.1で確認)ため、2列のgridで並べる */
  .images {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }}
  .images img {{ width: 100%; height: 70mm; object-fit: cover; border-radius: 4px; }}
  .body-text {{ white-space: pre-wrap; font-size: 12.5px; line-height: 1.8; }}
  .footer {{ margin-top: 24px; font-size: 9px; color: #888; border-top: 1px solid #ddd; padding-top: 8px; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <div class="images">{images_html}</div>
  <div class="body-text">{body_text}</div>
  <div class="footer">
    本資料はツナグモによりAIが自動生成しました。記載内容の正確性は必ずご自身でご確認のうえご利用ください。
  </div>
</body>
</html>"""


def _render_html(data: FlyerData) -> str:
    images_html = "".join(
        f'<img src="{html.escape(u)}">' for u in data.image_urls[:MAX_FLYER_IMAGES]
    )
    return _TEMPLATE.format(
        title=html.escape(data.title),
        images_html=images_html,
        body_text=html.escape(data.body_text),
    )


def _safe_url_fetcher(url: str, timeout: int = 10, ssl_context=None) -> dict:
    """WeasyPrint用の画像取得処理。image_urlsは顧客が送ってくる値なので、標準の取得処理
    (file://やhttp://社内アドレスも読みに行ける)は使わず、次の2通りだけ許可する(SSRF対策)。
    - ツナグモが生成した画像(/api/generated-images/...): ディスクから直接読む
    - 公開IPのhttps画像: リダイレクトは追わず、画像形式・サイズ上限を確認して読む
    """
    if url.startswith(_INTERNAL_BASE_URL):
        path = generated_image_path(url.removeprefix(_INTERNAL_BASE_URL + GENERATED_IMAGE_URL_PREFIX))
        if path is None or not path.is_file():
            raise FlyerGenerationError("画像が見つかりません")
        return {"string": path.read_bytes(), "mime_type": "image/jpeg"}

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not is_public_host(parsed.hostname):
        raise FlyerGenerationError("読み込みできない画像URLです")
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", url) as res:
            if not connected_to_public_address(res):
                raise FlyerGenerationError("読み込みできない画像URLです")
            content_type = res.headers.get("content-type", "").split(";")[0].strip().lower()
            if res.status_code != 200 or not content_type.startswith("image/"):
                raise FlyerGenerationError("画像を読み込めませんでした")
            chunks: list[bytes] = []
            total = 0
            for chunk in res.iter_bytes():
                total += len(chunk)
                if total > MAX_FLYER_IMAGE_BYTES:
                    raise FlyerGenerationError("画像のサイズが大きすぎます")
                chunks.append(chunk)
    return {"string": b"".join(chunks), "mime_type": content_type}


def generate_flyer_pdf(data: FlyerData) -> bytes:
    html_content = _render_html(data)
    try:
        # 生成画像のURLは相対パスなので、内部用のbase_urlで絶対URLにしてから_safe_url_fetcherで
        # ディスクに振り替える。取得に失敗した画像はWeasyPrintが警告を出して読み飛ばす。
        return weasyprint.HTML(
            string=html_content, base_url=_INTERNAL_BASE_URL + "/", url_fetcher=_safe_url_fetcher
        ).write_pdf()
    except Exception as e:
        raise FlyerGenerationError(f"マイソクPDFの生成に失敗しました: {e}") from e
