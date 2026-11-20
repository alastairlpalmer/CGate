"""Regression tests for the bugs found in the Training review of 2026-11-20
(docs/TRAINING_MODULE_PLAN.md, section 23)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access

from . import board, load, services
from .models import Activity, HorseTraining, Lot, LotDay, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.lot = Lot.objects.create(name='1st lot', usual_work_type=self.canter)
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(self.user)
        self.horse = Horse.objects.create(name='Ballybrook')
        services.add_to_lot(self.lot, [self.horse], day=self.today - timedelta(days=40))

    def days(self, *offsets):
        for offset in offsets:
            board.start_board(self.today + timedelta(days=offset), user=self.user, source='lots')

    def row(self, offset):
        return Activity.objects.filter(horse=self.horse, date=self.today + timedelta(days=offset)).first()


class StatusChangeTests(Base):
    def test_new_status_without_a_new_date_starts_today(self):
        self.days(-2, -1, 0, 1)
        url = reverse('training_horse_edit', args=[self.horse.pk])
        old = HorseTraining.objects.get(horse=self.horse).status_since
        self.client.post(url, {
            'status': 'box_rest', 'status_since': old.isoformat(), 'lot': self.lot.pk,
            'notes': '', 'update_boards': 'on',
        }, **POPUP)
        status = HorseTraining.objects.get(horse=self.horse)
        self.assertEqual((status.status, status.status_since), ('box_rest', self.today))
        # Past days are not deleted: they stay as they were, to confirm.
        self.assertEqual(self.row(-2).status, 'planned')
        self.assertEqual(self.row(-1).status, 'planned')
        self.assertEqual((self.row(0).status, self.row(0).reason), ('not_done', 'resting'))
        self.assertIsNone(self.row(1))

    def test_status_from_a_past_date_marks_the_days_since_then(self):
        self.days(-2, -1, 0, 1)
        board.apply_status_to_boards(self.horse, 'resting', self.today - timedelta(days=1), user=self.user)
        self.assertEqual(self.row(-2).status, 'planned')
        self.assertEqual(self.row(-1).status, 'not_done')
        self.assertEqual(self.row(0).status, 'not_done')
        self.assertIsNone(self.row(1))

    def test_back_in_work_from_a_later_date_is_not_on_todays_board(self):
        services.set_status(self.horse, 'box_rest', self.today)
        self.days(0)
        result = board.apply_status_to_boards(self.horse, 'in_work', self.today + timedelta(days=3), user=self.user)
        self.assertIsNone(result.added_to)
        self.assertIsNone(self.row(0))

    def test_new_lot_with_a_note_keeps_the_automatic_in_work(self):
        loose = Horse.objects.create(name='Loose')
        self.client.post(reverse('training_horse_edit', args=[loose.pk]), {
            'status': 'none', 'status_since': self.today.isoformat(), 'lot': self.lot.pk,
            'notes': 'Back from grass',
        }, **POPUP)
        status = HorseTraining.objects.get(horse=loose)
        self.assertEqual((status.status, status.notes), ('in_work', 'Back from grass'))


class FactTests(Base):
    def test_no_work_counts_from_the_status_start_when_it_is_later(self):
        old = Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=40),
                                      work_type=self.canter, status='done')
        rows = [old]
        chips = load.chips(rows, self.today, threshold=4, since=self.today - timedelta(days=1))
        self.assertFalse([c for c in chips if c['text'].startswith('No work')])
        self.assertEqual(load.idle_start(None, None), None)

    def test_rows_for_keeps_the_facts_fields(self):
        Activity.objects.create(horse=self.horse, date=self.today, work_type=self.canter, status='done', minutes=30)
        with self.assertNumQueries(2):
            rows = load.rows_for([self.horse.pk], self.today)[self.horse.pk]
            self.assertEqual(rows[0].work_type.name, 'Canter')
            self.assertEqual((rows[0].minutes, rows[0].effective_effort), (30, 2))


class NotConfirmedTests(Base):
    def test_a_horse_that_left_does_not_keep_a_done_lot_unconfirmed(self):
        other = Horse.objects.create(name='Red Kite')
        services.add_to_lot(self.lot, [other])
        self.days(-1)
        other.is_active = False
        other.save()
        lot_day = LotDay.objects.get(date=self.today - timedelta(days=1))
        board.mark_lot_done(lot_day, user=self.user, today=self.today)
        self.assertIsNone(board.last_not_confirmed_before(self.today))
        overview = self.client.get(reverse('training_home')).context['attention']
        self.assertEqual(overview['unconfirmed_lots'], 0)


class CopyTests(Base):
    def test_archived_work_type_is_not_copied(self):
        schooling = WorkType.objects.create(name='Schooling', kind='ridden', effort=2)
        self.days(-1)
        yesterday = LotDay.objects.get(date=self.today - timedelta(days=1))
        board.set_lot_work(yesterday, work_type=schooling, start_time=None, notes='', user=self.user)
        schooling.is_active = False
        schooling.save()
        board.start_board(self.today, user=self.user, source='copy')
        today = LotDay.objects.get(date=self.today)
        self.assertEqual(today.work_type, self.canter)
        self.assertEqual(self.row(0).work_type, self.canter)


class InputTests(Base):
    def test_board_dates_at_the_limits_are_404(self):
        for value in ('0001-01-01', '9999-12-31'):
            with self.subTest(value):
                self.assertEqual(self.client.get(reverse('training_board_day', args=[value])).status_code, 404)

    def test_move_with_a_bad_lot_is_400(self):
        self.days(0)
        response = self.client.post(reverse('training_activity_move', args=[self.row(0).pk]), {'lot_day': 'abc'})
        self.assertEqual(response.status_code, 400)
