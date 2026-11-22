"""Recurring yard costs repeat (Dev board, 22 Nov 2026), billing/recurring.py."""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.roles_testutils import make_admin

from .models import YardCost
from .recurring import add_months, create_repeats, due_dates
from .tasks import create_recurring_yard_costs


def cost(**kw):
    data = dict(category='rent', date=date(2026, 1, 31), description='Field rent', amount=Decimal('250.00'),
                vat_amount=Decimal('50.00'), supplier='Estate', notes='Standing order',
                is_recurring=True, recurrence_interval='monthly')
    data.update(kw)
    return YardCost.objects.create(**data)


class DatesTests(TestCase):

    def test_add_months_keeps_the_day_or_the_month_end(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 1, 31), 2), date(2026, 3, 31))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2028, 2, 29), 12), date(2029, 2, 28))

    def test_due_dates(self):
        monthly = cost()
        self.assertEqual(due_dates(monthly, date(2026, 4, 30)),
                         [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        weekly = cost(date=date(2026, 11, 1), recurrence_interval='weekly')
        self.assertEqual(due_dates(weekly, date(2026, 11, 22)),
                         [date(2026, 11, 8), date(2026, 11, 15), date(2026, 11, 22)])
        ended = cost(recurrence_end_date=date(2026, 3, 15))
        self.assertEqual(due_dates(ended, date(2026, 12, 31)), [date(2026, 2, 28)])
        self.assertEqual(due_dates(cost(is_recurring=False), date(2026, 12, 31)), [])
        self.assertEqual(due_dates(cost(recurrence_interval=''), date(2026, 12, 31)), [])
        quarterly = cost(recurrence_interval='quarterly')
        self.assertEqual(due_dates(quarterly, date(2026, 12, 31)),
                         [date(2026, 4, 30), date(2026, 7, 31), date(2026, 10, 31)])
        annual = cost(recurrence_interval='annual')
        self.assertEqual(due_dates(annual, date(2028, 1, 31)), [date(2027, 1, 31), date(2028, 1, 31)])


class CreateRepeatsTests(TestCase):

    def test_copies_once(self):
        source = cost()
        self.assertEqual(create_repeats(today=date(2026, 4, 30)), 3)
        self.assertEqual(create_repeats(today=date(2026, 4, 30)), 0)
        copies = YardCost.objects.filter(recurrence_source=source).order_by('date')
        self.assertEqual([c.date for c in copies], [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])
        first = copies[0]
        self.assertEqual((first.category, first.description, first.amount, first.vat_amount, first.supplier, first.notes),
                         ('rent', 'Field rent', Decimal('250.00'), Decimal('50.00'), 'Estate', 'Standing order'))
        self.assertFalse(first.is_recurring)
        self.assertEqual(create_repeats(today=date(2026, 5, 31)), 1)

    def test_a_deleted_copy_is_not_made_again(self):
        cost()
        create_repeats(today=date(2026, 3, 31))
        YardCost.objects.get(date=date(2026, 2, 28)).soft_delete()  # to the bin
        self.assertEqual(create_repeats(today=date(2026, 3, 31)), 0)
        self.assertFalse(YardCost.objects.filter(date=date(2026, 2, 28)).exists())
        # Emptied from the bin: still not made again.
        YardCost.all_objects.filter(date=date(2026, 2, 28)).delete()
        self.assertEqual(create_repeats(today=date(2026, 3, 31)), 0)
        self.assertEqual(YardCost.objects.get(recurrence_source__isnull=True).recurrence_made_until, date(2026, 3, 31))

    def test_stopping(self):
        source = cost()
        create_repeats(today=date(2026, 2, 28))
        YardCost.objects.filter(pk=source.pk).update(is_recurring=False)
        self.assertEqual(create_repeats(today=date(2026, 6, 30)), 0)
        YardCost.objects.filter(pk=source.pk).update(is_recurring=True)
        source.refresh_from_db()
        source.soft_delete()  # to the bin: no more copies
        self.assertEqual(create_repeats(today=date(2026, 6, 30)), 0)

    def test_the_daily_job(self):
        cost(date=timezone.localdate() - timedelta(weeks=2), recurrence_interval='weekly')
        self.assertEqual(create_recurring_yard_costs(), 2)
        entry = settings.CELERY_BEAT_SCHEDULE['create-recurring-yard-costs']
        self.assertEqual(entry['task'], 'billing.tasks.create_recurring_yard_costs')


class FormTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.today = timezone.localdate()

    def data(self, **kw):
        data = {'category': 'rent', 'date': (self.today - timedelta(weeks=3)).isoformat(),
                'description': 'Field rent', 'amount': '100', 'vat_amount': '0',
                'is_recurring': 'on', 'recurrence_interval': 'weekly', 'recurrence_end_date': '', 'notes': ''}
        data.update(kw)
        return data

    def test_saving_a_past_recurring_cost_makes_the_copies_now(self):
        response = self.client.post(reverse('yard_cost_create'), self.data(), follow=True)
        self.assertContains(response, 'Yard cost added. 3 repeats added, up to today.')
        self.assertEqual(YardCost.objects.filter(recurrence_source__isnull=False).count(), 3)
        self.assertContains(response, '>Repeat</span>')

    def test_how_often_is_needed_and_the_end_is_after_the_start(self):
        response = self.client.post(reverse('yard_cost_create'), self.data(recurrence_interval=''))
        self.assertContains(response, 'Choose how often the cost repeats.')
        response = self.client.post(reverse('yard_cost_create'), self.data(
            recurrence_end_date=(self.today - timedelta(weeks=4)).isoformat()))
        self.assertContains(response, 'The end date cannot be before the date of the cost.')
        self.assertFalse(YardCost.objects.exists())

    def test_duplicate_is_a_one_off(self):
        source = cost(date=self.today)
        self.client.post(reverse('yard_cost_duplicate', args=[source.pk]))
        copy = YardCost.objects.exclude(pk=source.pk).get()
        self.assertFalse(copy.is_recurring)
        self.assertEqual(copy.recurrence_interval, '')
