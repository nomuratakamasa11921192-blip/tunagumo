"""Render editable HTML cards and a local review page. No network calls."""
import html
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
posts = json.loads((HERE / 'posts.json').read_text(encoding='utf-8'))
browser_path = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
with sync_playwright() as pw:
    browser = pw.chromium.launch(**({'executable_path': browser_path} if browser_path else {}))
    for i, post in enumerate(posts['instagram'], 1):
        page = browser.new_page(viewport={'width': 1080, 'height': 1350}, device_scale_factor=1)
        page.goto((HERE/'cards.html').as_uri())
        page.evaluate('(id) => document.querySelectorAll(".card").forEach(c => {if(c.id !== id) c.remove()})', f'card{i}')
        page.add_style_tag(content='html,body{overflow:hidden;overflow-anchor:none} #card3 .lead{margin:20px 0} #card3 .film{margin:24px 0 18px;padding:25px} #card3 .room{height:180px} #card3 .eyebrow{margin-top:35px} #card3 h1{font-size:65px} .card{margin:0}')
        page.evaluate('document.fonts.ready')
        page.evaluate('window.scrollTo(0,0)')
        page.evaluate('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
        page.evaluate('window.scrollTo(0,0)')
        bounds = page.locator(f'#card{i}').evaluate('(c) => ({height:c.clientHeight, scroll:c.scrollHeight, bottom:c.lastElementChild.getBoundingClientRect().bottom})')
        assert bounds['scroll'] == bounds['height'] and bounds['bottom'] <= 1350, bounds
        page.screenshot(path=str(ROOT/'website/assets/instagram'/post['asset']), type='jpeg', quality=95)
        page.close()
    browser.close()

parts = ['<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ツナグモ 投稿原稿13件</title>',
         '<style>body{font-family:Meiryo,sans-serif;max-width:1050px;margin:40px auto;padding:20px;background:#f6f4ef;color:#202b38}article{background:white;padding:28px;margin:24px 0;border-radius:12px}img{width:100%;max-width:432px}pre{white-space:pre-wrap;font:inherit;line-height:1.9}h1{font-size:30px}small{color:#657079}</style>',
         '<h1>ツナグモ｜SNS公開前プレビュー</h1><p>X 10件・Instagram 3件。すべて未承認・未投稿です。</p><p>推奨順：Xは番号順に1日1〜2件、Instagramは週2〜3件。LINEの一斉配信は別途対象と内容を確認します。</p>']
for channel, items in posts.items():
    for post in items:
        parts.append(f'<article><small>{channel}</small><h2>{html.escape(post["title"])}</h2>')
        if post.get('asset'):
            parts.append(f'<img src="../../website/assets/instagram/{post["asset"]}" alt="{html.escape(post["title"])}">')
        parts.append(f'<pre>{html.escape(post["body"])}</pre></article>')
parts.append('</html>')
(HERE/'preview.html').write_text('\n'.join(parts), encoding='utf-8')
print('Rendered 3 cards and preview.html')
