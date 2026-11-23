"""Undo for a group move (core.views.locations.group_move_undo).

A horse that arrives in a location today cannot be moved again until
tomorrow: a stay cannot end before it began. So a group sent to the wrong
location needed an edit of each horse's stay. The Undo on the "moved"
message puts the group back in one tap: the new stay is deleted and the
stay before it opens again.

These tests hold what goes back, what stops a horse from going back, the
link in the message, and the notifications of the move.
"""

import time
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from core.models import (
    Horse, Location, LocationUsagePeriod, Owner, OwnershipShare, Placement, Turnout,
)
from core.roles_testutils import make_user_with_access
from core.services import LocationUsageService, PlacementService
from core.stables import StableService, add_stables, open_stay
from core.tests.test_location_move_horses import POPUP, MoveFixture
from core.turnout import TurnoutService
from core.views import _undo
from core.views._undo import group_move_undo_url, read_group_move_token, undo_url_from_tags
from invoicing.models import Invoice, InvoiceLineItem
from notifications.models import Notification, NotificationPreference


def token_of(url):
    """The signed part of an Undo link, as the view reads it."""
    return parse_qs(urlsplit(url).query)['t'][0]


def usage_history(location):
    """A location's usage and its periods, to compare before and after."""
    location.refresh_from_db()
    return location.usage, [
        (period.usage, period.start_date, period.end_date, period.source)
        for period in location.usage_periods.order_by('start_date', 'pk')
    ]


class UndoFixture(MoveFixture):
    def move(self, *horses, to=None, day=None, **extra):
        moved, failed = PlacementService.move_group(
            list(horses), new_location=to or self.clover, move_date=day or self.today, **extra,
        )
        self.assertEqual(failed, [])
        return moved

    def undo(self, moved, to=None, day=None):
        return PlacementService.undo_move_group(
            [placement.pk for placement in moved],
            destination_id=(to or self.clover).pk, move_date=day or self.today,
        )

    def leaves(self, horse):
        """The horse leaves the yard today. A new object, as a new request
        has: the one in the fixture remembers the stay from before the move."""
        PlacementService.depart_horse(Horse.objects.get(pk=horse.pk), self.today)


