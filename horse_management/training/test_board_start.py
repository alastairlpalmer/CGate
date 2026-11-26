"""A date with no board: the preview of what Start makes, and the Start
card (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 9.4).

The preview and ``start_board`` use one plan and the same row builders.
The first tests prove it for each way to start: the preview of a date is
compared with the board that Start then makes for it.
"""

from datetime import time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access

from . import board, services
from .models import (
    Activity, HorseTraining, Lot, LotDay, LotWeekPlan, Rider, TrainingSettings, Venue, WorkType,
)

ROW = ('horse_id', 'work_type_id', 'venue_id', 'follows_lot', 'rider_id', 'rider_is_owner', 'gear',
       'minutes', 'effort', 'position', 'status')


def lot_shape(lot_day, rows):
    return (
        lot_day.lot.name, lot_day.work_type.name, lot_day.venue_id, lot_day.start_time, lot_day.notes,
        [tuple(getattr(row, name) for name in ROW) for row in rows],
    )


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.walk = WorkType.objects.create(name='Walk', kind='ridden', effort=1)
        self.gallop = Venue.objects.create(name='Woodchip gallop', surface='woodchip')
        self.sam = Rider.objects.create(name='Sam')
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, usual_time=time(7, 30), position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.walk, position=1)
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(self.user)
        self.kestrel, self.quill, self.wren, self.alder = (
            Horse.objects.create(name=n) for n in ('Kestrel', 'Quill', 'Wren', 'Alder'))
        services.add_to_lot(self.first, [self.kestrel, self.quill])
        services.add_to_lot(self.second, [self.wren, self.alder])
        services.set_defaults(self.kestrel, rider=self.sam, gear='Martingale')
        services.set_defaults(self.wren, rider_is_owner=True)
        services.set_status(self.alder, HorseTraining.Status.BOX_REST)      # in a lot, not on a board

    def page(self, day=None, **params):
        url = reverse('training_board_day', args=[day.isoformat()]) if day else reverse('training_board')
        return self.client.get(url, params)


class PreviewIsWhatStartMakesTests(Base):
    def check(self, day, source):
        preview = board.preview_board(day, source)
        self.assertFalse(LotDay.objects.filter(date=day).exists())      # the preview saved nothing
        self.assertFalse(Activity.objects.filter(date=day).exists())
        expected = [lot_shape(lot_day, lot_day.rows) for lot_day in preview.lot_days]
        result = board.start_board(day, user=self.user, source=source)
        made = [
            lot_shape(lot_day, lot_day.activities.order_by('position', 'pk'))
            for lot_day in board.board_days(day)
        ]
        self.assertEqual(expected, made)
        self.assertEqual(preview.source, result.source)
        self.assertEqual(preview.horse_count, Activity.objects.filter(date=day).count())
        return preview

    def test_from_the_lots(self):
        preview = self.check(self.today, 'lots')
        self.assertEqual([len(lot_day.rows) for lot_day in preview.lot_days], [2, 1])
        kestrel = preview.lot_days[0].rows[0]
        self.assertEqual((kestrel.horse.name, kestrel.who, kestrel.gear), ('Kestrel', 'Sam', 'Martingale'))
        self.assertEqual(preview.lot_days[1].rows[0].who, 'The owner')
        self.assertEqual(preview.off, [('Alder', 'Box rest')])

    def test_from_the_week_plan(self):
        LotWeekPlan.objects.create(lot=self.first, weekday=self.today.weekday(), work_type=self.walk, venue=self.gallop)
        LotWeekPlan.objects.create(lot=self.second, weekday=self.today.weekday(), no_lot=True)
        preview = self.check(self.today, 'week_plan')
        self.assertEqual([(ld.lot.name, ld.work_type.name, ld.venue.name) for ld in preview.lot_days],
                         [('1st lot', 'Walk', 'Woodchip gallop')])
        self.assertIn('2nd lot', preview.note)

    def test_copy_the_last_board(self):
        yesterday = self.today - timedelta(days=1)
        board.start_board(yesterday, user=self.user, source='lots')
        old = LotDay.objects.get(date=yesterday, lot=self.first)
        board.set_lot_work(old, work_type=self.walk, start_time=time(8, 0), notes='Two steady canters.',
                           venue=self.gallop, user=self.user)
        # A horse's own work, a new lot since, a horse now off the board, and
        # a lot whose work type is archived now.
        Activity.objects.filter(horse=self.quill, date=yesterday).update(
            work_type=self.canter, follows_lot=False, minutes=20, effort=3, gear='Boots')
        third = Lot.objects.create(name='3rd lot', usual_work_type=self.canter, position=2)
        services.add_to_lot(third, [Horse.objects.create(name='Sorrel')])
        services.set_status(self.wren, HorseTraining.Status.RESTING)
        WorkType.objects.filter(pk=self.walk.pk).update(is_active=False)
        preview = self.check(self.today, 'copy')
        self.assertEqual(preview.copy_from, yesterday)
        quill = next(row for row in preview.lot_days[0].rows if row.horse_id == self.quill.pk)
        self.assertEqual((quill.follows_lot, quill.work_type, quill.minutes), (False, self.canter, 20))
        self.assertEqual([len(lot_day.rows) for lot_day in preview.lot_days], [2, 0, 1])

    def test_empty(self):
        preview = self.check(self.today, 'empty')
        self.assertEqual([len(lot_day.rows) for lot_day in preview.lot_days], [0, 0])
        self.assertEqual(preview.off, [])

    def test_copy_with_no_earlier_board_starts_from_the_lots(self):
        preview = self.check(self.today, 'copy')
        self.assertEqual(preview.source, 'lots')

    def test_a_week_plan_with_no_lots_that_day_makes_nothing(self):
        for lot in (self.first, self.second):
            LotWeekPlan.objects.create(lot=lot, weekday=self.today.weekday(), no_lot=True)
        preview = board.preview_board(self.today, 'week_plan')
        self.assertTrue(preview.nothing)
        self.assertEqual(preview.lot_days, [])
        self.assertFalse(board.start_board(self.today, user=self.user, source='week_plan').created)

    def test_the_queries_do_not_grow_with_the_horses(self):
        with self.assertNumQueries(9):
            board.preview_board(self.today, 'lots')
        services.add_to_lot(self.second, [Horse.objects.create(name=f'Horse {i}') for i in range(25)])
        with self.assertNumQueries(9):
            board.preview_board(self.today, 'lots')


