"""The app bar: one search and one Add, on every page.

It is rendered outside #main-content so a boosted navigation never swaps
it — that is what keeps it in place as you move between areas.
"""

from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access

PAGES = ('dashboard', 'horse_list', 'location_list', 'owner_list')


class AppBarPlacementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('barman')

    def setUp(self):
        self.client.force_login(self.user)

    def test_every_area_carries_the_same_bar(self):
        for name in PAGES:
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertIn('id="app-search-results"', body)
                self.assertIn('aria-label="Add"', body)

    def test_the_bar_sits_outside_the_swapped_region(self):
        """hx-boost swaps #main-content. Anything inside it would be
        rebuilt on every navigation, losing a half-typed search."""
        body = self.client.get(reverse('horse_list')).content.decode()
        bar = body.index('id="app-search-results"')
        main = body.index('id="main-content"')
        self.assertLess(bar, main, 'the app bar must precede #main-content')

    def test_search_works_for_a_role_that_cannot_see_the_dashboard(self):
        """The search was the dashboard's, and kept its gate when it moved.
        A role with the dashboard hidden saw a box that did nothing."""
        user = make_user_with_access('nodash', horses='view', locations='view')
        self.client.force_login(user)
        body = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn('id="app-search-results"', body)

        response = self.client.get(reverse('quick_find'), {'q': 'ali'})
        self.assertEqual(response.status_code, 200)

    def test_no_search_box_when_no_searchable_area_is_visible(self):
        user = make_user_with_access('feedonly', dashboard='full', feed='view')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('id="app-search-results"', body)

    def test_search_still_needs_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('quick_find'), {'q': 'ali'})
        self.assertEqual(response.status_code, 302)

    def test_anonymous_visitors_get_no_bar(self):
        self.client.logout()
        body = self.client.get(reverse('login')).content.decode()
        self.assertNotIn('id="app-search-results"', body)


class AddMenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('adder')

    def setUp(self):
        self.client.force_login(self.user)

    def test_offers_the_four_creates(self):
        body = self.client.get(reverse('dashboard')).content.decode()
        for name, label in (
            ('horse_new_arrival', 'Log arrival'),
            ('horse_create', 'Add horse'),
            ('location_create', 'Add location'),
            ('owner_create', 'Add owner'),
        ):
            with self.subTest(label=label):
                self.assertIn(reverse(name), body)
                self.assertIn(label, body)

    def test_each_entry_needs_full_access_to_its_area(self):
        user = make_user_with_access(
            'partial', dashboard='full', horses='full', owners='view',
            locations='view',
        )
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Log arrival', body)
        self.assertNotIn('Add location', body)
        self.assertNotIn('Add owner', body)

    def test_no_add_button_without_any_create_rights(self):
        user = make_user_with_access('readonly', dashboard='full', horses='view')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Log arrival', body)
        self.assertNotIn('aria-label="Add"', body)

    def test_pages_no_longer_repeat_the_creates(self):
        """They moved to the bar; two Add Horse buttons would be one too
        many."""
        for name, label in (
            ('horse_list', 'Add Horse'),
            ('location_list', 'Add Location'),
            ('owner_list', 'Add Owner'),
        ):
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertNotIn(label, body)


class AppBarSearchTests(TestCase):
    """One search, across everything, gated per area."""

    @classmethod
    def setUpTestData(cls):
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)
        cls.owner = Owner.objects.create(name='Alice Appleby')
        cls.field = Location.objects.create(name='Top Field', site='Colgate')
        cls.horse = Horse.objects.create(name='ALIHUNTER')
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.field,
            rate_type=cls.rate, start_date=timezone.localdate() - timedelta(days=5),
        )

    def _find(self, user, q):
        self.client.force_login(user)
        response = self.client.get(reverse('quick_find'), {'q': q})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_reaches_all_three_kinds_of_record(self):
        admin = make_admin('finder')
        self.assertIn('ALIHUNTER', self._find(admin, 'alihunter'))
        self.assertIn('Alice Appleby', self._find(admin, 'appleby'))
        self.assertIn('Top Field', self._find(admin, 'top'))

    def test_still_tolerates_a_typo(self):
        """The database answers first now; difflib must still catch what
        it cannot."""
        admin = make_admin('typist')
        self.assertIn('ALIHUNTER', self._find(admin, 'alihnter'))

    def test_a_hidden_area_never_appears_in_results(self):
        user = make_user_with_access(
            'norows', dashboard='full', horses='full',
        )
        body = self._find(user, 'a')
        self.assertNotIn('Alice Appleby', body)
        self.assertNotIn('Top Field', body)

    def test_a_group_sql_can_answer_skips_the_fuzzy_scan(self):
        """The scan is the slow path, and it is per group: horses here are
        answered by SQL, so no horse name should reach difflib. Owners and
        locations have no match for this query, so they still scan — that
        is the fallback doing its job."""
        from unittest.mock import patch
        admin = make_admin('quick')
        self.client.force_login(admin)
        with patch(
            'core.views.dashboard.is_fuzzy_match', return_value=False,
        ) as fuzzy:
            response = self.client.get(reverse('quick_find'), {'q': 'alihunter'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('ALIHUNTER', response.content.decode())
        scanned = [call.args[1] for call in fuzzy.call_args_list]
        self.assertNotIn('ALIHUNTER', scanned)
        self.assertIn('Alice Appleby', scanned)

    def test_results_link_through_to_each_filtered_list(self):
        """One box has to cover "take me there" and "show me all of them"
        — the page-level search boxes are gone."""
        admin = make_admin('through')
        body = self._find(admin, 'al')
        self.assertIn(f"{reverse('horse_list')}?search=al", body)
        self.assertIn(f"{reverse('owner_list')}?search=al", body)
        self.assertIn('See all horses matching', body)
        # "al" is in no location name, so that group is absent — a query
        # that is gets its link too.
        self.assertIn(
            f"{reverse('location_list')}?search=top", self._find(admin, 'top'),
        )

    def test_the_see_all_link_actually_filters_that_list(self):
        admin = make_admin('followthrough')
        self.client.force_login(admin)
        response = self.client.get(reverse('horse_list'), {'search': 'alihunter'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [h.name for h in response.context['horses']], ['ALIHUNTER'],
        )

    def test_a_hidden_area_gets_no_see_all_link(self):
        user = make_user_with_access('nolinks', dashboard='full', horses='full')
        body = self._find(user, 'a')
        self.assertNotIn('See all owners matching', body)
        self.assertNotIn('See all locations matching', body)

    def test_a_query_nothing_contains_falls_back_to_the_scan(self):
        from unittest.mock import patch
        admin = make_admin('slow')
        self.client.force_login(admin)
        with patch(
            'core.views.dashboard.is_fuzzy_match', return_value=False,
        ) as fuzzy:
            self.client.get(reverse('quick_find'), {'q': 'zzzqqq'})
        self.assertTrue(fuzzy.called)