class UndoServiceTests(UndoFixture):
    """What goes back."""

    def test_each_horse_is_back_in_the_stay_it_had(self):
        before = {
            horse: horse.placements.get() for horse in (self.ace, self.bella, self.clio)
        }
        moved = self.move(self.ace, self.bella, self.clio)
        restored, failed, gone = self.undo(moved)
        self.assertEqual((len(restored), failed, gone), (3, [], 0))
        for horse, stay in before.items():
            # One stay, the same row as before the move, open again.
            now = horse.placements.get()
            self.assertEqual(now.pk, stay.pk)
            self.assertIsNone(now.end_date)
            self.assertEqual(now.start_date, stay.start_date)
            self.assertEqual(now.location, self.sandhills)
        self.assertFalse(Placement.objects.filter(pk__in=[p.pk for p in moved]).exists())

    def test_the_owner_and_the_rate_of_the_old_stay_come_back(self):
        moved = self.move(self.bella, new_rate_type=self.grass)
        self.assertEqual(moved[0].rate_type, self.grass)
        self.undo(moved)
        stay = self.bella.placements.get()
        self.assertEqual((stay.owner, stay.rate_type), (self.tom, self.premium))

    def test_the_usage_history_of_both_locations_is_as_it_was(self):
        # Every horse leaves Sandhills: it starts to rest. River Paddock was
        # empty and rested: it gets horses. Undo takes both changes out again.
        LocationUsageService.set_usage(
            self.river, usage=Location.Usage.RESTED, change_date=self.today - timedelta(days=20),
            source=LocationUsagePeriod.Source.MANUAL,
        )
        source_before = usage_history(self.sandhills)
        target_before = usage_history(self.river)
        moved = self.move(self.ace, self.bella, self.clio, to=self.river)
        self.assertEqual(usage_history(self.sandhills)[0], Location.Usage.RESTED)
        self.assertEqual(usage_history(self.river)[0], Location.Usage.HORSES)
        self.undo(moved, to=self.river)
        self.assertEqual(usage_history(self.sandhills), source_before)
        self.assertEqual(usage_history(self.river), target_before)

    def test_a_location_that_had_horses_before_keeps_them(self):
        # Dot lives in Clover Leaze. The group comes and goes; Dot stays.
        before = usage_history(self.clover)
        self.undo(self.move(self.ace, self.bella))
        self.assertEqual(usage_history(self.clover), before)
        self.assertEqual(self.where(self.dot), self.clover)

    def test_a_move_dated_before_today_goes_back_also(self):
        day = self.today - timedelta(days=3)
        moved = self.move(self.ace, self.clio, day=day)
        restored, failed, _gone = self.undo(moved, day=day)
        self.assertEqual((len(restored), failed), (2, []))
        self.assertIsNone(self.ace.placements.get().end_date)

    def test_a_horse_gets_its_stable_back(self):
        barn = Location.objects.create(
            name='Top Barn', site='Somerford', location_type=Location.LocationType.BARN, layout='aisle')
        add_stables(barn, 4)
        echo = self.horse('Echo', barn, self.helen, self.grass)
        StableService.assign(echo, barn.stables.get(label='2'), self.today - timedelta(days=10))
        moved = self.move(echo)
        self.assertIsNone(open_stay(echo))
        self.undo(moved)
        stay = open_stay(echo)
        self.assertEqual(stay.stable.label, '2')
        self.assertEqual(stay.start_date, self.today - timedelta(days=10))

    def test_a_stabled_horse_keeps_its_stable_and_its_day_turnout(self):
        # Barn to barn. The stable belongs to the first barn; the day
        # turnout goes with the horse. Undo puts both back as they were.
        top = Location.objects.create(
            name='Top Barn', site='Somerford', location_type=Location.LocationType.BARN, layout='aisle')
        lower = Location.objects.create(
            name='Lower Barn', site='Somerford', location_type=Location.LocationType.BARN, layout='row')
        add_stables(top, 4)
        add_stables(lower, 4)
        echo = self.horse('Echo', top, self.helen, self.grass)
        since = self.today - timedelta(days=10)
        StableService.assign(echo, top.stables.get(label='3'), since)
        TurnoutService.start(echo, self.river, since)
        moved = self.move(echo, to=lower)
        self.assertIsNone(open_stay(echo))
        self.undo(moved, to=lower)
        stay = open_stay(echo)
        self.assertEqual((stay.stable.location, stay.stable.label, stay.start_date), (top, '3', since))
        turnout = Turnout.objects.get(horse=echo, end_date__isnull=True)
        self.assertEqual((turnout.location, turnout.start_date), (self.river, since))
        self.assertEqual(Turnout.objects.filter(horse=echo).count(), 1)

    def test_the_horse_stays_active(self):
        self.undo(self.move(self.ace))
        self.ace.refresh_from_db()
        self.assertTrue(self.ace.is_active)

    def test_a_second_undo_changes_nothing(self):
        moved = self.move(self.ace, self.bella)
        ids = [placement.pk for placement in moved]
        self.undo(moved)
        restored, failed, gone = PlacementService.undo_move_group(
            ids, destination_id=self.clover.pk, move_date=self.today)
        self.assertEqual((restored, failed, gone), ([], [], 2))
        self.assertEqual(self.where(self.ace), self.sandhills)


