"""The ceiling on how much of the horse list one page renders.

The active tab lists every horse, and every horse is rendered twice — a
card for phones and a table row from md up, about 7.7 kB of HTML each. A
hundred-horse yard was therefore sending a phone the better part of a
megabyte before it could show anything, and a four-hundred-horse yard
three of them. The page stops at ROW_CAP and offers the rest.

Nothing here changes for a yard under the cap, which is most of them —
that is what the first test is for.
"""

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from core.views.horses import ROW_CAP


def stable_horses(n, start=0):
    """`n` horses with names that sort predictably."""
    return Horse.objects.bulk_create([
        Horse(name=f'Horse {i + start:04d}', sex=Horse.Sex.MARE)
        for i in range(n)
    ])


class UnderTheCapTests(TestCase):
    """A normal yard never meets the ceiling."""

    @classmethod
    def setUpTestData(cls):
        stable_horses(ROW_CAP - 1)
        cls.url = reverse('horse_list')

    def setUp(self):
        self.client.force_login(make_admin('under'))

    def test_every_horse_is_listed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['rows_shown'], ROW_CAP - 1)
        self.assertEqual(response.context['rows_total'], ROW_CAP - 1)

    def test_the_page_says_nothing_about_a_cap(self):
        response = self.client.get(self.url)
        self.assertFalse(response.context['rows_capped'])
        self.assertNotContains(response, 'Show all')


class OverTheCapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.extra = 25
        stable_horses(ROW_CAP + cls.extra)
        cls.url = reverse('horse_list')

    def setUp(self):
        self.client.force_login(make_admin('over'))

    def test_the_page_stops_at_the_cap(self):
        response = self.client.get(self.url)
        self.assertTrue(response.context['rows_capped'])
        self.assertEqual(response.context['rows_shown'], ROW_CAP)
        self.assertEqual(response.context['rows_total'], ROW_CAP + self.extra)

    def test_the_page_says_what_it_has_stopped_at(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'Showing {ROW_CAP} of {ROW_CAP + self.extra} horses')
        self.assertContains(response, f'Show all {ROW_CAP + self.extra}')

    def test_only_the_capped_number_of_horses_is_rendered(self):
        """The point of the exercise: the bytes are not sent."""
        body = self.client.get(self.url).content.decode()
        # Each horse appears in a card and in a table row, so the row
        # marker is the honest thing to count.
        self.assertEqual(body.count('data-horse-row'), ROW_CAP * 2)

    def test_asking_for_the_rest_renders_all_of_them(self):
        response = self.client.get(self.url, {'all': '1'})
        self.assertFalse(response.context['rows_capped'])
        self.assertEqual(response.context['rows_shown'], ROW_CAP + self.extra)

    def test_the_capped_page_is_far_smaller_than_the_whole_list(self):
        capped = len(self.client.get(self.url).content)
        whole = len(self.client.get(self.url, {'all': '1'}).content)
        self.assertLess(capped, whole)
        # The saving is the tail of the list, so it should be substantial
        # rather than marginal.
        self.assertLess(capped, whole * 0.85)


class GroupedCapTests(TestCase):
    """The cap counts horses, not groups, and headings stay honest."""

    @classmethod
    def setUpTestData(cls):
        owner = Owner.objects.create(name='Milly Hine')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        cls.first = Location.objects.create(name='Aardvark Field', site='Colgate', capacity=200)
        cls.second = Location.objects.create(name='Zebra Field', site='Colgate', capacity=200)
        horses = stable_horses(ROW_CAP + 10)
        Placement.objects.bulk_create([
            Placement(
                horse=horse, owner=owner, rate_type=rate,
                location=cls.first if i < ROW_CAP - 5 else cls.second,
                start_date='2026-01-01',
            )
            for i, horse in enumerate(horses)
        ])
        cls.url = reverse('horse_list')

    def setUp(self):
        self.client.force_login(make_admin('grouped'))

    def groups(self, **params):
        params.setdefault('group_by', 'location')
        return self.client.get(self.url, params).context['grouped_horses']

    def test_the_cap_is_spent_across_the_groups_in_order(self):
        groups = {g['name']: g for g in self.groups()}
        shown = sum(len(g['horses']) for g in groups.values())
        self.assertEqual(shown, ROW_CAP)

    def test_a_group_still_says_how_many_it_holds(self):
        """The heading counts the location's horses, not the rendered ones."""
        groups = {g['name']: g for g in self.groups()}
        first = groups['Aardvark Field']
        self.assertEqual(first['count'], ROW_CAP - 5)

    def test_a_trimmed_group_says_how_many_it_is_holding_back(self):
        body = self.client.get(self.url, {'group_by': 'location'}).content.decode()
        self.assertIn('more here, not shown', body)

    def test_an_empty_group_is_never_trimmed_away(self):
        """An empty location is the answer to "where can this horse go"."""
        Location.objects.create(name='Empty Field', site='Colgate', capacity=6)
        names = [g['name'] for g in self.groups(show_empty='1')]
        self.assertIn('Empty Field', names)
