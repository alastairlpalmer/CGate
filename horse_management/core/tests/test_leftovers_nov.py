"""Small leftovers from the November bug hunts."""

from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Owner
from core.roles_testutils import make_admin
from invoicing.models import Invoice, Payment


class ArchivedAtTests(TestCase):

    def test_archiving_by_any_path_records_the_date(self):
        owner = Owner.objects.create(name='Jo')
        owner.is_archived = True
        owner.save()
        owner.refresh_from_db()
        self.assertIsNotNone(owner.archived_at)
        owner.is_archived = False
        owner.save(update_fields=['is_archived'])
        owner.refresh_from_db()
        self.assertIsNone(owner.archived_at)


class StatementBounceTests(TestCase):

    def test_a_bounced_address_is_named_as_the_reason(self):
        owner = Owner.objects.create(name='Jo', email='jo@example.com')
        Owner.objects.filter(pk=owner.pk).update(email_bounced_at=timezone.now())
        self.client.force_login(make_admin())
        response = self.client.post(reverse('owner_statement_email', args=[owner.pk]), follow=True)
        self.assertContains(response, 'not arriving')
        self.assertNotContains(response, 'Check email configuration')


class ReceivedLast30DaysTests(TestCase):

    def test_the_window_is_30_days_including_today(self):
        from core.dashboard import money

        today = timezone.localdate()
        owner = Owner.objects.create(name='Jo')
        invoice = Invoice.objects.create(
            owner=owner, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=today, total=Decimal('100.00'), status=Invoice.Status.SENT,
        )
        Payment.objects.create(invoice=invoice, date=today - timedelta(days=29), amount=Decimal('10'))
        Payment.objects.create(invoice=invoice, date=today - timedelta(days=30), amount=Decimal('20'))
        snapshot = money.snapshot(make_admin(), today=today)
        self.assertEqual(snapshot['invoices']['received_30d'], Decimal('10'))


class ReminderWordingTests(TestCase):

    def test_the_third_reminder_says_third(self):
        from notifications.emails import send_invoice_overdue_reminder

        owner = Owner.objects.create(name='Jo', email='jo@example.com')
        invoice = Invoice.objects.create(
            owner=owner, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=date(2026, 2, 28), total=Decimal('100.00'), status=Invoice.Status.OVERDUE,
        )
        send_invoice_overdue_reminder(invoice, stage=3)
        self.assertTrue(mail.outbox[-1].subject.startswith('Third reminder'))
        message = mail.outbox[-1]
        html = message.alternatives[0][0] if message.alternatives else message.body
        self.assertIn('Third reminder', html)
