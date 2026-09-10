"""The site board on a wide screen: the rail beside the map.

The Locations tab opens on it (templates/locations/_site_board.html) and
the cards it had before are one click away on the Map/Cards switch. The
phone shows the same site as a map with a sheet over it, from the same
payload and the same detail partial — locations_phone covers that side.

Covered here: what the rail carries, the values it is drawn from, and
the two things the detail panel gained for a wide screen — the area of
the ground and the nearest ground that is ready to take horses.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.boundary_import import area_hectares
from core.models import (
    Horse,
    Location,
    LocationUsagePeriod,
    Owner,
    Placement,
    RateType,
)
from core.roles_testutils import make_admin
from core.views.locations import _rest_bar_height

RAIL = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'loc-rail-detail'}
SHEET = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'loc-sheet-detail'}


def square(west, south, side=0.004):
    """A tidy parcel, so an area is something a person can check."""
    return {
        'type': 'Polygon',
        'coordinates': [[
            [west, south], [west + side, south],
            [west + side, south + side], [west, south + side], [west, south],
        ]],
    }


class AreaTests(TestCase):
    """Area comes off the boundary, because the boundary is where it is."""

    def test_a_parcel_measures_what_its_sides_say(self):
        """A square in degrees is a rectangle on the ground.

        0.01° of latitude is about 1.11 km anywhere; 0.01° of longitude
        is that times the cosine of the latitude — about 0.70 km at 51°N.
        So the parcel is roughly 1.11 × 0.70 km, near enough 78 ha, and
        an answer of 123 would mean the projection had been skipped.
        """
        hectares = area_hectares(square(-0.245, 51.06, side=0.01))
        self.assertAlmostEqual(hectares, 78, delta=2)

    def test_a_smaller_parcel_measures_smaller(self):
        big = area_hectares(square(-0.245, 51.06, side=0.01))
        small = area_hectares(square(-0.245, 51.06, side=0.005))
        self.assertLess(small, big)
        # Half the side is a quarter of the ground.
        self.assertAlmostEqual(small / big, 0.25, delta=0.02)

    def test_nothing_drawn_measures_nothing(self):
        self.assertIsNone(area_hectares(None))
        self.assertIsNone(area_hectares({}))

    def test_a_broken_shape_is_not_a_crash(self):
        self.assertIsNone(area_hectares({'type': 'Polygon', 'coordinates': 'nonsense'}))

    def test_the_answer_is_cached_per_boundary(self):
        key = (1, 'stamp')
        first = area_hectares(square(-0.245, 51.06), key=key)
        # A different shape under the same key comes back as the first:
        # the key is the location and when its boundary last changed.
        again = area_hectares(square(-0.245, 51.06, side=0.02), key=key)
        self.assertEqual(first, again)


class RestBarTests(TestCase):
    """The rest strip's bars: a shape, not a measurement."""

    def test_a_bar_never_falls_below_a_readable_stub(self):
        self.assertGreaterEqual(_rest_bar_height(0), 38)
        self.assertGreaterEqual(_rest_bar_height(None), 38)

    def test_longer_rested_is_taller(self):
        self.assertLess(_rest_bar_height(5), _rest_bar_height(20))

    def test_a_very_long_rest_does_not_tower_over_a_long_one(self):
        """Past a point the answer is just "long enough"."""
        self.assertEqual(_rest_bar_height(60), _rest_bar_height(400))
        self.assertEqual(_rest_bar_height(400), 100)


class BoardFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.owner = Owner.objects.create(name='Milly Hine')
        cls.rate = RateType.objects.create(name='Grass', daily_rate=5)

        cls.grazing = Location.objects.create(
            name='Red Hatches', site='Somerford', capacity=15,
            usage=Location.Usage.HORSES,
            latitude=Decimal('51.062'), longitude=Decimal('-0.245'),
            boundary=square(-0.245, 51.060), boundary_updated_at=timezone.now(),
        )
        # Rested, and the closer of the two ready parcels.
        cls.near_ready = Location.objects.create(
            name='Hawkhill', site='Somerford', capacity=12,
            usage=Location.Usage.RESTED,
            latitude=Decimal('51.066'), longitude=Decimal('-0.245'),
            boundary=square(-0.245, 51.064), boundary_updated_at=timezone.now(),
        )
        cls.far_ready = Location.objects.create(
            name='Hill-Whitakers', site='Somerford', capacity=10,
            usage=Location.Usage.RESTED,
            latitude=Decimal('51.090'), longitude=Decimal('-0.245'),
            boundary=square(-0.245, 51.088), boundary_updated_at=timezone.now(),
        )
        # Rested, but not long enough to offer.
        cls.recovering = Location.objects.create(
            name='Caravan Field', site='Somerford', capacity=4,
            usage=Location.Usage.RESTED,
            latitude=Decimal('51.063'), longitude=Decimal('-0.244'),
            boundary=square(-0.244, 51.061), boundary_updated_at=timezone.now(),
        )
        cls.unmapped = Location.objects.create(
            name='Stables top yard', site='Somerford', capacity=10,
            usage=Location.Usage.HORSES,
        )
        for location, days in (
            (cls.near_ready, 21), (cls.far_ready, 40), (cls.recovering, 3),
        ):
            LocationUsagePeriod.objects.create(
                location=location, usage=Location.Usage.RESTED,
                start_date=cls.today - timedelta(days=days),
            )

        cls.horse = Horse.objects.create(name='Antoinette', sex=Horse.Sex.MARE)
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.grazing,
            rate_type=cls.rate, start_date=cls.today - timedelta(days=11),
        )
        cls.url = reverse('location_list')


