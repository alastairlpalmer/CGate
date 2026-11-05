"""The 4 smaller speed fixes from the November 2026 large-yard review.

1. Reconciliation loaded every invoice and payment ever made and looped
   over them once a month. Now three grouped queries.
2. Vaccination and farrier reminders looked up each horse's owner (and
   each horse's latest farrier visit) one by one.
3. The Xero sweep and bulk push built a new client for every invoice.
4. The audit log page cost about four queries a line.

Each speed test checks that the work does not grow with the rows.
"""

import random
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core import audit
from core.models import Horse, Owner, OwnershipShare
from health.models import FarrierVisit, Vaccination, VaccinationType
from invoicing.models import Invoice, Payment
from invoicing.reconciliation import ISSUED, monthly_report

LONDON = ZoneInfo('Europe/London')
ZERO = Decimal('0.00')


def _count(func):
    with CaptureQueriesContext(connection) as ctx:
        func()
    return len(ctx.captured_queries)


def _old_monthly_report(year):
    """The report as it was worked out before (one loop a month)."""
    invoices = list(Invoice.objects.filter(status__in=ISSUED))
    payments = list(Payment.objects.filter(invoice__status__in=ISSUED))
    today = timezone.localdate()
    rows = []
    for m in range(1, 13):
        start = date(year, m, 1)
        if start > today:
            break
        end = date(year, m, monthrange(year, m)[1])
        count, total = 0, ZERO
        for inv in invoices:
            if start <= timezone.localtime(inv.created_at).date() <= end:
                count += 1
                total += inv.total
        in_month = [p for p in payments if start <= p.date <= end]
        received = sum((p.amount for p in in_month if not p.is_credit_note), ZERO)
        credited = sum((p.amount for p in in_month if p.is_credit_note), ZERO)
        issued_by_end = {
            inv.pk: inv.total for inv in invoices
            if timezone.localtime(inv.created_at).date() <= end
        }
        paid_by_end = sum(
            (p.amount for p in payments if p.invoice_id in issued_by_end and p.date <= end), ZERO,
        )
        rows.append((start, count, total, received, credited,
                     sum(issued_by_end.values(), ZERO) - paid_by_end))
    return rows


class ReconciliationTests(TestCase):

    def setUp(self):
        self.owner = Owner.objects.create(name='Reco Owner')
        self.n = 0

    def invoice(self, created, total, status=Invoice.Status.SENT):
        self.n += 1
        inv = Invoice.objects.create(
            owner=self.owner, invoice_number=f'REC-{self.n}',
            period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
            subtotal=Decimal(total), total=Decimal(total), status=status,
        )
        Invoice.objects.filter(pk=inv.pk).update(created_at=created)
        return inv

    def test_same_figures_as_before_on_mixed_data(self):
        rng = random.Random(7)
        invoices = []
        for _ in range(60):
            # 2025 and 2026, some at 23:30 UTC on a month end (the next
            # month in London summer time), some cancelled or draft.
            day = date(2025, 1, 1) + timedelta(days=rng.randrange(0, 700))
            hour = rng.choice([1, 12, 23])
            created = datetime(day.year, day.month, day.day, hour, 30, tzinfo=ZoneInfo('UTC'))
            status = rng.choice(ISSUED + [Invoice.Status.DRAFT, Invoice.Status.CANCELLED])
            invoices.append(self.invoice(created, f'{rng.randrange(20, 500)}.00', status))
        for inv in invoices:
            for _ in range(rng.randrange(0, 3)):
                Payment.objects.create(
                    invoice=inv, amount=Decimal(rng.randrange(5, 100)),
                    # Some dated before the invoice, some credit notes.
                    date=timezone.localtime(inv.created_at).date() + timedelta(days=rng.randrange(-5, 60)),
                    method=rng.choice([Payment.Method.BANK_TRANSFER, Payment.Method.CREDIT_NOTE]),
                )
        for year in (2025, 2026):
            new = [
                (m.month, m.invoice_count, m.total, m.received, m.credited, m.outstanding)
                for m in monthly_report(year).months
            ]
            self.assertEqual(new, _old_monthly_report(year), msg=year)

    def test_month_end_late_evening_counts_in_the_next_month(self):
        # 23:30 UTC on 31 July is 00:30 on 1 August in London.
        self.invoice(datetime(2025, 7, 31, 23, 30, tzinfo=ZoneInfo('UTC')), '100.00')
        months = monthly_report(2025).months
        self.assertEqual((months[6].invoice_count, months[7].invoice_count), (0, 1))

    def test_query_count_does_not_grow_with_the_ledger(self):
        def make(n):
            for i in range(n):
                inv = self.invoice(datetime(2025, 1 + i % 12, 10, tzinfo=LONDON), '50.00')
                Payment.objects.create(invoice=inv, amount=Decimal('10'), date=date(2025, 1 + i % 12, 20))

        make(2)
        few = _count(lambda: monthly_report(2025))
        make(20)
        self.assertEqual(_count(lambda: monthly_report(2025)), few)


