"""Training phase 2b: the lot board rules and pages
(docs/TRAINING_MODULE_PLAN.md, 5.3, 5.4, 5.12, 7.4, 8.2 and 9.3)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_user_with_access
from core.services import PlacementService

from . import board, services
from .models import Activity, LotDay, Lot, TrainingSettings, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.walk = WorkType.objects.create(name='Walk', kind='ridden', effort=1)
        self.walker = WorkType.objects.create(name='Horse walker', kind='not_ridden', effort=1)
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.walker, position=1)
        self.user = make_user_with_access('trainer', training='full', horses='view', health='full')
        self.client.force_login(self.user)
        self.horses = {}
        for name in ('Ballybrook', 'Red Kite', 'Juniper'):
            self.horses[name] = Horse.objects.create(name=name)
        services.add_to_lot(self.first, list(self.horses.values()))
        self.moon = Horse.objects.create(name='Dancing Moon')
        services.add_to_lot(self.second, [self.moon])

    def h(self, name):
        return self.horses[name]

    def start(self, day=None, source='lots'):
        return board.start_board(day or self.today, user=self.user, source=source)

    def lot_day(self, lot, day=None):
        return LotDay.objects.get(lot=lot, date=day or self.today)

    def row(self, horse, day=None):
        return Activity.objects.get(horse=horse, date=day or self.today, lot_day__isnull=False)


class StartTests(Base):
    def test_opening_the_board_makes_nothing_and_a_second_start_makes_one_board(self):
        response = self.client.get(reverse('training_board'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LotDay.objects.exists())
        url = reverse('training_board_start', args=[self.today.isoformat()])
        self.client.post(url, {'source': 'lots'})
        response = self.client.post(url, {'source': 'lots'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LotDay.objects.count(), 2)
        self.assertEqual(Activity.objects.count(), 4)

    def test_a_horse_on_box_rest_is_not_on_the_board_and_is_counted(self):
        services.set_status(self.h('Juniper'), 'box_rest')
        self.start()
        self.assertFalse(Activity.objects.filter(horse=self.h('Juniper')).exists())
        self.assertIn(('Box rest', 1), board.not_on_board_summary(self.today))

    def test_rows_follow_the_member_order_and_the_lots_work(self):
        self.start()
        rows = list(self.lot_day(self.first).activities.order_by('position'))
        self.assertEqual([r.horse.name for r in rows], ['Ballybrook', 'Red Kite', 'Juniper'])
        self.assertTrue(all(r.work_type == self.canter and r.follows_lot and r.status == 'planned' for r in rows))

    def test_empty_makes_lot_days_with_no_horses_and_add_horse_works(self):
        self.start(source='empty')
        self.assertEqual(LotDay.objects.count(), 2)
        self.assertFalse(Activity.objects.exists())
        result = board.add_horses(self.lot_day(self.second), [self.h('Ballybrook')], user=self.user)
        self.assertEqual(result.added, ['Ballybrook'])

    def test_copy_takes_the_last_board_with_its_moves_and_own_changes(self):
        saturday = self.today - timedelta(days=2)
        self.start(saturday)
        kite = self.row(self.h('Red Kite'), saturday)
        board.move_horse(kite, self.lot_day(self.second, saturday), user=self.user)
        bally = self.row(self.h('Ballybrook'), saturday)
        bally.work_type = self.walk
        board.apply_row_edit(bally, {'work_type'})
        bally.save()
        board.cancel_lot(self.lot_day(self.first, saturday), 'weather', user=self.user)
        result = self.start(source='copy')
        self.assertTrue(result.created)
        self.assertEqual(self.row(self.h('Red Kite')).lot_day.lot, self.second)
        bally_today = self.row(self.h('Ballybrook'))
        self.assertEqual((bally_today.work_type, bally_today.follows_lot, bally_today.status), (self.walk, False, 'planned'))
        self.assertEqual(self.row(self.h('Juniper')).status, 'planned')

    def test_copy_with_no_earlier_board_starts_from_the_lots(self):
        result = self.start(source='copy')
        self.assertEqual(result.source, 'lots')
        self.assertTrue(result.note)

    def test_the_setting_gives_the_default_source(self):
        TrainingSettings(board_source='empty').save()
        result = board.start_board(self.today, user=self.user)
        self.assertEqual(result.source, 'empty')


class LotWorkTests(Base):
    def test_a_lot_change_keeps_a_horses_own_change(self):
        self.start()
        kite = self.row(self.h('Red Kite'))
        kite.work_type = self.walker
        board.apply_row_edit(kite, {'work_type'})
        kite.save()
        board.set_lot_work(self.lot_day(self.first), work_type=self.walk, start_time=None, notes='', user=self.user)
        self.assertEqual(self.row(self.h('Red Kite')).work_type, self.walker)
        self.assertEqual(self.row(self.h('Ballybrook')).work_type, self.walk)

    def test_change_is_refused_on_a_lot_that_is_done(self):
        self.start()
        board.mark_lot_done(self.lot_day(self.first), user=self.user)
        with self.assertRaises(board.BoardError):
            board.set_lot_work(self.lot_day(self.first), work_type=self.walk, start_time=None, notes='', user=self.user)
        response = self.client.get(reverse('training_board'))
        self.assertNotContains(response, f'data-popup-title="Change {self.first.name}"')


class MarkTests(Base):
    def test_mark_lot_done_and_reopen_change_only_the_lots_rows(self):
        day = self.today - timedelta(days=7)
        self.start(day)
        own = self.row(self.h('Ballybrook'), day)
        services.mark_done([own], user=self.user)
        lame = self.row(self.h('Juniper'), day)
        services.mark_not_done([lame], 'unsound', user=self.user)
        result = board.mark_lot_done(self.lot_day(self.first, day), user=self.user)
        self.assertEqual(result.changed, 1)
        self.assertEqual(self.row(self.h('Red Kite'), day).status, 'done')
        self.assertEqual(self.row(self.h('Juniper'), day).status, 'not_done')
        board.reopen_lot(self.lot_day(self.first, day), user=self.user)
        self.assertEqual(self.row(self.h('Red Kite'), day).status, 'planned')
        self.assertEqual(self.row(self.h('Ballybrook'), day).status, 'done')
        self.assertEqual(self.row(self.h('Juniper'), day).status, 'not_done')

    def test_a_did_not_work_saved_first_stays_when_the_lot_is_marked_done(self):
        self.start()
        lame = self.row(self.h('Juniper'))
        services.mark_not_done([lame], 'unsound', user=self.user)
        board.mark_lot_done(self.lot_day(self.first), user=self.user)
        self.assertEqual(self.row(self.h('Juniper')).status, 'not_done')
        # The other order: a row the lot made Done cannot then be marked Did not work by the menu.
        result = services.mark_not_done([self.row(self.h('Red Kite'))], 'unsound', user=self.user)
        self.assertEqual(result.changed, 0)

    def test_mark_lot_done_is_refused_after_today_but_tomorrow_can_be_planned(self):
        tomorrow = self.today + timedelta(days=1)
        self.start(tomorrow)
        board.set_lot_work(self.lot_day(self.first, tomorrow), work_type=self.walk, start_time=None, notes='', user=self.user)
        with self.assertRaises(board.BoardError):
            board.mark_lot_done(self.lot_day(self.first, tomorrow), user=self.user)
        response = self.client.get(reverse('training_board_day', args=[tomorrow.isoformat()]))
        self.assertNotContains(response, 'Mark lot done')

    def test_cancel_lot_and_reopen(self):
        self.start()
        services.mark_done([self.row(self.h('Ballybrook'))], user=self.user)
        count = board.cancel_lot(self.lot_day(self.first), 'weather', user=self.user)
        self.assertEqual(count, 2)
        kite = self.row(self.h('Red Kite'))
        self.assertEqual((kite.status, kite.reason), ('not_done', 'weather'))
        self.assertEqual(self.row(self.h('Ballybrook')).status, 'done')
        board.reopen_lot(self.lot_day(self.first), user=self.user)
        self.assertEqual(self.row(self.h('Red Kite')).status, 'planned')
        self.assertEqual(self.row(self.h('Ballybrook')).status, 'done')

    def test_every_lot_action_leaves_history_with_the_user(self):
        self.start()
        lot_day = self.lot_day(self.first)
        self.client.post(reverse('training_lot_day_action', args=[lot_day.pk, 'done']))
        record = self.row(self.h('Red Kite')).history.first()
        self.assertEqual((record.status, record.history_user), ('done', self.user))
        lot_record = lot_day.history.first()
        self.assertEqual((lot_record.status, lot_record.history_user), ('done', self.user))


class StandingChangeTests(Base):
    def test_a_horse_added_to_the_lot_after_the_start_shows_in_the_hint(self):
        yesterday = self.today - timedelta(days=1)
        self.start(yesterday)
        self.start()
        late = Horse.objects.create(name='Tobermory')
        services.add_to_lot(self.first, [late])
        self.assertEqual(board.missing_members(self.lot_day(self.first)), [late])
        self.assertEqual(board.missing_members(self.lot_day(self.first, yesterday)), [])
        url = reverse('training_lot_day_horses', args=[self.lot_day(self.first).pk])
        self.client.post(url, {'horses': [late.pk]}, **POPUP)
        self.assertTrue(Activity.objects.filter(horse=late, date=self.today).exists())
        self.assertFalse(Activity.objects.filter(horse=late, date=yesterday).exists())

    def test_box_rest_after_the_start_turns_today_into_did_not_work(self):
        self.start()
        self.start(self.today + timedelta(days=1))
        juniper = self.h('Juniper')
        response = self.client.post(reverse('training_horse_edit', args=[juniper.pk]), {
            'status': 'box_rest', 'status_since': self.today.isoformat(), 'lot': self.first.pk,
            'notes': '', 'update_boards': 'on',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        row = self.row(juniper)
        self.assertEqual((row.status, row.reason), ('not_done', 'resting'))
        self.assertFalse(Activity.objects.filter(horse=juniper, date=self.today + timedelta(days=1)).exists())
        board.mark_lot_done(self.lot_day(self.first), user=self.user)
        self.assertEqual(self.row(juniper).status, 'not_done')

    def test_back_in_work_goes_on_todays_board(self):
        services.set_status(self.h('Juniper'), 'box_rest')
        self.start()
        board.apply_status_to_boards(self.h('Juniper'), 'in_work', self.today, user=self.user)
        self.assertEqual(self.row(self.h('Juniper')).lot_day.lot, self.first)

    def test_a_horse_that_departs_after_the_start_stays_planned(self):
        location = Location.objects.create(name='Top Field', site='Main')
        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        kite = self.h('Red Kite')
        Placement.objects.create(horse=kite, owner=owner, location=location, rate_type=rate,
                                 start_date=self.today - timedelta(days=30))
        self.start()
        PlacementService.depart_horse(kite, self.today)
        result = board.mark_lot_done(self.lot_day(self.first), user=self.user)
        self.assertEqual(result.left_yard, ['Red Kite'])
        self.assertEqual(self.row(kite).status, 'planned')
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, 'Left the yard')

    def test_removing_from_the_lot_can_take_the_horse_off_started_boards(self):
        self.start()
        member = self.first.members.get(horse=self.h('Red Kite'))
        self.client.post(reverse('training_lot_member', args=[member.pk]), {'action': 'remove', 'also_boards': 'on'})
        self.assertFalse(Activity.objects.filter(horse=self.h('Red Kite')).exists())


class RowTests(Base):
    def test_move_to_another_lot_today_and_a_second_move_is_a_message(self):
        self.start()
        kite = self.row(self.h('Red Kite'))
        target = self.lot_day(self.second)
        self.client.post(reverse('training_activity_move', args=[kite.pk]), {'lot_day': target.pk})
        kite.refresh_from_db()
        self.assertEqual((kite.lot_day, kite.work_type, kite.follows_lot), (target, self.walker, True))
        board.add_horses(self.lot_day(self.first), [self.h('Red Kite')], user=self.user)
        response = self.client.post(reverse('training_activity_move', args=[kite.pk]),
                                    {'lot_day': self.lot_day(self.first).pk}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_did_not_work_and_undo_on_one_row(self):
        self.start()
        kite = self.row(self.h('Red Kite'))
        self.client.post(reverse('training_activity_mark', args=[kite.pk]), {'action': 'not_done', 'reason': 'unsound'})
        self.assertEqual(self.row(self.h('Red Kite')).status, 'not_done')
        self.client.post(reverse('training_activity_mark', args=[kite.pk]), {'action': 'planned'})
        self.assertEqual(self.row(self.h('Red Kite')).status, 'planned')

    def test_a_new_date_in_the_form_takes_the_row_off_the_board(self):
        self.start()
        kite = self.row(self.h('Red Kite'))
        self.client.post(reverse('training_activity_update', args=[kite.pk]), {
            'horse': kite.horse_id, 'date': (self.today - timedelta(days=1)).isoformat(),
            'work_type': self.canter.pk, 'status': 'done',
        }, **POPUP)
        kite.refresh_from_db()
        self.assertIsNone(kite.lot_day)

    def test_only_done_and_did_not_work_reach_the_timeline(self):
        self.start()
        board.mark_lot_done(self.lot_day(self.first), user=self.user)
        response = self.client.get(reverse('horse_detail', args=[self.moon.pk]))
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'training'])
        response = self.client.get(reverse('horse_detail', args=[self.h('Red Kite').pk]))
        self.assertTrue([e for e in response.context['timeline_events'] if e['type'] == 'training'])


class PageTests(Base):
    def test_the_board_shows_lots_rows_and_the_not_confirmed_line(self):
        yesterday = self.today - timedelta(days=1)
        self.start(yesterday)
        self.start()
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, '1st lot')
        self.assertContains(response, 'Ballybrook')
        self.assertContains(response, 'Mark lot done')
        self.assertEqual(response.context['last_not_confirmed'], yesterday)

    def test_training_home_opens_the_board_and_the_phone_bar_has_the_tab(self):
        response = self.client.get(reverse('training_home'))
        self.assertRedirects(response, reverse('training_board'), fetch_redirect_response=False)
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, 'id="tab-training"')
        self.client.force_login(make_user_with_access('h', horses='view'))
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'id="tab-training"')

    def test_view_role_sees_the_board_and_cannot_act(self):
        self.start()
        self.client.force_login(make_user_with_access('v', training='view'))
        response = self.client.get(reverse('training_board'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Mark lot done')
        response = self.client.post(reverse('training_lot_day_action', args=[self.lot_day(self.first).pk, 'done']))
        self.assertEqual(response.status_code, 403)