class UndoRefusedTests(UndoFixture):
    """A horse goes back only if nothing changed after the move."""

    def test_a_horse_that_left_after_the_move_is_named_and_the_others_go_back(self):
        moved = self.move(self.ace, self.bella, self.clio)
        self.leaves(self.clio)
        restored, failed, gone = self.undo(moved)
        self.assertEqual([p.horse for p in restored], [self.ace, self.bella])
        self.assertEqual(failed, [(self.clio, 'it left Clover Leaze after the move.')])
        self.assertEqual(gone, 0)
        # Its two stays are as the departure left them.
        self.assertEqual(self.clio.placements.filter(end_date__isnull=True).count(), 0)
        self.assertEqual(self.clio.placements.count(), 2)

    def test_a_stay_corrected_to_another_location_is_left(self):
        moved = self.move(self.ace)
        stay = moved[0]
        stay.location = self.river
        stay.save()
        _restored, failed, _gone = self.undo(moved)
        self.assertEqual(failed, [(self.ace, 'its stay was changed after the move.')])
        self.assertEqual(self.where(self.ace), self.river)

    def test_a_stay_with_a_changed_start_date_is_left(self):
        day = self.today - timedelta(days=5)
        moved = self.move(self.ace, day=day)
        old = self.ace.placements.get(end_date__isnull=False)
        old.end_date = day - timedelta(days=3)
        old.save()
        stay = moved[0]
        stay.start_date = day - timedelta(days=2)
        stay.save()
        _restored, failed, _gone = self.undo(moved, day=day)
        self.assertEqual(failed, [(self.ace, 'its stay was changed after the move.')])

    def test_a_stay_on_an_invoice_is_left(self):
        moved = self.move(self.ace)
        invoice = Invoice.objects.create(
            owner=self.helen, invoice_number='INV-0001',
            period_start=self.today.replace(day=1), period_end=self.today,
            subtotal=Decimal('6.00'), total=Decimal('6.00'),
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, horse=self.ace, placement=moved[0],
            description='Livery', unit_price=Decimal('6.00'),
        )
        restored, failed, _gone = self.undo(moved)
        self.assertEqual(restored, [])
        self.assertEqual(failed, [(self.ace, 'its stay in Clover Leaze is on an invoice.')])
        self.assertEqual(self.where(self.ace), self.clover)

    def test_a_changed_old_stay_is_left(self):
        moved = self.move(self.ace)
        old = self.ace.placements.get(end_date__isnull=False)
        old.end_date = self.today - timedelta(days=4)
        old.save()
        _restored, failed, _gone = self.undo(moved)
        self.assertEqual(failed, [(self.ace, 'the stay before the move was changed.')])
        self.assertEqual(self.where(self.ace), self.clover)

    def test_a_refused_horse_is_not_half_done(self):
        # The old stay cannot open again: the new stay is not deleted.
        moved = self.move(self.ace)
        with mock.patch.object(Placement, 'save', side_effect=ValidationError('Sandhills is closed.')):
            restored, failed, _gone = self.undo(moved)
        self.assertEqual((restored, failed), ([], [(self.ace, 'Sandhills is closed.')]))
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertEqual(self.ace.placements.count(), 2)


