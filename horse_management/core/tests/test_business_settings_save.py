"""A Business settings save must not put the invoice counter back
(pre-launch review). The form's instance is read when the page loads; a
full save wrote the old counter, so each new invoice then failed."""

from decimal import Decimal

from django.test import TestCase

from core.forms import BusinessSettingsForm
from core.models import BusinessSettings


class BusinessSettingsSaveTests(TestCase):
    def test_a_save_keeps_numbers_taken_while_the_page_was_open(self):
        on_page = BusinessSettings.get_settings()
        number_then = on_page.next_invoice_number
        data = {
            'business_name': 'New Name', 'vat_rate': '0', 'default_payment_terms': '30',
            'invoice_prefix': on_page.invoice_prefix,
        }
        # Meanwhile an invoice and a credit note take their numbers.
        BusinessSettings.get_settings().get_next_invoice_number()
        BusinessSettings.get_settings().get_next_credit_note_number()

        form = BusinessSettingsForm(data, instance=on_page)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        fresh = BusinessSettings.objects.get()
        self.assertEqual(fresh.business_name, 'New Name')
        self.assertEqual(fresh.next_invoice_number, number_then + 1)
        self.assertEqual(fresh.vat_rate, Decimal('0'))
