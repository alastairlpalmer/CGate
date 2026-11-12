"""The screens for horses in stables (docs/BARNS_AND_STABLES_PLAN.md, phase 2a).

What the service does is in test_stable_stays.py. This is what a person
meets: the plan with horses on it, the stable's panel, the picker on the
move and arrival forms, the horse's own page — and who may press what.
Moving a horse is the ``horses`` feature; setting a barn up is
``locations``.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType, StableStay
from core.roles_testutils import make_admin, make_user_with_access
from core.stables import StableService, add_stables, open_stay, stray_stays

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
BARN = Location.LocationType.BARN


class ScreenTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Box care', daily_rate=Decimal('20'))
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type=BARN, layout='aisle')
        add_stables(cls.barn, 10)
        cls.field = Location.objects.create(name='Back Paddock', site='Manor Farm')

    def setUp(self):
        self.client.force_login(self.admin)

    def stable(self, label):
        return self.barn.stables.get(label=str(label))

    def horse_in(self, location, name='Marram', days_ago=30):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=days_ago),
        )
        return horse

    def barn_url(self):
        return reverse('location_detail', args=[self.barn.pk])


class BarnPageTests(ScreenTestCase):
    def test_the_plan_shows_who_is_where_and_the_tray_shows_the_rest(self):
        marram = self.horse_in(self.barn, 'Marram')
        self.horse_in(self.barn, 'Sixpence')
        StableService.assign(marram, self.stable(7))
        response = self.client.get(self.barn_url())
        self.assertContains(response, 'Stable 7, Marram')
        self.assertContains(response, 'barn-cell is-occupied')
        self.assertContains(response, '1 of 10 stables occupied')
        self.assertContains(response, 'data-testid="barn-tray"')
        self.assertEqual([h.name for h in response.context['table_horses']], ['Sixpence'])
        # The count at the top is still everyone in the barn.
        self.assertEqual(len(response.context['horses']), 2)
        self.assertContains(response, reverse('horse_stable', args=[Horse.objects.get(name='Sixpence').pk]))

    def test_a_shared_stable_names_the_first_and_counts_the_rest(self):
        for name in ('Vivian', 'Little Viv'):
            StableService.assign(self.horse_in(self.barn, name), self.stable(4), share=True)
        response = self.client.get(self.barn_url())
        self.assertContains(response, 'Stable 4, Little Viv and Vivian')
        self.assertContains(response, '+1')

    def test_a_link_from_a_horse_lights_its_stable(self):
        response = self.client.get(self.barn_url(), {'stable': self.stable(7).pk})
        self.assertEqual(response.context['highlight_stable'], self.stable(7).pk)
        self.assertContains(response, 'is-highlight')

    def test_the_barn_page_costs_the_same_however_many_stables(self):
        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.client.get(self.barn_url())
            return len(captured)

        for index in range(3):
            StableService.assign(self.horse_in(self.barn, f'Horse {index}'), self.stable(index + 1))
        few = queries()
        add_stables(self.barn, 30)
        for index in range(3, 20):
            StableService.assign(self.horse_in(self.barn, f'Horse {index}'), self.stable(index + 1))
        self.assertEqual(queries(), few)


class StablePanelTests(ScreenTestCase):
    def panel(self, label, **extra):
        return self.client.get(
            reverse('stable_panel', args=[self.barn.pk, self.stable(label).pk]), **{**POPUP, **extra})

    def test_an_empty_stable_offers_the_horses_with_none(self):
        waiting = self.horse_in(self.barn, 'Sixpence')
        StableService.assign(self.horse_in(self.barn, 'Marram'), self.stable(7))
        response = self.panel(3)
        self.assertContains(response, 'Nobody is in this stable')
        self.assertEqual(list(response.context['waiting']), [waiting])
        self.assertContains(response, 'Put Sixpence in stable 3')
        self.assertContains(response, f'stable={self.stable(3).pk}')

    def test_an_occupied_stable_offers_what_to_do_with_its_horse(self):
        marram = self.horse_in(self.barn, 'Marram')
        StableService.assign(marram, self.stable(7), self.today - timedelta(days=4))
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(9))
        response = self.panel(7)
        self.assertContains(response, 'Marram')
        self.assertContains(response, '4 days')
        self.assertContains(response, 'Move to another stable')
        self.assertContains(response, 'Swap with another horse')
        self.assertContains(response, reverse('horse_stable_vacate', args=[marram.pk]))

    def test_no_swap_when_nobody_else_has_a_stable(self):
        StableService.assign(self.horse_in(self.barn), self.stable(7))
        self.assertNotContains(self.panel(7), 'Swap with another horse')

    def test_history(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today - timedelta(days=9))
        StableService.assign(horse, self.stable(9), self.today - timedelta(days=2))
        self.assertContains(self.panel(7), 'data-testid="stable-history"')

    def test_outside_the_sheet_it_is_the_barn_with_the_stable_lit(self):
        response = self.client.get(reverse('stable_panel', args=[self.barn.pk, self.stable(7).pk]))
        self.assertRedirects(response, f"{self.barn_url()}?stable={self.stable(7).pk}")

    def test_a_gap_has_no_panel(self):
        from core.stables import add_gap
        gap = add_gap(self.barn, 'Tack room')
        self.assertEqual(
            self.client.get(reverse('stable_panel', args=[self.barn.pk, gap.pk]), **POPUP).status_code, 404)


class StableChangeTests(ScreenTestCase):
    def test_put_here_from_the_panel_with_an_undo(self):
        horse = self.horse_in(self.barn)
        response = self.client.post(
            reverse('stable_assign', args=[self.barn.pk, self.stable(7).pk]),
            {'horse': horse.pk}, **POPUP)
        self.assertEqual(response.status_code, 204)
        stay = open_stay(horse)
        self.assertEqual((stay.stable.label, stay.start_date), ('7', self.today))

        page = self.client.get(self.barn_url())
        self.assertContains(page, 'Marram is in stable 7.')
        self.assertContains(page, reverse('stable_stay_undo', args=[stay.pk]))

        self.client.post(reverse('stable_stay_undo', args=[stay.pk]))
        self.assertIsNone(open_stay(horse))

    def test_put_here_refuses_an_occupied_stable_without_the_say_so(self):
        StableService.assign(self.horse_in(self.barn, 'Marram'), self.stable(7))
        other = self.horse_in(self.barn, 'Sixpence')
        url = reverse('stable_assign', args=[self.barn.pk, self.stable(7).pk])
        self.client.post(url, {'horse': other.pk}, **POPUP)
        self.assertIsNone(open_stay(other))
        self.client.post(url, {'horse': other.pk, 'share': '1'}, **POPUP)
        self.assertEqual(open_stay(other).stable.label, '7')

    def test_choose_a_stable_form(self):
        horse = self.horse_in(self.barn)
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(2))
        url = reverse('horse_stable', args=[horse.pk])
        form = self.client.get(url, **POPUP)
        self.assertContains(form, '2 — Sixpence')
        self.assertContains(form, '7 — empty')
        self.assertContains(form, 'name="share_stable"')

        response = self.client.post(url, {
            'stable': self.stable(7).pk, 'day': (self.today - timedelta(days=3)).isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(open_stay(horse).start_date, self.today - timedelta(days=3))

    def test_the_form_asks_before_it_shares(self):
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(2))
        horse = self.horse_in(self.barn)
        url = reverse('horse_stable', args=[horse.pk])
        data = {'stable': self.stable(2).pk, 'day': self.today.isoformat()}
        response = self.client.post(url, data, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sixpence')
        self.assertIsNone(open_stay(horse))
        self.assertEqual(self.client.post(url, {**data, 'share_stable': 'on'}, **POPUP).status_code, 204)
        self.assertEqual(open_stay(horse).stable.label, '2')

    def test_moving_stable_undoes_back_to_the_stable_it_left(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today - timedelta(days=5))
        self.client.post(reverse('horse_stable', args=[horse.pk]), {
            'stable': self.stable(9).pk, 'day': self.today.isoformat(),
        }, **POPUP)
        stay = open_stay(horse)
        self.assertEqual(stay.stable.label, '9')
        page = self.client.get(self.barn_url())
        self.assertContains(page, 'moved from stable 7 to stable 9')
        self.client.post(
            f"{reverse('stable_stay_undo', args=[stay.pk])}?previous={self.stable(7).pk}")
        self.assertEqual(open_stay(horse).stable.label, '7')
        self.assertEqual(StableStay.objects.filter(horse=horse).count(), 1)

    def test_take_out_of_the_stable(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today - timedelta(days=5))
        response = self.client.post(reverse('horse_stable_vacate', args=[horse.pk]), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(open_stay(horse))
        self.assertTrue(horse.placements.filter(end_date__isnull=True).exists())

    def test_swap(self):
        marram, sixpence = self.horse_in(self.barn, 'Marram'), self.horse_in(self.barn, 'Sixpence')
        StableService.assign(marram, self.stable(7), self.today - timedelta(days=5))
        StableService.assign(sixpence, self.stable(9), self.today - timedelta(days=5))
        url = reverse('horse_stable_swap', args=[marram.pk])
        self.assertContains(self.client.get(url, **POPUP), 'Sixpence — stable 9')
        response = self.client.post(url, {'other': sixpence.pk, 'day': self.today.isoformat()}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(open_stay(marram).stable.label, '9')
        self.assertEqual(open_stay(sixpence).stable.label, '7')

    def test_a_horse_in_a_field_has_no_stable_to_choose(self):
        horse = self.horse_in(self.field)
        response = self.client.post(reverse('horse_stable', args=[horse.pk]), {
            'stable': self.stable(7).pk, 'day': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(open_stay(horse))

    def test_a_stable_with_a_horse_in_it_cannot_go_out_of_use(self):
        StableService.assign(self.horse_in(self.barn), self.stable(7))
        response = self.client.post(
            reverse('stable_edit', args=[self.barn.pk, self.stable(7).pk]),
            {'label': '7', 'note': ''}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Marram is in this stable')
        self.assertTrue(self.stable(7).in_use)

    def test_a_stable_with_history_is_not_removed(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7), self.today - timedelta(days=5))
        StableService.vacate(horse)
        self.client.post(reverse('stable_delete', args=[self.barn.pk, self.stable(7).pk]), **POPUP)
        self.assertTrue(self.barn.stables.filter(label='7').exists())


class StablePickerTests(ScreenTestCase):
    def test_options_for_a_barn_say_who_is_in_each(self):
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(2))
        response = self.client.get(reverse('stable_options'), {'new_location': self.barn.pk})
        self.assertContains(response, '2 — Sixpence')
        self.assertContains(response, '7 — empty')
        self.assertContains(response, 'name="share_stable"')

    def test_no_picker_for_a_field_or_a_barn_with_no_stables(self):
        bare = Location.objects.create(name='Old Barn', site='Manor Farm', location_type=BARN, layout='row')
        for location in (self.field, bare):
            response = self.client.get(reverse('stable_options'), {'location': location.pk})
            self.assertEqual(response.content.strip(), b'')

    def test_the_move_form_carries_the_picker_and_the_select_fetches_it(self):
        horse = self.horse_in(self.field)
        response = self.client.get(reverse('horse_move', args=[horse.pk]), **POPUP)
        self.assertContains(response, 'id="stable-picker"')
        self.assertContains(response, f'hx-get="{reverse("stable_options")}?horse={horse.pk}"')
        self.assertContains(response, 'hx-push-url="false"')

    def test_move_into_a_stable(self):
        horse = self.horse_in(self.field)
        response = self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': self.barn.pk, 'move_date': self.today.isoformat(),
            'stable': self.stable(7).pk, 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        stay = open_stay(horse)
        self.assertEqual((stay.stable.label, stay.start_date), ('7', self.today))

    def test_move_with_no_stable_goes_to_the_tray(self):
        horse = self.horse_in(self.field)
        self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': self.barn.pk, 'move_date': self.today.isoformat(), 'notes': '',
        }, **POPUP)
        self.assertEqual(horse.placements.get(end_date__isnull=True).location, self.barn)
        self.assertIsNone(open_stay(horse))

    def test_a_taken_stable_undoes_the_whole_move(self):
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(7))
        horse = self.horse_in(self.field)
        response = self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': self.barn.pk, 'move_date': self.today.isoformat(),
            'stable': self.stable(7).pk, 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sixpence')
        # Still in the paddock, on one placement: nothing was half done.
        self.assertEqual(horse.placements.count(), 1)
        self.assertEqual(horse.placements.get().location, self.field)
        # And the picker is back, with the stable still chosen.
        self.assertContains(response, f'<option value="{self.stable(7).pk}" selected>')

    def test_a_stable_from_another_barn_is_refused(self):
        other = Location.objects.create(name='Lower Barn', site='Manor Farm', location_type=BARN, layout='row')
        add_stables(other, 2)
        horse = self.horse_in(self.field)
        response = self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': self.barn.pk, 'move_date': self.today.isoformat(),
            'stable': other.stables.first().pk, 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(horse.placements.count(), 1)

    def test_arrive_an_existing_horse_into_a_stable(self):
        horse = Horse.objects.create(name='Low Tide')
        response = self.client.post(reverse('horse_arrive', args=[horse.pk]), {
            'location': self.barn.pk, 'owner': self.owner.pk, 'rate_type': self.rate.pk,
            'arrival_date': self.today.isoformat(), 'stable': self.stable(5).pk, 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(open_stay(horse).stable.label, '5')

    def test_arrive_a_new_horse_into_a_stable(self):
        response = self.client.post(reverse('horse_new_arrival'), {
            'name': 'Harbour Light', 'owner': self.owner.pk, 'location': self.barn.pk,
            'rate_type': self.rate.pk, 'arrival_date': self.today.isoformat(),
            'stable': self.stable(5).pk,
        })
        horse = Horse.objects.get(name='Harbour Light')
        self.assertRedirects(response, reverse('horse_detail', args=[horse.pk]))
        self.assertEqual(open_stay(horse).stable.label, '5')

    def test_a_new_arrival_into_a_taken_stable_creates_no_horse(self):
        StableService.assign(self.horse_in(self.barn, 'Sixpence'), self.stable(5))
        response = self.client.post(reverse('horse_new_arrival'), {
            'name': 'Harbour Light', 'owner': self.owner.pk, 'location': self.barn.pk,
            'rate_type': self.rate.pk, 'arrival_date': self.today.isoformat(),
            'stable': self.stable(5).pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Horse.objects.filter(name='Harbour Light').exists())

    def test_arrive_here_fills_the_form_in(self):
        response = self.client.get(
            reverse('horse_new_arrival'), {'location': self.barn.pk, 'stable': self.stable(5).pk})
        self.assertContains(response, f'<option value="{self.stable(5).pk}" selected>')


class HorsePagesTests(ScreenTestCase):
    def test_the_horse_page_names_the_stable_and_links_to_it(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7))
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertContains(response, f'{self.barn_url()}?stable={self.stable(7).pk}')
        self.assertContains(response, 'Stable 7')
        self.assertContains(response, 'Change stable')

    def test_a_horse_in_a_barn_with_no_stable_is_offered_one(self):
        horse = self.horse_in(self.barn)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertContains(response, 'no stable')
        self.assertContains(response, 'Choose a stable')

    def test_a_horse_in_a_field_says_nothing_about_stables(self):
        horse = self.horse_in(self.field)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertNotContains(response, 'data-testid="stable-line"')
        self.assertNotContains(response, reverse('horse_stable', args=[horse.pk]))

    def test_the_horse_list_puts_a_barn_in_stable_order(self):
        for name, label in (('Zephyr', 1), ('Marram', 7), ('Alder', 3)):
            StableService.assign(self.horse_in(self.barn, name), self.stable(label))
        self.horse_in(self.barn, 'Brook')
        response = self.client.get(reverse('horse_list'), {'group_by': 'location'})
        groups = {group['name']: group for group in response.context['grouped_horses']}
        self.assertEqual(
            [horse.name for horse in groups['Top Barn']['horses']],
            ['Zephyr', 'Alder', 'Marram', 'Brook'],
        )
        self.assertContains(response, 'stable-chip')

    def test_a_chosen_sort_wins_over_stable_order(self):
        for name, label in (('Zephyr', 1), ('Alder', 3)):
            StableService.assign(self.horse_in(self.barn, name), self.stable(label))
        response = self.client.get(reverse('horse_list'), {'group_by': 'location', 'sort': 'name'})
        groups = {group['name']: group for group in response.context['grouped_horses']}
        self.assertEqual([horse.name for horse in groups['Top Barn']['horses']], ['Alder', 'Zephyr'])

    def test_the_flat_list_prints_the_stable_after_the_barn(self):
        StableService.assign(self.horse_in(self.barn), self.stable(7))
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, 'Top Barn &middot; 7')


class StableAccessTests(ScreenTestCase):
    """Decision 15: a stable change is a horse move; stable set-up is not."""

    def setUp(self):
        self.horse = self.horse_in(self.barn)
        self.hand = make_user_with_access('hand', horses='full', locations='view')
        self.keeper = make_user_with_access('keeper', horses='view', locations='full')
        self.looker = make_user_with_access('looker', horses='view', locations='view')

    def assign(self):
        return self.client.post(
            reverse('stable_assign', args=[self.barn.pk, self.stable(7).pk]),
            {'horse': self.horse.pk}, **POPUP)

    def test_a_yard_hand_can_move_a_horse_and_cannot_add_a_stable(self):
        self.client.force_login(self.hand)
        self.assertEqual(self.assign().status_code, 204)
        self.assertEqual(open_stay(self.horse).stable.label, '7')
        response = self.client.post(reverse('stables_add', args=[self.barn.pk]), {'count': '1'}, **POPUP)
        self.assertNotIn(response.status_code, (200, 204))
        self.assertEqual(self.barn.stables.count(), 10)
        panel = self.client.get(reverse('stable_panel', args=[self.barn.pk, self.stable(7).pk]), **POPUP)
        self.assertContains(panel, 'Move to another stable')
        self.assertNotContains(panel, 'Edit stable')

    def test_whoever_sets_the_barn_up_cannot_move_a_horse(self):
        self.client.force_login(self.keeper)
        self.assertNotIn(self.assign().status_code, (200, 204))
        self.assertIsNone(open_stay(self.horse))
        response = self.client.post(reverse('stables_add', args=[self.barn.pk]), {'count': '1'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        panel = self.client.get(reverse('stable_panel', args=[self.barn.pk, self.stable(7).pk]), **POPUP)
        self.assertContains(panel, 'Edit stable')
        self.assertNotContains(panel, 'Put here')

    def test_view_only_sees_who_is_where_and_can_change_nothing(self):
        StableService.assign(self.horse, self.stable(7))
        self.client.force_login(self.looker)
        panel = self.client.get(reverse('stable_panel', args=[self.barn.pk, self.stable(7).pk]), **POPUP)
        self.assertContains(panel, 'Marram')
        self.assertNotContains(panel, 'Move to another stable')
        self.assertNotContains(panel, 'Edit stable')
        stay = open_stay(self.horse)
        for url, data in (
            (reverse('horse_stable', args=[self.horse.pk]), {'stable': self.stable(9).pk, 'day': self.today.isoformat()}),
            (reverse('horse_stable_vacate', args=[self.horse.pk]), {}),
            (reverse('horse_stable_swap', args=[self.horse.pk]), {}),
            (reverse('stable_stay_undo', args=[stay.pk]), {}),
        ):
            with self.subTest(url=url):
                self.assertNotIn(self.client.post(url, data, **POPUP).status_code, (200, 204, 302))
        self.assertEqual(open_stay(self.horse).stable.label, '7')


@override_settings(BARNS_ENABLED=False)
class StablesSwitchedOffTests(ScreenTestCase):
    def test_the_forms_have_no_picker(self):
        horse = self.horse_in(self.field)
        response = self.client.get(reverse('horse_move', args=[horse.pk]), **POPUP)
        self.assertNotContains(response, 'stable-picker')
        self.assertNotContains(response, 'hx-get="/locations/stable-options/"')

    def test_the_urls_are_not_found(self):
        horse = self.horse_in(self.barn)
        for url in (
            reverse('stable_options'),
            reverse('stable_panel', args=[self.barn.pk, self.stable(7).pk]),
            reverse('horse_stable', args=[horse.pk]),
            reverse('horse_stable_swap', args=[horse.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, **POPUP).status_code, 404)

    def test_the_horse_page_says_nothing_about_stables(self):
        horse = self.horse_in(self.barn)
        response = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertNotContains(response, 'data-testid="stable-line"')


class DataCheckTests(ScreenTestCase):
    def test_nothing_strays_through_the_service(self):
        horse = self.horse_in(self.barn)
        StableService.assign(horse, self.stable(7))
        self.assertFalse(stray_stays().exists())
        call_command('check_data_quality', verbosity=0)

    def test_a_stay_written_behind_the_service_is_found(self):
        horse = self.horse_in(self.field)
        stay = StableStay.objects.create(horse=horse, stable=self.stable(7), start_date=self.today)
        self.assertEqual(list(stray_stays()), [stay])
        with self.assertRaises(CommandError):
            call_command('check_data_quality', verbosity=0)
