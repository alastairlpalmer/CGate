"""Training phase 3: week plan, venues and riders
(docs/TRAINING_MODULE_PLAN.md, 7.5 and 9.4)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Owner, OwnershipShare
from core.roles_testutils import make_user_with_access

from . import board, services
from .forms import ActivityForm
from .models import Activity, HorseTraining, Lot, LotDay, LotWeekPlan, Rider, Venue, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def next_weekday(weekday):
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


class Base(TestCase):
    def setUp(self):
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.fast = WorkType.objects.create(name='Fast work', kind='ridden', effort=3)
        self.walker = WorkType.objects.create(name='Horse walker', kind='not_ridden', effort=1)
        self.woodchip = Venue.objects.create(name='Woodchip gallop', surface='woodchip')
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.walker, position=1)
        self.sam = Rider.objects.create(name='Sam')
        self.jo = Rider.objects.create(name='Jo')
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(self.user)
        self.bally = Horse.objects.create(name='Ballybrook')
        self.kite = Horse.objects.create(name='Red Kite')
        services.add_to_lot(self.first, [self.bally, self.kite])
        self.moon = Horse.objects.create(name='Dancing Moon')
        services.add_to_lot(self.second, [self.moon])


class WeekPlanTests(Base):
    def test_a_tuesday_board_starts_with_each_lots_tuesday_work_and_venue(self):
        LotWeekPlan.objects.create(lot=self.first, weekday=1, work_type=self.fast, venue=self.woodchip)
        tuesday = next_weekday(1)
        board.start_board(tuesday, user=self.user, source='week_plan')
        first = LotDay.objects.get(lot=self.first, date=tuesday)
        self.assertEqual((first.work_type, first.venue), (self.fast, self.woodchip))
        row = Activity.objects.get(horse=self.bally, date=tuesday)
        self.assertEqual((row.work_type, row.venue), (self.fast, self.woodchip))
        second = LotDay.objects.get(lot=self.second, date=tuesday)
        self.assertEqual((second.work_type, second.venue), (self.walker, None))

    def test_no_lot_on_sunday_for_every_lot_makes_no_board_and_says_so(self):
        for lot in (self.first, self.second):
            LotWeekPlan.objects.create(lot=lot, weekday=6, no_lot=True)
        sunday = next_weekday(6)
        result = board.start_board(sunday, user=self.user, source='week_plan')
        self.assertFalse(result.created)
        self.assertFalse(LotDay.objects.filter(date=sunday).exists())
        self.assertIn('no lots on Sunday', result.note)
        response = self.client.post(reverse('training_board_start', args=[sunday.isoformat()]),
                                    {'source': 'week_plan'}, follow=True)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('no lots on Sunday' in m for m in messages), messages)

    def test_one_lot_off_on_a_day_leaves_the_others(self):
        LotWeekPlan.objects.create(lot=self.second, weekday=6, no_lot=True)
        sunday = next_weekday(6)
        result = board.start_board(sunday, user=self.user, source='week_plan')
        self.assertEqual([ld.lot for ld in result.lot_days], [self.first])
        self.assertIn('2nd lot', result.note)

    def test_the_week_plan_form_saves_seven_days(self):
        url = reverse('training_lot_week_plan', args=[self.first.pk])
        data = {f'work_{d}': '' for d in range(7)}
        data.update({f'venue_{d}': '' for d in range(7)})
        data.update({'work_1': str(self.fast.pk), 'venue_1': str(self.woodchip.pk), 'work_6': 'none'})
        response = self.client.post(url, data, **POPUP)
        self.assertEqual(response.status_code, 204)
        plans = {p.weekday: p for p in LotWeekPlan.objects.filter(lot=self.first)}
        self.assertEqual(set(plans), {1, 6})
        self.assertEqual((plans[1].work_type, plans[1].venue), (self.fast, self.woodchip))
        self.assertTrue(plans[6].no_lot)


class RiderTests(Base):
    def test_new_rows_take_the_default_rider_and_a_day_change_does_not_change_it(self):
        services.set_defaults(self.bally, rider=self.sam, gear='Martingale')
        today = timezone.localdate()
        board.start_board(today, user=self.user, source='lots')
        row = Activity.objects.get(horse=self.bally, date=today)
        self.assertEqual((row.rider, row.gear), (self.sam, 'Martingale'))
        self.client.post(reverse('training_activity_update', args=[row.pk]), {
            'horse': self.bally.pk, 'date': today.isoformat(), 'work_type': self.canter.pk,
            'status': 'planned', 'who': str(self.jo.pk), 'gear': 'Martingale',
        }, **POPUP)
        row.refresh_from_db()
        self.assertEqual(row.rider, self.jo)
        self.assertEqual(HorseTraining.objects.get(horse=self.bally).default_rider, self.sam)

    def test_an_archived_rider_leaves_the_picker_and_old_records_keep_the_name(self):
        row = Activity.objects.create(horse=self.bally, date=timezone.localdate(), work_type=self.canter,
                                      status='done', rider=self.sam)
        self.client.post(reverse('training_setup_archive', args=['rider', self.sam.pk]))
        self.sam.refresh_from_db()
        self.assertFalse(self.sam.is_active)
        values = [v for v, _ in ActivityForm().fields['who'].choices]
        self.assertNotIn(str(self.sam.pk), values)
        self.assertIn(str(self.sam.pk), [v for v, _ in ActivityForm(instance=row).fields['who'].choices])
        self.assertEqual(row.rider_label, 'Sam')

    def test_a_rider_with_a_login_sees_their_own_horses_first(self):
        self.sam.user = self.user
        self.sam.save()
        services.set_defaults(self.kite, rider=self.sam)
        board.start_board(timezone.localdate(), user=self.user, source='lots')
        response = self.client.get(reverse('training_board'))
        self.assertEqual([r.horse for r in response.context['my_rows']], [self.kite])
        self.assertContains(response, 'Your horses today (1)')

    def test_the_rider_filter_shows_one_riders_horses(self):
        services.set_defaults(self.kite, rider=self.sam)
        board.start_board(timezone.localdate(), user=self.user, source='lots')
        response = self.client.get(reverse('training_board') + f'?rider={self.sam.pk}')
        shown = [r.horse for ld in response.context['lot_days'] for r in ld.rows]
        self.assertEqual(shown, [self.kite])

    def test_the_owner_as_rider_shows_the_owners_name_and_not_a_list_rider(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        OwnershipShare.objects.create(horse=self.bally, owner=owner, share_percentage=100, is_primary_contact=True)
        form = ActivityForm(data={
            'horse': self.bally.pk, 'date': timezone.localdate().isoformat(), 'work_type': self.canter.pk,
            'status': 'done', 'who': 'owner',
        })
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertTrue(row.rider_is_owner)
        self.assertIsNone(row.rider)
        row = Activity.objects.select_related('horse').get(pk=row.pk)
        self.assertEqual(row.rider_label, 'Jo Bloggs')
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Activity.objects.filter(pk=row.pk).update(rider=self.sam)

    def test_bulk_add_with_no_rider_gives_each_horse_its_own(self):
        services.set_defaults(self.bally, rider=self.sam)
        services.set_defaults(self.kite, rider_is_owner=True)
        self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'training', 'horse_ids': [self.bally.pk, self.kite.pk],
            'date': timezone.localdate().isoformat(), 'work_type': self.canter.pk, 'status': 'done',
        }, **POPUP)
        self.assertEqual(Activity.objects.get(horse=self.bally).rider, self.sam)
        self.assertTrue(Activity.objects.get(horse=self.kite).rider_is_owner)

    def test_status_popup_saves_the_usual_rider_and_gear(self):
        response = self.client.post(reverse('training_horse_edit', args=[self.moon.pk]), {
            'status': 'in_work', 'status_since': timezone.localdate().isoformat(), 'lot': self.second.pk,
            'notes': '', 'default_who': str(self.jo.pk), 'default_gear': 'Boots',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        row = HorseTraining.objects.get(horse=self.moon)
        self.assertEqual((row.default_rider, row.default_gear), (self.jo, 'Boots'))


class VenueTests(Base):
    def test_a_lot_day_venue_goes_to_the_rows_that_follow_the_lot(self):
        today = timezone.localdate()
        board.start_board(today, user=self.user, source='lots')
        lot_day = LotDay.objects.get(lot=self.first, date=today)
        board.set_lot_work(lot_day, work_type=self.canter, start_time=None, notes='', venue=self.woodchip, user=self.user)
        self.assertEqual(Activity.objects.get(horse=self.bally, date=today).venue, self.woodchip)

    def test_set_up_lists_venues_riders_and_the_week_plan(self):
        response = self.client.get(reverse('training_setup'))
        self.assertContains(response, 'Woodchip gallop')
        self.assertContains(response, 'Sam')
        self.assertContains(response, 'Edit week')
