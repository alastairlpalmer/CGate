"""Tests for the `search` query param on list views.

Each list page exposes a shared live-search input (includes/search_bar.html);
these tests cover the server-side queryset filtering it relies on, and — by
going through the test client — that each list template still renders.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner
from invoicing.models import Invoice

User = get_user_model()


class ListSearchTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='searcher',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()

        cls.alice = Owner.objects.create(name='Alice Appleby', email='alice@example.com', phone='07700111222')
        cls.bob = Owner.objects.create(name='Bob Bramble', email='bob@stables.net', phone='07700999888')

        cls.paddock = Location.objects.create(name='Top Paddock', site='Colgate')
        cls.barn = Location.objects.create(name='Big Barn', site='Somerford')

        cls.dobbin = Horse.objects.create(name='Dobbin')
        cls.star = Horse.objects.create(name='Star')

        cls.invoice_a = Invoice.objects.create(
            owner=cls.alice, invoice_number='INV-0001',
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        )
        cls.invoice_b = Invoice.objects.create(
            owner=cls.bob, invoice_number='INV-0002',
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        )

        cls.charge_vet = ExtraCharge.objects.create(
            horse=cls.dobbin, owner=cls.alice, charge_type='vet',
            date=date(2026, 6, 1), description='Emergency callout', amount=120,
        )
        cls.charge_feed = ExtraCharge.objects.create(
            horse=cls.star, owner=cls.bob, charge_type='feed',
            date=date(2026, 6, 2), description='Hay bales', amount=45,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_owner_list_search_by_name(self):
        response = self.client.get(reverse('owner_list'), {'search': 'alice'})
        self.assertContains(response, 'Alice Appleby')
        self.assertNotContains(response, 'Bob Bramble')

    def test_owner_list_search_by_email_and_phone(self):
        response = self.client.get(reverse('owner_list'), {'search': 'stables.net'})
        self.assertEqual(list(response.context['owners']), [self.bob])
        response = self.client.get(reverse('owner_list'), {'search': '07700111'})
        self.assertEqual(list(response.context['owners']), [self.alice])

    def test_owner_list_search_no_match_shows_clear_link(self):
        response = self.client.get(reverse('owner_list'), {'search': 'zzz'})
        self.assertContains(response, 'No matches')

    def test_location_list_search_by_name_and_site(self):
        response = self.client.get(reverse('location_list'), {'search': 'paddock'})
        self.assertEqual(list(response.context['locations']), [self.paddock])
        response = self.client.get(reverse('location_list'), {'search': 'somerford'})
        self.assertEqual(list(response.context['locations']), [self.barn])

    def test_location_history_tab_ignores_search(self):
        response = self.client.get(reverse('location_list'), {'tab': 'history', 'search': 'paddock'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['locations']), 2)

    def test_invoice_list_search_by_number_and_owner(self):
        response = self.client.get(reverse('invoice_list'), {'search': 'INV-0001'})
        self.assertEqual(list(response.context['invoices']), [self.invoice_a])
        response = self.client.get(reverse('invoice_list'), {'search': 'bramble'})
        self.assertEqual(list(response.context['invoices']), [self.invoice_b])

    def test_charge_list_search_by_description_horse_owner(self):
        response = self.client.get(reverse('charge_list'), {'search': 'hay'})
        self.assertEqual(list(response.context['charges']), [self.charge_feed])
        response = self.client.get(reverse('charge_list'), {'search': 'dobbin'})
        self.assertEqual(list(response.context['charges']), [self.charge_vet])
        response = self.client.get(reverse('charge_list'), {'search': 'alice'})
        self.assertEqual(list(response.context['charges']), [self.charge_vet])

    def test_horse_list_renders_with_search(self):
        response = self.client.get(reverse('horse_list'), {'search': 'dobbin'})
        self.assertContains(response, 'Dobbin')
        self.assertNotContains(response, 'Star')

    def test_list_pages_render_without_search(self):
        for url_name in ('owner_list', 'location_list', 'invoice_list', 'charge_list', 'horse_list'):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)
            self.assertContains(response, 'id="list-results"', msg_prefix=url_name)
