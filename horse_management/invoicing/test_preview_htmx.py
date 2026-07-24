"""Regression tests for the create-invoice live preview (blank-page bug).

The boosted <body> sets hx-select="#main-content", hx-swap="outerHTML" and
hx-push-url="true"; htmx inherits those onto the preview widgets' requests
unless each is overridden locally, which blanked the page and pushed the
partial's URL (CSRF token included) into the address bar.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.forms import PREVIEW_HTMX_ATTRS


class PreviewHtmxAttrsTests(TestCase):
    """The widget attrs must override every inherited boost attribute."""

    def test_inherited_boost_attributes_are_overridden(self):
        self.assertEqual(PREVIEW_HTMX_ATTRS['hx-select'], 'unset')
        self.assertEqual(PREVIEW_HTMX_ATTRS['hx-swap'], 'innerHTML')
        self.assertEqual(PREVIEW_HTMX_ATTRS['hx-push-url'], 'false')

    def test_csrf_token_excluded_from_get_params(self):
        self.assertIn('csrfmiddlewaretoken', PREVIEW_HTMX_ATTRS['hx-params'])
        self.assertTrue(PREVIEW_HTMX_ATTRS['hx-params'].startswith('not '))

    def test_create_page_renders_overrides_on_widgets(self):
        self.client.force_login(make_admin())
        response = self.client.get(reverse('invoice_create'))
        content = response.content.decode()
        self.assertIn('hx-select="unset"', content)
        self.assertIn('hx-push-url="false"', content)


class PreviewFullPageFallbackTests(TestCase):
    """A non-htmx (full page) hit on the partial endpoint must not render a
    bare fragment — users may still have the pushed URL in their history."""

    def setUp(self):
        self.staff = make_admin()
        self.owner = Owner.objects.create(name="Alice", email="a@example.com")
        loc = Location.objects.create(site="Colgate", name="Top")
        rate = RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))
        horse = Horse.objects.create(name="Ghost")
        Placement.objects.create(
            horse=horse, owner=self.owner, location=loc,
            rate_type=rate, start_date=date(2026, 1, 1),
        )
        self.client.force_login(self.staff)
        self.params = {
            'owner': self.owner.pk,
            'period_start': '2026-06-01',
            'period_end': '2026-06-30',
        }

    def test_full_page_request_redirects_to_create_form(self):
        response = self.client.get(reverse('invoice_preview'), self.params)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f"{reverse('invoice_create')}?owner={self.owner.pk}",
        )

    def test_full_page_request_without_owner_redirects_plain(self):
        response = self.client.get(reverse('invoice_preview'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('invoice_create'))

    def test_htmx_request_still_returns_partial(self):
        response = self.client.get(
            reverse('invoice_preview'), self.params, HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Amount Due')
        # £5/day × 30 days of June
        self.assertContains(response, '150.00')
