"""Tests for the card order on the Locations tab of the location list.

By default the page puts the fields with the most horses first
(``?sort=fullest``). ``?sort=default`` keeps the plain site/name order.
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

        # Site "Alpha": two fields with one horse each (2 horses in total).
        cls.alpha_a = Location.objects.create(name='A Field', site='Alpha', capacity=4)
        cls.alpha_b = Location.objects.create(name='B Field', site='Alpha', capacity=4)
        # Site "Beta": two horses, none, one (3 horses in total).
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

    def test_fullest_first_is_the_default(self):
        response = self.client.get(reverse('location_list'))
        self.assertEqual(response.context['location_sort'], 'fullest')
        self.assertEqual(self._order(response), [
            # Beta has 3 horses to Alpha's 2, so it comes first; inside Beta
            # the field with two horses leads, then one, then the empty one.
            ('Beta', [self.beta_a.pk, self.beta_c.pk, self.beta_b.pk]),
            ('Alpha', [self.alpha_a.pk, self.alpha_b.pk]),
        ])

    def test_default_sort_keeps_site_and_name_order(self):
        response = self.client.get(reverse('location_list'), {'sort': 'default'})
        self.assertEqual(response.context['location_sort'], 'default')
        self.assertEqual(self._order(response), [
            ('Alpha', [self.alpha_a.pk, self.alpha_b.pk]),
            ('Beta', [self.beta_a.pk, self.beta_b.pk, self.beta_c.pk]),
        ])

    def test_unknown_sort_falls_back_to_fullest_first(self):
        response = self.client.get(reverse('location_list'), {'sort': 'bogus'})
        self.assertEqual(response.context['location_sort'], 'fullest')

    def test_site_horse_counts_survive_sorting(self):
        response = self.client.get(reverse('location_list'))
        counts = {site: n for site, _locs, n in response.context['grouped_locations']}
        self.assertEqual(counts, {'Alpha': 2, 'Beta': 3})

    def test_toggle_is_rendered_and_marks_the_active_option(self):
        response = self.client.get(reverse('location_list'))
        self.assertContains(response, 'Most horses first')
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

    def test_usage_tab_ignores_sort(self):
        response = self.client.get(reverse('location_list'), {'tab': 'usage', 'sort': 'default'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('grouped_locations', response.context)

    def test_retired_history_tab_redirects_rather_than_sorting(self):
        response = self.client.get(reverse('location_list'), {'tab': 'history', 'sort': 'default'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('tab=movements', response['Location'])
