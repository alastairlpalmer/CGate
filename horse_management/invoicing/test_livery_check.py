"""Livery that an issued invoice missed (pre-launch review, blocker 4).

A stay recorded after that month's invoice, or a back-dated move to a
dearer rate, was never billed: the monthly run skipped the covered days
and a manual invoice for them was refused.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from core.services import PlacementService
from invoicing import adjustments
from invoicing.services import InvoiceService

JUNE = (date(2026, 6, 1), date(2026, 6, 30))
JULY = (date(2026, 7, 1), date(2026, 7, 31))


class LiveryCheckTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Anna', email='anna@example.com')
        self.grass = RateType.objects.create(name='Grass', daily_rate=Decimal('10.00'))
        self.stable = RateType.objects.create(name='Stable', daily_rate=Decimal('20.00'))
        self.field = Location.objects.create(site='Colgate', name='Top Field')
        self.barn = Location.objects.create(site='Colgate', name='Barn')
        self.ghost = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=self.ghost, owner=self.owner, location=self.field, rate_type=self.grass,
            start_date=date(2026, 6, 1),
        )
        self.june = InvoiceService.create_invoice(self.owner, *JUNE)

    def test_nothing_to_add_when_no_stay_changed(self):
        self.assertEqual(adjustments.shortfalls(), [])

    def test_a_stay_recorded_after_the_invoice_is_found_and_billed_next(self):
        late = Horse.objects.create(name='Late')
        Placement.objects.create(
            horse=late, owner=self.owner, location=self.field, rate_type=self.grass,
            start_date=date(2026, 6, 19),
        )
        # The monthly run and a manual invoice both skip June: it is covered.
        self.assertEqual(InvoiceService.uncovered_periods(self.owner, *JUNE), [])
        [item] = adjustments.shortfalls()
        self.assertEqual((item.invoice, item.horse, item.amount), (self.june, late, Decimal('120.00')))

        charge = adjustments.add_charge(self.june.pk, late.pk)
        self.assertEqual(charge.amount, Decimal('120.00'))
        self.assertEqual(charge.adjusts_invoice, self.june)
        self.assertEqual(adjustments.shortfalls(), [])
        self.assertIsNone(adjustments.add_charge(self.june.pk, late.pk))

        # The charge is dated today, so the next invoice made bills it.
        from django.utils import timezone
        today = timezone.localdate()
        nxt = InvoiceService.create_invoice(self.owner, JULY[0], today)
        self.assertTrue(nxt.line_items.filter(charge=charge).exists())

    def test_a_back_dated_move_to_a_dearer_rate_is_found(self):
        PlacementService.move_horse(
            self.ghost, new_location=self.barn, move_date=date(2026, 6, 16), new_rate_type=self.stable,
        )
        [item] = adjustments.shortfalls()
        self.assertEqual(item.billed, Decimal('300.00'))
        self.assertEqual(item.due, Decimal('450.00'))
        self.assertEqual(item.amount, Decimal('150.00'))

    def test_a_cancelled_invoice_is_not_checked(self):
        from invoicing.models import Invoice

        self.june.status = Invoice.Status.CANCELLED
        self.june.save(update_fields=['status'])
        Placement.objects.create(
            horse=Horse.objects.create(name='Late'), owner=self.owner, location=self.field,
            rate_type=self.grass, start_date=date(2026, 6, 19),
        )
        self.assertEqual(adjustments.shortfalls(), [])

    def test_the_page_and_the_button(self):
        late = Horse.objects.create(name='Late')
        Placement.objects.create(
            horse=late, owner=self.owner, location=self.field, rate_type=self.grass,
            start_date=date(2026, 6, 19),
        )
        viewer = make_user_with_access(username='viewer', dashboard='full', invoices='view')
        self.client.force_login(viewer)
        page = self.client.get(reverse('livery_check'))
        self.assertContains(page, self.june.invoice_number)
        self.assertNotContains(page, 'Add to next invoice')
        self.client.post(reverse('livery_check_add', args=[self.june.pk, late.pk]))
        self.assertFalse(ExtraCharge.objects.exists())

        self.client.force_login(make_admin())
        self.assertContains(self.client.get(reverse('invoice_list')), 'changed after its invoice was made')
        response = self.client.post(reverse('livery_check_add', args=[self.june.pk, late.pk]))
        self.assertRedirects(response, reverse('livery_check'), fetch_redirect_response=False)
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('120.00'))
        self.assertContains(self.client.get(reverse('livery_check')), 'All livery is billed')
