"""マイソク(募集図面・チラシ)PDFの自動生成(13位)。

物件情報のテキストと画像URLをHTMLテンプレートに流し込み、PDF化する。凝ったテンプレート
エンジン(Jinja2等)は使わず、単純な文字列置換で十分なため新規依存を増やさない。
PDF化にはWeasyPrint(Pure Python、HTML+CSS→PDF)を使う。
"""

import html
from dataclasses import dataclass, field

import weasyprint

MAX_FLYER_IMAGES = 6


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
  .images {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }}
  .images img {{ width: 47%; height: 90mm; object-fit: cover; border-radius: 4px; }}
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


def generate_flyer_pdf(data: FlyerData) -> bytes:
    html_content = _render_html(data)
    try:
        return weasyprint.HTML(string=html_content).write_pdf()
    except Exception as e:
        raise FlyerGenerationError(f"マイソクPDFの生成に失敗しました: {e}") from e
