"""The Movements tab on the horse list.

It shows the same placement log as the Locations page's Movement History
tab, through the same query function, so the two cannot drift. The
Locations tab stays where it is — this is a second way in, not a move.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from core.views.placements import movement_history


class MovementsTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('mover')
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)
        cls.alice = Owner.objects.create(name='Alice Appleby')
        cls.bob = Owner.objects.create(name='Bob Bramble')
        cls.barn = Location.objects.create(name='Big Barn', site='Colgate')
        cls.paddock = Location.objects.create(name='Top Paddock', site='Colgate')

        today = timezone.localdate()
        cls.zara = Horse.objects.create(name='Zara')
        cls.milo = Horse.objects.create(name='Milo')

        # Zara moved barn -> paddock: one ended placement, one current.
        cls.ended = Placement.objects.create(
            horse=cls.zara, owner=cls.alice, location=cls.barn,
            rate_type=cls.rate, start_date=today - timedelta(days=100),
            end_date=today - timedelta(days=40),
        )
        cls.current = Placement.objects.create(
            horse=cls.zara, owner=cls.alice, location=cls.paddock,
            rate_type=cls.rate, start_date=today - timedelta(days=39),
        )
        cls.other = Placement.objects.create(
            horse=cls.milo, owner=cls.bob, location=cls.barn,
            rate_type=cls.rate, start_date=today - timedelta(days=10),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _placements(self, **params):
        params.setdefault('tab', 'movements')
        response = self.client.get(reverse('horse_list'), params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['shows_movements'])
        return list(response.context['placements'])

    # ── The tab ──────────────────────────────────────────────────────

    def test_tab_renders_the_log(self):
        response = self.client.get(
            reverse('horse_list'), {'tab': 'movements'},
        )
        self.assertContains(response, 'Movements')
        self.assertContains(response, 'Big Barn')
        self.assertContains(response, 'Top Paddock')

    def test_tab_is_offered_on_the_other_views(self):
        response = self.client.get(reverse('horse_list'))
        self.assertTrue(response.context['movements_available'])
        self.assertFalse(response.context['shows_movements'])
        self.assertContains(response, 'tab=movements')

    def test_grouping_is_skipped_on_the_tab(self):
        """Nothing there reads the horse list, so it is not built."""
        response = self.client.get(
            reverse('horse_list'), {'tab': 'movements', 'group_by': 'location'},
        )
        self.assertNotIn('grouped_horses', response.context)
        self.assertEqual(list(response.context['horses']), [])

    # ── Filters ──────────────────────────────────────────────────────

    def test_defaults_to_current_placements(self):
        placements = self._placements()
        self.assertEqual(response_pks(placements), {self.current.pk, self.other.pk})
        self.assertEqual(self.client.get(
            reverse('horse_list'), {'tab': 'movements'},
        ).context['current_status'], 'active')

    def test_ended_filter(self):
        self.assertEqual(
            response_pks(self._placements(status='ended')), {self.ended.pk},
        )

    def test_all_filter(self):
        self.assertEqual(
            response_pks(self._placements(status='all')),
            {self.ended.pk, self.current.pk, self.other.pk},
        )

    def test_unknown_status_falls_back_to_current(self):
        response = self.client.get(
            reverse('horse_list'), {'tab': 'movements', 'status': 'sideways'},
        )
        self.assertEqual(response.context['current_status'], 'active')

    def test_location_filter_applies_to_the_log(self):
        """The page's existing Location filter narrows the log too."""
        placements = self._placements(status='all', location=self.barn.pk)
        self.assertEqual(response_pks(placements), {self.ended.pk, self.other.pk})

    def test_owner_filter_applies_to_the_log(self):
        placements = self._placements(status='all', owner=self.bob.pk)
        self.assertEqual(response_pks(placements), {self.other.pk})

    def test_newest_first(self):
        placements = self._placements(status='all')
        dates = [p.start_date for p in placements]
        self.assertEqual(dates, sorted(dates, reverse=True))

    # ── Shared with the Locations page ───────────────────────────────

    def test_locations_history_tab_redirects_here(self):
        """Phase 5 retired that tab. Its URL still has to land somewhere
        useful, so it comes here."""
        response = self.client.get(
            reverse('location_list'), {'tab': 'history'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f"{reverse('horse_list')}?tab=movements",
        )

    def test_the_redirect_carries_the_filters(self):
        """A bookmarked, filtered log must arrive still filtered."""
        response = self.client.get(reverse('location_list'), {
            'tab': 'history', 'status': 'ended', 'location': self.barn.pk,
        })
        self.assertEqual(response.status_code, 302)
        target = response['Location']
        self.assertIn('tab=movements', target)
        self.assertIn('status=ended', target)
        self.assertIn(f'location={self.barn.pk}', target)

        followed = self.client.get(target)
        self.assertEqual(
            response_pks(followed.context['placements']), {self.ended.pk},
        )

    def test_placements_url_lands_on_the_movements_tab(self):
        """/placements/ pointed at the Locations tab that no longer exists."""
        response = self.client.get(reverse('placement_list'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/horses/?tab=movements')

    def test_add_placement_is_offered_on_the_tab(self):
        """It used to sit on the Locations history tab."""
        response = self.client.get(
            reverse('horse_list'), {'tab': 'movements'},
        )
        self.assertContains(response, reverse('placement_create'))
        self.assertContains(response, 'Add Placement')

    def test_add_placement_is_not_offered_on_the_horse_views(self):
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'Add Placement')

    def test_helper_caps_the_log(self):
        request = self.client.get(
            reverse('horse_list'), {'tab': 'movements'},
        ).wsgi_request
        placements, status = movement_history(request)
        self.assertLessEqual(len(placements), 50)
        self.assertEqual(status, 'active')

    # ── Permission ───────────────────────────────────────────────────

    def test_tab_hidden_without_the_locations_feature(self):
        """The log reads locations, so it needs that feature."""
        user = make_user_with_access('nolocs', horses='full')
        self.client.force_login(user)
        response = self.client.get(reverse('horse_list'))
        self.assertFalse(response.context['movements_available'])
        self.assertNotContains(response, 'tab=movements')

    def test_tab_url_falls_back_without_the_locations_feature(self):
        user = make_user_with_access('nolocs2', horses='full')
        self.client.force_login(user)
        response = self.client.get(
            reverse('horse_list'), {'tab': 'movements'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['shows_movements'])


def response_pks(placements):
    return {p.pk for p in placements}
