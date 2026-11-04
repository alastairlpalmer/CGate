"""Feed, costs and finance findings from the second bug hunt."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import FeedOut, FeedStock, YardCost
from core.models import Location
from core.roles_testutils import make_admin


class Base(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())
        self.today = timezone.localdate()
        self.main = Location.objects.create(site='Main', name='Top')
        self.other = Location.objects.create(site='Other', name='Far')

    def stock(self, **fields):
        data = {
            'site': 'Main', 'feed_type': 'hay', 'date': self.today.isoformat(),
            'quantity': '10', 'unit': 'small_bales', 'entry_type': 'delivery',
            'supplier': '', 'cost': '', 'notes': '',
        }
        data.update(fields)
        return data


class FeedStockTests(Base):

    def test_the_adjust_page_saves_waste_without_a_cost(self):
        response = self.client.post(reverse('feed_stock_adjust'), self.stock(
            quantity='3', entry_type='waste',
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FeedStock.objects.get().quantity, Decimal('-3'))

    def test_waste_on_the_delivery_form_reduces_stock_and_books_no_cost(self):
        self.client.post(reverse('feed_stock_create'), self.stock(
            quantity='3', entry_type='waste', cost='15.00',
        ))
        self.assertEqual(FeedStock.objects.get().quantity, Decimal('-3'))
        self.assertFalse(YardCost.objects.exists())

    def test_a_negative_delivery_is_refused(self):
        response = self.client.post(reverse('feed_stock_create'), self.stock(quantity='-10', cost='50'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FeedStock.objects.exists())
        self.assertFalse(YardCost.objects.exists())

    def test_a_negative_feed_out_is_refused_not_a_500(self):
        response = self.client.post(reverse('feed_out_create', args=[self.main.pk]), {
            'date': self.today.isoformat(), 'feed_type': 'hay',
            'quantity_numeric': '-2', 'unit': 'small_bales', 'total_cost': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FeedOut.objects.exists())

    def test_waste_shows_as_stock_going_out(self):
        FeedStock.objects.create(
            site='Main', feed_type='hay', date=self.today, quantity=Decimal('-3'),
            unit='small_bales', entry_type='waste',
        )
        response = self.client.get(reverse('feed_dashboard'))
        self.assertNotContains(response, '+-3')


class AutoCostTests(Base):

    def test_a_feed_out_takes_its_own_sites_price(self):
        FeedStock.objects.create(
            site='Main', feed_type='hay', date=self.today - timedelta(days=10),
            quantity=Decimal('10'), unit='round_bales', cost=Decimal('300'),
        )
        FeedStock.objects.create(
            site='Other', feed_type='hay', date=self.today - timedelta(days=1),
            quantity=Decimal('10'), unit='round_bales', cost=Decimal('900'),
        )
        self.client.post(reverse('feed_out_create', args=[self.main.pk]), {
            'date': self.today.isoformat(), 'feed_type': 'hay',
            'quantity_numeric': '1', 'unit': 'round_bales', 'total_cost': '',
        })
        self.assertEqual(FeedOut.objects.get().total_cost, Decimal('30.00'))


class CostsTotalsTests(Base):

    def test_future_costs_are_not_in_this_month_or_this_year(self):
        YardCost.objects.create(category='rent', date=self.today, amount=Decimal('100'), description='Now')
        YardCost.objects.create(
            category='rent', date=self.today + timedelta(days=40), amount=Decimal('100'),
            description='Next month',
        )
        YardCost.objects.create(
            category='rent', date=self.today.replace(month=1, day=1).replace(year=self.today.year + 1),
            amount=Decimal('1000'), description='Next year',
        )
        response = self.client.get(reverse('costs_list'))
        self.assertEqual(response.context['month_total'], Decimal('100'))
        self.assertEqual(response.context['year_total'], Decimal('100'))


class FinancesRevenueTests(Base):

    def test_revenue_is_net_of_vat_and_credit_notes(self):
        from core.models import Owner
        from invoicing.models import Invoice, Payment

        owner = Owner.objects.create(name='Jo')
        invoice = Invoice.objects.create(
            owner=owner, period_start=self.today.replace(day=1), period_end=self.today,
            due_date=self.today, subtotal=Decimal('100.00'), vat_amount=Decimal('20.00'),
            total=Decimal('120.00'), status=Invoice.Status.SENT,
        )
        Payment.objects.create(
            invoice=invoice, date=self.today, amount=Decimal('60.00'),
            method=Payment.Method.CREDIT_NOTE,
        )
        response = self.client.get(reverse('finances'))
        self.assertAlmostEqual(response.context['revenue_this_month'], 50.0)
