"""The desktop sidebar (templates/includes/_sidebar.html).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP1: the search moved into the
sidebar, Support and Settings sit above the profile card, and the
collapsed rail no longer puts the logo and the expand button side by side.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access


def _sidebar(html):
    start = html.index('<aside class="app-sidebar')
    return html[start:html.index('</aside>', start)]


class SidebarContentTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('sidebarer', first_name='Anita', last_name='Cruz', email='anita@example.com')

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, name='horse_list'):
        return self.client.get(reverse(name)).content.decode()

    def test_search_is_in_the_sidebar(self):
        sidebar = _sidebar(self._get())
        self.assertIn('id="side-search-input"', sidebar)
        self.assertIn('id="side-search-results"', sidebar)
        self.assertIn(reverse('quick_find'), sidebar)

    def test_each_search_box_has_its_own_ids(self):
        """Two boxes (sidebar and phone bar), never one id twice."""
        html = self._get()
        for element_id in ('side-search-input', 'side-search-results',
                           'app-search-input', 'app-search-results'):
            with self.subTest(id=element_id):
                self.assertEqual(html.count(f'id="{element_id}"'), 1)

    def test_phone_search_box_is_hidden_on_a_computer(self):
        html = self._get()
        tag = re.search(r'<div data-app-search class="([^"]+)"', html)
        self.assertIsNotNone(tag)
        self.assertIn('lg:hidden', tag.group(1))

    def test_support_and_settings_sit_above_the_profile_card(self):
        sidebar = _sidebar(self._get())
        support = sidebar.index('mailto:help@c-gate.app')
        settings_link = sidebar.index(f'href="{reverse("app_settings")}"')
        card = sidebar.index('sidebar-user-card')
        nav_end = sidebar.index('User footer')
        self.assertLess(nav_end, support)
        self.assertLess(support, settings_link)
        self.assertLess(settings_link, card)

    def test_profile_card_shows_name_and_email(self):
        sidebar = _sidebar(self._get())
        self.assertIn('Anita Cruz', sidebar)
        self.assertIn('anita@example.com', sidebar)

    def test_profile_menu_keeps_sign_out_and_account_security(self):
        sidebar = _sidebar(self._get())
        self.assertIn(f'action="{reverse("logout")}"', sidebar)
        self.assertIn(reverse('account_security'), sidebar)

    def test_settings_marked_active_on_the_settings_page(self):
        sidebar = _sidebar(self._get('app_settings'))
        self.assertRegex(sidebar, r'class="nav-link-active" title="Settings"')


class SidebarSearchAccessTests(TestCase):

    def test_no_search_box_for_a_role_that_can_search_nothing(self):
        user = make_user_with_access('feedonly', feed='view')
        self.client.force_login(user)
        html = self.client.get(reverse('feed_dashboard')).content.decode()
        self.assertNotIn('side-search-input', html)
        self.assertNotIn('app-search-input', html)


class RailLayoutTests(SimpleTestCase):
    """The collapsed rail is 4rem with a 40px row. The logo (36px) and the
    expand button used to sit side by side there and overlapped."""

    css = (Path(settings.BASE_DIR) / 'static/css/input.css').read_text()
    base = (Path(settings.BASE_DIR) / 'templates/base.html').read_text()

    def _rule(self, selector):
        match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def test_expand_button_sits_on_top_of_the_logo(self):
        rule = self._rule('.sidebar-collapsed .sidebar-toggle')
        self.assertIn('position: absolute', rule)
        self.assertIn('opacity: 0', rule)

    def test_expand_button_shows_on_hover_and_keyboard_focus(self):
        self.assertIn('.sidebar-collapsed .sidebar-brand:hover .sidebar-toggle', self.css)
        self.assertIn('.sidebar-collapsed .sidebar-toggle:focus-visible', self.css)

    def test_slash_focuses_the_search_box_that_shows(self):
        self.assertIn("querySelectorAll('[data-search-input]')", self.base)
        self.assertIn('getClientRects().length', self.base)
