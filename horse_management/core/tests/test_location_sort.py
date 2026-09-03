"""Tests for the card order on the Locations tab of the location list.

By default the page puts fields with no horses first (``?sort=empty``), so
free space is visible at the top. ``?sort=default`` keeps the plain
site/name order.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType

User = get_user_model()


class LocationSortTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='sorter',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()
        from core.roles_testutils import administrator_role, assign_role
        assign_role(cls.user, administrator_role())

        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        today = timezone.now().date()

        # Site "Alpha": every field has a horse.
        cls.alpha_a = Location.objects.create(name='A Field', site='Alpha', capacity=4)
        cls.alpha_b = Location.objects.create(name='B Field', site='Alpha', capacity=4)
        # Site "Beta": one full field, one empty field, one field with two horses.
        cls.beta_a = Location.objects.create(name='A Field', site='Beta', capacity=4)
        cls.beta_b = Location.objects.create(name='B Field', site='Beta', capacity=4)
        cls.beta_c = Location.objects.create(name='C Field', site='Beta', capacity=4)

        def place(location, name):
            horse = Horse.objects.create(name=name)
            Placement.objects.create(
                horse=horse, owner=owner, location=location, rate_type=rate,
                start_date=today - timedelta(days=10),
            )

        place(cls.alpha_a, 'Alpha One')
        place(cls.alpha_b, 'Alpha Two')
        place(cls.beta_a, 'Beta One')
        place(cls.beta_a, 'Beta Two')
        place(cls.beta_c, 'Beta Three')

    def setUp(self):
        self.client.force_login(self.user)

    def _order(self, response):
        return [
            (site, [loc.pk for loc in locs])
            for site, locs, _count in response.context['grouped_locations']
        ]

    def test_empty_first_is_the_default(self):
        response = self.client.get(reverse('location_list'))
        self.assertEqual(response.context['location_sort'], 'empty')
        self.assertEqual(self._order(response), [
            # Beta has an empty field, so it moves above Alpha; inside Beta
            # the empty field leads, then the field with one horse, then two.
            ('Beta', [self.beta_b.pk, self.beta_c.pk, self.beta_a.pk]),
            ('Alpha', [self.alpha_a.pk, self.alpha_b.pk]),
        ])

    def test_default_sort_keeps_site_and_name_order(self):
        response = self.client.get(reverse('location_list'), {'sort': 'default'})
        self.assertEqual(response.context['location_sort'], 'default')
        self.assertEqual(self._order(response), [
            ('Alpha', [self.alpha_a.pk, self.alpha_b.pk]),
            ('Beta', [self.beta_a.pk, self.beta_b.pk, self.beta_c.pk]),
        ])

    def test_unknown_sort_falls_back_to_empty_first(self):
        response = self.client.get(reverse('location_list'), {'sort': 'bogus'})
        self.assertEqual(response.context['location_sort'], 'empty')

    def test_site_horse_counts_survive_sorting(self):
        response = self.client.get(reverse('location_list'))
        counts = {site: n for site, _locs, n in response.context['grouped_locations']}
        self.assertEqual(counts, {'Alpha': 2, 'Beta': 3})

    def test_toggle_is_rendered_and_marks_the_active_option(self):
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, 'Empty first')
        self.assertContains(response, '?sort=default')
        # The default sort is not written into the search form.
        self.assertNotContains(response, 'name="sort"')

        response = self.client.get(reverse('location_list'), {'sort': 'default'})
        # Live search must keep the chosen sort.
        self.assertContains(response, '<input type="hidden" name="sort" value="default">')

    def test_search_and_sort_work_together(self):
        response = self.client.get(reverse('location_list'), {'search': 'beta', 'sort': 'default'})
        self.assertEqual(self._order(response), [
            ('Beta', [self.beta_a.pk, self.beta_b.pk, self.beta_c.pk]),
        ])
        # The toggle links carry the search term along.
        self.assertContains(response, '?search=beta')
        self.assertContains(response, '?sort=default&search=beta')

    def test_other_tabs_ignore_sort(self):
        for tab in ('history', 'usage'):
            response = self.client.get(reverse('location_list'), {'tab': tab, 'sort': 'default'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('grouped_locations', response.context)