class UndoLinkTests(UndoFixture):
    """The signed link in the message."""

    def read(self, url, user=None):
        return read_group_move_token(token_of(url), user or self.admin)

    def test_it_names_the_stays_the_place_and_the_day(self):
        moved = self.move(self.ace, self.bella)
        url = group_move_undo_url(self.admin, moved)
        self.assertTrue(url.startswith(reverse('group_move_undo') + '?t='))
        self.assertEqual(self.read(url), {
            'destination_id': self.clover.pk,
            'move_date': self.today,
            'placement_ids': [placement.pk for placement in moved],
            'share_owner_id': None,
        })

    def test_the_message_code_accepts_it_as_an_undo_link(self):
        url = group_move_undo_url(self.admin, self.move(self.ace))
        self.assertEqual(undo_url_from_tags(_undo.undo_tag(url)), url)
        self.assertNotRegex(url, r'\s')

    def test_it_is_for_the_person_who_moved_the_horses(self):
        other = make_user_with_access('other', horses='full')
        url = group_move_undo_url(self.admin, self.move(self.ace))
        self.assertIsNone(self.read(url, other))

    def test_a_changed_link_is_refused(self):
        url = group_move_undo_url(self.admin, self.move(self.ace))
        token = token_of(url)
        self.assertIsNotNone(read_group_move_token(token, self.admin))
        self.assertIsNone(read_group_move_token(token[:-2] + 'xx', self.admin))
        self.assertIsNone(read_group_move_token('', self.admin))
        self.assertIsNone(read_group_move_token('not-a-token', self.admin))
        # Signed by this app for another purpose.
        self.assertIsNone(read_group_move_token(signing.dumps({'u': self.admin.pk}), self.admin))

    def test_it_stops_after_twelve_hours(self):
        url = group_move_undo_url(self.admin, self.move(self.ace))
        soon = time.time() + 11 * 60 * 60
        late = time.time() + 13 * 60 * 60
        with mock.patch('django.core.signing.time.time', return_value=soon):
            self.assertIsNotNone(self.read(url))
        with mock.patch('django.core.signing.time.time', return_value=late):
            self.assertIsNone(self.read(url))

    def test_a_horse_that_had_no_location_is_not_in_it(self):
        # "Move to Location" on the horse list can place a horse that has
        # no location. That is an arrival: there is no stay to open again.
        stray = Horse.objects.create(name='Stray', sex='mare')
        OwnershipShare.objects.create(
            horse=stray, owner=self.helen, share_percentage=100, is_primary_contact=True)
        moved, failed = PlacementService.move_group(
            [self.ace, stray], new_location=self.clover, move_date=self.today,
            new_rate_type=self.grass,
        )
        self.assertEqual((len(moved), failed), (2, []))
        ids = self.read(group_move_undo_url(self.admin, moved))['placement_ids']
        self.assertEqual(ids, [self.ace.placements.get(end_date__isnull=True).pk])
        # A move of such horses only: no Undo.
        self.assertEqual(group_move_undo_url(self.admin, moved[1:]), '')

    def test_no_horse_no_link(self):
        self.assertEqual(group_move_undo_url(self.admin, []), '')

    def test_a_link_that_a_server_can_refuse_is_not_offered(self):
        moved = self.move(self.ace, self.bella)
        with mock.patch.object(_undo, 'GROUP_MOVE_UNDO_MAX_URL', 40):
            self.assertEqual(group_move_undo_url(self.admin, moved), '')

    def test_a_field_full_of_horses_has_a_short_link(self):
        # 60 stays with numbers as a busy yard has them.
        stays = [
            mock.Mock(pk=48000 + 7 * n, location_id=912, start_date=date(2026, 11, 23))
            for n in range(60)
        ]
        self.assertLess(len(group_move_undo_url(self.admin, stays)), 600)


