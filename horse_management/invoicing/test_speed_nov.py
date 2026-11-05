"""Speed fixes from the November 2026 large-yard review.

Each test checks that the work does not grow with the number of rows:
the query count for 2 rows and for 6 rows must be the same.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.dashboard import attention
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from invoicing.models import Invoice
from invoicing.services import InvoiceService

START = date(2026, 6, 1)
END = date(2026, 6, 30)


class Base(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.location = Location.objects.create(site='Colgate', name='Top')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))

    def owner_with_horse(self, name):
        owner = Owner.objects.create(name=name, email=f'{name.lower()}@example.com')
        horse = Horse.objects.create(name=f'{name} horse')
        Placement.objects.create(
            horse=horse, owner=owner, location=self.location, rate_type=self.rate,
            start_date=date(2026, 1, 1),
        )
        return owner, horse

    def count(self, func):
        with CaptureQueriesContext(connection) as ctx:
            func()
        return len(ctx.captured_queries)


class CsvExportQueryTests(Base):

    def test_query_count_does_not_grow_with_invoices(self):
        self.client.force_login(self.admin)
        url = reverse('invoice_export_csv')

        def make(n, offset):
            for i in range(n):
                owner, _ = self.owner_with_horse(f'Owner{offset + i}')
                InvoiceService.create_invoice(owner, START, END).mark_as_sent()

        make(2, 0)
        few = self.count(lambda: self.client.get(url).content)
        make(4, 10)
        response = self.client.get(url)
        self.assertIn('Owner13', response.content.decode())  # the new rows are in the file
        many = self.count(lambda: self.client.get(url).content)
        self.assertEqual(few, many)


class InvoiceWriteQueryTests(Base):

    def test_query_count_does_not_grow_with_charges(self):
        def run(name, charges):
            owner, horse = self.owner_with_horse(name)
            for i in range(charges):
                ExtraCharge.objects.create(
                    horse=horse, owner=owner, charge_type='vet', description=f'Vet {i}',
                    date=START + timedelta(days=i), amount=Decimal('10.00'),
                    split_by_ownership=False,
                )
            return self.count(lambda: InvoiceService.create_invoice(owner, START, END))

        run('Warm', 1)  # first call creates settings rows and fills caches
        few = run('Few', 1)
        many = run('Many', 5)
        self.assertEqual(few, many)
        self.assertFalse(ExtraCharge.objects.filter(invoiced=False).exists())
        self.assertEqual(
            Invoice.objects.get(owner__name='Many').line_items.filter(charge__isnull=False).count(), 5,
        )


class DashboardInvoiceRowTests(Base):

    def test_many_overdue_invoices_fold_into_one_row(self):
        today = timezone.localdate()
        for i in range(8):
            owner, _ = self.owner_with_horse(f'Late{i}')
            invoice = InvoiceService.create_invoice(owner, START, END)
            invoice.mark_as_sent()
            Invoice.objects.filter(pk=invoice.pk).update(due_date=today - timedelta(days=10 + i))
        items = attention.collect(self.admin, today=today)
        result = attention.rows([i for i in items if i.kind == 'invoice'], self.admin)
        self.assertEqual(len(result), attention.INVOICE_ROWS_SHOWN + 1)
        folded = [row for row in result if row.key == 'invoices-more']
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0].title, '3 more unpaid invoices')
        self.assertEqual(folded[0].url, reverse('aged_debtors'))


class OwnerListPageTests(Base):

    def test_owner_list_shows_fifty_a_page(self):
        Owner.objects.bulk_create(
            [Owner(name=f'Owner {i:03d}', email=f'o{i}@example.com') for i in range(60)],
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('owner_list'))
        self.assertEqual(len(response.context['owners']), 50)
        self.assertTrue(response.context['is_paginated'])
        second = self.client.get(reverse('owner_list'), {'page': 2})
        self.assertEqual(len(second.context['owners']), 10)
