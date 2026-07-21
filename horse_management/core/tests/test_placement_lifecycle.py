"""Tests for the arrival/move/departure lifecycle keeping Horse.is_active
in step with placements.

Regression tests for production bugs where horses returning to the yard
stayed flagged as Departed (arrive/move didn't reactivate them), and where
horses could be stranded inactive-with-an-open-placement — showing Move and
Depart buttons on the record page while the search dropdown said Departed.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import HorseForm
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from core.services import PlacementService


class LifecycleTestCase(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.other_location = Location.objects.create(name='Bottom Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='ALIHUNTER')

    def _departed_horse(self):
        """Horse that left the yard two weeks ago (closed placement, inactive)."""
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today - timedelta(days=14),
        )
        self.horse.is_active = False
        self.horse.save(update_fields=['is_active'])
        return self.horse

    def _stranded_horse(self):
        """Horse flagged departed while its placement is still open."""
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
        )
        Horse.objects.filter(pk=self.horse.pk).update(is_active=False)
        self.horse.refresh_from_db()
        return self.horse


class ArriveHorseTests(LifecycleTestCase):
    def test_arrival_reactivates_departed_horse(self):
        horse = self._departed_horse()
        placement = PlacementService.arrive_horse(
            horse, owner=self.owner, location=self.location,
            rate_type=self.rate, arrival_date=self.today,
        )
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertIsNone(placement.end_date)

    def test_arrival_backdated_before_recorded_departure_supersedes_it(self):
        # Snowy's case: departed dated today (or later), then logged as
        # arriving back on an earlier date. The return supersedes the
        # recorded departure instead of being rejected as an overlap.
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today,  # departure recorded today
        )
        self.horse.is_active = False
        self.horse.save(update_fields=['is_active'])

        arrival = self.today - timedelta(days=4)  # e.g. back on 10/7
        placement = PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, arrival_date=arrival,
        )
        old.refresh_from_db()
        self.horse.refresh_from_db()
        # Old stay now ends the day before the return — no double billing
        self.assertEqual(old.end_date, arrival - timedelta(days=1))
        self.assertEqual(placement.start_date, arrival)
        self.assertIsNone(placement.end_date)
        self.assertTrue(self.horse.is_active)

    def test_arrival_same_day_as_departure_supersedes_it(self):
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today,
        )
        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, arrival_date=self.today,
        )
        old.refresh_from_db()
        self.assertEqual(old.end_date, self.today - timedelta(days=1))

    def test_arrival_before_previous_stay_started_is_rejected(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=5),
            end_date=self.today,
        )
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            PlacementService.arrive_horse(
                self.horse, owner=self.owner, location=self.other_location,
                rate_type=self.rate,
                arrival_date=self.today - timedelta(days=10),
            )

    def test_move_back_supersedes_recorded_departure(self):
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today,
        )
        Horse.objects.filter(pk=self.horse.pk).update(is_active=False)
        self.horse.refresh_from_db()

        arrival = self.today - timedelta(days=4)
        PlacementService.move_horse(
            self.horse, new_location=self.other_location, move_date=arrival,
            new_owner=self.owner, new_rate_type=self.rate,
        )
        old.refresh_from_db()
        self.horse.refresh_from_db()
        self.assertEqual(old.end_date, arrival - timedelta(days=1))
        self.assertTrue(self.horse.is_active)

    def test_arrived_horse_returns_to_current_list(self):
        horse = self._departed_horse()
        PlacementService.arrive_horse(
            horse, owner=self.owner, location=self.location,
            rate_type=self.rate, arrival_date=self.today,
        )
        current = Horse.objects.filter(
            is_active=True, placements__end_date__isnull=True,
        )
        self.assertIn(horse, current)


class MoveHorseTests(LifecycleTestCase):
    def test_move_reactivates_stranded_horse(self):
        horse = self._stranded_horse()
        new_placement = PlacementService.move_horse(
            horse, new_location=self.other_location, move_date=self.today,
        )
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertEqual(new_placement.location, self.other_location)
        # Old placement closed the day before the move
        old = horse.placements.exclude(pk=new_placement.pk).get()
        self.assertEqual(old.end_date, self.today - timedelta(days=1))

    def test_move_keeps_active_horse_active(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.other_location, move_date=self.today,
        )
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)


class DepartureConfirmTests(LifecycleTestCase):
    def test_confirm_departure_refuses_open_placement(self):
        # A horse with an open placement is still on the yard — confirming
        # it as departed must be refused, not silently close the live
        # placement (that departed horses that had merely moved fields).
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        self.assertFalse(PlacementService.confirm_departure(self.horse))
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)
        self.assertTrue(
            self.horse.placements.filter(end_date__isnull=True).exists()
        )

    def test_confirm_departure_deactivates_unplaced_horse(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
            end_date=self.today - timedelta(days=1),
        )
        self.assertTrue(PlacementService.confirm_departure(self.horse))
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_active)

    def test_bulk_confirm_skips_open_placements(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        count = PlacementService.confirm_departures_bulk([self.horse.pk])
        self.assertEqual(count, 0)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)
        self.assertTrue(
            self.horse.placements.filter(end_date__isnull=True).exists()
        )

    def test_cancel_departure_never_reopens_older_placement_when_placed(self):
        # An open placement means re-opening an older one would double-place
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=90),
            end_date=self.today - timedelta(days=60),
        )
        current = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        result = PlacementService.cancel_departure(self.horse)
        self.assertEqual(result.pk, current.pk)
        old.refresh_from_db()
        self.assertEqual(old.end_date, self.today - timedelta(days=60))

    def test_cancel_departure_repairs_stranded_horse(self):
        # Pridie's case: flagged departed while her placement is still open —
        # undoing the departure just clears the flag.
        horse = self._stranded_horse()
        result = PlacementService.cancel_departure(horse)
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertEqual(result.location, self.location)
        self.assertEqual(horse.placements.count(), 1)

    def test_cancel_departure_undoes_auto_rest(self):
        from core.models import LocationUsagePeriod
        LocationUsagePeriod.objects.create(
            location=self.location, usage=Location.Usage.HORSES,
            start_date=self.today - timedelta(days=60), source='auto',
        )
        self.location.usage = Location.Usage.HORSES
        self.location.save(update_fields=['usage'])
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.depart_horse(self.horse, self.today - timedelta(days=2))
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.RESTED)

        PlacementService.cancel_departure(self.horse)
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.HORSES)
        # The auto rest period is gone; the horses period is open again
        open_period = self.location.usage_periods.get(end_date__isnull=True)
        self.assertEqual(open_period.usage, Location.Usage.HORSES)
        self.assertFalse(
            self.location.usage_periods.filter(
                usage=Location.Usage.RESTED
            ).exists()
        )

    def test_cancel_departure_reactivates_horse(self):
        placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
            end_date=self.today,
        )
        Horse.objects.filter(pk=self.horse.pk).update(is_active=False)
        self.horse.refresh_from_db()

        reopened = PlacementService.cancel_departure(self.horse)
        self.assertEqual(reopened.pk, placement.pk)
        self.assertIsNone(reopened.end_date)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)


class HorseFormDeactivationGuardTests(LifecycleTestCase):
    def test_cannot_untick_active_while_placement_open(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        form = HorseForm(
            data={'name': self.horse.name, 'has_passport': 'on'},
            instance=self.horse,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('is_active', form.errors)

    def test_stranded_horse_remains_editable(self):
        # Already-inconsistent records must not be blocked from unrelated edits.
        horse = self._stranded_horse()
        form = HorseForm(
            data={'name': horse.name, 'has_passport': 'on', 'notes': 'needs rug'},
            instance=horse,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_deactivating_with_no_open_placement_allowed(self):
        horse = self._departed_horse()
        horse.is_active = True
        horse.save(update_fields=['is_active'])
        form = HorseForm(
            data={'name': horse.name, 'has_passport': 'on'},
            instance=horse,
        )
        self.assertTrue(form.is_valid(), form.errors)


class ArriveMoveViewTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_admin()
        self.client.force_login(self.staff)

    def test_arrive_view_saves_reactivates_and_returns_to_list(self):
        horse = self._departed_horse()
        response = self.client.post(
            reverse('horse_arrive', args=[horse.pk]),
            {
                'location': self.location.pk,
                'owner': self.owner.pk,
                'rate_type': self.rate.pk,
                'arrival_date': self.today.isoformat(),
                'notes': '',
            },
        )
        self.assertRedirects(response, reverse('horse_list'))
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertTrue(horse.placements.filter(end_date__isnull=True).exists())

    def test_move_view_returns_to_list(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        response = self.client.post(
            reverse('horse_move', args=[self.horse.pk]),
            {
                'new_location': self.other_location.pk,
                'move_date': self.today.isoformat(),
                'notes': '',
            },
        )
        self.assertRedirects(response, reverse('horse_list'))

    def test_reactivate_repairs_stranded_horse(self):
        horse = self._stranded_horse()
        response = self.client.post(reverse('horse_reactivate', args=[horse.pk]))
        self.assertRedirects(response, reverse('horse_detail', args=[horse.pk]))
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        # The existing open placement is untouched — no new rows, no new dates
        self.assertEqual(horse.placements.count(), 1)
        self.assertTrue(horse.placements.filter(end_date__isnull=True).exists())

    def test_reactivate_refuses_horse_without_placement(self):
        horse = self._departed_horse()
        self.client.post(reverse('horse_reactivate', args=[horse.pk]))
        horse.refresh_from_db()
        self.assertFalse(horse.is_active)

    def test_bulk_move_moves_selected_horses(self):
        horse2 = Horse.objects.create(name='SNOWY')
        for h in (self.horse, horse2):
            Placement.objects.create(
                horse=h, owner=self.owner, location=self.location,
                rate_type=self.rate, start_date=self.today - timedelta(days=30),
            )
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'move',
                'horse_ids': [self.horse.pk, horse2.pk],
                'new_location': self.other_location.pk,
                'move_date': self.today.isoformat(),
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 204)
        for h in (self.horse, horse2):
            open_placement = h.placements.get(end_date__isnull=True)
            self.assertEqual(open_placement.location, self.other_location)
            self.assertEqual(open_placement.start_date, self.today)
            # Old placement closed the day before the move
            self.assertTrue(
                h.placements.filter(
                    location=self.location,
                    end_date=self.today - timedelta(days=1),
                ).exists()
            )

    def test_bulk_move_reports_per_horse_failures(self):
        # First horse can move; second arrived today so a same-day move is invalid.
        horse2 = Horse.objects.create(name='SNOWY')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        Placement.objects.create(
            horse=horse2, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today,
        )
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'move',
                'horse_ids': [self.horse.pk, horse2.pk],
                'new_location': self.other_location.pk,
                'move_date': self.today.isoformat(),
                'notes': '',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.horse.placements.get(end_date__isnull=True).location,
            self.other_location,
        )
        # The failed horse keeps its original placement
        self.assertEqual(
            horse2.placements.get(end_date__isnull=True).location,
            self.location,
        )

    def test_bulk_restore_reopens_wrongly_departed_horses(self):
        # The mass-departure accident: two horses departed together by
        # mistake, one horse still correctly placed.
        horse2 = Horse.objects.create(name='SNOWY')
        placed = Horse.objects.create(name='STAYS')
        for h in (self.horse, horse2):
            Placement.objects.create(
                horse=h, owner=self.owner, location=self.location,
                rate_type=self.rate,
                start_date=self.today - timedelta(days=30),
                end_date=self.today,
            )
        Horse.objects.filter(pk__in=[self.horse.pk, horse2.pk]).update(is_active=False)
        Placement.objects.create(
            horse=placed, owner=self.owner, location=self.other_location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )

        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'restore',
                'horse_ids': [self.horse.pk, horse2.pk, placed.pk],
            },
        )
        self.assertEqual(response.status_code, 204)
        for h in (self.horse, horse2):
            h.refresh_from_db()
            self.assertTrue(h.is_active)
            open_placement = h.placements.get(end_date__isnull=True)
            self.assertEqual(open_placement.location, self.location)
            self.assertEqual(
                open_placement.start_date, self.today - timedelta(days=30)
            )
        # The correctly-placed horse is untouched
        self.assertEqual(placed.placements.count(), 1)

    def test_bulk_restore_repairs_stranded_horse(self):
        stranded = self._stranded_horse()
        response = self.client.post(
            reverse('bulk_health_apply'),
            {'action_type': 'restore', 'horse_ids': [stranded.pk]},
        )
        self.assertEqual(response.status_code, 204)
        stranded.refresh_from_db()
        self.assertTrue(stranded.is_active)
        self.assertEqual(stranded.placements.count(), 1)

    def test_bulk_departure_before_arrival_reports_error_instead_of_500(self):
        # Bella's case: placement started after the chosen departure date.
        # The bulk endpoint must report it by name, not crash with a 500.
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=2),
        )
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'actual_departure',
                'horse_ids': [self.horse.pk],
                'date': (self.today - timedelta(days=4)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)
        self.assertTrue(
            self.horse.placements.filter(end_date__isnull=True).exists()
        )

    def test_bulk_departure_departs_valid_horses_and_reports_invalid(self):
        late_arrival = Horse.objects.create(name='BELLA')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        Placement.objects.create(
            horse=late_arrival, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today,
        )
        departure = self.today - timedelta(days=4)
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'actual_departure',
                'horse_ids': [self.horse.pk, late_arrival.pk],
                'date': departure.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        late_arrival.refresh_from_db()
        # Valid horse departed and deactivated
        self.assertFalse(self.horse.is_active)
        self.assertEqual(
            self.horse.placements.get().end_date, departure
        )
        # Invalid horse untouched
        self.assertTrue(late_arrival.is_active)
        self.assertTrue(
            late_arrival.placements.filter(end_date__isnull=True).exists()
        )

    def test_bulk_expected_departure_still_works(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        future = self.today + timedelta(days=14)
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'expected_departure',
                'horse_ids': [self.horse.pk],
                'date': future.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.horse.placements.get().expected_departure, future
        )

    def test_bulk_restore_forbidden_for_viewers(self):
        # Viewer has full health access but only view on locations — placement
        # actions through the bulk endpoint must still be denied.
        viewer = make_viewer(username='viewer2')
        self.client.force_login(viewer)
        response = self.client.post(
            reverse('bulk_health_apply'),
            {'action_type': 'restore', 'horse_ids': [self.horse.pk]},
        )
        self.assertEqual(response.status_code, 403)

    def test_bulk_move_forbidden_for_viewers(self):
        viewer = make_viewer(username='viewer')
        self.client.force_login(viewer)
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'move',
                'horse_ids': [self.horse.pk],
                'new_location': self.other_location.pk,
                'move_date': self.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_failed_arrival_rerenders_with_visible_error(self):
        # Overlapping arrival: the horse is still openly placed elsewhere.
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        response = self.client.post(
            reverse('horse_arrive', args=[self.horse.pk]),
            {
                'location': self.other_location.pk,
                'owner': self.owner.pk,
                'rate_type': self.rate.pk,
                'arrival_date': self.today.isoformat(),
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        messages = [m.message for m in response.context['messages']]
        self.assertTrue(any('already has a placement' in m for m in messages))


class PlacementEditDeleteTests(LifecycleTestCase):
    """Timeline corrections: editing and deleting placement history rows."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_admin())
        self.placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=2),
        )

    def _form_data(self, **overrides):
        data = {
            'horse': self.horse.pk,
            'owner': self.owner.pk,
            'location': self.location.pk,
            'rate_type': self.rate.pk,
            'start_date': self.placement.start_date.isoformat(),
            'end_date': '',
            'expected_departure': '',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_edit_returns_to_next_url(self):
        next_url = reverse('horse_detail', args=[self.horse.pk])
        corrected_start = self.today - timedelta(days=6)
        response = self.client.post(
            reverse('placement_update', args=[self.placement.pk])
            + f'?next={next_url}',
            self._form_data(start_date=corrected_start.isoformat()),
        )
        self.assertRedirects(response, next_url)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.start_date, corrected_start)

    def test_edit_ignores_offsite_next_url(self):
        response = self.client.post(
            reverse('placement_update', args=[self.placement.pk])
            + '?next=https://evil.example/',
            self._form_data(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], reverse('location_list') + '?tab=history'
        )

    def test_edit_overlap_shows_error_not_500(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
            end_date=self.today - timedelta(days=10),
        )
        # Stretch the current stay back over the older one
        response = self.client.post(
            reverse('placement_update', args=[self.placement.pk]),
            self._form_data(
                start_date=(self.today - timedelta(days=20)).isoformat()
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has a placement')

    def test_delete_removes_placement_and_returns_to_horse(self):
        next_url = reverse('horse_detail', args=[self.horse.pk])
        response = self.client.post(
            reverse('placement_delete', args=[self.placement.pk])
            + f'?next={next_url}',
        )
        self.assertRedirects(response, next_url)
        self.assertFalse(
            Placement.objects.filter(pk=self.placement.pk).exists()
        )

    def test_delete_forbidden_for_viewers(self):
        self.client.force_login(make_viewer())
        response = self.client.post(
            reverse('placement_delete', args=[self.placement.pk]),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Placement.objects.filter(pk=self.placement.pk).exists()
        )


class SupersedeTrimLimitTests(LifecycleTestCase):
    """Batch 2: superseding a departure must never silently rewrite weeks
    of recorded (potentially invoiced) history."""

    def test_large_history_rewrite_refused(self):
        from django.core.exceptions import ValidationError
        end = self.today - timedelta(days=5)
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=200),
            end_date=end,
        )
        with self.assertRaises(ValidationError) as ctx:
            PlacementService.arrive_horse(
                self.horse, owner=self.owner, location=self.other_location,
                rate_type=self.rate,
                arrival_date=end - timedelta(days=60),
            )
        self.assertIn('rewrite', str(ctx.exception))
        old.refresh_from_db()
        self.assertEqual(old.end_date, end)

    def test_small_trim_allowed_and_reported(self):
        old = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today,
        )
        arrival = self.today - timedelta(days=4)
        placement = PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, arrival_date=arrival,
        )
        trimmed = placement.superseded_trim
        self.assertIsNotNone(trimmed)
        self.assertEqual(trimmed.pk, old.pk)
        self.assertEqual(trimmed.superseded_from, self.today)
        self.assertEqual(trimmed.end_date, arrival - timedelta(days=1))

    def test_future_departure_days_do_not_count_as_history(self):
        # 60 planned (future) days being trimmed is fine — only elapsed
        # days count towards the rewrite limit.
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today + timedelta(days=60),
        )
        placement = PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, arrival_date=self.today,
        )
        self.assertIsNotNone(placement.superseded_trim)