class UndoButtonTests(UndoFixture):
    """The button, from the tap on "Move horses" to the tap on "Undo"."""

    def undo_link(self, response):
        tags = [
            message.extra_tags for message in response.wsgi_request._messages
            if message.level_tag == 'success'
        ]
        self.assertEqual(len(tags), 1)
        return undo_url_from_tags(tags[0])

    def moved_from_the_sheet(self, *horses, **extra):
        response = self.client.post(self.url, self.data(*horses, **extra), **POPUP)
        self.assertEqual(response.status_code, 204)
        return response

    def undo_link_on_the_page(self, *horses):
        """Move from the sheet. The sheet then refreshes the page, which
        shows the message (and takes it out of the queue). The link."""
        link = self.undo_link(self.moved_from_the_sheet(*horses))
        self.client.get(reverse('help_index'))
        return link

    def test_the_moved_message_has_an_undo_button(self):
        response = self.moved_from_the_sheet(self.ace, self.bella, self.clio)
        link = self.undo_link(response)
        self.assertTrue(link.startswith('/horses/moves/undo/?t='))
        # The page that the sheet refreshes shows it in the message.
        page = self.client.get(reverse('location_list')).content.decode()
        toast = page.split('id="toast-container"', 1)[1].split('<main', 1)[0]
        self.assertIn('3 horses moved from Sandhills to Clover Leaze.', toast)
        self.assertIn(f'action="{link.replace("&", "&amp;")}"', toast)
        self.assertIn('>Undo</button>', toast)
        # It does not close by itself: no countdown on a message with Undo.
        self.assertNotIn('toast-countdown', toast.split('3 horses moved', 1)[1].split('</form>', 1)[0])

    def test_the_full_page_form_has_it_too(self):
        response = self.client.post(self.url, self.data(self.ace))
        self.assertRedirects(response, reverse('location_detail', args=[self.sandhills.pk]))
        self.assertTrue(self.undo_link(response))

    def test_undo_puts_the_group_back_and_says_so(self):
        link = self.undo_link_on_the_page(self.ace, self.bella, self.clio)
        back = reverse('location_list')
        response = self.client.post(link, {'next': back})
        self.assertRedirects(response, back)
        for horse in (self.ace, self.bella, self.clio):
            self.assertEqual(self.where(horse), self.sandhills)
            self.assertEqual(horse.placements.count(), 1)
        self.assertEqual(self.said(response), ['Move undone. 3 horses are back in Sandhills.'])

    def test_one_horse_is_named(self):
        link = self.undo_link_on_the_page(self.ace)
        response = self.client.post(link)
        self.assertEqual(self.said(response), ['Move undone. Ace is back in Sandhills.'])
        # No page was named: the horse list.
        self.assertRedirects(response, reverse('horse_list'))

    def test_a_second_tap_changes_nothing(self):
        link = self.undo_link_on_the_page(self.ace, self.bella)
        self.client.post(link)
        self.client.get(reverse('help_index'))
        response = self.client.post(link)
        self.assertEqual(self.said(response), ['This move was undone already.'])
        self.assertEqual(self.where(self.ace), self.sandhills)

    def test_a_horse_that_cannot_go_back_is_named(self):
        link = self.undo_link_on_the_page(self.ace, self.clio)
        self.leaves(self.clio)
        response = self.client.post(link)
        self.assertEqual(self.said(response), [
            'Move undone. Ace is back in Sandhills.',
            'Not put back: Clio. It left Clover Leaze after the move.',
        ])

    def test_no_horse_can_go_back(self):
        link = self.undo_link_on_the_page(self.ace)
        self.leaves(self.ace)
        response = self.client.post(link)
        self.assertEqual(self.said(response), ['Not put back: Ace. It left Clover Leaze after the move.'])

    def test_the_move_must_be_asked_for_with_a_post(self):
        link = self.undo_link(self.moved_from_the_sheet(self.ace))
        self.assertEqual(self.client.get(link).status_code, 405)
        self.assertEqual(self.where(self.ace), self.clover)

    def test_a_bad_or_old_link_says_what_to_do(self):
        link = self.undo_link_on_the_page(self.ace)
        for bad in (reverse('group_move_undo'), link[:-3] + 'abc'):
            response = self.client.post(bad, {'next': reverse('location_list')})
            self.assertRedirects(response, reverse('location_list'))
            self.assertEqual(self.said(response), [
                "This move cannot be undone here any more. "
                "To put a horse back, change its stay on the horse's page.",
            ])
        late = time.time() + 13 * 60 * 60
        with mock.patch('django.core.signing.time.time', return_value=late):
            self.client.post(link)
        self.assertEqual(self.where(self.ace), self.clover)

    def test_the_link_of_one_person_does_not_work_for_another(self):
        link = self.undo_link_on_the_page(self.ace)
        self.client.force_login(make_user_with_access('other', horses='full', locations='full'))
        response = self.client.post(link)
        self.assertIn('cannot be undone here any more', self.said(response)[0])
        self.assertEqual(self.where(self.ace), self.clover)

    def test_a_person_who_can_only_look_at_horses_cannot_undo(self):
        link = self.undo_link(self.moved_from_the_sheet(self.ace))
        self.client.force_login(make_user_with_access('looker', horses='view', locations='view'))
        self.client.post(link)
        self.assertEqual(self.where(self.ace), self.clover)

    def test_it_does_not_send_the_person_to_another_site(self):
        link = self.undo_link(self.moved_from_the_sheet(self.ace))
        response = self.client.post(link, {'next': 'https://example.org/away'})
        self.assertRedirects(response, reverse('horse_list'))

    def test_when_no_horse_moved_there_is_no_message_and_no_button(self):
        late = self.horse('Echo', self.sandhills, self.helen, self.grass, days=0)
        response = self.client.post(self.url, self.data(late), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.wsgi_request._messages), [])