class StartCardTests(Base):
    def test_the_page_shows_what_the_board_starts_with(self):
        response = self.page()
        self.assertContains(response, f"No board for {self.today:%a} {self.today.day} {self.today:%b} yet.")
        for name in ('Kestrel', 'Quill', 'Wren', 'Martingale', 'The owner', 'Not started'):
            self.assertContains(response, name)
        self.assertContains(response, 'In a lot, but not in work')
        self.assertContains(response, '<input type="hidden" name="source" value="lots">', html=True)
        self.assertContains(response, 'Start the board')
        self.assertFalse(LotDay.objects.exists())               # principle 5.3
        self.assertFalse(TrainingSettings.objects.exists())

    def test_each_way_to_start_is_a_link_and_the_usual_way_is_marked(self):
        response = self.page()
        self.assertEqual([s['value'] for s in response.context['start_sources'] if s['usual']], ['lots'])
        for value in ('week_plan', 'lots', 'empty'):
            self.assertContains(response, f'href="?source={value}"')
        self.assertNotContains(response, 'href="?source=copy"')         # no earlier board
        self.assertContains(response, 'Copy the last board (there is none yet)')

    def test_a_chosen_way_changes_the_preview_and_what_start_sends(self):
        response = self.page(source='empty')
        self.assertEqual(response.context['preview'].source, 'empty')
        self.assertNotContains(response, 'Kestrel')
        self.assertContains(response, '<input type="hidden" name="source" value="empty">', html=True)
        self.client.post(reverse('training_board_start', args=[self.today.isoformat()]), {'source': 'empty'})
        self.assertEqual(LotDay.objects.filter(date=self.today).count(), 2)
        self.assertFalse(Activity.objects.exists())

    def test_copy_names_the_board_it_copies(self):
        yesterday = self.today - timedelta(days=1)
        board.start_board(yesterday, user=self.user, source='lots')
        response = self.page(source='copy')
        self.assertContains(response, f"Copy {yesterday:%a} {yesterday.day} {yesterday:%b}")
        self.assertEqual(response.context['preview'].source, 'copy')

    def test_a_source_that_is_not_one_uses_the_usual_way(self):
        self.assertEqual(self.page(source='nonsense').context['preview'].source, 'lots')

    def test_a_week_plan_with_no_lots_has_no_start(self):
        for lot in (self.first, self.second):
            LotWeekPlan.objects.create(lot=lot, weekday=self.today.weekday(), no_lot=True)
        response = self.page(source='week_plan')
        self.assertContains(response, f"The week plan has no lots on {self.today:%A}.")
        self.assertNotContains(response, 'Start the board')

    def test_a_yard_with_no_lots_is_sent_to_make_them(self):
        Lot.objects.update(is_active=False)
        response = self.page()
        self.assertContains(response, 'Make your lots')
        self.assertNotContains(response, 'Start the board')

    def test_a_view_role_sees_the_preview_and_cannot_start_or_choose(self):
        self.client.force_login(make_user_with_access('viewer', training='view'))
        response = self.page(source='empty')
        self.assertEqual(response.context['preview'].source, 'lots')    # the yard's usual way, not the address
        self.assertContains(response, 'Kestrel')
        self.assertContains(response, 'Someone with full access to Training starts it.')
        self.assertNotContains(response, 'Start the board')
        self.assertNotContains(response, 'href="?source=')
        self.assertNotContains(response, reverse('training_day_note', args=[self.today.isoformat()]))

    def test_a_day_with_a_board_has_no_start_card(self):
        board.start_board(self.today, user=self.user, source='lots')
        response = self.page()
        self.assertNotIn('preview', response.context)
        self.assertNotContains(response, 'Not started')
