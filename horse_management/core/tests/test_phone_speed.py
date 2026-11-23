"""The phone app, phase 2: faster taps.

docs/MOBILE_APP_PLAN.md, items B1 to B8. tools/qa/phone_review.js checks
the size of one tap in a browser; these tests hold the server's part.
"""

import gzip
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.roles_testutils import make_admin

BOOSTED = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_BOOSTED': 'true', 'HTTP_HX_TARGET': 'main-content'}


class SlimPageTests(TestCase):
    """B1: a tap gets the page without its frame."""

    def setUp(self):
        self.client.force_login(make_admin('boss'))

    def test_a_tap_gets_the_title_the_messages_and_the_content(self):
        html = self.client.get(reverse('horse_list'), **BOOSTED).content.decode()
        self.assertRegex(html, r'<title>[^<]+</title>')
        self.assertEqual(html.count('id="toast-container"'), 1)
        self.assertEqual(html.count('<main id="main-content"'), 1)

    def test_a_tap_does_not_get_the_frame(self):
        html = self.client.get(reverse('dashboard'), **BOOSTED).content.decode()
        for part in ('<script', '<link rel="stylesheet"', 'id="mobile-tab-bar"', 'id="more-sheet"',
                     'id="popup-panel"', 'data-sidebar-toggle', 'class="app-bar', 'id="offline-mark"',
                     '<symbol', '<footer'):
            with self.subTest(part=part):
                self.assertNotIn(part, html)

    def test_a_page_load_gets_the_frame(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        for part in ('js/htmx.min.js', 'css/styles.css', 'id="mobile-tab-bar"', 'id="more-sheet"',
                     'data-sidebar-toggle', 'class="app-bar', 'id="offline-mark"'):
            with self.subTest(part=part):
                self.assertIn(part, html)

    def test_the_content_is_the_same_for_a_tap_and_a_page_load(self):
        import re

        def content(response):
            main = re.search(r'<main id="main-content".*</main>', response.content.decode(), re.S).group(0)
            # The CSRF token is masked again for each response.
            return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', '', main)

        url = reverse('horse_list')
        self.assertEqual(content(self.client.get(url)), content(self.client.get(url, **BOOSTED)))

    def test_a_tap_is_much_smaller(self):
        url = reverse('account_security')
        full = len(self.client.get(url).content)
        slim = len(self.client.get(url, **BOOSTED).content)
        self.assertLess(slim, full / 5)

    def test_a_message_reaches_a_tap(self):
        # A save answers with a redirect; htmx follows it with the same
        # headers, and the message must be in the short page.
        response = self.client.post(reverse('sign_out_other_devices'), follow=True, **BOOSTED)
        html = response.content.decode()
        self.assertTrue('You were not signed in anywhere else.' in html, 'the message is not in the short page')
        self.assertTrue('id="mobile-tab-bar"' not in html, 'the answer after the redirect is not short')

    def test_a_back_that_htmx_fetches_again_gets_the_whole_page(self):
        # htmx puts this answer in place of the whole <body>.
        html = self.client.get(
            reverse('horse_list'), HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true',
        ).content.decode()
        self.assertIn('id="mobile-tab-bar"', html)
        self.assertIn('js/htmx.min.js', html)

    def test_the_403_page_is_short_for_a_tap_and_still_has_its_content(self):
        from core.roles_testutils import make_viewer

        self.client.force_login(make_viewer('reader'))
        response = self.client.get(reverse('horse_create'), **BOOSTED)
        self.assertEqual(response.status_code, 403)
        html = response.content.decode()
        self.assertIn('id="main-content"', html)
        self.assertIn('Permission Denied', html)
        self.assertNotIn('id="mobile-tab-bar"', html)


class SlimPageCacheTests(TestCase):
    """B1: a browser must not show the short page for a page load."""

    def setUp(self):
        self.client.force_login(make_admin('boss'))

    def test_every_page_says_it_depends_on_the_header(self):
        for extra in ({}, BOOSTED):
            with self.subTest(boosted=bool(extra)):
                response = self.client.get(reverse('horse_list'), **extra)
                self.assertIn('HX-Boosted', response['Vary'])

    def test_the_short_page_is_not_kept_by_the_browser(self):
        self.assertEqual(self.client.get(reverse('horse_list'), **BOOSTED)['Cache-Control'], 'no-store')

    def test_a_page_load_keeps_its_own_cache_rules(self):
        self.assertNotIn('no-store', self.client.get(reverse('horse_list')).get('Cache-Control', ''))

    def test_an_answer_that_is_not_a_page_is_left_alone(self):
        response = self.client.get(reverse('web_health_check'), **BOOSTED)
        self.assertNotIn('HX-Boosted', response.get('Vary', ''))
        self.assertNotIn('no-store', response.get('Cache-Control', ''))


class SizeBudgetTests(SimpleTestCase):
    """B5: what a phone downloads on its first visit has a limit.

    The sizes are as gzip sends them. A change that goes over a limit is
    not wrong by itself: first look for what can load later (a script
    that only one page needs loads with that page, as Chart.js and
    Leaflet do), and raise the limit only when nothing can.
    """

    STYLES_LIMIT = 45_000   # styles.css is 39 KB today
    SCRIPTS_LIMIT = 120_000  # the scripts in <head> are 107 KB today

    base = Path(settings.BASE_DIR)

    def compressed(self, path):
        return len(gzip.compress((self.base / 'static' / path).read_bytes(), 9))

    def test_the_stylesheet_is_under_its_limit(self):
        size = self.compressed('css/styles.css')
        self.assertLess(size, self.STYLES_LIMIT, f'styles.css is {size} bytes compressed')

    def test_the_scripts_every_page_loads_are_under_their_limit(self):
        head = (self.base / 'templates' / 'base.html').read_text().split('</head>')[0]
        scripts = re.findall(r"<script[^>]*src=\"{% static_v '(js/[^']+)' %}\"", head)
        self.assertGreater(len(scripts), 10)
        size = sum(self.compressed(path) for path in scripts)
        self.assertLess(size, self.SCRIPTS_LIMIT, f'{len(scripts)} scripts, {size} bytes compressed')
