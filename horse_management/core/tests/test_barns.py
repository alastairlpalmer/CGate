"""Barns and stables, phase 1 (docs/BARNS_AND_STABLES_PLAN.md).

A barn is a Location like any other: horses arrive, move and leave as
they always did. What changes is that the land rules stop applying — an
empty barn is empty, not rested — and that it carries a plan of its
stables. No horse has a stable yet; that is phase 2.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.dashboard import board
from core.models import (
    Horse, Location, LocationUsagePeriod, Owner, Placement, RateType, Stable,
)
from core.roles_testutils import make_admin, make_user_with_access
from core.services import LocationUsageService, PlacementService
from core.stables import (
    add_gap, add_stables, barn_plan, convert_to_barn, remove_stable,
    sync_barn_capacity,
)

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
BARN = Location.LocationType.BARN


def make_barn(name='Top Barn', site='Manor Farm', stables=0, layout=Location.Layout.AISLE, **kwargs):
    barn = Location.objects.create(
        name=name, site=site, location_type=BARN, layout=layout, **kwargs)
    if stables:
        add_stables(barn, stables)
    return barn


class BarnTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Box care', daily_rate=20)
        cls.today = timezone.localdate()

    def setUp(self):
        self.client.force_login(self.admin)

    def place(self, location, name='Marram', days_ago=10):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=days_ago),
        )
        return horse


class BarnModelTests(BarnTestCase):
    def test_a_location_is_a_field_unless_told_otherwise(self):
        loc = Location.objects.create(name='Back Paddock', site='Manor Farm')
        self.assertEqual(loc.location_type, Location.LocationType.FIELD)
        self.assertFalse(loc.is_barn)
        self.assertEqual(loc.layout, '')

    def test_queryset_helpers(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        barn = make_barn()
        self.assertEqual(list(Location.objects.barns()), [barn])
        self.assertEqual(list(Location.objects.fields()), [field])

    def test_only_a_barn_has_a_layout(self):
        loc = Location(name='Back Paddock', site='Manor Farm', layout=Location.Layout.ROW)
        with self.assertRaises(ValidationError):
            loc.full_clean()

    def test_only_a_barn_has_stables(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        with self.assertRaises(ValidationError):
            Stable(location=field, label='1', position=1).full_clean()

    def test_a_gap_cannot_be_out_of_use(self):
        barn = make_barn()
        with self.assertRaises(ValidationError):
            Stable(location=barn, label='Tack', position=1, is_gap=True, in_use=False).full_clean()


class BarnCapacityTests(BarnTestCase):
    def test_capacity_is_the_stables_in_use(self):
        barn = make_barn(stables=20)
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 20)

        stable = barn.stables.get(label='7')
        stable.in_use = False
        stable.save()
        sync_barn_capacity(barn)
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 19)

    def test_a_gap_is_not_capacity(self):
        barn = make_barn(stables=20)
        add_gap(barn, 'Tack room', after=barn.stables.get(label='10'))
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 20)
        self.assertEqual(barn.stables.count(), 21)

    def test_a_barn_with_no_stables_keeps_its_typed_capacity(self):
        barn = make_barn(capacity=12)
        self.assertEqual(sync_barn_capacity(barn), 12)
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 12)

    def test_add_stables_numbers_on_from_the_highest(self):
        barn = make_barn(stables=3)
        added = add_stables(barn, 2)
        self.assertEqual([s.label for s in added], ['4', '5'])
        self.assertEqual(
            list(barn.stables.order_by('position').values_list('label', 'position')),
            [('1', 1), ('2', 2), ('3', 3), ('4', 4), ('5', 5)],
        )

    def test_a_gap_moves_the_cells_after_it_one_place_on(self):
        barn = make_barn(stables=4)
        add_gap(barn, 'Tack room', after=barn.stables.get(label='2'))
        self.assertEqual(
            list(barn.stables.order_by('position').values_list('label', flat=True)),
            ['1', '2', 'Tack room', '3', '4'],
        )
        self.assertEqual(
            list(barn.stables.order_by('position').values_list('position', flat=True)),
            [1, 2, 3, 4, 5],
        )

    def test_a_gap_can_go_first(self):
        barn = make_barn(stables=2)
        add_gap(barn, 'Door')
        self.assertEqual(
            list(barn.stables.order_by('position').values_list('label', flat=True)),
            ['Door', '1', '2'],
        )

    def test_removing_from_the_middle_closes_the_order(self):
        barn = make_barn(stables=20)
        remove_stable(barn.stables.get(label='9'))
        positions = list(barn.stables.order_by('position').values_list('position', flat=True))
        self.assertEqual(positions, list(range(1, 20)))
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 19)

    def test_plan_marks_each_cell(self):
        barn = make_barn(stables=3)
        off = barn.stables.get(label='2')
        off.in_use = False
        off.save()
        add_gap(barn, 'Feed room', after=barn.stables.get(label='3'))
        plan = barn_plan(barn)
        self.assertEqual([cell['state'] for cell in plan['cells']], ['empty', 'off', 'empty', 'gap'])
        self.assertEqual(
            (plan['stable_count'], plan['in_use_count'], plan['out_of_use_count']), (3, 2, 1))


class BarnUsageRulesTests(BarnTestCase):
    """The land rules are for land."""

    def test_a_horse_arriving_at_an_empty_barn_records_no_usage(self):
        barn = make_barn(stables=4)
        self.place(barn)
        self.assertFalse(LocationUsagePeriod.objects.filter(location=barn).exists())
        barn.refresh_from_db()
        self.assertEqual(barn.usage, Location.Usage.HORSES)

    def test_the_last_horse_leaving_does_not_rest_the_barn(self):
        barn = make_barn(stables=4)
        horse = self.place(barn)
        PlacementService.depart_horse(horse, self.today)
        barn.refresh_from_db()
        self.assertEqual(barn.usage, Location.Usage.HORSES)
        self.assertFalse(LocationUsagePeriod.objects.filter(location=barn).exists())

    def test_a_field_still_rests(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        horse = self.place(field)
        PlacementService.depart_horse(horse, self.today - timedelta(days=1))
        field.refresh_from_db()
        self.assertEqual(field.usage, Location.Usage.RESTED)

    def test_moving_from_a_barn_to_a_field_only_touches_the_field(self):
        barn = make_barn(stables=4)
        field = Location.objects.create(
            name='Back Paddock', site='Manor Farm', usage=Location.Usage.RESTED)
        horse = self.place(barn)
        PlacementService.move_horse(horse, new_location=field, move_date=self.today)
        barn.refresh_from_db()
        field.refresh_from_db()
        self.assertEqual(barn.usage, Location.Usage.HORSES)
        self.assertFalse(LocationUsagePeriod.objects.filter(location=barn).exists())
        self.assertEqual(field.usage, Location.Usage.HORSES)

    def test_usage_cannot_be_set_by_hand(self):
        barn = make_barn()
        with self.assertRaises(ValidationError):
            LocationUsageService.set_usage(
                barn, usage=Location.Usage.RESTED, change_date=self.today)

    def test_the_usage_form_is_not_there_for_a_barn(self):
        barn = make_barn()
        url = reverse('location_set_usage', args=[barn.pk])
        self.assertEqual(self.client.get(url, **POPUP).status_code, 404)
        self.assertEqual(self.client.post(url, {
            'usage': 'rested', 'change_date': self.today.isoformat(),
        }).status_code, 404)

    def test_the_usage_tab_lists_no_barn(self):
        make_barn()
        Location.objects.create(name='Back Paddock', site='Manor Farm')
        response = self.client.get(reverse('location_list'), {'tab': 'usage'})
        names = [row['location'].name for _site, rows in response.context['usage_overview'] for row in rows]
        self.assertEqual(names, ['Back Paddock'])


class BarnBoardTests(BarnTestCase):
    def test_a_barn_with_horses_is_in_use_not_grazing(self):
        self.assertEqual(
            board.rest_state(count=3, capacity=20, holds_horses=True, rest_days=None, is_barn=True),
            'housed',
        )
        self.assertEqual(board.REST_STATES['housed'][0], 'In use')

    def test_an_empty_barn_is_empty_and_an_overfull_one_is_over(self):
        self.assertEqual(
            board.rest_state(count=0, capacity=20, holds_horses=True, rest_days=None, is_barn=True),
            'empty',
        )
        self.assertEqual(
            board.rest_state(count=22, capacity=20, holds_horses=True, rest_days=None, is_barn=True),
            'over',
        )

    def test_a_field_is_unchanged(self):
        self.assertEqual(
            board.rest_state(count=3, capacity=20, holds_horses=True, rest_days=None), 'grazing')

    def test_a_barn_point_is_drawn_small_whatever_it_holds(self):
        self.assertEqual(board.circle_radius_m(20, is_barn=True), board.BARN_CIRCLE_M)
        self.assertGreater(board.circle_radius_m(20), board.BARN_CIRCLE_M)

    def test_map_payload_says_which_are_barns_and_keeps_kind_for_the_shape(self):
        barn = make_barn(stables=20, latitude='51.100000', longitude='-0.200000')
        self.place(barn)
        payload = board.map_locations('Manor Farm')
        loc = payload['locations'][0]
        self.assertTrue(loc['is_barn'])
        self.assertEqual(loc['kind'], 'circle')
        self.assertEqual(loc['radius_m'], board.BARN_CIRCLE_M)
        self.assertEqual(loc['rest_state'], 'housed')
        self.assertEqual(loc['rest_label'], 'In use')
        self.assertEqual((loc['count'], loc['capacity']), (1, 20))

    def test_preview_panel_for_a_barn(self):
        barn = make_barn(stables=20)
        self.place(barn)
        response = self.client.get(
            reverse('location_preview', args=[barn.pk]),
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='loc-rail-detail',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['rest_label'], 'In use')
        self.assertEqual(response.context['neighbours'], [])
        self.assertIsNone(response.context['area_ha'])
        self.assertNotContains(response, 'Grazing')
        self.assertNotContains(response, 'Mark rested')
        self.assertNotContains(response, reverse('location_set_usage', args=[barn.pk]))


class BarnPagesTests(BarnTestCase):
    def test_add_a_barn_with_stables(self):
        response = self.client.post(reverse('barn_create'), {
            'name': 'Top Barn', 'site': 'Manor Farm', 'layout': 'courtyard',
            'stable_count': '20', 'first_number': '1', 'description': '',
        })
        barn = Location.objects.get(name='Top Barn')
        self.assertRedirects(response, reverse('location_detail', args=[barn.pk]))
        self.assertTrue(barn.is_barn)
        self.assertEqual(barn.usage, Location.Usage.HORSES)
        self.assertEqual(barn.layout, 'courtyard')
        self.assertEqual(barn.capacity, 20)
        self.assertEqual(barn.stables.count(), 20)
        self.assertEqual(barn.stables.order_by('position').first().label, '1')

    def test_add_a_barn_numbered_from_elsewhere(self):
        self.client.post(reverse('barn_create'), {
            'name': 'Lower Barn', 'site': 'Manor Farm', 'layout': 'row',
            'stable_count': '3', 'first_number': '21',
        })
        barn = Location.objects.get(name='Lower Barn')
        self.assertEqual(
            list(barn.stables.order_by('position').values_list('label', flat=True)),
            ['21', '22', '23'],
        )

    def test_add_a_barn_with_no_stables_keeps_the_typed_capacity(self):
        self.client.post(reverse('barn_create'), {
            'name': 'Old Barn', 'site': 'Manor Farm', 'layout': 'row',
            'stable_count': '0', 'capacity': '8',
        })
        barn = Location.objects.get(name='Old Barn')
        self.assertEqual(barn.capacity, 8)
        self.assertFalse(barn.stables.exists())

    def test_a_barn_needs_a_layout(self):
        response = self.client.post(reverse('barn_create'), {
            'name': 'Top Barn', 'site': 'Manor Farm', 'layout': '', 'stable_count': '4',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('layout', response.context['form'].errors)
        self.assertFalse(Location.objects.filter(name='Top Barn').exists())

    def test_the_add_barn_sheet(self):
        response = self.client.get(reverse('barn_create'), **POPUP)
        self.assertContains(response, 'Number of stables')
        self.assertNotContains(response, 'name="usage"')

    def test_the_add_location_form_is_unchanged(self):
        response = self.client.get(reverse('location_create'), **POPUP)
        self.assertContains(response, 'name="usage"')
        self.assertNotContains(response, 'name="layout"')
        self.assertNotContains(response, 'Number of stables')

    def test_editing_a_barn_uses_the_barn_form_and_keeps_its_usage(self):
        barn = make_barn(stables=4)
        url = reverse('location_update', args=[barn.pk])
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'name="layout"')
        self.assertNotContains(response, 'name="usage"')
        self.assertNotContains(response, 'name="capacity"')
        self.assertNotContains(response, 'Number of stables')

        response = self.client.post(url, {
            'name': 'Top Barn', 'site': 'Manor Farm', 'layout': 'row', 'description': 'By the gate',
        })
        self.assertRedirects(response, reverse('location_detail', args=[barn.pk]))
        barn.refresh_from_db()
        self.assertEqual(barn.layout, 'row')
        self.assertEqual(barn.usage, Location.Usage.HORSES)
        self.assertEqual(barn.capacity, 4)
        self.assertFalse(LocationUsagePeriod.objects.filter(location=barn).exists())

    def test_barn_page_shows_the_plan(self):
        barn = make_barn(stables=6, layout=Location.Layout.COURTYARD)
        self.place(barn)
        response = self.client.get(reverse('location_detail', args=[barn.pk]))
        self.assertContains(response, 'data-testid="barn-plan"')
        self.assertContains(response, 'data-testid="barn-badge"')
        self.assertContains(response, 'Stable 1, empty')
        self.assertContains(response, 'In this barn, no stable')
        self.assertContains(response, 'Marram')
        self.assertContains(response, 'Yard')
        # No land-use tab, and no way to it.
        self.assertNotContains(response, '?tab=usage')
        self.assertEqual(len(response.context['barn_plan']['cells']), 6)

    def test_usage_tab_on_a_barn_falls_back_to_the_plan(self):
        barn = make_barn(stables=2)
        response = self.client.get(reverse('location_detail', args=[barn.pk]), {'tab': 'usage'})
        self.assertEqual(response.context['current_tab'], 'current')
        self.assertContains(response, 'data-testid="barn-plan"')

    def test_a_barn_keeps_its_feed_and_history_tabs(self):
        barn = make_barn(stables=2)
        for tab in ('feed', 'history'):
            response = self.client.get(reverse('location_detail', args=[barn.pk]), {'tab': tab})
            self.assertEqual(response.context['current_tab'], tab)

    def test_a_barn_with_no_stables_says_so(self):
        barn = make_barn()
        response = self.client.get(reverse('location_detail', args=[barn.pk]))
        self.assertContains(response, 'data-testid="barn-empty"')
        self.assertContains(response, reverse('stables_add', args=[barn.pk]))

    def test_a_field_page_has_no_plan(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        response = self.client.get(reverse('location_detail', args=[field.pk]))
        self.assertNotIn('barn_plan', response.context)
        self.assertNotContains(response, 'data-testid="barn-plan"')
        self.assertContains(response, '?tab=usage')

    def test_cards_group_barns_then_fields_on_a_site_with_both(self):
        make_barn(stables=20)
        Location.objects.create(name='Back Paddock', site='Manor Farm')
        Location.objects.create(name='Hill Field', site='Other Farm')
        response = self.client.get(reverse('location_list'), {'view': 'cards'})
        grouped = {site: locs for site, locs, _n in response.context['grouped_locations']}
        manor = grouped['Manor Farm']
        self.assertEqual([loc.name for loc in manor], ['Top Barn', 'Back Paddock'])
        self.assertEqual([loc.type_heading for loc in manor], ['Barns', 'Fields'])
        self.assertEqual(manor[0].stable_summary, (20, 0))
        # A site with one type gets no heading at all.
        self.assertEqual([loc.type_heading for loc in grouped['Other Farm']], [''])
        self.assertContains(response, 'data-testid="location-type-heading"', count=2)
        self.assertContains(response, '20 stables')

    def test_horse_list_gives_a_barn_no_land_use_strip(self):
        barn = make_barn(stables=2)
        self.place(barn)
        response = self.client.get(reverse('horse_list'), {'group_by': 'location'})
        groups = {group['name']: group for group in response.context['grouped_horses']}
        self.assertTrue(groups['Top Barn']['is_barn'])
        self.assertNotContains(response, reverse('location_set_usage', args=[barn.pk]))


class StableSetupTests(BarnTestCase):
    def test_add_stables(self):
        barn = make_barn(stables=4)
        response = self.client.post(reverse('stables_add', args=[barn.pk]), {'count': '2'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 6)

    def test_add_stables_stops_at_the_limit(self):
        barn = make_barn(stables=4)
        response = self.client.post(reverse('stables_add', args=[barn.pk]), {'count': '500'}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(barn.stables.count(), 4)

    def test_add_a_gap(self):
        barn = make_barn(stables=4)
        after = barn.stables.get(label='2')
        response = self.client.post(reverse('stable_add_gap', args=[barn.pk]), {
            'label': 'Tack room', 'after': after.pk,
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        gap = barn.stables.get(label='Tack room')
        self.assertTrue(gap.is_gap)
        self.assertEqual(gap.position, 3)
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 4)

    def test_rename_and_take_out_of_use(self):
        barn = make_barn(stables=4)
        stable = barn.stables.get(label='3')
        url = reverse('stable_edit', args=[barn.pk, stable.pk])
        self.assertContains(self.client.get(url, **POPUP), 'name="in_use"')
        response = self.client.post(url, {'label': 'Foaling', 'note': 'Door repair'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        stable.refresh_from_db()
        self.assertEqual((stable.label, stable.in_use, stable.note), ('Foaling', False, 'Door repair'))
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 3)

    def test_two_stables_cannot_share_a_name(self):
        barn = make_barn(stables=4)
        stable = barn.stables.get(label='3')
        response = self.client.post(
            reverse('stable_edit', args=[barn.pk, stable.pk]),
            {'label': '2', 'in_use': 'on'}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertIn('label', response.context['form'].errors)

    def test_a_gap_has_no_in_use_switch(self):
        barn = make_barn(stables=2)
        gap = add_gap(barn, 'Tack room')
        response = self.client.get(reverse('stable_edit', args=[barn.pk, gap.pk]), **POPUP)
        self.assertNotContains(response, 'name="in_use"')

    def test_remove_a_stable(self):
        barn = make_barn(stables=4)
        stable = barn.stables.get(label='2')
        response = self.client.post(reverse('stable_delete', args=[barn.pk, stable.pk]), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Stable.objects.filter(pk=stable.pk).exists())
        barn.refresh_from_db()
        self.assertEqual(barn.capacity, 3)

    def test_remove_is_post_only(self):
        barn = make_barn(stables=4)
        stable = barn.stables.first()
        self.assertEqual(
            self.client.get(reverse('stable_delete', args=[barn.pk, stable.pk])).status_code, 405)

    def test_a_stable_is_only_reachable_through_its_own_barn(self):
        barn = make_barn(stables=2)
        other = make_barn(name='Lower Barn', stables=2)
        stable = other.stables.first()
        self.assertEqual(
            self.client.get(reverse('stable_edit', args=[barn.pk, stable.pk]), **POPUP).status_code, 404)

    def test_stable_urls_are_not_there_for_a_field(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        self.assertEqual(
            self.client.get(reverse('stables_add', args=[field.pk]), **POPUP).status_code, 404)

    def test_outside_the_sheet_a_form_goes_to_the_barn(self):
        barn = make_barn(stables=2)
        response = self.client.get(reverse('stables_add', args=[barn.pk]))
        self.assertRedirects(response, reverse('location_detail', args=[barn.pk]))


class ConvertToBarnTests(BarnTestCase):
    def test_convert_keeps_placements_history_and_outline(self):
        boundary = {'type': 'Polygon', 'coordinates': [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
        loc = Location.objects.create(name='Big Barn', site='Manor Farm', boundary=boundary)
        horse = self.place(loc, days_ago=30)
        earlier = LocationUsagePeriod.objects.filter(location=loc).count()
        self.assertTrue(earlier)

        response = self.client.post(reverse('location_convert_to_barn', args=[loc.pk]), {
            'layout': 'aisle', 'stable_count': '10', 'first_number': '1',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)

        loc.refresh_from_db()
        self.assertTrue(loc.is_barn)
        self.assertEqual(loc.layout, 'aisle')
        self.assertEqual(loc.usage, Location.Usage.HORSES)
        self.assertEqual(loc.capacity, 10)
        self.assertEqual(loc.boundary, boundary)
        self.assertEqual(horse.current_placement.location, loc)
        # The past is kept; nothing is left open.
        self.assertEqual(LocationUsagePeriod.objects.filter(location=loc).count(), earlier)
        self.assertFalse(
            LocationUsagePeriod.objects.filter(location=loc, end_date__isnull=True).exists())

    def test_a_usage_period_that_began_today_is_dropped_not_inverted(self):
        loc = Location.objects.create(name='Big Barn', site='Manor Farm')
        LocationUsagePeriod.objects.create(
            location=loc, usage=Location.Usage.HORSES, start_date=self.today)
        convert_to_barn(loc, layout=Location.Layout.ROW)
        self.assertFalse(LocationUsagePeriod.objects.filter(location=loc).exists())

    def test_a_barn_cannot_be_converted_again(self):
        barn = make_barn()
        self.assertEqual(self.client.post(
            reverse('location_convert_to_barn', args=[barn.pk]), {'layout': 'row'}, **POPUP,
        ).status_code, 404)

    def test_the_edit_page_offers_it_for_a_field_only(self):
        field = Location.objects.create(name='Big Barn', site='Manor Farm')
        barn = make_barn()
        self.assertContains(
            self.client.get(reverse('location_update', args=[field.pk])),
            'data-testid="convert-to-barn"')
        self.assertNotContains(
            self.client.get(reverse('location_update', args=[barn.pk])),
            'data-testid="convert-to-barn"')


class BarnAccessTests(BarnTestCase):
    def setUp(self):
        self.viewer = make_user_with_access('looker', locations='view', horses='view')
        self.client.force_login(self.viewer)

    def test_view_only_sees_the_plan_and_nothing_to_press(self):
        barn = make_barn(stables=4)
        add_gap(barn, 'Tack room')
        response = self.client.get(reverse('location_detail', args=[barn.pk]))
        self.assertContains(response, 'data-testid="barn-plan"')
        self.assertContains(response, 'barn-cell is-empty')
        stable = barn.stables.filter(is_gap=False).first()
        self.assertNotContains(response, reverse('stables_add', args=[barn.pk]))
        self.assertNotContains(response, reverse('stable_add_gap', args=[barn.pk]))
        self.assertNotContains(response, reverse('stable_edit', args=[barn.pk, stable.pk]))

    def test_view_only_cannot_write(self):
        barn = make_barn(stables=4)
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        stable = barn.stables.first()
        attempts = [
            (reverse('barn_create'), {'name': 'X', 'site': 'Y', 'layout': 'row'}),
            (reverse('stables_add', args=[barn.pk]), {'count': '1'}),
            (reverse('stable_add_gap', args=[barn.pk]), {'label': 'Tack'}),
            (reverse('stable_edit', args=[barn.pk, stable.pk]), {'label': 'Z'}),
            (reverse('stable_delete', args=[barn.pk, stable.pk]), {}),
            (reverse('location_convert_to_barn', args=[field.pk]), {'layout': 'row'}),
        ]
        for url, data in attempts:
            with self.subTest(url=url):
                response = self.client.post(url, data, **POPUP)
                self.assertNotIn(response.status_code, (200, 204))
        self.assertEqual(barn.stables.count(), 4)
        self.assertFalse(Location.objects.filter(name='X').exists())
        field.refresh_from_db()
        self.assertFalse(field.is_barn)
        stable.refresh_from_db()
        self.assertNotEqual(stable.label, 'Z')


@override_settings(BARNS_ENABLED=False)
class BarnsSwitchedOffTests(BarnTestCase):
    def test_nothing_offers_a_barn(self):
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        self.assertNotContains(self.client.get(reverse('location_list'), {'view': 'cards'}), 'Add barn')
        self.assertNotContains(
            self.client.get(reverse('location_update', args=[field.pk])),
            'data-testid="convert-to-barn"')

    def test_barn_urls_are_not_found(self):
        barn = make_barn(stables=2)
        field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        stable = barn.stables.first()
        for url in (
            reverse('barn_create'),
            reverse('stables_add', args=[barn.pk]),
            reverse('stable_add_gap', args=[barn.pk]),
            reverse('stable_edit', args=[barn.pk, stable.pk]),
            reverse('location_convert_to_barn', args=[field.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, **POPUP).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('stable_delete', args=[barn.pk, stable.pk])).status_code, 404)

    def test_a_barn_that_exists_draws_no_plan_and_groups_under_no_heading(self):
        barn = make_barn(stables=2)
        Location.objects.create(name='Back Paddock', site='Manor Farm')
        response = self.client.get(reverse('location_detail', args=[barn.pk]))
        self.assertNotIn('barn_plan', response.context)
        self.assertNotContains(response, 'data-testid="barn-plan"')
        response = self.client.get(reverse('location_list'), {'view': 'cards'})
        self.assertNotContains(response, 'data-testid="location-type-heading"')

    def test_a_barn_that_exists_is_still_not_rested(self):
        # The flag hides the feature; it must not hand a barn back to the
        # land rules half way through a yard's use of it.
        barn = make_barn(stables=2)
        horse = self.place(barn)
        PlacementService.depart_horse(horse, self.today)
        barn.refresh_from_db()
        self.assertEqual(barn.usage, Location.Usage.HORSES)
