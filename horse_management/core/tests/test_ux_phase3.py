"""Lists that orient and scan well (docs/UX_REVIEW_PLAN.md, phase 3).

List pages show their title on a wide screen, a page's own search box
reads as a filter, location cards say which horses are there, and a phone
horse row keeps one button so the name has the width.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin


class PhaseThreeTestCase(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Roz Tobin')
        self.field = Location.objects.create(site='Colgate', name='Top', capacity=6)
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        for name in ('Dobbin', 'Ida', 'Bramble', 'Clover', 'Arrow'):
            self._place(name)
        self.client.force_login(make_admin())

    def _place(self, name, location=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location or self.field,
            rate_type=self.rate, start_date=date(2026, 7, 1),
        )
        return horse

    def body(self, name, **params):
        return self.client.get(reverse(name), params).content.decode()


class ListTitleTests(PhaseThreeTestCase):
    def test_list_pages_show_their_title_on_a_wide_screen(self):
        for name, title in (
            ('horse_list', 'Horses'), ('invoice_list', 'Invoices'),
            ('charge_list', 'Extra Charges'), ('location_list', 'Locations'),
            ('health_dashboard', 'Health'),
        ):
            with self.subTest(page=name):
                self.assertIn(
                    f'<h1 class="sr-only lg:not-sr-only page-title">{title}</h1>',
                    self.body(name),
                )


class FilterWordingTests(PhaseThreeTestCase):
    def test_page_search_says_filter(self):
        self.assertIn('placeholder="Filter invoices by owner or number…"', self.body('invoice_list'))
        self.assertIn('placeholder="Filter charges by', self.body('charge_list'))


class LocationCardTests(PhaseThreeTestCase):
    def test_card_names_the_first_horses_in_name_order(self):
        body = self.body('location_list', view='cards')
        self.assertIn('Arrow, Bramble, Clover', body)
        self.assertIn('+2 more', body)

    def test_an_empty_location_says_so(self):
        Location.objects.create(site='Colgate', name='Rested', capacity=4)
        self.assertIn('No horses', self.body('location_list', view='cards'))

    def test_a_moved_horse_is_named_on_its_new_card_only(self):
        other = Location.objects.create(site='Colgate', name='Bottom', capacity=6)
        horse = Horse.objects.get(name='Arrow')
        Placement.objects.filter(horse=horse).update(end_date=date(2026, 7, 31))
        Placement.objects.create(
            horse=horse, owner=self.owner, location=other,
            rate_type=self.rate, start_date=date(2026, 8, 1),
        )
        body = self.body('location_list', view='cards')
        self.assertIn('Bramble, Clover, Dobbin', body)
        self.assertIn('+1 more', body)
        self.assertRegex(body, r'>\s*Arrow\s*</p>')

    def test_more_cards_cost_no_more_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('location_list'), {'view': 'cards'})
            return len(ctx.captured_queries)

        before = queries()
        for n in range(3):
            loc = Location.objects.create(site='Colgate', name=f'Paddock {n}', capacity=4)
            self._place(f'Extra {n}', loc)
        self.assertEqual(queries(), before)


class PhoneRowTests(PhaseThreeTestCase):
    def test_move_is_in_the_row_menu_for_a_phone(self):
        body = self.body('horse_list')
        self.assertIn('class="btn-icon hidden md:inline-flex" title="Move"', body)
        self.assertIn('role="menuitem" class="more-menu-item md:hidden"', body)
