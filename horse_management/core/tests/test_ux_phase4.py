"""Data views that are accurate and balanced (docs/UX_REVIEW_PLAN.md,
phase 4): invoice horses in name order, a folded activity log, yard board
flags that say why, and health warnings in the horse list.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from core.views.dashboard import _fold_activity
from health.models import FarrierVisit
from invoicing.utils import group_line_items_by_horse


def _line(horse, total='10.00'):
    return SimpleNamespace(
        horse_id=horse.pk if horse else None, horse=horse,
        line_total=Decimal(total), line_type='livery', share_percentage=100,
        charge=None, pk=1,
    )


class InvoiceHorseOrderTests(SimpleTestCase):
    def test_horses_in_natural_name_order_and_other_charges_last(self):
        star10 = SimpleNamespace(pk=1, name='Star 10')
        star2 = SimpleNamespace(pk=2, name='Star 2')
        bella = SimpleNamespace(pk=3, name='bella')
        groups = group_line_items_by_horse(
            [_line(star10), _line(None), _line(star2), _line(bella)])
        self.assertEqual(
            [g['horse_name'] for g in groups],
            ['bella', 'Star 2', 'Star 10', 'Other Charges'],
        )


class ActivityFoldTests(SimpleTestCase):
    def test_after_eight_events_the_rest_fold(self):
        days = [
            {'events': [SimpleNamespace() for _ in range(5)]},
            {'events': [SimpleNamespace() for _ in range(5)]},
            {'events': [SimpleNamespace() for _ in range(2)]},
        ]
        self.assertEqual(_fold_activity(days), 4)
        self.assertEqual([e.folded for e in days[1]['events']],
                         [False, False, False, True, True])
        self.assertFalse(days[1]['folded'])
        self.assertTrue(days[2]['folded'])

    def test_a_short_log_folds_nothing(self):
        days = [{'events': [SimpleNamespace() for _ in range(3)]}]
        self.assertEqual(_fold_activity(days), 0)
        self.assertFalse(days[0]['folded'])


class HealthListBase(TestCase):
    """Three horses: farrier 3 days overdue, farrier due in 5 days, fine."""

    def setUp(self):
        self.today = timezone.localdate()
        owner = Owner.objects.create(name='Roz Tobin')
        self.field = Location.objects.create(site='Colgate', name='Top', capacity=8)
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.owner = owner
        self.late = self._place('Bramble')
        self.soon = self._place('Clover')
        self.fine = self._place('Dobbin')
        FarrierVisit.objects.create(
            horse=self.late, date=self.today - timedelta(days=70),
            next_due_date=self.today - timedelta(days=3),
        )
        FarrierVisit.objects.create(
            horse=self.soon, date=self.today - timedelta(days=40),
            next_due_date=self.today + timedelta(days=5),
        )
        self.client.force_login(make_admin())

    def _place(self, name):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.field,
            rate_type=self.rate, start_date=date(2026, 1, 1),
        )
        return horse

    def body(self, **params):
        return self.client.get(reverse('horse_list'), params).content.decode()


class HorseListHealthTests(HealthListBase):

    def test_rows_carry_a_dot_with_words(self):
        body = self.body()
        self.assertIn('class="health-dot is-overdue" title="Farrier overdue"', body)
        self.assertIn('class="health-dot is-due" title="Farrier due soon"', body)
        self.assertIn('<span class="sr-only">(Farrier overdue)</span>', body)
        self.assertIn('Needs action <b>2</b>', body)

    def test_needs_action_shows_only_flagged_horses(self):
        body = self.body(needs='1')
        self.assertIn('Bramble', body)
        self.assertIn('Clover', body)
        self.assertNotIn('Dobbin', body)

    def test_no_health_access_no_dots(self):
        self.client.force_login(make_user_with_access('horses_only', horses='view'))
        body = self.body()
        self.assertNotIn('health-dot is-', body)
        self.assertNotIn('Needs action', body)

    def test_more_flagged_horses_cost_no_more_queries(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('horse_list'))
            return len(ctx.captured_queries)

        before = queries()
        for n in range(4):
            horse = self._place(f'Extra {n}')
            FarrierVisit.objects.create(
                horse=horse, date=self.today - timedelta(days=70),
                next_due_date=self.today - timedelta(days=1),
            )
        self.assertEqual(queries(), before)


class NeedsColumnTests(HealthListBase):
    """The "Needs action" view says what each horse needs, how late it is,
    and gives the record button, on the table and on a phone card."""

    def test_the_needs_view_has_a_needs_column(self):
        body = self.body(needs='1')
        self.assertIn('<th>Needs</th>', body)
        self.assertIn('<td data-col="needs">', body)
        self.assertIn('Farrier · ', body)
        self.assertIn('3 days overdue', body)
        self.assertIn('in 5 days', body)
        self.assertIn('Record farrier visit', body)

    def test_overdue_comes_before_due_soon_for_one_horse(self):
        from health.models import Vaccination, VaccinationType
        flu = VaccinationType.objects.create(name='Flu', interval_months=12)
        Vaccination.objects.create(
            horse=self.late, vaccination_type=flu,
            date_given=self.today - timedelta(days=360),
            next_due_date=self.today + timedelta(days=4),
        )
        body = self.body(needs='1')
        row = body.split(f'id="horse-{self.late.pk}"', 1)[1].split('</tr>', 1)[0]
        self.assertLess(row.index('3 days overdue'), row.index('in 4 days'))

    def test_the_full_list_has_no_needs_column(self):
        body = self.body()
        self.assertNotIn('<th>Needs</th>', body)
        self.assertNotIn('data-col="needs"', body)

    def test_a_view_only_user_sees_what_is_needed_without_buttons(self):
        self.client.force_login(make_user_with_access('looker', horses='view', health='view'))
        body = self.body(needs='1')
        self.assertIn('3 days overdue', body)
        self.assertNotIn('Record farrier visit', body)


class AllHealthKindsTests(HealthListBase):
    """Worming, vet follow-ups and high egg counts are flagged as well as
    vaccinations and farrier visits."""

    def test_worming_vet_and_egg_count_are_flagged(self):
        from health.models import VetVisit, WormEggCount, WormingTreatment
        WormingTreatment.objects.create(
            horse=self.fine, date=self.today - timedelta(days=100),
            next_due_date=self.today - timedelta(days=2), product_name='Equest',
        )
        VetVisit.objects.create(
            horse=self.fine, date=self.today - timedelta(days=20),
            reason='Lameness check', follow_up_date=self.today + timedelta(days=3),
        )
        WormEggCount.objects.create(
            horse=self.soon, date=self.today - timedelta(days=4), count=450,
        )
        body = self.body(needs='1')
        self.assertIn('Needs action · show all', body)
        self.assertIn('Worming · last was Equest', body)
        self.assertIn('2 days overdue', body)
        self.assertIn('Vet follow-up · Lameness check', body)
        self.assertIn('Record vet visit', body)
        self.assertIn('Egg count 450 EPG', body)
        self.assertIn('Record worming', body)
        # Dobbin had nothing due before; now he is on the list.
        self.assertIn('Dobbin', body)

    def test_a_high_egg_count_alone_is_a_due_flag(self):
        from health.models import WormEggCount
        WormEggCount.objects.create(
            horse=self.fine, date=self.today - timedelta(days=4), count=450,
        )
        body = self.body()
        row = body.split(f'id="horse-{self.fine.pk}"', 1)[1].split('</tr>', 1)[0]
        self.assertIn('class="health-dot is-due" title="High egg count"', row)
