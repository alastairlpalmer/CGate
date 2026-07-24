"""Regression tests: health dashboard htmx controls must override the
boosted <body>'s inherited hx-select="#main-content".

The tab buttons, per-tab filter forms, Clear buttons and pagination all
target #health-table-area, and the view answers those htmx requests with
a bare partial that contains no #main-content — with the inherited
hx-select the swap selected nothing and blanked the table area (same
class of bug as the create-invoice blank page).
"""

from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin

HTMX_HEADERS = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'health-table-area'}

TABS = ['vaccinations', 'farrier', 'worming', 'egg_counts', 'conditions', 'vet_visits']


class HealthDashboardHtmxInheritanceTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())

    def test_tab_buttons_disinherit_hx_select(self):
        response = self.client.get(reverse('health_dashboard'))
        content = response.content.decode()
        # One overriding tab button per tab (+ overview)
        self.assertGreaterEqual(
            content.count('hx-target="#health-table-area" hx-select="unset"'),
            len(TABS),
        )

    def test_htmx_tab_request_returns_partial_not_full_page(self):
        for tab in TABS:
            with self.subTest(tab=tab):
                response = self.client.get(
                    reverse('health_dashboard'), {'type': tab}, **HTMX_HEADERS
                )
                content = response.content.decode()
                self.assertNotIn('id="main-content"', content)
                self.assertNotIn('<html', content)

    def test_partial_filter_controls_disinherit_hx_select(self):
        """The filter form and Clear button arrive inside the swapped
        partial itself, so they too must carry the override."""
        for tab in TABS:
            with self.subTest(tab=tab):
                response = self.client.get(
                    reverse('health_dashboard'), {'type': tab}, **HTMX_HEADERS
                )
                content = response.content.decode()
                self.assertGreaterEqual(
                    content.count(
                        'hx-target="#health-table-area" hx-select="unset"'
                    ),
                    2,
                    f'{tab} partial is missing hx-select="unset" overrides',
                )