class BulkActionUndoTests(UndoFixture):
    """"Move to Location" for ticked horses on the horse list."""

    def bulk_move(self, *horses, **extra):
        data = {
            'action_type': 'move', 'horse_ids': [horse.pk for horse in horses],
            'new_location': self.river.pk, 'move_date': self.today.isoformat(), 'notes': '',
        }
        data.update(extra)
        response = self.client.post(reverse('bulk_health_apply'), data, **POPUP)
        self.assertEqual(response.status_code, 204)
        return response

    def undo_link(self, response):
        return next(
            undo_url_from_tags(message.extra_tags) for message in response.wsgi_request._messages
            if message.level_tag == 'success'
        )

    def test_horses_from_two_locations_go_back_to_their_own(self):
        response = self.bulk_move(self.ace, self.bella, self.dot)
        self.assertIn('3 horses moved to River Paddock.', self.said(response))
        self.client.get(reverse('help_index'))
        answer = self.client.post(self.undo_link(response), {'next': reverse('horse_list')})
        self.assertEqual(self.said(answer), ['Move undone. 3 horses are back where they were.'])
        self.assertEqual(self.where(self.ace), self.sandhills)
        self.assertEqual(self.where(self.bella), self.sandhills)
        self.assertEqual(self.where(self.dot), self.clover)

    def test_a_refused_horse_is_not_in_the_undo(self):
        # Dot is in Clover Leaze already, so only Ace moves there.
        response = self.bulk_move(self.ace, self.dot, new_location=self.clover.pk)
        link = self.undo_link(response)
        asked = read_group_move_token(token_of(link), self.admin)
        self.assertEqual(len(asked['placement_ids']), 1)
        self.client.post(link)
        self.assertEqual(self.where(self.ace), self.sandhills)
        self.assertEqual(self.where(self.dot), self.clover)
        self.assertEqual(self.dot.placements.count(), 1)

    def test_other_bulk_actions_have_no_undo(self):
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'expected_departure', 'horse_ids': [self.ace.pk],
            'date': (self.today + timedelta(days=7)).isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.undo_link(response), '')


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class UndoNotificationTests(UndoFixture):
    """The "moved" notifications of an undone move."""

    def setUp(self):
        super().setUp()
        self.groom = make_user_with_access('groom', horses='view', locations='view')

    def move_and_tell(self, *horses):
        with self.captureOnCommitCallbacks(execute=True):
            return self.move(*horses)

    def undo_and_tell(self, moved):
        with self.captureOnCommitCallbacks(execute=True):
            return self.undo(moved)

    def titles(self, user=None):
        return sorted(Notification.objects.filter(recipient=user or self.groom).values_list('title', flat=True))

    def test_notifications_nobody_read_go_and_nothing_new_is_sent(self):
        moved = self.move_and_tell(self.ace, self.bella)
        self.assertEqual(self.titles(), ['Ace moved', 'Bella moved'])
        self.undo_and_tell(moved)
        self.assertEqual(self.titles(), [])

    def test_a_person_who_read_one_is_told_that_the_horses_are_back(self):
        moved = self.move_and_tell(self.ace, self.bella)
        Notification.objects.filter(recipient=self.groom, title='Ace moved').update(read_at=self.today_time())
        self.undo_and_tell(moved)
        self.assertEqual(self.titles(), ['Move undone: 2 horses are back in Sandhills'])
        note = Notification.objects.get(recipient=self.groom)
        self.assertEqual(note.url, reverse('location_detail', args=[self.sandhills.pk]))
        self.assertIn('Ace, Bella', note.body)

    def test_one_horse_is_named_in_the_new_notification(self):
        moved = self.move_and_tell(self.ace)
        Notification.objects.filter(recipient=self.groom).update(read_at=self.today_time())
        self.undo_and_tell(moved)
        note = Notification.objects.get(recipient=self.groom)
        self.assertEqual(note.title, 'Move undone: Ace is back in Sandhills')
        self.assertEqual(note.url, reverse('horse_detail', args=[self.ace.pk]))

    def test_a_person_with_movements_by_email_is_told(self):
        NotificationPreference.objects.create(
            user=self.groom, category=Notification.Category.MOVEMENT, in_app=True, email=True)
        moved = self.move_and_tell(self.ace, self.bella)
        self.undo_and_tell(moved)
        self.assertEqual(self.titles(), ['Move undone: 2 horses are back in Sandhills'])

    def test_only_the_horses_that_went_back_lose_their_notification(self):
        moved = self.move_and_tell(self.ace, self.clio)
        self.leaves(self.clio)
        self.undo_and_tell(moved)
        self.assertEqual(self.titles(), ['Clio moved'])

    def test_the_same_move_made_again_tells_people_again(self):
        self.undo_and_tell(self.move_and_tell(self.ace))
        self.move_and_tell(self.ace)
        self.assertEqual(self.titles(), ['Ace moved'])

    def test_a_second_slip_on_the_same_day_is_told_again(self):
        for _time in range(2):
            moved = self.move_and_tell(self.ace)
            Notification.objects.filter(recipient=self.groom, title='Ace moved').update(
                read_at=self.today_time())
            self.undo_and_tell(moved)
        self.assertEqual(self.titles(), ['Move undone: Ace is back in Sandhills'] * 2)

    def test_the_person_who_did_the_undo_is_not_told(self):
        NotificationPreference.objects.create(
            user=self.groom, category=Notification.Category.MOVEMENT, in_app=True, email=True)
        response = self.client.post(self.url, self.data(self.ace), **POPUP)
        link = undo_url_from_tags(next(
            message.extra_tags for message in response.wsgi_request._messages))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(link)
        self.assertEqual(self.titles(), ['Move undone: Ace is back in Sandhills'])
        self.assertEqual(self.titles(self.admin), [])

    def test_a_fault_in_the_notifications_does_not_stop_the_undo(self):
        moved = self.move_and_tell(self.ace)
        with mock.patch('notifications.centre._move_undone', side_effect=RuntimeError('boom')):
            restored, _failed, _gone = self.undo_and_tell(moved)
        self.assertEqual(len(restored), 1)
        self.assertEqual(self.where(self.ace), self.sandhills)

    def today_time(self):
        from django.utils import timezone
        return timezone.now()


