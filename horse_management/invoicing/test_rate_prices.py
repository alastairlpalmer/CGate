"""Rate prices from a chosen date (RatePrice)."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RatePrice, RateType
from core.roles_testutils import make_admin
from invoicing.services import InvoiceService

OCT = (date(2026, 10, 1), date(2026, 10, 31))


class Base(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Anna')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('10.00'))
        self.horse = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, rate_type=self.rate,
            location=Location.objects.create(site='S', name='Top'),
            start_date=date(2026, 9, 1),
        )

    def livery(self, owner, period):
        return InvoiceService.calculate_livery_charges(owner, *period)


class BillingTests(Base):

    def test_a_new_rate_has_its_price_from_the_start(self):
        self.assertEqual(self.rate.prices.get().daily_rate, Decimal('10.00'))
        self.assertEqual(self.livery(self.owner, OCT)[0]['amount'], Decimal('310.00'))

    def test_a_change_mid_month_bills_each_side_at_its_price(self):
        RatePrice.objects.create(rate_type=self.rate, daily_rate=Decimal('20.00'),
                                 effective_from=date(2026, 10, 11))
        lines = self.livery(self.owner, OCT)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['amount'], Decimal('10') * 10 + Decimal('20') * 21)
        self.assertIn('10 days at £10.00, 21 days at £20.00', lines[0]['description'])

    def test_the_days_before_the_change_keep_the_old_price(self):
        RatePrice.objects.create(rate_type=self.rate, daily_rate=Decimal('20.00'),
                                 effective_from=date(2026, 11, 1))
        self.assertEqual(self.livery(self.owner, OCT)[0]['amount'], Decimal('310.00'))

    def test_co_owners_billed_either_side_of_a_change_pay_the_same_rate(self):
        other = Owner.objects.create(name='Ben')
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=50,
                                      is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.horse, owner=other, share_percentage=50)
        first = self.livery(self.owner, OCT)[0]['amount']
        RatePrice.objects.create(rate_type=self.rate, daily_rate=Decimal('20.00'),
                                 effective_from=date(2026, 12, 1))
        second = self.livery(other, OCT)[0]['amount']
        self.assertEqual(first + second, Decimal('310.00'))


class SettingsPageTests(Base):

    def setUp(self):
        super().setUp()
        self.client.force_login(make_admin())
        self.url = reverse('rate_type_update', args=[self.rate.pk])

    def test_a_past_change_updates_the_shown_price(self):
        self.client.post(self.url, {
            'change_price': '1', 'daily_rate': '12.50',
            'effective_from': (timezone.localdate() - timedelta(days=3)).isoformat(),
        })
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.daily_rate, Decimal('12.50'))
        self.assertEqual(self.rate.prices.count(), 2)

    def test_a_future_change_is_planned_and_applied_on_the_day(self):
        from core.tasks import apply_rate_prices

        start = timezone.localdate() + timedelta(days=10)
        self.client.post(self.url, {
            'change_price': '1', 'daily_rate': '15.00', 'effective_from': start.isoformat(),
        })
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.daily_rate, Decimal('10.00'))
        self.assertContains(self.client.get(self.url), '(planned)')
        RatePrice.objects.filter(effective_from=start).update(effective_from=timezone.localdate())
        apply_rate_prices()
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.daily_rate, Decimal('15.00'))

    def test_the_edit_form_no_longer_changes_the_price_directly(self):
        self.client.post(self.url, {'name': 'Grass', 'daily_rate': '99', 'is_active': 'on'})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.daily_rate, Decimal('10.00'))

    def test_a_change_inside_issued_invoices_warns(self):
        InvoiceService.create_invoice(self.owner, *OCT)
        response = self.client.post(self.url, {
            'change_price': '1', 'daily_rate': '20.00', 'effective_from': '2026-10-15',
        }, follow=True)
        self.assertContains(response, 'already cover days from 15 Oct 2026')
        # The issued invoice keeps its price.
        self.assertEqual(self.owner.invoices.get().subtotal, Decimal('310.00'))

    def test_a_planned_change_can_be_removed(self):
        price = RatePrice.objects.create(rate_type=self.rate, daily_rate=Decimal('20.00'),
                                         effective_from=timezone.localdate() + timedelta(days=5))
        self.client.post(self.url, {'delete_price': price.pk})
        self.assertFalse(RatePrice.objects.filter(pk=price.pk).exists())
        first = self.rate.prices.get()
        self.client.post(self.url, {'delete_price': first.pk})
        self.assertTrue(RatePrice.objects.filter(pk=first.pk).exists())
