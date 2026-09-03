"""Tests for the entity-spine grouping on the horse list.

The grouped axes build from locations, sites and owners and hang horses
off them, rather than bucketing the horse list. That is what lets an
empty field appear at all — and an empty field is the answer to "where
can this horse go".
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access


class EntityGroupSpineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('spine')

        cls.rate = RateType.objects.create(name='Full livery', daily_rate=30)
        cls.alice = Owner.objects.create(name='Alice Appleby')
        cls.bob = Owner.objects.create(name='Bob Bramble')
        cls.no_horses = Owner.objects.create(name='Zoe Zero')

        cls.barn = Location.objects.create(
            name='Big Barn', site='Colgate', capacity=4,
        )
        cls.paddock = Location.objects.create(
            name='Top Paddock', site='Colgate', capacity=2,
        )
        # Empty, and resting — invisible to any horse-built grouping.
        cls.resting = Location.objects.create(
            name='Bottom Field', site='Somerford', capacity=6,
            usage=Location.Usage.RESTED,
        )
        cls.archived = Location.objects.create(
            name='Old Yard', site='Somerford', is_archived=True,
        )

        today = timezone.localdate()
        cls.zara = cls._place('Zara', cls.alice, cls.barn, today)
        cls.milo = cls._place('Milo', cls.alice, cls.barn, today)
        cls.apple = cls._place('Apple', cls.bob, cls.paddock, today)

    @classmethod
    def _place(cls, name, owner, location, start):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=owner, location=location,
            rate_type=cls.rate, start_date=start,
        )
        return horse

    def setUp(self):
        self.client.force_login(self.user)

    def _groups(self, **params):
        response = self.client.get(reverse('horse_list'), params)
        self.assertEqual(response.status_code, 200)
        return response.context['grouped_horses']

    def _names(self, **params):
        return [g['name'] for g in self._groups(**params)]

    # ── Empty groups ─────────────────────────────────────────────────

    def test_empty_location_appears_by_default(self):
        """The whole point: a rested, empty field is still a card."""
        names = self._names(group_by='location')
        self.assertIn('Bottom Field', names)
        empty = next(g for g in self._groups(group_by='location')
                     if g['name'] == 'Bottom Field')
        self.assertEqual(empty['count'], 0)
        self.assertEqual(empty['horses'], [])

    def test_empty_locations_can_be_hidden(self):
        names = self._names(group_by='location', show_empty='0')
        self.assertNotIn('Bottom Field', names)
        self.assertIn('Big Barn', names)

    def test_owners_without_horses_are_hidden_by_default(self):
        """A roll of owners with no horses is noise, not an answer."""
        self.assertNotIn('Zoe Zero', self._names(group_by='owner'))

    def test_owners_without_horses_can_be_shown(self):
        self.assertIn('Zoe Zero', self._names(group_by='owner', show_empty='1'))

    def test_empty_site_appears_by_default(self):
        """Somerford holds only the resting field, so it has no horses."""
        self.assertIn('Somerford', self._names(group_by='site'))

    # ── Archived locations ───────────────────────────────────────────

    def test_archived_location_is_not_offered_as_an_empty_group(self):
        self.assertNotIn('Old Yard', self._names(group_by='location'))

    def test_horse_on_an_archived_location_still_appears(self):
        """Archiving hides a field from pickers. It must not hide a horse."""
        stray = self._place(
            'Stray', self.bob, self.archived, timezone.localdate(),
        )
        groups = self._groups(group_by='location')
        old_yard = next(g for g in groups if g['name'] == 'Old Yard')
        self.assertEqual([h.name for h in old_yard['horses']], [stray.name])

    # ── Capacity and land use on the header ──────────────────────────

    def test_location_group_carries_capacity_and_availability(self):
        barn = next(g for g in self._groups(group_by='location')
                    if g['name'] == 'Big Barn')
        self.assertEqual(barn['capacity'], 4)
        self.assertEqual(barn['count'], 2)
        self.assertEqual(barn['availability'], 2)

    def test_full_location_reports_no_availability(self):
        paddock = next(g for g in self._groups(group_by='location')
                       if g['name'] == 'Top Paddock')
        self.assertEqual(paddock['capacity'], 2)
        self.assertEqual(paddock['availability'], 1)

    def test_location_group_carries_land_use(self):
        resting = next(g for g in self._groups(group_by='location')
                       if g['name'] == 'Bottom Field')
        self.assertEqual(resting['usage'], Location.Usage.RESTED)
        self.assertEqual(resting['usage_display'], 'Rested')

    def test_capacity_ring_renders_for_a_location_with_capacity(self):
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertContains(response, '2/4')
        self.assertContains(response, 'Rested')

    # ── Site axis ────────────────────────────────────────────────────

    def test_site_axis_groups_by_site(self):
        groups = self._groups(group_by='site')
        colgate = next(g for g in groups if g['name'] == 'Colgate')
        self.assertEqual(colgate['count'], 3)
        self.assertEqual(
            sorted(h.name for h in colgate['horses']),
            ['Apple', 'Milo', 'Zara'],
        )

    def test_site_axis_offers_its_own_sorts(self):
        response = self.client.get(reverse('horse_list'), {'group_by': 'site'})
        keys = [o['key'] for o in response.context['sort_options']]
        self.assertIn('location', keys)
        self.assertIn('owner', keys)

    # ── Unplaced and unowned horses ──────────────────────────────────

    def test_group_sort_by_count_still_works_with_empty_groups(self):
        names = self._names(group_by='location', gsort='-count')
        self.assertEqual(names[0], 'Big Barn')
        self.assertEqual(names[-1], 'Bottom Field')

    def test_horse_sort_still_applies_inside_a_group(self):
        groups = self._groups(group_by='location', sort='-name')
        barn = next(g for g in groups if g['name'] == 'Big Barn')
        self.assertEqual([h.name for h in barn['horses']], ['Zara', 'Milo'])


class AxisPermissionTests(TestCase):
    """Horses, owners and locations are separate features.

    An axis that reads another feature's records is only offered when the
    role can see that feature.
    """

    @classmethod
    def setUpTestData(cls):
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)
        cls.owner = Owner.objects.create(name='Alice Appleby')
        cls.field = Location.objects.create(name='Top Field', site='Colgate')
        horse = Horse.objects.create(name='Zara')
        Placement.objects.create(
            horse=horse, owner=cls.owner, location=cls.field,
            rate_type=cls.rate, start_date=timezone.localdate(),
        )

    def _axes_for(self, **levels):
        user = make_user_with_access('gated', **levels)
        self.client.force_login(user)
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response.status_code, 200)
        return response.context['available_axes']

    def test_all_axes_when_every_feature_is_visible(self):
        axes = self._axes_for(horses='full', owners='view', locations='view')
        self.assertEqual(axes, ['all', 'site', 'location', 'owner'])

    def test_owner_axis_hidden_without_the_owners_feature(self):
        axes = self._axes_for(horses='full', locations='view')
        self.assertNotIn('owner', axes)
        self.assertIn('location', axes)

    def test_location_axes_hidden_without_the_locations_feature(self):
        axes = self._axes_for(horses='full', owners='view')
        self.assertNotIn('location', axes)
        self.assertNotIn('site', axes)
        self.assertIn('owner', axes)

    def test_hidden_axis_in_the_url_falls_back_to_all(self):
        """A stale bookmark must still show the horses."""
        user = make_user_with_access('nolocs', horses='full')
        self.client.force_login(user)
        response = self.client.get(
            reverse('horse_list'), {'group_by': 'location'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['group_by'], 'all')

    def test_hidden_axis_button_is_not_rendered(self):
        user = make_user_with_access('nolocs2', horses='full', owners='view')
        self.client.force_login(user)
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'group_by=location')
        self.assertContains(response, 'group_by=owner')


class AxisQueryCountTests(TestCase):
    """The entity spine must cost a constant number of queries.

    Building groups from locations and owners is the whole point of the
    rewrite, but it is also exactly where a per-group query creeps in.
    These caps make that fail the suite instead of the yard's phone.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('counter')
        rate = RateType.objects.create(name='Grass', daily_rate=10)
        today = timezone.localdate()
        # Enough rows that any per-group query pattern shows up.
        for i in range(15):
            owner = Owner.objects.create(name=f'Owner {i:02d}')
            location = Location.objects.create(
                name=f'Field {i:02d}', site=f'Site {i % 3}', capacity=4,
            )
            horse = Horse.objects.create(name=f'Horse {i:02d}')
            Placement.objects.create(
                horse=horse, owner=owner, location=location,
                rate_type=rate, start_date=today - timedelta(days=30),
            )
        # Empty locations and owners, so the spine really has to reach for
        # rows the horse list would never mention.
        for i in range(10):
            Location.objects.create(name=f'Spare {i:02d}', site='Spare')
            Owner.objects.create(name=f'Spare Owner {i:02d}')

    def setUp(self):
        self.client.force_login(self.user)

    def _count(self, **params):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('horse_list'), params)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_every_axis_stays_within_the_cap(self):
        # Measured at 11-12 with 25 locations and 25 owners; the cap leaves
        # headroom for middleware without hiding a per-group query.
        for params in (
            {},
            {'group_by': 'site'},
            {'group_by': 'location'},
            {'group_by': 'location', 'show_empty': '0'},
            {'group_by': 'owner'},
            {'group_by': 'owner', 'show_empty': '1'},
        ):
            with self.subTest(**params):
                self.assertLessEqual(self._count(**params), 18)

    def test_showing_empty_groups_adds_no_queries_per_group(self):
        """25 locations, not 25 queries."""
        lean = self._count(group_by='location', show_empty='0')
        full = self._count(group_by='location', show_empty='1')
        self.assertLessEqual(full, lean + 1)
