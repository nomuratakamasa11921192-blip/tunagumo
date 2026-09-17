"""Browser regressions: python -m unittest discover -s saas/frontend/tests -v.

Requires Playwright and Chromium. All requests are intercepted locally;
no server, credentials, paid API calls or customer data are used.
"""
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

HTML = Path(__file__).resolve().parents[1] / 'index.html'


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        # A preinstalled Chromium can be selected for offline/slow-network setup.
        # When unset, use the browser installed by Playwright as before.
        try:
            cls.browser = cls.playwright.chromium.launch(
                executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None
            )
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.held = []
        self.delay = None
        self.session = dict(session_id='current', status='RUNNING', request_text='処理中の依頼', created_at='2026-09-17T00:00:00', result={})
        self.context.add_init_script("localStorage.setItem('tsunagumo_api_key', 'test-only')")
        self.page.route('**/*', self.route)
        self.page.goto('http://frontend.test/')
        expect(self.page.locator('#session-list li')).to_have_count(1)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def route(self, route):
        path = urlparse(route.request.url).path
        if (route.request.method, path) == self.delay:
            self.held.append(route)
            return
        if path == '/':
            route.fulfill(content_type='text/html', body=HTML.read_text(encoding='utf-8'))
            return
        data = {}
        if path == '/api/sessions':
            data = {'sessions': [self.session]}
        elif path == '/api/sessions/current':
            data = self.session
        elif path == '/api/account/usage':
            data = dict(this_month_session_count=3, all_time_session_count=8, plan='light',
                        ai_usage_percent=40, ai_remaining_percent=110, addon_purchased=True,
                        higgsfield_key_registered=False)
        elif path == '/api/account/inquiry-settings':
            data = dict(line_webhook_url='https://example.test/webhook')
        elif path == '/api/market-data/prefectures':
            data = []
        route.fulfill(json=data)

    def release(self, data=None, status=200):
        self.assertEqual(len(self.held), 1)
        self.held.pop().fulfill(status=status, content_type='application/json', body=json.dumps(data or {}))
        self.page.wait_for_timeout(100)

    def new_draft(self):
        self.page.click('#new-request-btn')
        self.page.fill('#new-text', '入力途中の文章を保持')

    def assert_draft(self):
        expect(self.page.locator('#new-text')).to_have_value('入力途中の文章を保持')

    def open_session(self, status='COMPLETED', result=None):
        self.session.update(status=status, result=result or {'reply': '完了'})
        self.page.click('#session-list li')
        expect(self.page.locator('#main-content')).to_contain_text('処理中の依頼')

    def test_delayed_detail_does_not_replace_new_draft(self):
        self.delay = ('GET', '/api/sessions/current')
        self.page.click('#session-list li')
        self.new_draft()
        self.release(self.session)
        self.assert_draft()

    def test_polling_response_does_not_replace_new_draft(self):
        self.open_session('RUNNING')
        self.delay = ('GET', '/api/sessions/current')
        self.page.wait_for_timeout(2200)
        self.new_draft()
        self.release(self.session)
        self.assert_draft()

    def test_answer_completion_does_not_return_to_previous_session(self):
        self.open_session('CLARIFYING', {'questions': ['希望は？']})
        self.delay = ('POST', '/api/sessions/current/answer')
        self.page.fill('.clarify-answer', '春日部')
        self.page.click('#clarify-submit')
        self.new_draft()
        self.release()
        self.assert_draft()

    def test_approval_completion_does_not_return_to_previous_session(self):
        self.open_session('AWAITING_APPROVAL', {'approval_summary': {'headline': '確認'}})
        self.delay = ('POST', '/api/sessions/current/approve')
        self.page.click('#approve-btn')
        self.new_draft()
        self.release()
        self.assert_draft()

    def test_image_completion_does_not_return_to_previous_session(self):
        self.open_session()
        self.delay = ('POST', '/api/sessions/current/generate-image')
        self.page.click('#generate-image-btn')
        self.new_draft()
        self.release()
        self.assert_draft()

    def test_image_failure_after_navigation_does_not_raise_dom_error(self):
        self.open_session()
        self.delay = ('POST', '/api/sessions/current/generate-image')
        self.page.click('#generate-image-btn')
        self.new_draft()
        self.release({'detail': '生成失敗'}, 500)
        self.assert_draft()

    def test_create_completion_preserves_another_new_draft(self):
        self.new_draft()
        self.delay = ('POST', '/api/sessions')
        self.page.click('#new-submit')
        self.new_draft()
        self.release({'session_id': 'current'})
        self.assert_draft()

    def test_import_completion_preserves_another_new_draft(self):
        self.new_draft()
        self.delay = ('POST', '/api/property-url/extract')
        self.page.fill('#property-url', 'https://example.com/property')
        self.page.click('#property-url-import')
        self.new_draft()
        self.release({'text': '古い取込結果', 'image_urls': []})
        self.assert_draft()

    def test_settings_response_after_navigation_does_not_raise_dom_error(self):
        self.delay = ('GET', '/api/account/usage')
        self.page.click('#settings-btn')
        self.new_draft()
        self.release(dict(plan='light', ai_usage_percent=0, ai_remaining_percent=100))
        self.assert_draft()

    def test_usage_displays_percentages_without_cost(self):
        self.page.click('#settings-btn')
        usage = self.page.locator('#usage-body')
        expect(usage).to_contain_text('40%')
        expect(usage).to_contain_text('110%')
        expect(usage).to_contain_text('追加購入分を含む')
        expect(usage).not_to_contain_text('$')
        expect(usage).not_to_contain_text('NaN')

    def test_answer_still_polls_and_displays_completion_on_same_view(self):
        self.open_session('CLARIFYING', {'questions': ['希望は？']})
        self.delay = ('POST', '/api/sessions/current/answer')
        self.page.fill('.clarify-answer', '春日部')
        self.page.click('#clarify-submit')
        self.session.update(status='COMPLETED', result={'reply': '回答を反映しました'})
        self.release()
        expect(self.page.locator('#main-content')).to_contain_text('回答を反映しました')


if __name__ == '__main__':
    unittest.main()