@override_settings(LOCATION_MAPS_ENABLED=True)
class BoardPageTests(BoardFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def body(self, **params):
        return self.client.get(self.url, params).content.decode()

    def test_the_rail_names_the_site_and_says_how_much_is_drawn(self):
        body = self.body()
        self.assertIn('data-site-board', body)
        self.assertIn('Somerford', body)
        self.assertIn('4 of 5 locations on the map', body)
        self.assertIn('2 ready to take horses', body)

    def test_the_rest_strip_has_a_bar_for_every_location(self):
        body = self.body()
        self.assertEqual(body.count('data-loc-bar'), 5)
        self.assertIn('Rest &amp; rotation', body)

    def test_the_rest_strip_runs_longest_rested_first(self):
        order = [
            loc['name'] for loc in
            self.client.get(self.url).context['board_rest_order']
        ]
        self.assertEqual(order[:3], ['Hill-Whitakers', 'Hawkhill', 'Caravan Field'])

    def test_a_rail_row_carries_its_area_and_how_full_it_is(self):
        body = self.body()
        self.assertIn('data-loc-row', body)
        self.assertIn(' ha', body)
        self.assertIn('loc-card-fill', body)

    def test_the_filter_and_both_sorts_are_there(self):
        body = self.body()
        self.assertIn('data-rail-filter', body)
        self.assertIn('data-loc-sort="horses"', body)
        self.assertIn('data-loc-sort="rest"', body)

    def test_the_map_carries_a_key_for_what_its_colours_mean(self):
        body = self.body()
        self.assertIn('loc-map-key', body)
        for label in ('Grazing', 'Rested', 'Recovering', 'Over capacity'):
            with self.subTest(label=label):
                self.assertIn(label, body)

    def test_locations_with_no_boundary_are_listed_apart(self):
        body = self.body()
        self.assertIn('Not on the map', body)
        self.assertIn('data-unmapped', body)

    def test_the_map_hands_a_tap_to_the_rail_rather_than_navigating(self):
        self.assertIn('picks: true', self.body())

    def test_the_standalone_map_on_the_dashboard_card_still_navigates(self):
        """`picks` is opt-in, so nothing else changes behaviour."""
        body = self.client.get(reverse('dashboard')).content.decode()
        if 'locationMap(' in body:
            self.assertIn('picks: false', body)


@override_settings(LOCATION_MAPS_ENABLED=True)
class BoardDetailTests(BoardFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def rail(self, location=None):
        url = reverse('location_preview', args=[(location or self.grazing).pk])
        return self.client.get(url, **RAIL).content.decode()

    def test_the_rail_gets_the_same_partial_as_the_sheet(self):
        url = reverse('location_preview', args=[self.grazing.pk])
        for headers in (RAIL, SHEET):
            with self.subTest(target=headers['HTTP_HX_TARGET']):
                response = self.client.get(url, **headers)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'locations/partials/preview.html')

    def test_anything_that_is_not_the_board_still_goes_to_the_page(self):
        url = reverse('location_preview', args=[self.grazing.pk])
        response = self.client.get(
            url, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body',
        )
        self.assertRedirects(
            response, reverse('location_detail', args=[self.grazing.pk]),
        )

    def test_the_rail_shows_the_area_of_the_ground(self):
        self.assertIn(' ha', self.rail())

    def test_a_location_with_no_boundary_has_no_area_to_show(self):
        body = self.rail(self.unmapped)
        self.assertNotIn(' ha</span>', body)

    def test_the_rail_offers_the_nearest_ready_ground_first(self):
        body = self.rail()
        self.assertIn('Nearest ready ground', body)
        self.assertLess(
            body.index('Hawkhill'), body.index('Hill-Whitakers'),
            'the closer of the two ready parcels must be offered first',
        )

    def test_ground_that_is_still_recovering_is_not_offered(self):
        self.assertNotIn('Caravan Field', self.rail())

    def test_the_location_itself_is_not_offered_as_somewhere_to_go(self):
        body = self.rail(self.near_ready)
        chips = body.split('Nearest ready ground')[-1]
        self.assertNotIn('Hawkhill', chips)

    def test_the_rail_offers_Edit_where_the_sheet_offers_Directions(self):
        """Directions is a phone's answer and a desktop's dead end."""
        rail = self.rail()
        sheet = self.client.get(
            reverse('location_preview', args=[self.grazing.pk]), **SHEET,
        ).content.decode()
        self.assertIn('Edit location', rail)
        self.assertNotIn('Directions', rail)
        self.assertIn('Directions', sheet)

    def test_the_back_link_reads_for_the_shell_it_is_in(self):
        self.assertIn('All locations', self.rail())
        sheet = self.client.get(
            reverse('location_preview', args=[self.grazing.pk]), **SHEET,
        ).content.decode()
        self.assertIn('Somerford', sheet)


@override_settings(LOCATION_MAPS_ENABLED=True)
class SiteSwitchTests(BoardFixture):
    """Switching site on a wide screen.

    The phone drops a menu out of the app bar; that markup is `lg:hidden`,
    so before this the rail was the one shape of the page with no way off
    its own site.
    """

    def setUp(self):
        self.client.force_login(make_admin())
        Location.objects.create(
            name='Long Acre', site='Bicknoller', capacity=8,
            usage=Location.Usage.HORSES,
        )

    def body(self, **params):
        return self.client.get(self.url, params).content.decode()

    def test_the_rail_offers_the_other_site(self):
        body = self.body()
        self.assertIn('data-rail-sites', body)
        self.assertIn('data-board-action="sites"', body)
        self.assertIn('Bicknoller', body)

    def test_switching_stays_on_the_map(self):
        """A site link that dropped ?view=map would land on the cards."""
        self.assertIn('?view=map&amp;site=Bicknoller', self.body())

    def test_each_site_says_how_much_of_it_is_drawn_before_you_go(self):
        body = self.body()
        self.assertIn('mapped', body)
        self.assertIn('horses', body)

    def test_the_links_work_without_javascript(self):
        response = self.client.get(self.url, {'view': 'map', 'site': 'Bicknoller'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['board_site'], 'Bicknoller')

    def test_the_site_links_carry_no_htmx_of_their_own(self):
        """Ordinary boosted links. An hx-select reaching them from the
        shell is what swapped the whole page into #main-content, one more
        app bar and sidebar per switch."""
        body = self.body()
        panel = body[body.index('data-rail-sites'):]
        panel = panel[:panel.index('loc-rail-list')]
        # The site rows only. "+ Add a location" is a pop-up and carries
        # its own htmx, on itself, which is the pattern this is defending.
        rows = [
            chunk for chunk in panel.split('<a ')[1:]
            if 'loc-site-item' in chunk[:chunk.index('>')]
        ]
        self.assertEqual(len(rows), 2)
        for row in rows:
            with self.subTest(row=row[:60]):
                self.assertNotIn('hx-select', row[:row.index('>')])
                self.assertNotIn('hx-push-url', row[:row.index('>')])
        self.assertIn('?view=map&amp;site=Bicknoller', panel)

    def test_one_site_gets_no_switch(self):
        """A control with one place to go is worse than no control."""
        Location.objects.filter(site='Bicknoller').delete()
        body = self.body()
        self.assertNotIn('data-rail-sites', body)
        self.assertNotIn('data-board-action="sites"', body)


@override_settings(LOCATION_MAPS_ENABLED=True)
class ChromeTests(BoardFixture):
    """The two switches above the board share one row.

    Stacked, they cost the board a band of empty page before it started.
    """

    def setUp(self):
        self.client.force_login(make_admin())

    def test_both_switches_sit_in_one_row(self):
        body = self.client.get(self.url).content.decode()
        row = body.split('loc-chrome')[1].split('{# /chrome #}')[0]
        self.assertIn('aria-label="Locations view"', row)
        self.assertIn('aria-label="Locations layout"', row)

    def test_the_usage_tab_has_no_layout_switch_to_show(self):
        body = self.client.get(self.url, {'tab': 'usage'}).content.decode()
        self.assertNotIn('aria-label="Locations layout"', body)


@override_settings(LOCATION_MAPS_ENABLED=True)
class PreviewUrlTests(BoardFixture):
    """Opening a location must not rewrite the address bar.

    locations_board.js and locations_mobile.js name their shell as the
    htmx request's source, so the request inherits whatever that element
    carries. The boosted <body> sets hx-push-url="true"; inheriting it
    made the address bar read /locations/12/preview/ — a partial, so a
    refresh dropped the map, and the address bar went on naming a
    location after it had been closed.
    """

    def setUp(self):
        self.client.force_login(make_admin())

    def test_neither_shell_pushes_the_preview_url(self):
        """The override rides on a leaf inside each shell.

        On the shell itself it reached the links inside it too — see
        ShellInheritanceTests, which is the bug that taught us.
        """
        body = self.client.get(self.url).content.decode()
        self.assertEqual(
            body.count('<span hidden data-preview-source hx-push-url="false"></span>'),
            2,
            'one source for the rail, one for the sheet',
        )

    def test_a_direct_visit_to_a_preview_still_goes_to_the_page(self):
        """The fallback that makes the pushed URL survivable at all."""
        response = self.client.get(
            reverse('location_preview', args=[self.grazing.pk])
        )
        self.assertRedirects(
            response, reverse('location_detail', args=[self.grazing.pk]),
        )


@override_settings(LOCATION_MAPS_ENABLED=True)
class ShellInheritanceTests(BoardFixture):
    """What the shells must not hand to the links inside them.

    htmx reads hx-select and hx-push-url off the requesting element AND
    its ancestors. Putting the detail request's overrides on the shell
    gave them to every ordinary link inside it as well: switching site
    then swapped the whole response into #main-content, so the page grew
    a second app bar and sidebar with every switch and the address never
    changed. The overrides belong on a leaf.
    """

    def setUp(self):
        self.client.force_login(make_admin())

    def opening_tag(self, marker):
        body = self.client.get(self.url).content.decode()
        start = body.index(marker)
        return body[body.rindex('<div', 0, start):body.index('>', start) + 1]

    def test_neither_shell_carries_an_override_for_its_links(self):
        for marker in ('data-site-board', 'data-phone-map'):
            with self.subTest(shell=marker):
                tag = self.opening_tag(marker)
                self.assertNotIn('hx-push-url', tag)
                self.assertNotIn('hx-select', tag)

    def test_each_shell_carries_a_leaf_to_make_the_request_from(self):
        body = self.client.get(self.url).content.decode()
        self.assertEqual(body.count('data-preview-source'), 2)
        self.assertIn('<span hidden data-preview-source hx-push-url="false"></span>', body)



@override_settings(LOCATION_MAPS_ENABLED=True)
class DetailActionTests(BoardFixture):
    """An action may only ask for the pop-up if its view can fill it.

    Logging an arrival is a page of its own — locations/location_arrive.html
    extends base.html — so asking for it in the pop-up put the whole
    application, app bar and sidebar included, inside the sheet.
    """

    def setUp(self):
        self.client.force_login(make_admin())

    def detail(self):
        url = reverse('location_preview', args=[self.grazing.pk])
        return self.client.get(url, **RAIL).content.decode()

    def test_moving_horses_is_an_ordinary_link(self):
        body = self.detail()
        move = body[body.index('Move horses') - 400:body.index('Move horses')]
        self.assertNotIn('popup-body', move.split('<a ')[-1])

    def test_the_actions_that_do_open_the_popup_have_a_partial_to_show(self):
        """Each of these views answers a pop-up request with a form only."""
        for name, args in (
            ('feed_out_create', {'location_pk': self.grazing.pk}),
            ('location_set_usage', {'pk': self.grazing.pk}),
            ('location_update', {'pk': self.grazing.pk}),
            ('location_create', {}),
        ):
            with self.subTest(view=name):
                response = self.client.get(
                    reverse(name, kwargs=args),
                    HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body',
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'data-app-search')
