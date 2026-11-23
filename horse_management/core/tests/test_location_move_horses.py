"""'Move horses' on a location: the horses that live there go to another
location as a group (core.views.locations.location_move_horses).

Until 23 November 2026 the Locations page's "Move horses" button went to
the Log Arrival page. That page lists only horses with no location, so it
could not move the horses of the location in front of the person. These
tests hold the move itself, what stops it, and each place it opens from.
"""

import json
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.forms import GroupMoveForm, LocationMoveForm, move_destinations
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from core.services import PlacementService

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
SHEET = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'loc-sheet-detail'}


class MoveFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin('boss')
        self.client.force_login(self.admin)
        self.helen = Owner.objects.create(name='Helen Marsh')
        self.tom = Owner.objects.create(name='Tom Ellery')
        self.grass = RateType.objects.create(name='Grass', daily_rate=Decimal('6.00'))
        self.premium = RateType.objects.create(name='Premium', daily_rate=Decimal('9.00'))
        self.sandhills = Location.objects.create(name='Sandhills', site='Somerford', capacity=12)
        self.clover = Location.objects.create(name='Clover Leaze', site='Somerford', capacity=4)
        self.river = Location.objects.create(name='River Paddock', site='Mill Lane')
        self.ace = self.horse('Ace', self.sandhills, self.helen, self.grass)
        self.bella = self.horse('Bella', self.sandhills, self.tom, self.premium)
        self.clio = self.horse('Clio', self.sandhills, self.helen, self.grass)
        self.dot = self.horse('Dot', self.clover, self.tom, self.grass)
        self.url = reverse('location_move_horses', args=[self.sandhills.pk])

    def horse(self, name, location, owner, rate, days=30):
        horse = Horse.objects.create(name=name, sex='mare')
        Placement.objects.create(
            horse=horse, owner=owner, location=location, rate_type=rate,
            start_date=self.today - timedelta(days=days),
        )
        return horse

    def data(self, *horses, **extra):
        data = {
            'horse_ids': [horse.pk for horse in horses],
            'new_location': self.clover.pk,
            'move_date': self.today.isoformat(),
            'notes': '',
        }
        data.update(extra)
        return data

    def where(self, horse):
        return horse.placements.get(end_date__isnull=True).location

    def said(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]


class MoveSheetTests(MoveFixture):
    """What the sheet shows."""

    def sheet(self, url=None):
        return self.client.get(url or self.url, **POPUP).content.decode()

    def test_the_sheet_gets_the_form_only_and_a_direct_visit_gets_a_page(self):
        sheet = self.sheet()
        self.assertIn('data-testid="move-horses-form"', sheet)
        self.assertNotIn('<html', sheet)
        page = self.client.get(self.url).content.decode()
        self.assertIn('<h1 class="page-title">Move horses</h1>', page)
        self.assertIn('data-testid="move-horses-form"', page)
        self.assertIn('id="mobile-tab-bar"', page)

    def test_every_horse_that_lives_here_is_listed_and_ticked(self):
        sheet = self.sheet()
        names = re.findall(r'<span class="move-pick-name">([^<]+)</span>', sheet)
        self.assertEqual(names, ['Ace', 'Bella', 'Clio'])
        self.assertEqual(len(re.findall(r'name="horse_ids" value="\d+"[^>]* checked', sheet)), 3)
        self.assertIn('3 of 3 to move', sheet)
        # The horse of another location is not offered.
        self.assertNotIn('Dot', sheet)

    def test_the_script_gets_the_ticked_horses_as_text(self):
        response = self.client.get(self.url, **POPUP)
        ids = [str(horse.pk) for horse in (self.ace, self.bella, self.clio)]
        self.assertEqual(json.loads(response.context['ticked_json']), ids)
        self.assertEqual(json.loads(response.context['movable_json']), ids)

    def test_a_row_says_whose_the_horse_is_and_since_when_it_is_here(self):
        sheet = self.sheet()
        since = (self.today - timedelta(days=30)).strftime('%-d %b')
        self.assertIn(f'Helen Marsh · here since {since}', sheet)

    def test_a_horse_that_arrived_today_is_shown_and_cannot_be_ticked(self):
        late = self.horse('Echo', self.sandhills, self.helen, self.grass, days=0)
        sheet = self.sheet()
        row = sheet.split(f'value="{late.pk}"', 1)[1].split('</li>', 1)[0]
        self.assertIn(' disabled', row.split('>', 1)[0])
        self.assertNotIn(' checked', row.split('>', 1)[0])
        self.assertIn('data-testid="move-stuck"', row)
        self.assertIn('3 of 4 to move', sheet)

    def test_the_place_the_horses_are_in_is_not_offered(self):
        sheet = self.sheet()
        options = re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', sheet.split('name="new_location"', 1)[1].split('</select>', 1)[0])
        self.assertEqual(sorted(name for _pk, name in options), ['Clover Leaze (1 of 4)', 'River Paddock (empty)'])
        self.assertNotIn(str(self.sandhills.pk), [pk for pk, _name in options])

    def test_places_are_grouped_by_site(self):
        sheet = self.sheet()
        self.assertIn('<optgroup label="Mill Lane">', sheet)
        self.assertIn('<optgroup label="Somerford">', sheet)

    def test_the_form_knows_the_room_in_each_place_that_has_a_limit(self):
        room = json.loads(self.client.get(self.url, **POPUP).context['room_json'])
        self.assertEqual(room, {str(self.clover.pk): 3})   # River Paddock has no limit

    def test_the_date_is_today_and_no_later_day_can_be_picked(self):
        sheet = self.sheet()
        box = sheet.split('name="move_date"', 1)[1].split('>', 1)[0]
        self.assertIn(f'max="{self.today.isoformat()}"', box)
        self.assertIn(f'value="{self.today.isoformat()}"', box)

    def test_rate_and_notes_wait_behind_more_options(self):
        sheet = self.sheet()
        more = sheet.split('<details class="group"', 1)[1].split('</details>', 1)[0]
        self.assertIn('name="new_rate_type"', more)
        self.assertIn('name="notes"', more)
        self.assertNotIn('name="new_owner"', sheet)
        self.assertNotIn('name="expected_departure"', sheet)

    def test_a_location_with_no_horses_says_so_and_cannot_save(self):
        sheet = self.sheet(reverse('location_move_horses', args=[self.river.pk]))
        self.assertIn('data-testid="move-empty"', sheet)
        button = sheet.split('data-testid="move-submit"', 1)[1].split('>', 1)[0]
        self.assertIn(' disabled', button)

    def test_no_such_location(self):
        self.assertEqual(self.client.get(reverse('location_move_horses', args=[99999])).status_code, 404)


