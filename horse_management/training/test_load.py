"""Training phase 4: workload facts, dashboard items, print, TV and CSV
(docs/TRAINING_MODULE_PLAN.md, 9.5)."""

from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.dashboard import activity as timeline
from core.dashboard.attention import collect
from core.models import Horse
from core.roles_testutils import make_user_with_access

from . import board, load, services
from .models import Activity, HorseTraining, Lot, LotDay, LotWeekPlan, TrainingSettings, WorkType

FRIDAY = date(2026, 11, 20)   # a Friday


class LoadFunctionTests(TestCase):
    """Each fact, on rows made in memory: a gap, a rest day, a Did not work,
    a Planned day in the gap, and a horse with no records."""

    def setUp(self):
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.fast = WorkType.objects.create(name='Fast work', kind='ridden', effort=3)
        self.rest = WorkType.objects.create(name='Rest day', kind='rest', effort=0)
        self.horse = Horse.objects.create(name='Ballybrook')

    def row(self, days_ago, work, status='done', minutes=None, reason=''):
        return Activity.objects.create(
            horse=self.horse, date=FRIDAY - timedelta(days=days_ago), work_type=work,
            status=status, minutes=minutes, reason=reason or ('weather' if status == 'not_done' else ''),
        )

    def rows(self):
        return load.rows_for([self.horse.pk], FRIDAY)[self.horse.pk]

    def test_a_horse_with_no_records(self):
        rows = self.rows()
        self.assertIsNone(load.last_work_date(rows, FRIDAY))
        self.assertIsNone(load.days_since_work(rows, FRIDAY))
        self.assertIsNone(load.days_since_fast(rows, FRIDAY))
        self.assertEqual((load.days_worked_7(rows, FRIDAY), load.fast_sessions_7(rows, FRIDAY)), (0, 0))
        self.assertEqual((load.minutes_7(rows, FRIDAY), load.minutes_28(rows, FRIDAY)), (0, 0))
        self.assertEqual((load.fast_days_running(rows, FRIDAY), load.unconfirmed_days(rows, FRIDAY)), (0, 0))
        self.assertEqual(load.chips(rows, FRIDAY, threshold=3), [])

    def test_a_gap_a_rest_day_and_a_did_not_work(self):
        self.row(10, self.canter, minutes=40)
        self.row(5, self.rest)                      # a rest is not work
        self.row(4, self.canter, status='not_done')  # nor is Did not work
        rows = self.rows()
        self.assertEqual(load.last_work_date(rows, FRIDAY), FRIDAY - timedelta(days=10))
        self.assertEqual(load.days_since_work(rows, FRIDAY), 10)
        self.assertEqual(load.days_worked_7(rows, FRIDAY), 0)
        self.assertEqual((load.minutes_7(rows, FRIDAY), load.minutes_28(rows, FRIDAY)), (0, 40))
        self.assertEqual(load.chips(rows, FRIDAY, threshold=3), [{'text': 'No work for 10 days', 'tone': 'warn'}])

    def test_planned_days_in_the_gap_say_not_confirmed(self):
        self.row(5, self.canter)
        for days_ago in (4, 3, 2, 1):
            self.row(days_ago, self.canter, status='planned')
        rows = self.rows()
        self.assertEqual(load.unconfirmed_days(rows, FRIDAY), 4)
        self.assertEqual(load.chips(rows, FRIDAY, threshold=3), [{'text': '4 days not confirmed', 'tone': 'warn'}])

    def test_fast_work_counts(self):
        self.row(2, self.fast, minutes=20)
        self.row(1, self.fast, minutes=20)
        self.row(0, self.canter, minutes=30)
        rows = self.rows()
        self.assertEqual(load.days_since_fast(rows, FRIDAY), 1)
        self.assertEqual(load.fast_sessions_7(rows, FRIDAY), 2)
        self.assertEqual(load.fast_days_running(rows, FRIDAY), 2)
        self.assertEqual(load.minutes_7(rows, FRIDAY), 70)
        self.assertIn({'text': 'Fast work 2 days running', 'tone': 'info'}, load.chips(rows, FRIDAY, threshold=3))

    def test_first_work_after_a_long_gap(self):
        self.row(16, self.canter)
        self.row(0, self.canter, status='planned')
        rows = self.rows()
        self.assertEqual(load.first_work_gap(rows, FRIDAY), 16)
        self.assertEqual(load.chips(rows, FRIDAY, threshold=3)[0]['text'], 'First work in 16 days')

    def test_days_this_week_count_only_done_work(self):
        self.row(4, self.canter)                      # Monday
        self.row(3, self.canter)                      # Tuesday
        self.row(2, self.canter, status='not_done')   # Wednesday: does not count
        self.row(0, self.canter)                      # Friday
        self.row(7, self.canter)                      # last Friday: not this week
        rows = self.rows()
        self.assertEqual(load.week_days_worked(rows, FRIDAY), 3)
        self.assertIn({'text': '3 of 5 days this week', 'tone': 'neutral'},
                      load.chips(rows, FRIDAY, threshold=3, days_per_week=5))

    def test_the_strip_has_a_letter_and_a_label_for_each_day(self):
        self.row(0, self.fast)
        self.row(1, self.rest)
        self.row(2, self.canter, status='not_done')
        days = load.strip(self.rows(), FRIDAY)
        self.assertEqual(len(days), 28)
        self.assertEqual([d['letter'] for d in days[-3:]], ['×', 'R', 'F'])
        self.assertIn('Fast work, fast, done', days[-1]['label'])
        self.assertEqual(days[0]['letter'], '')


class DashboardTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.lot = Lot.objects.create(name='1st lot', usual_work_type=self.canter)
        self.horse = Horse.objects.create(name='Ballybrook')
        services.add_to_lot(self.lot, [self.horse])
        services.set_status(self.horse, 'in_work', self.today - timedelta(days=1))
        self.user = make_user_with_access('trainer', training='full', dashboard='full', horses='view')

    def kinds(self, user=None):
        return [item.kind for item in collect(user or self.user, today=self.today)]

    def test_board_not_started_shows_and_goes_when_it_starts(self):
        self.assertIn('training_board', self.kinds())
        board.start_board(self.today, user=self.user, source='lots')
        self.assertNotIn('training_board', self.kinds())

    def test_no_lot_today_in_the_week_plan_means_no_warning(self):
        LotWeekPlan.objects.create(lot=self.lot, weekday=self.today.weekday(), no_lot=True)
        self.assertNotIn('training_board', self.kinds())

    def test_a_lot_not_confirmed_stays_until_confirmed_or_14_days_old(self):
        yesterday = self.today - timedelta(days=1)
        board.start_board(yesterday, user=self.user, source='lots')
        self.assertIn('training_unconfirmed', self.kinds())
        later = [item.kind for item in collect(self.user, today=self.today + timedelta(days=1))]
        self.assertIn('training_unconfirmed', later)
        old = [item.kind for item in collect(self.user, today=yesterday + timedelta(days=15))]
        self.assertNotIn('training_unconfirmed', old)
        self.assertTrue(Activity.objects.filter(status='planned').exists())   # nothing changed it (D4)
        board.mark_lot_done(LotDay.objects.get(date=yesterday), user=self.user)
        self.assertNotIn('training_unconfirmed', self.kinds())

    def test_no_work_for_n_days(self):
        HorseTraining.objects.filter(horse=self.horse).update(status_since=self.today - timedelta(days=5))
        items = [i for i in collect(self.user, today=self.today) if i.kind == 'training_no_work']
        self.assertEqual([i.detail for i in items], ['No work for 5 days'])
        TrainingSettings(no_work_days=7).save()
        self.assertNotIn('training_no_work', self.kinds())

    def test_a_role_without_training_sees_no_training_items_or_rows(self):
        board.start_board(self.today, user=self.user, source='lots')
        board.mark_lot_done(LotDay.objects.get(date=self.today), user=self.user)
        hidden = make_user_with_access('h', dashboard='full', horses='view')
        self.assertFalse([k for k in self.kinds(hidden) if k.startswith('training')])
        days = timeline.recent(hidden, today=self.today)
        self.assertFalse([e for d in days for e in d['events'] if e.kind == 'training'])
        days = timeline.recent(self.user, today=self.today)
        self.assertTrue([e for d in days for e in d['events'] if e.kind == 'training'])

    @override_settings(TRAINING_ENABLED=False)
    def test_switched_off_gives_no_items(self):
        self.assertFalse([k for k in self.kinds() if k.startswith('training')])


class SheetAndCsvTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.user = make_user_with_access('trainer', training='full')
        self.client.force_login(self.user)
        for n in range(3):
            lot = Lot.objects.create(name=f'Lot {n + 1}', usual_work_type=self.canter, position=n)
            services.add_to_lot(lot, [Horse.objects.create(name=f'Horse {n}-{i}') for i in range(10)])
        board.start_board(self.today, user=self.user, source='lots')

    def test_print_and_tv_views_show_every_horse_and_no_buttons(self):
        response = self.client.get(reverse('training_board_sheet', args=[self.today.isoformat(), 'print']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '@page { size: A4 portrait')
        self.assertEqual(response.content.decode().count('<td class="tick">'), 30)
        self.assertNotContains(response, 'Mark lot done')
        response = self.client.get(reverse('training_board_sheet', args=[self.today.isoformat(), 'tv']))
        self.assertContains(response, 'http-equiv="refresh" content="60"')

    def test_csv_downloads_the_filtered_log(self):
        response = self.client.get(reverse('training_log') + '?status=planned&format=csv')
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        lines = response.content.decode('utf-8-sig').strip().splitlines()
        self.assertEqual(lines[0].split(',')[:4], ['Date', 'Horse', 'Lot', 'Work'])
        self.assertEqual(len(lines), 31)

    def test_a_role_without_training_cannot_open_the_sheet(self):
        self.client.force_login(make_user_with_access('h', horses='view'))
        response = self.client.get(reverse('training_board_sheet', args=[self.today.isoformat(), 'print']))
        self.assertEqual(response.status_code, 302)