class SupersedeUsageRepairTests(LifecycleTestCase):
    """Batch 2: superseding a departure repairs the old field's usage chain."""

    def _occupied_location(self, location, start):
        from core.models import LocationUsagePeriod
        LocationUsagePeriod.objects.create(
            location=location, usage=Location.Usage.HORSES,
            start_date=start, source='auto',
        )
        location.usage = Location.Usage.HORSES
        location.save(update_fields=['usage'])

    def test_same_field_return_unrests_field(self):
        self._occupied_location(self.location, self.today - timedelta(days=60))
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.depart_horse(self.horse, self.today)
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.RESTED)

        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, arrival_date=self.today,
        )
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.HORSES)
        self.assertFalse(
            self.location.usage_periods.filter(
                usage=Location.Usage.RESTED
            ).exists()
        )

    def test_different_field_return_backdates_rest(self):
        self._occupied_location(self.location, self.today - timedelta(days=60))
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.depart_horse(self.horse, self.today)

        arrival = self.today - timedelta(days=3)
        PlacementService.arrive_horse(
            self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate, arrival_date=arrival,
        )
        rest = self.location.usage_periods.get(end_date__isnull=True)
        self.assertEqual(rest.usage, Location.Usage.RESTED)
        # Field actually emptied when the trimmed stay ended (arrival - 1),
        # so the rest starts the day after that: the arrival date itself.
        self.assertEqual(rest.start_date, arrival)
        previous = self.location.usage_periods.get(
            usage=Location.Usage.HORSES
        )
        self.assertEqual(previous.end_date, arrival - timedelta(days=1))