class MoveSaveTests(MoveFixture):
    """What a save does."""

    def test_the_ticked_horses_move_and_an_unticked_horse_stays(self):
        response = self.client.post(self.url, self.data(self.ace, self.bella), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertEqual(self.where(self.bella), self.clover)
        self.assertEqual(self.where(self.clio), self.sandhills)
        self.assertIn('2 horses moved from Sandhills to Clover Leaze.', self.said(response))

    def test_the_old_stay_ends_the_day_before_and_the_new_one_starts_on_the_day(self):
        self.client.post(self.url, self.data(self.ace), **POPUP)
        old = self.ace.placements.get(location=self.sandhills)
        new = self.ace.placements.get(location=self.clover)
        self.assertEqual(old.end_date, self.today - timedelta(days=1))
        self.assertEqual(new.start_date, self.today)
        self.assertIsNone(new.end_date)

    def test_each_horse_keeps_its_owner_and_its_rate(self):
        self.client.post(self.url, self.data(self.ace, self.bella), **POPUP)
        ace = self.ace.placements.get(end_date__isnull=True)
        bella = self.bella.placements.get(end_date__isnull=True)
        self.assertEqual((ace.owner, ace.rate_type), (self.helen, self.grass))
        self.assertEqual((bella.owner, bella.rate_type), (self.tom, self.premium))

    def test_a_rate_in_more_options_goes_to_every_horse(self):
        self.client.post(self.url, self.data(self.ace, self.bella, new_rate_type=self.premium.pk), **POPUP)
        rates = {p.rate_type for p in Placement.objects.filter(location=self.clover, horse__in=[self.ace, self.bella])}
        self.assertEqual(rates, {self.premium})

    def test_a_day_before_today_can_be_the_move_date(self):
        day = self.today - timedelta(days=2)
        self.client.post(self.url, self.data(self.ace, move_date=day.isoformat()), **POPUP)
        self.assertEqual(self.ace.placements.get(location=self.clover).start_date, day)

    def test_a_direct_visit_goes_back_to_the_location_page(self):
        response = self.client.post(self.url, self.data(self.ace))
        self.assertRedirects(response, reverse('location_detail', args=[self.sandhills.pk]), fetch_redirect_response=False)
        self.assertEqual(self.where(self.ace), self.clover)

    def test_one_horse_reads_as_one(self):
        response = self.client.post(self.url, self.data(self.ace), **POPUP)
        self.assertIn('1 horse moved from Sandhills to Clover Leaze.', self.said(response))

    def test_a_group_that_goes_over_the_limit_moves_and_gets_a_warning(self):
        # Clover Leaze has 1 horse and a limit of 4.
        self.clover.capacity = 2
        self.clover.save()
        response = self.client.post(self.url, self.data(self.ace, self.bella, self.clio), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertIn('Clover Leaze now has 4 horses. Its limit is 2.', self.said(response))

    def test_rested_ground_that_goes_over_its_limit_gets_the_warning_too(self):
        rested = Location.objects.create(
            name='Hawkhill', site='Somerford', capacity=2, usage=Location.Usage.RESTED)
        response = self.client.post(
            self.url, self.data(self.ace, self.bella, self.clio, new_location=rested.pk), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertIn('Hawkhill now has 3 horses. Its limit is 2.', self.said(response))

    def test_a_group_that_fits_gets_no_warning(self):
        response = self.client.post(self.url, self.data(self.ace, self.bella), **POPUP)
        self.assertFalse([line for line in self.said(response) if 'limit' in line])


class MoveRefusedTests(MoveFixture):
    """What stops a move, and what the person is told."""

    def kept(self, response):
        """The sheet stays open with the form."""
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-testid="move-horses-form"', html)
        return html

    def test_no_horse_ticked(self):
        html = self.kept(self.client.post(self.url, self.data(), **POPUP))
        self.assertIn('Choose at least one horse to move.', html)
        self.assertEqual(self.where(self.ace), self.sandhills)

    def test_no_place_chosen_keeps_the_ticks(self):
        html = self.kept(self.client.post(self.url, self.data(self.ace, new_location=''), **POPUP))
        self.assertEqual(len(re.findall(r'name="horse_ids" value="\d+"[^>]* checked', html)), 1)
        self.assertIn('1 of 3 to move', html)

    def test_the_same_place_is_not_a_choice(self):
        self.kept(self.client.post(self.url, self.data(self.ace, new_location=self.sandhills.pk), **POPUP))
        self.assertEqual(self.ace.placements.count(), 1)

    def test_an_archived_place_is_not_a_choice(self):
        self.river.is_archived = True
        self.river.save()
        self.kept(self.client.post(self.url, self.data(self.ace, new_location=self.river.pk), **POPUP))
        self.assertEqual(self.where(self.ace), self.sandhills)

    def test_a_day_after_today_moves_nothing_and_says_why(self):
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        html = self.kept(self.client.post(self.url, self.data(self.ace, self.bella, move_date=tomorrow), **POPUP))
        self.assertIn('No horse was moved:', html)
        self.assertIn('Ace: A move can&#x27;t be dated in the future.', html)
        self.assertEqual(self.where(self.ace), self.sandhills)

    def test_a_horse_that_arrived_today_stays_and_the_others_go(self):
        late = self.horse('Echo', self.sandhills, self.helen, self.grass, days=0)
        response = self.client.post(self.url, self.data(self.ace, late), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertEqual(self.where(late), self.sandhills)
        said = self.said(response)
        self.assertIn('1 horse moved from Sandhills to Clover Leaze.', said)
        self.assertTrue(any(line.startswith('Not moved: Echo. Move date must be after') for line in said), said)

    def test_a_horse_that_someone_else_moved_meanwhile_is_not_moved_again(self):
        # The sheet was open with Clio ticked; someone moved her to River Paddock.
        PlacementService.move_horse(self.clio, new_location=self.river, move_date=self.today)
        response = self.client.post(self.url, self.data(self.ace, self.clio), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.where(self.clio), self.river)
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertIn('1 horse was not in Sandhills any more, and was not moved.', self.said(response))

    def test_only_horses_that_are_gone_were_ticked(self):
        html = self.kept(self.client.post(self.url, self.data(self.dot), **POPUP))
        self.assertIn('The horses you chose are not in Sandhills any more.', html)
        self.assertEqual(self.where(self.dot), self.clover)

    def test_a_horse_of_another_location_cannot_be_moved_from_here(self):
        # Dot lives in Clover Leaze. A hand-made post names her from Sandhills.
        self.client.post(self.url, self.data(self.ace, self.dot, new_location=self.river.pk), **POPUP)
        self.assertEqual(self.where(self.dot), self.clover)
        self.assertEqual(self.where(self.ace), self.river)

    def test_a_departed_horse_is_not_listed_and_not_moved(self):
        self.clio.is_active = False
        self.clio.save()
        sheet = self.client.get(self.url, **POPUP).content.decode()
        self.assertNotIn('Clio', sheet)


class MoveAccessTests(MoveFixture):
    def test_moving_needs_full_access_to_horses(self):
        for name, access, expected in (
            ('yardhand', {'horses': 'full', 'locations': 'view'}, 200),
            ('looker', {'horses': 'view', 'locations': 'full'}, 403),
            ('office', {'locations': 'full'}, 403),
        ):
            with self.subTest(role=name):
                self.client.force_login(make_user_with_access(name, **access))
                self.assertEqual(self.client.get(self.url, **POPUP).status_code, expected)
                response = self.client.post(self.url, self.data(self.ace), **POPUP)
                self.assertEqual(response.status_code, 204 if expected == 200 else 403)

    def test_signed_out_goes_to_sign_in(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class MoveGroupServiceTests(MoveFixture):
    """PlacementService.move_group: one way to move a group, for this sheet
    and for the bulk action "Move to Location"."""

    def test_it_gives_the_new_stays_and_the_horses_that_stayed(self):
        moved, failed = PlacementService.move_group(
            [self.ace, self.dot], new_location=self.clover, move_date=self.today,
        )
        self.assertEqual([p.horse for p in moved], [self.ace])
        self.assertEqual(failed, [(self.dot, 'it is in Clover Leaze already.')])

    def test_from_a_location_leaves_a_horse_that_is_not_there(self):
        moved, failed = PlacementService.move_group(
            [self.ace, self.dot], new_location=self.river, move_date=self.today,
            from_location=self.sandhills,
        )
        self.assertEqual([p.horse for p in moved], [self.ace])
        self.assertEqual(failed, [(self.dot, 'it is not in Sandhills any more.')])
        self.assertEqual(self.where(self.dot), self.clover)

    def test_one_refused_horse_does_not_undo_the_others(self):
        late = self.horse('Echo', self.sandhills, self.helen, self.grass, days=0)
        moved, failed = PlacementService.move_group(
            [self.ace, late, self.bella], new_location=self.clover, move_date=self.today,
        )
        self.assertEqual([p.horse for p in moved], [self.ace, self.bella])
        self.assertEqual([horse for horse, _reason in failed], [late])
        # The refused horse's stay is as it was: open, with no end date.
        self.assertIsNone(late.placements.get().end_date)

    def test_the_bulk_action_uses_it(self):
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'move', 'horse_ids': [self.ace.pk, self.dot.pk],
            'new_location': self.clover.pk, 'move_date': self.today.isoformat(), 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        said = self.said(response)
        self.assertIn('1 horse moved to Clover Leaze.', said)
        self.assertIn('Not moved — Dot: it is in Clover Leaze already.', said)
        # No second stay in the same place for the horse that was there.
        self.assertEqual(self.dot.placements.count(), 1)


class GroupMoveFormTests(MoveFixture):
    def test_a_group_form_has_no_field_for_one_horse(self):
        form = GroupMoveForm()
        self.assertEqual(list(form.fields), ['new_location', 'new_rate_type', 'move_date', 'notes'])

    @override_settings(BARNS_ENABLED=True)
    def test_with_barns_on_it_has_no_stable_and_asks_the_server_nothing(self):
        # The single move's place select fetches a stable picker for one
        # horse; a group form has no place to show it.
        form = GroupMoveForm()
        self.assertNotIn('stable', form.fields)
        self.assertNotIn('share_stable', form.fields)
        attrs = form.fields['new_location'].widget.attrs
        self.assertFalse([key for key in attrs if key.startswith('hx-')], attrs)
        self.assertNotIn('x-ref', form.fields['new_rate_type'].widget.attrs)

    def test_the_date_box_offers_no_day_after_today(self):
        self.assertEqual(GroupMoveForm().fields['move_date'].widget.attrs['max'], self.today.isoformat())

    def test_destinations_say_how_full_each_place_is(self):
        self.horse('Fern', self.river, self.tom, self.grass)
        self.horse('Gem', self.river, self.tom, self.grass)
        choices, room = move_destinations(exclude_pk=self.sandhills.pk)
        self.assertEqual(choices[0], ('', 'Choose a location…'))
        self.assertEqual(dict(choices[1:]), {
            'Mill Lane': [(self.river.pk, 'River Paddock (2 horses)')],
            'Somerford': [(self.clover.pk, 'Clover Leaze (1 of 4)')],
        })
        self.assertEqual(room, {self.clover.pk: 3})

    def test_rested_ground_with_a_limit_says_its_room(self):
        # Rested ground is where a group goes next: its limit counts.
        rested = Location.objects.create(
            name='Hawkhill', site='Somerford', capacity=10, usage=Location.Usage.RESTED)
        choices, room = move_destinations(exclude_pk=self.sandhills.pk)
        self.assertIn((rested.pk, 'Hawkhill (empty, room for 10)'), dict(choices[1:])['Somerford'])
        self.assertEqual(room[rested.pk], 10)

    def test_a_full_place_has_no_room_and_not_less_than_none(self):
        self.clover.capacity = 1
        self.clover.save()
        self.horse('Fern', self.clover, self.tom, self.grass)
        _choices, room = move_destinations()
        self.assertEqual(room[self.clover.pk], 0)

    def test_the_location_form_does_not_take_its_own_location(self):
        form = LocationMoveForm({'new_location': self.sandhills.pk, 'move_date': self.today.isoformat()}, source=self.sandhills)
        self.assertFalse(form.is_valid())
        self.assertIn('new_location', form.errors)


@override_settings(LOCATION_MAPS_ENABLED=True)
class EntryPointTests(MoveFixture):
    """Each place "Move horses" opens from."""

    def preview(self, location, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse('location_preview', args=[location.pk]), **SHEET).content.decode()

    def first_button(self, html):
        actions = html.split('class="loc-sheet-actions"', 1)[1]
        return re.search(r'<a [^>]*class="loc-action is-primary"[^>]*>[^<]+</a>', actions, re.S).group(0)

    def test_the_locations_sheet_opens_the_group_move_and_not_the_arrival_page(self):
        button = self.first_button(self.preview(self.sandhills))
        self.assertIn('>Move horses</a>', button)
        self.assertIn(f'href="{self.url}"', button)
        self.assertIn(f'hx-get="{self.url}"', button)
        self.assertIn('hx-target="#popup-body"', button)
        self.assertIn('data-popup-title="Move horses from Sandhills"', button)
        self.assertNotIn('/arrive/', button)

    def test_the_arrival_is_a_link_under_the_horses(self):
        html = self.preview(self.sandhills)
        link = html.split('data-testid="loc-arrival-link"', 1)[0].rsplit('<a ', 1)[1]
        self.assertIn(f'href="{reverse("location_arrive", args=[self.sandhills.pk])}"', link)

    def test_a_location_with_no_horses_offers_the_arrival_under_its_own_name(self):
        html = self.preview(self.river)
        button = self.first_button(html)
        self.assertIn('>Log arrival</a>', button)
        self.assertIn(f'href="{reverse("location_arrive", args=[self.river.pk])}"', button)
        self.assertNotIn('Move horses', html)
        self.assertNotIn('data-testid="loc-arrival-link"', html)

    def test_a_role_that_cannot_move_horses_gets_the_arrival_or_the_list(self):
        keeper = make_user_with_access('keeper', locations='full', horses='view')
        self.assertIn('>Log arrival</a>', self.first_button(self.preview(self.sandhills, keeper)))
        looker = make_user_with_access('looker', locations='view', horses='view')
        self.assertIn('>View horses</a>', self.first_button(self.preview(self.sandhills, looker)))

    def test_the_location_page_has_the_button_when_horses_live_there(self):
        page = self.client.get(reverse('location_detail', args=[self.sandhills.pk])).content.decode()
        button = page.split('data-testid="location-move-horses"', 1)[0].rsplit('<a ', 1)[1]
        self.assertIn(f'hx-get="{self.url}"', button)
        self.assertIn('data-popup-title="Move horses from Sandhills"', button)
        empty = self.client.get(reverse('location_detail', args=[self.river.pk])).content.decode()
        self.assertNotIn('data-testid="location-move-horses"', empty)

    def test_a_location_group_of_the_horse_list_has_the_link(self):
        page = self.client.get(reverse('horse_list'), {'group_by': 'location'}).content.decode()
        links = re.findall(r'href="(/locations/\d+/move-horses/)"[^>]*data-testid="group-move-horses"', page.replace('\n', ' '))
        self.assertIn(self.url, links)
        self.assertIn('Move horses from Sandhills', page)

    def test_the_horse_list_has_no_link_for_a_role_that_cannot_move(self):
        self.client.force_login(make_user_with_access('looker', horses='view', locations='view'))
        page = self.client.get(reverse('horse_list'), {'group_by': 'location'}).content.decode()
        self.assertNotIn('data-testid="group-move-horses"', page)
