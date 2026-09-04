"""Tests for the horse list "Group by" toggle and its Sort pop-out.

The Active tab groups by All (the default flat list), Location or Owner.
Each grouping offers its own sort menu; the Departed tab and search
results are flat lists with a sort menu of their own.
"""

import re
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import administrator_role, assign_role

User = get_user_model()


class HorseListGroupSortTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='grouper',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()
        assign_role(cls.user, administrator_role())

        cls.rate = RateType.objects.create(name='Full livery', daily_rate=30)
        cls.alice = Owner.objects.create(name='Alice Appleby')
        cls.bob = Owner.objects.create(name='Bob Bramble')

        cls.barn = Location.objects.create(name='Big Barn', site='Colgate')
        cls.paddock = Location.objects.create(name='Top Paddock', site='Colgate')

        today = timezone.localdate()

        # Big Barn holds two horses, Top Paddock one — enough to tell the
        # count sorts apart. Alice owns two, Bob one.
        cls.zara = cls._horse('Zara', sex='mare', age=4, owner=cls.alice,
                              location=cls.barn, start=today - timedelta(days=30))
        cls.milo = cls._horse('Milo', sex='gelding', age=12, owner=cls.alice,
                              location=cls.barn, start=today - timedelta(days=10))
        cls.apple = cls._horse('Apple', sex='mare', age=8, owner=cls.bob,
                               location=cls.paddock, start=today - timedelta(days=200))

        # Departed, for the departed-tab sorts.
        cls.rusty = Horse.objects.create(name='Rusty', sex='gelding', age=20)
        Placement.objects.create(
            horse=cls.rusty, owner=cls.bob, location=cls.paddock,
            rate_type=cls.rate, start_date=today - timedelta(days=400),
            end_date=today - timedelta(days=100),
        )
        cls.rusty.is_active = False
        cls.rusty.save()

        cls.old_boy = Horse.objects.create(name='Old Boy', sex='stallion', age=25)
        Placement.objects.create(
            horse=cls.old_boy, owner=cls.alice, location=cls.barn,
            rate_type=cls.rate, start_date=today - timedelta(days=500),
            end_date=today - timedelta(days=300),
        )
        cls.old_boy.is_active = False
        cls.old_boy.save()

    @classmethod
    def _horse(cls, name, sex, age, owner, location, start):
        horse = Horse.objects.create(name=name, sex=sex, age=age)
        Placement.objects.create(
            horse=horse, owner=owner, location=location,
            rate_type=cls.rate, start_date=start,
        )
        return horse

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse('horse_list'), params)

    def _group_names(self, response):
        return [g['name'] for g in response.context['grouped_horses']]

    def _horse_names(self, response):
        return [
            h.name
            for group in response.context['grouped_horses']
            for h in group['horses']
        ]

    # ── Grouping ─────────────────────────────────────────────────────

    def test_all_is_the_default_grouping(self):
        """No group_by in the URL gives one flat card of every horse."""
        response = self._get()
        self.assertEqual(response.context['group_by'], 'all')
        self.assertEqual(self._group_names(response), ['All Horses'])
        self.assertEqual(
            self._horse_names(response), ['Apple', 'Milo', 'Zara'],
        )
        self.assertEqual(response.context['grouped_horses'][0]['count'], 3)

    def test_unknown_group_by_falls_back_to_all(self):
        response = self._get(group_by='colour')
        self.assertEqual(response.context['group_by'], 'all')

    def test_group_by_location_still_groups(self):
        response = self._get(group_by='location')
        self.assertEqual(self._group_names(response), ['Big Barn', 'Top Paddock'])

    def test_group_by_owner_still_groups(self):
        response = self._get(group_by='owner')
        self.assertEqual(
            self._group_names(response), ['Alice Appleby', 'Bob Bramble'],
        )

    # ── Horse sorts ──────────────────────────────────────────────────

    def test_sort_name_descending(self):
        response = self._get(sort='-name')
        self.assertEqual(
            self._horse_names(response), ['Zara', 'Milo', 'Apple'],
        )

    def test_sort_age_youngest_first(self):
        response = self._get(sort='age')
        self.assertEqual(
            self._horse_names(response), ['Zara', 'Apple', 'Milo'],
        )

    def test_sort_age_oldest_first(self):
        response = self._get(sort='-age')
        self.assertEqual(
            self._horse_names(response), ['Milo', 'Apple', 'Zara'],
        )

    def test_age_sort_uses_date_of_birth_when_set(self):
        """calculated_age wins over the stored age field, as on the row."""
        today = timezone.localdate()
        foal = self._horse(
            'Foal', sex='filly', age=None, owner=self.bob,
            location=self.paddock, start=today,
        )
        foal.date_of_birth = today - timedelta(days=400)
        foal.save()
        response = self._get(sort='age')
        self.assertEqual(self._horse_names(response)[0], 'Foal')

    def test_horses_without_an_age_sort_last_in_both_directions(self):
        self._horse('Nameless Age', sex='mare', age=None, owner=self.bob,
                    location=self.paddock, start=timezone.localdate())
        for sort in ('age', '-age'):
            with self.subTest(sort=sort):
                names = self._horse_names(self._get(sort=sort))
                self.assertEqual(names[-1], 'Nameless Age')

    def test_sort_by_gender(self):
        response = self._get(sort='sex')
        # Geldings before mares, then name A–Z inside each gender.
        self.assertEqual(
            self._horse_names(response), ['Milo', 'Apple', 'Zara'],
        )

    def test_sort_by_owner(self):
        response = self._get(sort='owner')
        self.assertEqual(
            self._horse_names(response), ['Milo', 'Zara', 'Apple'],
        )

    def test_sort_by_recently_arrived(self):
        response = self._get(sort='-arrived')
        self.assertEqual(
            self._horse_names(response), ['Milo', 'Zara', 'Apple'],
        )

    def test_sort_applies_inside_each_group(self):
        response = self._get(group_by='location', sort='-name')
        self.assertEqual(
            self._horse_names(response), ['Zara', 'Milo', 'Apple'],
        )

    def test_sort_not_offered_here_falls_back_to_name(self):
        """`sort=owner` survives in the URL when you switch to grouping by
        owner, where it says nothing — the view must not honour it."""
        response = self._get(group_by='owner', sort='owner')
        self.assertEqual(response.context['sort'], 'name')

    # ── Group sorts ──────────────────────────────────────────────────

    def test_groups_by_most_horses_first(self):
        response = self._get(group_by='location', gsort='-count')
        self.assertEqual(self._group_names(response), ['Big Barn', 'Top Paddock'])
        response = self._get(group_by='owner', gsort='-count')
        self.assertEqual(
            self._group_names(response), ['Alice Appleby', 'Bob Bramble'],
        )

    def test_groups_by_fewest_horses_first(self):
        response = self._get(group_by='location', gsort='count')
        self.assertEqual(self._group_names(response), ['Top Paddock', 'Big Barn'])

    def test_groups_by_name_descending(self):
        response = self._get(group_by='owner', gsort='-name')
        self.assertEqual(
            self._group_names(response), ['Bob Bramble', 'Alice Appleby'],
        )

    def test_group_sort_menu_is_offered_only_when_grouping(self):
        self.assertIsNone(self._get().context['group_sort_options'])
        self.assertIsNotNone(
            self._get(group_by='location').context['group_sort_options'],
        )

    # ── Departed tab and search ──────────────────────────────────────

    def test_departed_tab_sorts_in_the_database(self):
        response = self._get(status='departed', sort='-departed')
        names = [h.name for h in response.context['horses']]
        self.assertEqual(names, ['Rusty', 'Old Boy'])

        response = self._get(status='departed', sort='departed')
        names = [h.name for h in response.context['horses']]
        self.assertEqual(names, ['Old Boy', 'Rusty'])

    def test_departed_tab_age_sort(self):
        response = self._get(status='departed', sort='-age')
        names = [h.name for h in response.context['horses']]
        self.assertEqual(names, ['Old Boy', 'Rusty'])

    def test_departed_tab_offers_departure_sorts_only(self):
        keys = [o['key'] for o in self._get(status='departed').context['sort_options']]
        self.assertIn('-departed', keys)
        self.assertNotIn('-arrived', keys)

    def test_search_results_are_sorted(self):
        response = self._get(search='a', sort='-name')
        names = [h.name for h in response.context['horses']]
        self.assertEqual(names, sorted(names, reverse=True))

    # ── Rendering ────────────────────────────────────────────────────

    def test_page_renders_for_every_grouping(self):
        for group_by in ('all', 'location', 'owner'):
            with self.subTest(group_by=group_by):
                response = self._get(group_by=group_by)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Group by')

    def test_sort_menu_renders_on_the_flat_views(self):
        for params in ({'status': 'departed'}, {'search': 'a'}):
            with self.subTest(**params):
                response = self._get(**params)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Sort by')

    # ── The "Show" block: Active / Departed / Movements in the menu ──

    def test_show_block_marks_the_open_view(self):
        """Which list you are on is picked inside the Sort pop-out, on every
        view, and the chip for the open list is the filled one."""
        cases = (
            ({}, 'Active'),
            ({'status': 'departed'}, 'Departed'),
            ({'search': 'a'}, 'Active'),
            ({'tab': 'movements'}, 'Movements'),
        )
        for params, expected in cases:
            with self.subTest(**params):
                response = self._get(**params)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                chips = re.findall(
                    r'class="show-chip( is-active)?">\s*(?:<[^>]+>\s*)*(?:<[^>]+>)?'
                    r'\s*<span class="flex-1">(\w+)</span>',
                    html,
                )
                self.assertEqual(
                    [label for _, label in chips],
                    ['Active', 'Departed', 'Movements'],
                )
                self.assertEqual(
                    [label for active, label in chips if active],
                    [expected],
                )

    def test_show_block_carries_the_counts(self):
        response = self._get()
        self.assertContains(
            response,
            '<span class="show-chip-count">%d</span>'
            % response.context['total_current'],
        )
        self.assertContains(
            response,
            '<span class="show-chip-count">%d</span>'
            % response.context['total_departed'],
        )

    def test_movements_menu_has_no_horse_sorts(self):
        """The placement log does not sort like a horse list: its menu is
        the Show block alone, reached from a "View" button."""
        response = self._get(tab='movements')
        self.assertContains(response, 'show-chip')
        self.assertNotContains(response, 'Sort by')
        self.assertNotContains(response, 'Order horses by')
        self.assertContains(response, 'View')

    def test_toolbar_no_longer_has_the_second_rail(self):
        response = self._get()
        self.assertNotContains(response, 'aria-label="Which horses"><span class="segmented-thumb"')
        self.assertNotContains(response, 'Rail 2')