class ModelChokePointTests(LifecycleTestCase):
    """Batch 2: Placement.save/delete and Horse.clean enforce the lifecycle
    invariant for every write path (forms, admin, future code)."""

    def test_creating_open_placement_reactivates_horse(self):
        horse = self._departed_horse()
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today,
        )
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)

    def test_reopening_placement_reactivates_horse(self):
        horse = self._departed_horse()
        placement = horse.placements.get()
        placement.end_date = None
        placement.save()
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)

    def test_closing_placement_rests_emptied_field(self):
        from core.models import LocationUsagePeriod
        LocationUsagePeriod.objects.create(
            location=self.location, usage=Location.Usage.HORSES,
            start_date=self.today - timedelta(days=60), source='auto',
        )
        self.location.usage = Location.Usage.HORSES
        self.location.save(update_fields=['usage'])
        placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        placement.end_date = self.today
        placement.save()
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.RESTED)

    def test_deleting_open_placement_rests_emptied_field(self):
        from core.models import LocationUsagePeriod
        LocationUsagePeriod.objects.create(
            location=self.location, usage=Location.Usage.HORSES,
            start_date=self.today - timedelta(days=60), source='auto',
        )
        self.location.usage = Location.Usage.HORSES
        self.location.save(update_fields=['usage'])
        placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        placement.delete()
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.RESTED)

    def test_same_location_move_keeps_field_on_horses(self):
        from core.models import LocationUsagePeriod
        LocationUsagePeriod.objects.create(
            location=self.location, usage=Location.Usage.HORSES,
            start_date=self.today - timedelta(days=60), source='auto',
        )
        self.location.usage = Location.Usage.HORSES
        self.location.save(update_fields=['usage'])
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.location, move_date=self.today,
        )
        self.location.refresh_from_db()
        self.assertEqual(self.location.usage, Location.Usage.HORSES)

    def test_horse_full_clean_blocks_deactivation_when_placed(self):
        from django.core.exceptions import ValidationError
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        self.horse.is_active = False
        with self.assertRaises(ValidationError) as ctx:
            self.horse.full_clean()
        self.assertIn('is_active', ctx.exception.message_dict)

    def test_placement_update_view_reopen_reactivates_horse(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(make_admin(username='choke-admin'))
        horse = self._departed_horse()
        placement = horse.placements.get()
        response = self.client.post(
            reverse('placement_update', args=[placement.pk]),
            {
                'horse': horse.pk,
                'owner': self.owner.pk,
                'location': self.location.pk,
                'rate_type': self.rate.pk,
                'start_date': placement.start_date.isoformat(),
                'end_date': '',
                'expected_departure': '',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertTrue(horse.placements.filter(end_date__isnull=True).exists())


class PendingDeparturesWidgetTests(LifecycleTestCase):
    """The widget offers one confirm-everything button and honest per-group
    labels ('Confirm' / 'Confirm N', never 'Confirm All' on one horse)."""

    def setUp(self):
        super().setUp()
        from core.models import DashboardPreference
        self.admin = make_admin(username='dash-admin')
        pref = DashboardPreference.get_for(self.admin)
        pref.layout = {'pending_departures': {'visible': True, 'order': 0}}
        pref.save()
        self.client.force_login(self.admin)

    def _pending(self, horse, days_ago_end):
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today - timedelta(days=days_ago_end),
        )

    def test_single_horse_group_says_confirm_not_confirm_all(self):
        self._pending(self.horse, 1)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Confirm All')
        self.assertContains(response, 'Confirm')

    def test_confirm_everything_button_lists_all_pending_horses(self):
        horse2 = Horse.objects.create(name='SNOWY')
        self._pending(self.horse, 1)
        self._pending(horse2, 2)  # different date → different group
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Confirm all 2')

        response = self.client.post(
            reverse('confirm_departures_bulk'),
            {'horse_ids': [self.horse.pk, horse2.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.horse.refresh_from_db()
        horse2.refresh_from_db()
        self.assertFalse(self.horse.is_active)
        self.assertFalse(horse2.is_active)

    def test_no_confirm_everything_button_for_single_pending_horse(self):
        self._pending(self.horse, 1)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Confirm all 1')


class PendingDeparturesSelectionTests(LifecycleTestCase):
    """Regression tests: the widget must never list (or the services depart)
    a horse that still has an open placement. Every field move leaves a
    closed placement behind, and the old query matched all of them —
    'Confirm all' then closed the live placement of every horse that had
    ever moved fields."""

    def setUp(self):
        super().setUp()
        from core.models import DashboardPreference
        self.admin = make_admin(username='pending-admin')
        pref = DashboardPreference.get_for(self.admin)
        pref.layout = {'pending_departures': {'visible': True, 'order': 0}}
        pref.save()
        self.client.force_login(self.admin)

    def _moved_horse(self):
        """Active horse standing in other_location after a field move —
        has a closed historical placement from the move."""
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.other_location,
            move_date=self.today - timedelta(days=10),
        )
        self.horse.refresh_from_db()
        return self.horse

    def test_moved_horse_is_not_a_pending_departure(self):
        self._moved_horse()
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'pending-departures-card')

    def test_confirm_departure_refuses_currently_placed_horse(self):
        horse = self._moved_horse()
        self.assertFalse(PlacementService.confirm_departure(horse))
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertTrue(horse.placements.filter(end_date__isnull=True).exists())

    def test_bulk_confirm_skips_placed_horses_and_counts_honestly(self):
        moved = self._moved_horse()
        pending = Horse.objects.create(name='LEAVER')
        Placement.objects.create(
            horse=pending, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
            end_date=self.today - timedelta(days=1),
        )
        count = PlacementService.confirm_departures_bulk([moved.pk, pending.pk])
        self.assertEqual(count, 1)
        moved.refresh_from_db()
        pending.refresh_from_db()
        self.assertTrue(moved.is_active)
        self.assertTrue(moved.placements.filter(end_date__isnull=True).exists())
        self.assertFalse(pending.is_active)

    def test_horse_with_move_history_appears_once_under_latest_departure(self):
        # Departed horse that also moved fields while it was here: two
        # closed placements, only the latest one is the departure.
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
            end_date=self.today - timedelta(days=30),
        )
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.other_location,
            rate_type=self.rate,
            start_date=self.today - timedelta(days=29),
            end_date=self.today - timedelta(days=2),
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'pending-departures-card')
        departures = response.context['pending_departures']
        all_ids = [pk for g in departures for pk in g['horse_ids']]
        self.assertEqual(all_ids, [str(self.horse.pk)])
        self.assertEqual(departures[0]['date'], self.today - timedelta(days=2))


class LogoutMethodTests(TestCase):
    """Django 5's LogoutView is POST-only; the UI must render logout as a
    POST form, never a GET link (a GET link 405s and signs nobody out)."""

    def test_get_logout_is_rejected(self):
        admin = make_admin(username='logout-admin')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)

    def test_post_logout_signs_out(self):
        admin = make_admin(username='logout-admin2')
        self.client.force_login(admin)
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_base_template_renders_logout_as_post_form(self):
        admin = make_admin(username='logout-admin3')
        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard'))
        content = response.content.decode()
        self.assertIn(f'action="{reverse("logout")}"', content)
        self.assertNotIn(f'href="{reverse("logout")}"', content)


