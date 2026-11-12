"""The screens for day turnout (docs/BARNS_AND_STABLES_PLAN.md, phase 3).

What the rules do is in test_turnout.py. This is what a person meets: the
turnout sheet for a horse and for a barn, and the places that now show a
field's turnout horses — its page, its card, the board, the horse list
and the finance chart.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.dashboard import board
from core.models import Horse, Location, Owner, Placement, RateType, Turnout
from core.roles_testutils import make_admin, make_user_with_access
from core.stables import StableService, add_stables
from core.turnout import TurnoutService, open_turnout

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class TurnoutScreenTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Box care', daily_rate=Decimal('20'))
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type='barn', layout='aisle')
        add_stables(cls.barn, 20)
        cls.paddock = Location.objects.create(
            name='Back Paddock', site='Manor Farm', capacity=6, usage=Location.Usage.RESTED)
        cls.hill = Location.objects.create(
            name='Hill Field', site='Manor Farm', capacity=8, usage=Location.Usage.RESTED)

    def setUp(self):
        self.client.force_login(self.admin)

    def horse_in(self, location, name='Marram', days_ago=30):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=days_ago),
        )
        return horse


class HorseTurnoutTests(TurnoutScreenTestCase):
    def test_the_horse_page_offers_turnout_to_a_stabled_horse(self):
        horse = self.horse_in(self.barn)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertContains(response, 'data-testid="turnout-line"')
        self.assertContains(response, 'No turnout set')
        self.assertContains(response, 'Set turnout')

    def test_and_says_where_it_goes_out(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertContains(response, 'Out by day in')
        self.assertContains(response, reverse('location_detail', args=[self.paddock.pk]))
        self.assertContains(response, 'Change turnout')

    def test_a_horse_that_lives_out_has_no_turnout_line(self):
        horse = self.horse_in(self.hill)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertNotContains(response, 'data-testid="turnout-line"')

    def test_the_sheet_offers_ground_and_no_barns(self):
        horse = self.horse_in(self.barn)
        response = self.client.get(reverse('horse_turnout', args=[horse.pk]), **POPUP)
        self.assertContains(response, 'Back Paddock')
        self.assertContains(response, 'No turnout')
        self.assertNotContains(response, '>Top Barn</option>')
        # One horse: no tick list.
        self.assertNotContains(response, 'data-testid="turnout-horses"')

    def test_set_change_and_stop(self):
        horse = self.horse_in(self.barn)
        url = reverse('horse_turnout', args=[horse.pk])
        post = lambda location: self.client.post(url, {  # noqa: E731
            'horses': [horse.pk], 'location': location, 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(post(self.paddock.pk).status_code, 204)
        self.assertEqual(open_turnout(horse).location, self.paddock)
        self.assertEqual(post(self.hill.pk).status_code, 204)
        self.assertEqual(open_turnout(horse).location, self.hill)
        self.assertEqual(post('none').status_code, 204)
        self.assertIsNone(open_turnout(horse))

    def test_a_date_that_cannot_be_shows_on_the_form(self):
        horse = self.horse_in(self.barn, days_ago=3)
        response = self.client.post(reverse('horse_turnout', args=[horse.pk]), {
            'horses': [horse.pk], 'location': self.paddock.pk,
            'day': (self.today - timedelta(days=10)).isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot start before that')
        self.assertIsNone(open_turnout(horse))

    def test_a_barn_is_not_ground(self):
        horse = self.horse_in(self.barn)
        response = self.client.post(reverse('horse_turnout', args=[horse.pk]), {
            'horses': [horse.pk], 'location': self.barn.pk, 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(open_turnout(horse))


class BarnTurnoutTests(TurnoutScreenTestCase):
    def setUp(self):
        super().setUp()
        self.horses = [self.horse_in(self.barn, name) for name in ('Alder', 'Brook', 'Cedar')]
        self.url = reverse('barn_turnout', args=[self.barn.pk])

    def test_the_barn_page_has_the_button(self):
        response = self.client.get(reverse('location_detail', args=[self.barn.pk]))
        self.assertContains(response, 'data-testid="barn-turnout"')

    def test_the_sheet_lists_every_horse_ticked(self):
        response = self.client.get(self.url, **POPUP)
        self.assertContains(response, 'data-testid="turnout-horses"')
        self.assertEqual(response.content.decode().count('name="horses"'), 3)
        self.assertEqual(response.content.decode().count(' checked'), 3)

    def test_set_for_some_then_stop_for_all(self):
        chosen = [self.horses[0].pk, self.horses[2].pk]
        response = self.client.post(self.url, {
            'horses': chosen, 'location': self.paddock.pk, 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Turnout.objects.filter(end_date__isnull=True).count(), 2)
        self.assertIsNone(open_turnout(self.horses[1]))
        page = self.client.get(reverse('location_detail', args=[self.barn.pk]))
        self.assertContains(page, '2 horses go out in Back Paddock.')

        self.client.post(self.url, {
            'horses': [h.pk for h in self.horses], 'location': 'none', 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertFalse(Turnout.objects.filter(end_date__isnull=True).exists())

    def test_the_sheet_will_not_save_until_something_is_chosen(self):
        # Every horse is ticked when it opens. If it also opened on "No
        # turnout", one stray Save would stop the whole barn's turnout.
        for horse in self.horses:
            TurnoutService.start(horse, self.paddock)
        response = self.client.post(self.url, {
            'horses': [h.pk for h in self.horses], 'location': '', 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertIn('location', response.context['form'].errors)
        self.assertEqual(Turnout.objects.filter(end_date__isnull=True).count(), 3)

    def test_one_bad_horse_changes_none(self):
        late = self.horse_in(self.barn, 'Late', days_ago=1)
        response = self.client.post(self.url, {
            'horses': [self.horses[0].pk, late.pk], 'location': self.paddock.pk,
            'day': (self.today - timedelta(days=5)).isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turnout.objects.exists())

    def test_a_horse_from_another_barn_cannot_be_slipped_in(self):
        other_barn = Location.objects.create(
            name='Lower Barn', site='Manor Farm', location_type='barn', layout='row')
        stranger = self.horse_in(other_barn, 'Stranger')
        response = self.client.post(self.url, {
            'horses': [stranger.pk], 'location': self.paddock.pk, 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(open_turnout(stranger))

    def test_the_plan_marks_a_horse_that_goes_out(self):
        StableService.assign(self.horses[0], self.barn.stables.get(label='3'))
        TurnoutService.start(self.horses[0], self.paddock)
        response = self.client.get(reverse('location_detail', args=[self.barn.pk]))
        self.assertContains(response, 'barn-cell-out')
        self.assertContains(response, 'out by day in Back Paddock')


class FieldShowsItsTurnoutTests(TurnoutScreenTestCase):
    def setUp(self):
        super().setUp()
        for name in ('Resident 1', 'Resident 2'):
            self.horse_in(self.paddock, name, days_ago=20)
        for name in ('Marram', 'Sixpence', 'Low Tide'):
            TurnoutService.start(self.horse_in(self.barn, name), self.paddock)

    def test_the_field_page_lists_them_apart_from_the_residents(self):
        response = self.client.get(reverse('location_detail', args=[self.paddock.pk]))
        self.assertContains(response, 'data-testid="turnout-here"')
        self.assertEqual([h.name for h in response.context['out_here']], ['Low Tide', 'Marram', 'Sixpence'])
        self.assertEqual([h.name for h in response.context['table_horses']], ['Resident 1', 'Resident 2'])
        self.assertContains(response, 'Lives in Top Barn')
        # 6 places, 2 living there and 3 out by day: 1 left.
        self.assertEqual(response.context['location'].availability, 1)

    def test_the_card_counts_both_in_its_ring(self):
        response = self.client.get(reverse('location_list'), {'view': 'cards'})
        paddock = next(
            loc for _site, locs, _n in response.context['grouped_locations']
            for loc in locs if loc.name == 'Back Paddock')
        self.assertEqual((paddock.horse_count, paddock.turnout_count, paddock.using), (2, 3, 5))
        self.assertEqual(paddock.availability, 1)
        self.assertContains(response, '+3 out by day')

    def test_the_board_counts_both_on_the_tile_and_residents_only_for_the_site(self):
        band = board.sites_overview(site='Manor Farm')[0]
        tiles = {tile['location'].name: tile for tile in band['tiles']}
        self.assertEqual((tiles['Back Paddock']['count'], tiles['Back Paddock']['turnout']), (5, 3))
        self.assertEqual(tiles['Back Paddock']['availability'], 1)
        self.assertEqual(tiles['Top Barn']['count'], 3)
        # Five horses on the site, not eight: nobody is counted twice.
        self.assertEqual(band['horses'], 5)

    def test_the_preview_panel_lists_them(self):
        response = self.client.get(
            reverse('location_preview', args=[self.paddock.pk]),
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='loc-rail-detail')
        self.assertContains(response, 'data-testid="preview-turnout"')
        self.assertEqual(response.context['count'], 5)
        self.assertEqual(response.context['residents'], 2)

    def test_the_horse_list_says_how_many_go_out_and_tags_them_under_their_barn(self):
        response = self.client.get(reverse('horse_list'), {'group_by': 'location'})
        groups = {group['name']: group for group in response.context['grouped_horses']}
        paddock = groups['Back Paddock']
        self.assertEqual((paddock['count'], paddock['turnout_count'], paddock['ring_count']), (2, 3, 5))
        self.assertEqual(paddock['availability'], 1)
        self.assertEqual(len(groups['Top Barn']['horses']), 3)
        self.assertContains(response, 'data-testid="group-turnout"')
        self.assertContains(response, 'turnout-chip')


class TurnoutOnlyGroundTests(TurnoutScreenTestCase):
    """Decision 12: ground used only for day turnout adds no capacity."""

    def setUp(self):
        super().setUp()
        for name in ('Marram', 'Sixpence'):
            TurnoutService.start(self.horse_in(self.barn, name), self.paddock)

    def test_it_is_turnout_and_shows_as_in_use(self):
        self.paddock.refresh_from_db()
        self.assertEqual(self.paddock.usage, Location.Usage.TURNOUT)
        payload = board.map_locations('Manor Farm')
        paddock = next(loc for loc in payload['locations'] if loc['name'] == 'Back Paddock')
        self.assertEqual((paddock['count'], paddock['capacity']), (2, 6))
        self.assertTrue(paddock['holds_horses'])
        self.assertEqual(paddock['rest_state'], 'grazing')
        self.assertEqual(paddock['rest_label'], 'Turnout')

    def test_the_site_capacity_is_the_barn_alone(self):
        band = board.sites_overview(site='Manor Farm')[0]
        self.assertEqual((band['horses'], band['capacity']), (2, 20))

    def test_the_finance_chart_counts_the_barn_alone(self):
        response = self.client.get(reverse('finances'))
        data = response.context['capacity_data']
        index = data['labels'].index('Manor Farm')
        self.assertEqual((data['horses'][index], data['capacity'][index]), (2, 20))

    def test_turnout_is_a_usage_the_pages_can_show(self):
        response = self.client.get(reverse('location_list'), {'tab': 'usage'})
        self.assertContains(response, 'Turnout')
        response = self.client.get(reverse('location_detail', args=[self.paddock.pk]))
        self.assertContains(response, 'Turnout')


class TurnoutAccessTests(TurnoutScreenTestCase):
    def setUp(self):
        self.horse = self.horse_in(self.barn)
        self.data = {
            'horses': [self.horse.pk], 'location': self.paddock.pk, 'day': self.today.isoformat(),
        }

    def test_a_yard_hand_can_set_it(self):
        self.client.force_login(make_user_with_access('hand', horses='full', locations='view'))
        response = self.client.post(reverse('horse_turnout', args=[self.horse.pk]), self.data, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(open_turnout(self.horse).location, self.paddock)

    def test_whoever_only_sets_the_yard_up_cannot(self):
        self.client.force_login(make_user_with_access('keeper', horses='view', locations='full'))
        for url in (reverse('horse_turnout', args=[self.horse.pk]), reverse('barn_turnout', args=[self.barn.pk])):
            self.assertNotIn(self.client.post(url, self.data, **POPUP).status_code, (200, 204))
        self.assertIsNone(open_turnout(self.horse))
        page = self.client.get(reverse('location_detail', args=[self.barn.pk]))
        self.assertNotContains(page, 'data-testid="barn-turnout"')

    @override_settings(BARNS_ENABLED=False)
    def test_switched_off(self):
        self.client.force_login(self.admin)
        for url in (reverse('horse_turnout', args=[self.horse.pk]), reverse('barn_turnout', args=[self.barn.pk])):
            self.assertEqual(self.client.get(url, **POPUP).status_code, 404)
        page = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertNotContains(page, 'data-testid="turnout-line"')


class TurnoutDataCheckTests(TurnoutScreenTestCase):
    def test_the_command_finds_a_turnout_written_behind_the_service(self):
        horse = self.horse_in(self.barn)
        TurnoutService.start(horse, self.paddock)
        call_command('check_data_quality', verbosity=0)
        loose = self.horse_in(self.hill, 'Loose')
        Turnout.objects.create(horse=loose, location=self.paddock, start_date=self.today)
        with self.assertRaises(CommandError):
            call_command('check_data_quality', verbosity=0)
