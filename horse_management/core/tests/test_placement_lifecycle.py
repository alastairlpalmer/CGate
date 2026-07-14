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
    def test_confirm_departure_closes_open_placement(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        PlacementService.confirm_departure(self.horse)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_active)
        self.assertFalse(
            self.horse.placements.filter(end_date__isnull=True).exists()
        )

    def test_bulk_confirm_closes_open_placements(self):
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        count = PlacementService.confirm_departures_bulk([self.horse.pk])
        self.assertEqual(count, 1)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_active)
        self.assertFalse(
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
