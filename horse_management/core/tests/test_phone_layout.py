"""The phone app, phase 3: key information first.

docs/MOBILE_APP_PLAN.md, items C1 to C12. The top bar itself is in
test_ui_review_fixes.PhoneTopBarTests.
"""

import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access


def tab_bar(html):
    start = html.index('id="mobile-tab-bar"')
    return html[start:html.index('</nav>', start)]


def more_sheet(html):
    start = html.index('id="more-sheet"')
    return html[start:html.index('sheet-safe-bottom', start)]


class HealthTabTests(TestCase):
    """C2: with no Training tab, Health takes the free place on the bar."""

    def page(self, url='dashboard'):
        return self.client.get(reverse(url)).content.decode()

    @override_settings(TRAINING_ENABLED=False)
    def test_without_training_health_is_a_tab_and_not_in_more(self):
        self.client.force_login(make_admin('boss'))
        html = self.page()
        self.assertIn('id="tab-health"', tab_bar(html))
        self.assertNotIn('id="tab-training"', tab_bar(html))
        self.assertNotIn(reverse('health_dashboard') + '"', more_sheet(html))
        # Farrier and Breeding stay in More.
        self.assertIn(reverse('farrier_list'), more_sheet(html))
        self.assertIn(reverse('breeding_list'), more_sheet(html))

    @override_settings(TRAINING_ENABLED=True)
    def test_with_training_the_tab_is_training_and_health_is_in_more(self):
        self.client.force_login(make_admin('boss'))
        html = self.page()
        self.assertIn('id="tab-training"', tab_bar(html))
        self.assertNotIn('id="tab-health"', tab_bar(html))
        self.assertIn(reverse('health_dashboard') + '"', more_sheet(html))

    @override_settings(TRAINING_ENABLED=True)
    def test_a_role_with_no_training_gets_the_health_tab(self):
        self.client.force_login(make_user_with_access('groom', horses='view', health='view'))
        html = self.page('horse_list')
        self.assertIn('id="tab-health"', tab_bar(html))
        self.assertNotIn('id="tab-training"', tab_bar(html))

    @override_settings(TRAINING_ENABLED=False)
    def test_a_role_with_no_health_gets_no_fourth_tab(self):
        self.client.force_login(make_user_with_access('office', horses='view'))
        html = self.page('horse_list')
        self.assertNotIn('id="tab-health"', tab_bar(html))

    @override_settings(TRAINING_ENABLED=False)
    def test_the_tab_is_marked_on_health_pages_and_not_on_farrier_pages(self):
        self.client.force_login(make_admin('boss'))
        marked = 'class="tab-bar-item active"'
        on_health = tab_bar(self.page('health_dashboard'))
        tab = on_health[on_health.index('id="tab-health"'):]
        self.assertIn(marked, tab[:400])
        on_farrier = tab_bar(self.page('farrier_list'))
        tab = on_farrier[on_farrier.index('id="tab-health"'):]
        self.assertNotIn(marked, tab[:400])
        # The script that marks tabs after a tap has the same rule.
        self.assertIn('data-nav-skip="/health/farrier /health/breeding/"', on_farrier)


class TabletTests(SimpleTestCase):
    """C12: a tablet on a stand can turn, and gets no keyboard hint."""

    base = Path(settings.BASE_DIR)

    def test_the_installed_app_is_not_locked_to_portrait(self):
        manifest = json.loads((self.base / 'static' / 'manifest.json').read_text())
        self.assertNotIn('orientation', manifest)

    def test_the_press_slash_hint_hides_on_a_touch_screen(self):
        css = (self.base / 'static' / 'css' / 'input.css').read_text()
        self.assertRegex(css, r'@media \(pointer: coarse\) \{\s*\.search-key-hint \{ display: none !important; \}')
        search = (self.base / 'templates' / 'includes' / '_app_search.html').read_text()
        self.assertIn('class="search-key-hint ', search)
