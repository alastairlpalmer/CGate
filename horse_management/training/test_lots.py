"""Training phase 2a: lots, training status and the board setting
(docs/TRAINING_MODULE_PLAN.md, 7.3, 7.4, 7.6, 8.3 and 9.2)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_user_with_access
from core.services import PlacementService

from . import services
from .models import Activity, HorseTraining, Lot, LotMember, TrainingSettings, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.canter, position=1)
        self.bally = Horse.objects.create(name='Ballybrook')
        self.kite = Horse.objects.create(name='Red Kite')
        self.moon = Horse.objects.create(name='Dancing Moon')
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(self.user)

    def messages_of(self, response):
        return [str(m) for m in response.wsgi_request._messages]


class MembershipTests(Base):
    def test_a_horse_cannot_be_in_two_lots_and_the_second_add_is_a_message(self):
        services.add_to_lot(self.first, [self.bally])
        response = self.client.post(reverse('training_lot_add_horses', args=[self.second.pk]),
                                    {'horses': [self.bally.pk]}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(services.standing_lot(self.bally), self.first)
        self.assertTrue(any('already in 1st lot' in m for m in self.messages_of(response)))

    def test_adding_starts_work_but_keeps_box_rest(self):
        services.set_status(self.kite, HorseTraining.Status.BOX_REST)
        result = services.add_to_lot(self.first, [self.bally, self.kite])
        self.assertEqual(result.added, [self.bally, self.kite])
        self.assertEqual(result.started_work, [self.bally])
        self.assertEqual(services.training_status(self.bally), 'in_work')
        self.assertEqual(services.training_status(self.kite), 'box_rest')

    def test_order_up_down_move_and_remove(self):
        services.add_to_lot(self.first, [self.bally, self.kite, self.moon])
        kite = LotMember.objects.get(horse=self.kite)
        self.client.post(reverse('training_lot_member', args=[kite.pk]), {'action': 'up'})
        order = list(self.first.members.order_by('position').values_list('horse__name', flat=True))
        self.assertEqual(order, ['Red Kite', 'Ballybrook', 'Dancing Moon'])
        self.assertFalse(services.move_member(LotMember.objects.get(horse=self.kite), 'up'))
        self.client.post(reverse('training_lot_member', args=[kite.pk]), {'action': 'move', 'lot': self.second.pk})
        self.assertEqual(services.standing_lot(self.kite), self.second)
        self.client.post(reverse('training_lot_member', args=[kite.pk]), {'action': 'remove'})
        self.assertIsNone(services.standing_lot(self.kite))

    def test_an_inactive_horse_is_not_added(self):
        self.moon.is_active = False
        self.moon.save()
        result = services.add_to_lot(self.first, [self.moon])
        self.assertEqual(result.left, [self.moon])
        self.assertFalse(LotMember.objects.exists())


class ArchiveTests(Base):
    def test_a_lot_with_horses_cannot_be_archived_and_an_empty_one_can(self):
        services.add_to_lot(self.first, [self.bally])
        response = self.client.post(reverse('training_lot_archive', args=[self.first.pk]))
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)
        self.assertTrue(any('first' in m for m in self.messages_of(response)))
        self.client.post(reverse('training_lot_archive', args=[self.second.pk]))
        self.second.refresh_from_db()
        self.assertFalse(self.second.is_active)
        self.client.post(reverse('training_lot_archive', args=[self.second.pk]))
        self.second.refresh_from_db()
        self.assertTrue(self.second.is_active)


class LeftTheYardTests(Base):
    def test_a_horse_that_departs_stays_a_member_and_comes_back_on_cancel(self):
        location = Location.objects.create(name='Top Field', site='Main')
        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        Placement.objects.create(horse=self.bally, owner=owner, location=location, rate_type=rate,
                                 start_date=self.today - timedelta(days=30))
        services.add_to_lot(self.first, [self.bally])
        PlacementService.depart_horse(self.bally, self.today)
        self.bally.refresh_from_db()
        self.assertFalse(self.bally.is_active)
        self.assertEqual(services.standing_lot(self.bally), self.first)
        response = self.client.get(reverse('training_lots'))
        lot = next(lot for lot in response.context['lots'] if lot.pk == self.first.pk)
        self.assertEqual([m.horse for m in lot.left_yard], [self.bally])
        self.assertEqual(lot.on_yard, [])
        PlacementService.cancel_departure(self.bally)
        response = self.client.get(reverse('training_lots'))
        lot = next(lot for lot in response.context['lots'] if lot.pk == self.first.pk)
        self.assertEqual([m.horse for m in lot.on_yard], [self.bally])


class NoWriteOnGetTests(Base):
    def test_pages_make_no_status_or_settings_rows(self):
        self.client.get(reverse('horse_detail', args=[self.bally.pk]))
        self.client.get(reverse('training_lots'))
        self.client.get(reverse('training_horse_edit', args=[self.bally.pk]), **POPUP)
        self.client.get(reverse('training_setup'))
        self.assertFalse(HorseTraining.objects.exists())
        self.assertFalse(TrainingSettings.objects.exists())
        self.assertEqual(TrainingSettings.load().board_source, 'lots')


class StatusPopupTests(Base):
    def url(self, horse):
        return reverse('training_horse_edit', args=[horse.pk])

    def test_choosing_a_lot_only_starts_work(self):
        response = self.client.post(self.url(self.bally), {
            'status': 'none', 'status_since': self.today.isoformat(), 'lot': self.first.pk, 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(services.standing_lot(self.bally), self.first)
        self.assertEqual(services.training_status(self.bally), 'in_work')

    def test_a_chosen_status_wins_over_the_automatic_one(self):
        self.client.post(self.url(self.bally), {
            'status': 'light', 'status_since': self.today.isoformat(), 'lot': self.first.pk, 'notes': 'Back from rest',
        }, **POPUP)
        row = HorseTraining.objects.get(horse=self.bally)
        self.assertEqual((row.status, row.notes), ('light', 'Back from rest'))

    def test_status_change_moves_the_start_date_and_keeps_history(self):
        services.set_status(self.bally, 'in_work', self.today - timedelta(days=10))
        self.client.post(self.url(self.bally), {
            'status': 'box_rest', 'status_since': self.today.isoformat(), 'lot': '', 'notes': '',
        }, **POPUP)
        row = HorseTraining.objects.get(horse=self.bally)
        self.assertEqual((row.status, row.status_since), ('box_rest', self.today))
        self.assertEqual(row.history.count(), 2)

    def test_the_horse_card_shows_status_and_lot(self):
        services.add_to_lot(self.first, [self.bally])
        response = self.client.get(reverse('horse_detail', args=[self.bally.pk]))
        card = response.context['training_card']
        self.assertEqual((card['status'], card['lot']), ('In work', self.first))
        self.assertContains(response, 'Training status for Ballybrook')


class OtherPageTests(Base):
    def test_log_filters_by_the_horses_now_in_a_lot(self):
        services.add_to_lot(self.first, [self.bally])
        Activity.objects.create(horse=self.bally, date=self.today, work_type=self.canter, status='done')
        Activity.objects.create(horse=self.kite, date=self.today, work_type=self.canter, status='done')
        response = self.client.get(reverse('training_log') + f'?lot={self.first.pk}')
        self.assertEqual([a.horse for a in response.context['activities']], [self.bally])

    def test_board_setting_saves_and_offers_the_week_plan_from_phase_3(self):
        response = self.client.get(reverse('training_setup'))
        values = [value for value, _ in response.context['board_form'].fields['board_source'].choices]
        self.assertEqual(values, ['week_plan', 'lots', 'copy', 'empty'])
        self.client.post(reverse('training_setup'), {'board_source': 'copy'})
        self.assertEqual(TrainingSettings.load().board_source, 'copy')

    def test_in_work_horses_with_no_lot_are_named(self):
        services.set_status(self.moon, 'in_work')
        response = self.client.get(reverse('training_lots'))
        self.assertEqual(response.context['in_work_no_lot'], [self.moon])

    def test_view_role_sees_lots_but_cannot_change_them(self):
        services.add_to_lot(self.first, [self.bally])
        self.client.force_login(make_user_with_access('viewer', training='view'))
        response = self.client.get(reverse('training_lots'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Add horses')
        member = LotMember.objects.get(horse=self.bally)
        response = self.client.post(reverse('training_lot_member', args=[member.pk]), {'action': 'remove'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(LotMember.objects.filter(pk=member.pk).exists())