class ReminderQueryTests(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.flu = VaccinationType.objects.create(name='Flu', reminder_days_before=30)
        self.n = 0

    def horse(self):
        self.n += 1
        owner = Owner.objects.create(name=f'Owner {self.n}', email=f'o{self.n}@example.com')
        horse = Horse.objects.create(name=f'Horse {self.n}')
        OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)
        return horse

    def test_vaccination_owner_lookups_do_not_grow_with_horses(self):
        from notifications.tasks import send_vaccination_reminders

        def run(n):
            Vaccination.objects.all().delete()
            for _ in range(n):
                Vaccination.objects.create(
                    horse=self.horse(), vaccination_type=self.flu,
                    date_given=self.today - timedelta(days=350),
                    next_due_date=self.today + timedelta(days=10),
                )
            with patch('notifications.tasks.send_vaccination_digest', return_value=True) as send:
                queries = _count(send_vaccination_reminders)
            self.assertEqual(send.call_count, n)
            # The claim update is one query an owner; the rest is fixed.
            return queries - n

        self.assertEqual(run(2), run(6))

    def test_farrier_lookups_do_not_grow_with_horses(self):
        from notifications.tasks import send_farrier_reminders

        def run(n):
            FarrierVisit.objects.all().delete()
            for _ in range(n):
                horse = self.horse()
                FarrierVisit.objects.create(
                    horse=horse, date=self.today - timedelta(days=60),
                    next_due_date=self.today - timedelta(days=50),
                )
                # The current visit is the later one.
                FarrierVisit.objects.create(
                    horse=horse, date=self.today - timedelta(days=40),
                    next_due_date=self.today + timedelta(days=3),
                )
            with patch('notifications.tasks.send_farrier_digest', return_value=True) as send:
                queries = _count(send_farrier_reminders)
            self.assertEqual(send.call_count, n)
            for call in send.call_args_list:
                self.assertEqual([v.next_due_date for v in call.args[1]], [self.today + timedelta(days=3)])
            return queries - n

        self.assertEqual(run(2), run(6))

    def test_owner_rule_is_the_same_as_current_owner(self):
        from core.models import prime_current_owners

        a = Owner.objects.create(name='Big share')
        b = Owner.objects.create(name='Primary contact')
        both = Horse.objects.create(name='Both')
        OwnershipShare.objects.create(horse=both, owner=a, share_percentage=70)
        OwnershipShare.objects.create(horse=both, owner=b, share_percentage=30, is_primary_contact=True)
        largest = Horse.objects.create(name='Largest')
        OwnershipShare.objects.create(horse=largest, owner=b, share_percentage=40)
        OwnershipShare.objects.create(horse=largest, owner=a, share_percentage=60)
        nobody = Horse.objects.create(name='Nobody')
        expected = {h.pk: h.current_owner for h in Horse.objects.filter(pk__in=[both.pk, largest.pk, nobody.pk])}
        horses = list(Horse.objects.filter(pk__in=expected))
        prime_current_owners(horses)
        self.assertEqual({h.pk: h.current_owner for h in horses}, expected)
        self.assertEqual(expected[both.pk], b)
        self.assertEqual(expected[largest.pk], a)
        self.assertIsNone(expected[nobody.pk])


class XeroClientReuseTests(TestCase):

    def setUp(self):
        from xero_integration.models import XeroInvoiceSync
        from xero_integration.test_push_resilience import _invoice, _real_connection

        _real_connection()
        self.invoices = [_invoice(f'Owner {i}', f'Horse {i}') for i in range(3)]
        for i, inv in enumerate(self.invoices):
            inv.mark_as_sent()
            XeroInvoiceSync.objects.create(
                invoice=inv, xero_invoice_id=f'x-{i}', sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
            )

    @patch('xero_integration.tasks.time.sleep')
    def test_the_nightly_sweep_builds_one_client(self, _sleep):
        from xero_integration.tasks import sync_xero_invoice_statuses

        client = MagicMock()
        client.get_invoice.return_value = {'Status': 'AUTHORISED'}
        with patch('xero_integration.services.XeroClient', return_value=client) as cls:
            summary = sync_xero_invoice_statuses()
        self.assertIn('Checked 3', summary)
        self.assertEqual(cls.call_count, 1)
        self.assertEqual(client.get_invoice.call_count, 3)

    @patch('xero_integration.tasks.time.sleep')
    def test_the_bulk_push_builds_one_client(self, _sleep):
        from xero_integration.models import XeroInvoiceSync
        from xero_integration.tasks import push_invoices_to_xero

        XeroInvoiceSync.objects.all().delete()
        client = MagicMock()
        client.find_contact_by_name.return_value = None
        client.create_contact.side_effect = lambda *a, **k: {'ContactID': f'c-{client.create_contact.call_count}'}
        client.create_invoice.side_effect = lambda *a, **k: {
            'InvoiceID': f'i-{client.create_invoice.call_count}', 'InvoiceNumber': 'X',
        }
        with patch('xero_integration.services.XeroClient', return_value=client) as cls:
            summary = push_invoices_to_xero([inv.pk for inv in self.invoices])
        self.assertEqual(summary, 'Pushed 3 invoice(s) to Xero, 0 failed.')
        self.assertEqual(cls.call_count, 1)


class AuditLogQueryTests(TestCase):

    def test_query_count_does_not_grow_with_lines(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user('auditor')

        def make(n, offset):
            for i in range(n):
                owner = Owner.objects.create(name=f'Audit {offset + i}')
                owner._history_user = user
                owner.email = f'a{offset + i}@example.com'
                owner.save()
                inv = Invoice.objects.create(
                    owner=owner, invoice_number=f'AUD-{offset + i}',
                    period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                    subtotal=Decimal('10'), total=Decimal('10'),
                )
                inv.total = Decimal('12')
                inv.save()

        make(2, 0)
        few = _count(lambda: audit.timeline())
        make(10, 100)
        self.assertEqual(_count(lambda: audit.timeline()), few)

        entries, _ = audit.timeline()
        changed = next(e for e in entries if e.what == 'Changed invoice' and 'AUD-109' in e.subject)
        self.assertEqual(changed.subject, 'AUD-109 - Audit 109')
        self.assertEqual([(c.field, c.old, c.new) for c in changed.changes], [('Total', '10.00', '12.00')])
