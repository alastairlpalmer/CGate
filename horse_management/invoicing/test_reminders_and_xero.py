"""Dev board "Configure send/paid/chase email logic", 23 Nov 2026:
reminder days and message in Settings, "Send reminder now" on an overdue
invoice, and part payments made in Xero brought back
(invoicing/reminders.py, xero_integration/services.py)."""

from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice, Payment
from invoicing.reminders import can_remind, parse_days, reminder_days
from invoicing.services import InvoiceService
from notifications.tasks import send_overdue_invoice_reminders
from xero_integration.models import XeroInvoiceSync
from xero_integration.services import _xero_date, check_xero_invoice_status

CONSOLE = 'django.core.mail.backends.console.EmailBackend'


def overdue_invoice(days_overdue=8, email='jo@example.com', paused=False):
    owner = Owner.objects.create(name='Jo Bloggs', email=email, pause_reminders=paused)
    loc = Location.objects.create(site='S', name='Top Field')
    rate = RateType.objects.create(name='Grass livery', daily_rate=Decimal('5.00'))
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    Placement.objects.create(horse=Horse.objects.create(name='Avicii'), owner=owner, location=loc,
                             rate_type=rate, start_date=end.replace(day=1) - timedelta(days=10))
    invoice = InvoiceService.create_invoice(owner, end.replace(day=1), end)
    Invoice.objects.filter(pk=invoice.pk).update(
        status=Invoice.Status.SENT, due_date=timezone.localdate() - timedelta(days=days_overdue))
    invoice.refresh_from_db()
    return invoice


class DaysTests(TestCase):

    def test_parse(self):
        self.assertEqual(parse_days(''), [])
        self.assertEqual(parse_days(' 1, 14 ,30 '), [1, 14, 30])
        self.assertEqual(parse_days('3;10'), [3, 10])
        for bad, words in (('1, x', 'whole numbers'), ('1,2,3,4,5,6', '5 reminders or fewer'),
                           ('0, 5', 'from 1 to 365'), ('14, 1', 'in order'), ('7, 7', 'in order')):
            with self.assertRaisesMessage(ValueError, words):
                parse_days(bad)

    @override_settings(OVERDUE_REMINDER_DAYS=[1, 14, 30])
    def test_settings_or_the_server(self):
        self.assertEqual(reminder_days(), [1, 14, 30])
        BusinessSettings.objects.update_or_create(pk=1, defaults={'overdue_reminder_days': '7, 21'})
        self.assertEqual(reminder_days(), [7, 21])


class SettingsPageTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())

    def post(self, days, message=''):
        settings_obj = BusinessSettings.get_settings()
        data = {'business_name': settings_obj.business_name, 'vat_registration': 'N/A', 'vat_rate': '0',
                'default_payment_terms': '30', 'invoice_prefix': 'INV',
                'overdue_reminder_days': days, 'overdue_reminder_message': message, 'save_business': '1'}
        return self.client.post(reverse('app_settings'), data, follow=True)

    def test_the_fields_are_on_the_page(self):
        body = self.client.get(reverse('app_settings')).content.decode()
        self.assertIn('data-testid="reminder-settings"', body)
        self.assertIn('name="overdue_reminder_days"', body)
        self.assertIn('name="overdue_reminder_message"', body)

    def test_saves_tidy_and_refuses_bad_days(self):
        self.post('7,21', 'Call Sam on 07700 900123.')
        saved = BusinessSettings.get_settings()
        self.assertEqual(saved.overdue_reminder_days, '7, 21')
        self.assertEqual(saved.overdue_reminder_message, 'Call Sam on 07700 900123.')
        response = self.post('21, 7')
        self.assertContains(response, 'Put the days in order')
        self.assertEqual(BusinessSettings.get_settings().overdue_reminder_days, '7, 21')


class ScheduleTests(TestCase):

    def test_the_job_uses_the_yards_days_and_message(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={
            'overdue_reminder_days': '5, 20', 'overdue_reminder_message': 'Call Sam on 07700 900123.'})
        invoice = overdue_invoice(days_overdue=6)
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Call Sam on 07700 900123.', mail.outbox[0].alternatives[0][0] if mail.outbox[0].alternatives
                      else mail.outbox[0].body)
        invoice.refresh_from_db()
        self.assertEqual(invoice.overdue_reminder_count, 1)

    def test_not_before_the_first_day(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={'overdue_reminder_days': '10'})
        overdue_invoice(days_overdue=6)
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 0)


class SendNowTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())

    def test_the_menu_offers_it_for_an_overdue_invoice_only(self):
        invoice = overdue_invoice()
        self.assertTrue(can_remind(invoice))
        self.assertContains(self.client.get(reverse('invoice_detail', args=[invoice.pk])), 'data-testid="send-reminder"')
        Invoice.objects.filter(pk=invoice.pk).update(due_date=timezone.localdate() + timedelta(days=3))
        self.assertNotContains(self.client.get(reverse('invoice_detail', args=[invoice.pk])), 'data-testid="send-reminder"')

    def test_send_now_counts_as_the_next_step(self):
        invoice = overdue_invoice()
        response = self.client.post(reverse('invoice_send_reminder', args=[invoice.pk]), follow=True)
        self.assertContains(response, 'Reminder 1 sent to jo@example.com.')
        self.assertEqual(len(mail.outbox), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.overdue_reminder_count, 1)
        self.assertIsNotNone(invoice.last_overdue_reminder_at)
        # The daily job does not send the same step again.
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_after_the_last_step_it_repeats_the_final_notice(self):
        invoice = overdue_invoice()
        Invoice.objects.filter(pk=invoice.pk).update(overdue_reminder_count=3)
        self.client.post(reverse('invoice_send_reminder', args=[invoice.pk]))
        self.assertTrue(mail.outbox[0].subject.startswith('Final notice'))

    def test_a_paused_owner_can_still_get_one_by_hand(self):
        invoice = overdue_invoice(paused=True)
        self.client.post(reverse('invoice_send_reminder', args=[invoice.pk]))
        self.assertEqual(len(mail.outbox), 1)

    def test_refused_when_not_due_no_email_or_no_mail_server(self):
        invoice = overdue_invoice(days_overdue=-3)
        response = self.client.post(reverse('invoice_send_reminder', args=[invoice.pk]), follow=True)
        self.assertContains(response, 'Only an overdue invoice')
        invoice = overdue_invoice(email='')
        self.assertFalse(can_remind(invoice))
        invoice = Invoice.objects.filter(owner__email='jo@example.com').first()
        Invoice.objects.filter(pk=invoice.pk).update(due_date=timezone.localdate() - timedelta(days=4))
        with override_settings(DEBUG=False, EMAIL_BACKEND=CONSOLE):
            response = self.client.post(reverse('invoice_send_reminder', args=[invoice.pk]), follow=True)
        self.assertContains(response, 'Email is not set up on this server')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get(reverse('invoice_send_reminder', args=[invoice.pk])).status_code, 405)


class FakeXero:
    def __init__(self, data):
        self.data = data

    def get_invoice(self, _id):
        return self.data


class XeroPaymentsTests(TestCase):

    def setUp(self):
        self.invoice = overdue_invoice()
        self.total = self.invoice.total
        self.sync = XeroInvoiceSync.objects.create(invoice=self.invoice, xero_invoice_id='X-1',
                                                   sync_status=XeroInvoiceSync.SyncStatus.PUSHED)

    def check(self, status, payments):
        return check_xero_invoice_status(self.sync, client=FakeXero({'Status': status, 'Payments': payments}))

    def test_dates(self):
        self.assertEqual(_xero_date('/Date(1763596800000+0000)/'), date(2025, 11, 20))
        self.assertEqual(_xero_date('2026-11-20T00:00:00'), date(2026, 11, 20))
        self.assertIsNone(_xero_date('soon'))

    def test_a_part_payment_comes_back_once(self):
        part = (self.total / 2).quantize(Decimal('0.01'))
        payment = {'PaymentID': 'P-1', 'Amount': str(part), 'Date': '2026-11-20T00:00:00', 'Reference': 'BACS'}
        self.check('AUTHORISED', [payment])
        self.check('AUTHORISED', [payment])
        self.invoice.refresh_from_db()
        rows = list(self.invoice.payments.all())
        self.assertEqual([(p.amount, p.method, p.date, p.reference) for p in rows],
                         [(part, 'xero', date(2026, 11, 20), 'BACS')])
        self.assertIn(self.invoice.status, (Invoice.Status.SENT, Invoice.Status.OVERDUE))
        self.assertEqual(self.invoice.balance_due, self.total - part)

    def test_paid_in_two_parts(self):
        half = (self.total / 2).quantize(Decimal('0.01'))
        self.check('AUTHORISED', [{'PaymentID': 'P-1', 'Amount': str(half), 'Date': '2026-11-01T00:00:00'}])
        self.check('PAID', [{'PaymentID': 'P-1', 'Amount': str(half), 'Date': '2026-11-01T00:00:00'},
                            {'PaymentID': 'P-2', 'Amount': str(self.total - half), 'Date': '2026-11-10T00:00:00'}])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(sorted(p.xero_payment_id for p in self.invoice.payments.all()), ['P-1', 'P-2'])
        self.assertEqual(self.invoice.balance_due, Decimal('0.00'))

    def test_paid_with_no_payment_list_still_balances(self):
        self.check('PAID', [])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(Payment.objects.get(invoice=self.invoice).reference, 'Reported paid by Xero')

    def test_deleted_payments_are_skipped(self):
        self.check('AUTHORISED', [{'PaymentID': 'P-9', 'Amount': '10.00', 'Status': 'DELETED'}])
        self.assertFalse(self.invoice.payments.exists())