class FutureDepartureTests(LifecycleTestCase):
    """A future-dated departure must not close the live placement — that made
    the horse vanish from its field, the active list and capacity counts for
    days while it was still standing on the yard. It becomes a scheduled
    departure (expected_departure) instead."""

    def _placed_horse(self):
        return Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )

    def test_future_departure_schedules_instead_of_closing(self):
        self._placed_horse()
        friday = self.today + timedelta(days=4)
        placement = PlacementService.depart_horse(self.horse, friday)
        self.assertIsNone(placement.end_date)
        self.assertEqual(placement.expected_departure, friday)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_active)
        # Still current everywhere: open placement intact
        self.assertTrue(
            self.horse.placements.filter(end_date__isnull=True).exists()
        )

    def test_today_departure_still_closes_and_deactivates(self):
        self._placed_horse()
        placement = PlacementService.depart_horse(self.horse, self.today)
        self.assertEqual(placement.end_date, self.today)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_active)


class ArrivalOwnerShareSyncTests(LifecycleTestCase):
    """Arriving under a different owner must move a singly-owned horse's
    ownership share (like move_horse already did) — otherwise
    Horse.current_owner keeps pointing at the old owner and later health/
    billing costs are invoiced to whoever owned the horse before it was
    sold."""

    def test_arrival_moves_single_ownership_share_to_new_owner(self):
        from core.models import OwnershipShare
        from decimal import Decimal
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.owner,
            share_percentage=Decimal('100.00'), is_primary_contact=True,
        )
        new_owner = Owner.objects.create(name='New Owner')
        PlacementService.arrive_horse(
            self.horse, owner=new_owner, location=self.location,
            rate_type=self.rate, arrival_date=self.today,
        )
        share = self.horse.ownership_shares.get()
        self.assertEqual(share.owner, new_owner)
        self.assertEqual(self.horse.current_owner, new_owner)

    def test_arrival_never_touches_fractional_co_ownership(self):
        from core.models import OwnershipShare
        from decimal import Decimal
        co_owner = Owner.objects.create(name='Co Owner')
        OwnershipShare.objects.create(
            horse=self.horse, owner=self.owner,
            share_percentage=Decimal('50.00'), is_primary_contact=True,
        )
        OwnershipShare.objects.create(
            horse=self.horse, owner=co_owner,
            share_percentage=Decimal('50.00'),
        )
        new_owner = Owner.objects.create(name='Buyer')
        PlacementService.arrive_horse(
            self.horse, owner=new_owner, location=self.location,
            rate_type=self.rate, arrival_date=self.today,
        )
        owners = set(
            self.horse.ownership_shares.values_list('owner__name', flat=True)
        )
        self.assertEqual(owners, {'Jo Bloggs', 'Co Owner'})


class HorseListCountTests(LifecycleTestCase):
    """The Current/Departed tab badges must use NOT EXISTS(open placement)
    semantics — the old aggregate counted every horse that had ever moved
    fields as departed."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_admin(username='count-admin'))

    def test_moved_horse_counts_as_current_not_departed(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.move_horse(
            self.horse, new_location=self.other_location, move_date=self.today,
        )
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response.context['total_current'], 1)
        self.assertEqual(response.context['total_departed'], 0)

    def test_unplaced_active_horse_counts_as_departed_limbo(self):
        # Never placed: shows on the Departed tab, so it must count there too.
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response.context['total_current'], 0)
        self.assertEqual(response.context['total_departed'], 1)


class ToastContainerTests(TestCase):
    """The toast container must always render (with its stable id) so the
    boosted-navigation handler can lift fresh messages out of the full
    response — hx-select only swaps #main-content."""

    def test_toast_container_present_without_messages(self):
        self.client.force_login(make_admin(username='toast-admin'))
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'id="toast-container"')
