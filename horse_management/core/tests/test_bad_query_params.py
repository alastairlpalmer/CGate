"""Junk in a filter query string must give an empty/unfiltered page, not a
500. Every site here passed the raw string into an integer FK lookup
(``filter(owner_id='abc')`` raises ValueError at query time), or built a
date/template name from it.
"""

from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin


class BadQueryParamTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def _ok(self, url, **headers):
        resp = self.client.get(url, **headers)
        self.assertEqual(resp.status_code, 200, url)

    def test_horse_list_filters(self):
        self._ok(reverse('horse_list') + '?location=abc&owner=x')
        self._ok(reverse('horse_list') + '?tab=movements&location=abc&owner=x')
        self._ok(reverse('horse_list') + '?group_by=location&range=year&year=0')

    def test_location_usage_year_out_of_range(self):
        for year in ('0', '-1', '99999', 'abc'):
            self._ok(reverse('location_list') + f'?tab=usage&range=year&year={year}')

    def test_health_dashboard_filters(self):
        for tab in ('vaccinations', 'farrier', 'worming', 'egg_counts',
                    'conditions', 'vet_visits'):
            self._ok(reverse('health_dashboard') + f'?type={tab}&horse=abc')

    def test_health_dashboard_unknown_tab_partial(self):
        # The tab name used to be interpolated into a template path.
        for tab in ('zzz', '../../base'):
            self._ok(
                reverse('health_dashboard') + f'?type={tab}',
                HTTP_HX_REQUEST='true', HTTP_HX_TARGET='health-table-area',
            )

    def test_health_list_views(self):
        for name in ('vaccination_list', 'farrier_list', 'worming_list',
                     'egg_count_list', 'condition_list', 'vet_visit_list',
                     'breeding_list'):
            try:
                url = reverse(name)
            except Exception:
                continue
            self._ok(url + '?horse=abc')

    def test_invoicing_filters(self):
        self._ok(reverse('invoice_list') + '?owner=abc')
        self._ok(reverse('invoice_create') + '?owner=abc&period_start=2026-01-01&period_end=2026-01-31')

    def test_billing_filters(self):
        self._ok(reverse('charge_list') + '?horse=abc&owner=x')
        self._ok(reverse('charge_create') + '?horse=abc')
