"""The horse quick view: the pop-up sheet's first stop from the app bar
search (horses/partials/quick_view.html, core.views.horses.horse_quick_view).

A horse row in the search results opens this card instead of the full
profile — photo, location, owner, colour, age, and the day-to-day actions —
with the full profile one tap away in the footer and via the row's arrow.
Outside the sheet the view just redirects to the profile, so the row's href
still works without JavaScript.
"""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}


class QuickViewFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name='Maite Marre')
        cls.location = Location.objects.create(name='Grain store field', site='Colgate')
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)
        cls.horse = Horse.objects.create(
            name='Fanny', color=Horse.Color.BAY, sex=Horse.Sex.MARE,
            date_of_birth=date(timezone.localdate().year - 7, 1, 1),
            passport_number='GB123',
        )
        cls.placement = Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.location,
            rate_type=cls.rate, start_date=timezone.localdate() - timedelta(days=30),
        )
        cls.url = reverse('horse_quick_view', args=[cls.horse.pk])


class SearchRowTests(QuickViewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def _results(self):
        return self.client.get(reverse('quick_find'), {'q': 'fanny'}).content.decode()

    def test_a_horse_row_opens_the_quick_view_in_the_sheet(self):
        body = self._results()
        self.assertIn(f'hx-get="{self.url}"', body)
        self.assertIn('hx-target="#popup-body"', body)
        self.assertIn('data-popup-title="Quick view"', body)

    def test_the_row_still_goes_to_the_profile_without_javascript(self):
        """The wide link keeps the profile as its href; the arrow at the
        row's edge goes there directly and stays out of the arrow-key
        sequence (tabindex=-1)."""
        body = self._results()
        profile = reverse('horse_detail', args=[self.horse.pk])
        self.assertEqual(body.count(f'href="{profile}"'), 2)
        self.assertIn('class="quick-find-open" tabindex="-1"', body)
        self.assertIn('aria-label="Open the full profile of Fanny"', body)

    def test_owner_and_location_rows_are_unchanged(self):
        body = self.client.get(reverse('quick_find'), {'q': 'maite'}).content.decode()
        self.assertIn(reverse('owner_detail', args=[self.owner.pk]), body)
        self.assertNotIn('quick-view', body)


class QuickViewContentTests(QuickViewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_only_the_sheet_gets_the_card(self):
        """A direct visit or a boosted navigation lands on the profile."""
        for extra in ({}, BOOSTED):
            with self.subTest(headers=extra or 'plain'):
                response = self.client.get(self.url, **extra)
                self.assertRedirects(
                    response, reverse('horse_detail', args=[self.horse.pk]),
                    fetch_redirect_response=False,
                )

    def test_shows_the_key_facts(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/quick_view.html')
        body = response.content.decode()
        self.assertIn('Fanny', body)
        self.assertIn('Grain store field', body)
        self.assertIn('Colgate', body)
        self.assertIn('Maite Marre', body)
        self.assertIn('Bay', body)
        self.assertIn('7 years', body)
        self.assertIn('GB123', body)
        self.assertIn(reverse('horse_detail', args=[self.horse.pk]), body)
        self.assertIn('Open full profile', body)

    def test_shows_the_photo_or_the_initial(self):
        body = self.client.get(self.url, **POPUP).content.decode()
        # No photo on the fixture horse: the 96px initial placeholder stands in
        self.assertIn('w-24 h-24', body)
        self.assertIn('>F<', body)

    def test_offers_the_day_to_day_actions_in_the_same_sheet(self):
        body = self.client.get(self.url, **POPUP).content.decode()
        for name, args in (
            ('horse_move', [self.horse.pk]),
            ('horse_quick_edit', [self.horse.pk]),
            ('horse_photo_add', [self.horse.pk]),
        ):
            with self.subTest(action=name):
                self.assertIn(f'hx-get="{reverse(name, args=args)}"', body)
        for name in ('vaccination_create', 'farrier_create', 'worming_create',
                     'egg_count_create', 'condition_create', 'vet_visit_create',
                     'breeding_create'):
            with self.subTest(record=name):
                self.assertIn(f'hx-get="{reverse(name)}?horse={self.horse.pk}"', body)
        self.assertIn('Add record', body)

    def test_breeding_is_offered_for_mares_only(self):
        gelding = Horse.objects.create(name='Avicii', sex=Horse.Sex.GELDING)
        body = self.client.get(
            reverse('horse_quick_view', args=[gelding.pk]), **POPUP
        ).content.decode()
        self.assertNotIn(reverse('breeding_create'), body)

    def test_a_horse_off_the_yard_offers_log_arrival_instead_of_move(self):
        away = Horse.objects.create(name='Beech')
        body = self.client.get(
            reverse('horse_quick_view', args=[away.pk]), **POPUP
        ).content.decode()
        self.assertIn('Not on the yard', body)
        self.assertIn(f'hx-get="{reverse("horse_arrive", args=[away.pk])}"', body)
        self.assertNotIn(reverse('horse_move', args=[away.pk]), body)

    def test_departed_horses_say_so(self):
        self.horse.is_active = False
        self.horse.save()
        body = self.client.get(self.url, **POPUP).content.decode()
        self.assertIn('Departed', body)


class QuickViewAccessTests(QuickViewFixture):
    def test_view_only_access_gets_the_facts_but_no_actions(self):
        self.client.force_login(make_user_with_access('looker', horses='view'))
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Grain store field', body)
        self.assertNotIn('Quick actions', body)
        self.assertNotIn(reverse('horse_move', args=[self.horse.pk]), body)
        self.assertNotIn('Add record', body)
        # No owner/location area: names stay, links go
        self.assertNotIn(reverse('owner_detail', args=[self.owner.pk]), body)
        self.assertNotIn(reverse('location_detail', args=[self.location.pk]), body)
        self.assertIn('Maite Marre', body)

    def test_health_access_alone_gets_the_add_record_menu_only(self):
        self.client.force_login(
            make_user_with_access('medic', horses='view', health='full')
        )
        body = self.client.get(self.url, **POPUP).content.decode()
        self.assertIn('Add record', body)
        self.assertIn(reverse('vaccination_create'), body)
        self.assertNotIn(reverse('horse_move', args=[self.horse.pk]), body)
        self.assertNotIn(reverse('horse_quick_edit', args=[self.horse.pk]), body)

    def test_no_horses_access_means_no_card(self):
        self.client.force_login(make_user_with_access('nobody', owners='view'))
        response = self.client.get(self.url, **POPUP)
        self.assertNotEqual(response.status_code, 200)