class SingleMoveUndoTests(UndoFixture):
    """The move of one horse (core.views.horses.horse_move) has the same Undo."""

    def move_one(self, horse, location=None, popup=True, **data):
        response = self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': (location or self.clover).pk, 'move_date': self.today.isoformat(),
            'notes': '', **data,
        }, **(POPUP if popup else {}))
        self.assertEqual(response.status_code, 204 if popup else 302)
        return response

    def undo_link(self, response):
        link = next(
            undo_url_from_tags(message.extra_tags) for message in response.wsgi_request._messages
            if message.level_tag == 'success'
        )
        # The page shows the message, and that takes it out of the queue.
        self.client.get(reverse('help_index'))
        return link

    def share(self, horse, owner):
        return OwnershipShare.objects.create(
            horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)

    def test_the_moved_message_has_undo_and_it_puts_the_horse_back(self):
        stay = self.ace.placements.get()
        link = self.undo_link(self.move_one(self.ace))
        self.assertTrue(link.startswith(reverse('group_move_undo') + '?t='))
        self.assertEqual(self.where(self.ace), self.clover)
        response = self.client.post(link, {'next': reverse('horse_detail', args=[self.ace.pk])})
        self.assertRedirects(response, reverse('horse_detail', args=[self.ace.pk]))
        self.assertEqual(self.said(response), ['Move undone. Ace is back in Sandhills.'])
        now = self.ace.placements.get()
        self.assertEqual((now.pk, now.end_date, now.location), (stay.pk, None, self.sandhills))

    def test_the_full_page_form_has_it_also(self):
        response = self.move_one(self.ace, popup=False)
        self.assertTrue(self.undo_link(response))

    def test_a_move_that_changed_the_owner_gives_the_horse_back(self):
        share = self.share(self.ace, self.helen)
        link = self.undo_link(self.move_one(self.ace, new_owner=self.tom.pk))
        share.refresh_from_db()
        self.assertEqual(share.owner, self.tom)
        self.assertEqual(read_group_move_token(token_of(link), self.admin)['share_owner_id'], self.helen.pk)
        self.client.post(link)
        share.refresh_from_db()
        self.assertEqual(share.owner, self.helen)
        stay = self.ace.placements.get()
        self.assertEqual((stay.owner, stay.location), (self.helen, self.sandhills))

    def test_a_share_that_somebody_changed_after_the_move_is_left(self):
        share = self.share(self.ace, self.helen)
        link = self.undo_link(self.move_one(self.ace, new_owner=self.tom.pk))
        other = Owner.objects.create(name='Priya Shah')
        share.owner = other
        share.save()
        self.client.post(link)
        share.refresh_from_db()
        self.assertEqual(share.owner, other)
        self.assertEqual(self.where(self.ace), self.sandhills)

    def test_a_move_that_kept_the_owner_names_no_owner(self):
        self.share(self.ace, self.helen)
        link = self.undo_link(self.move_one(self.ace))
        self.assertIsNone(read_group_move_token(token_of(link), self.admin)['share_owner_id'])

    def test_a_horse_with_two_owners_keeps_its_shares(self):
        OwnershipShare.objects.create(horse=self.ace, owner=self.helen, share_percentage=60, is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.ace, owner=self.tom, share_percentage=40)
        link = self.undo_link(self.move_one(self.ace, new_owner=self.tom.pk))
        self.assertIsNone(read_group_move_token(token_of(link), self.admin)['share_owner_id'])
        self.client.post(link)
        self.assertEqual(
            sorted(self.ace.ownership_shares.values_list('owner__name', 'share_percentage')),
            [('Helen Marsh', 60), ('Tom Ellery', 40)])

    def test_a_stable_chosen_in_the_move_goes_with_it(self):
        barn = Location.objects.create(
            name='Top Barn', site='Somerford', location_type=Location.LocationType.BARN, layout='aisle')
        add_stables(barn, 4)
        link = self.undo_link(self.move_one(self.ace, location=barn, stable=barn.stables.get(label='2').pk))
        self.assertEqual(open_stay(Horse.objects.get(pk=self.ace.pk)).stable.label, '2')
        self.client.post(link)
        self.assertIsNone(open_stay(Horse.objects.get(pk=self.ace.pk)))
        self.assertEqual(self.where(self.ace), self.sandhills)
        # The stable is free again for another horse.
        self.assertFalse(barn.stables.get(label='2').stays.filter(end_date__isnull=True).exists())

    def test_the_expected_departure_date_is_as_it_was(self):
        leaves = self.today + timedelta(days=9)
        stay = self.ace.placements.get()
        stay.expected_departure = leaves
        stay.save()
        link = self.undo_link(self.move_one(self.ace, expected_departure=''))
        self.client.post(link)
        self.assertEqual(self.ace.placements.get().expected_departure, leaves)

    def test_a_rate_change_in_the_same_location_is_undone_with_the_usage_as_it_was(self):
        # The service allows it (a rate change at foaling is one): the
        # same location, a new stay. The location does not start to rest.
        before = usage_history(self.clover)
        new = PlacementService.move_horse(
            Horse.objects.get(pk=self.dot.pk), new_location=self.clover,
            move_date=self.today, new_rate_type=self.premium)
        restored, failed, _gone = PlacementService.undo_move_group(
            [new.pk], destination_id=self.clover.pk, move_date=self.today)
        self.assertEqual((len(restored), failed), (1, []))
        self.assertEqual(self.dot.placements.get().rate_type, self.grass)
        self.assertEqual(usage_history(self.clover), before)

    def test_a_horse_that_comes_back_to_the_yard_has_no_undo(self):
        # The move form also brings back a horse that left. That is an
        # arrival: there is no open stay to go back to.
        PlacementService.depart_horse(Horse.objects.get(pk=self.ace.pk), self.today - timedelta(days=3))
        response = self.move_one(
            Horse.objects.get(pk=self.ace.pk),
            new_owner=self.helen.pk, new_rate_type=self.grass.pk)
        self.assertEqual(self.undo_link(response), '')
        self.assertEqual(self.where(self.ace), self.clover)
