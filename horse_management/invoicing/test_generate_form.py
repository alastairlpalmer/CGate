"""Generate monthly: the month just ended by default, and no future month
(pre-launch review)."""

from datetime import date
from unittest import mock

from django.test import SimpleTestCase

from invoicing.forms import MonthlyInvoiceForm


@mock.patch('django.utils.timezone.localdate', return_value=date(2026, 1, 15))
class MonthlyInvoiceFormTests(SimpleTestCase):
    def test_the_default_is_the_month_just_ended(self, _today):
        form = MonthlyInvoiceForm()
        self.assertEqual((form.fields['year'].initial, form.fields['month'].initial), (2025, 12))

    def test_a_future_month_is_refused(self, _today):
        self.assertFalse(MonthlyInvoiceForm({'year': 2026, 'month': 2}).is_valid())
        self.assertFalse(MonthlyInvoiceForm({'year': 2027, 'month': 1}).is_valid())

    def test_this_month_and_earlier_are_accepted(self, _today):
        self.assertTrue(MonthlyInvoiceForm({'year': 2026, 'month': 1}).is_valid())
        self.assertTrue(MonthlyInvoiceForm({'year': 2025, 'month': 6}).is_valid())
