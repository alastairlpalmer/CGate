"""Tests for the arrival/move/departure lifecycle keeping Horse.is_active
in step with placements.

Regression tests for production bugs where horses returning to the yard
stayed flagged as Departed (arrive/move didn't reactivate them), and where
horses could be stranded inactive-with-an-open-placement — showing Move and
Depart buttons on the record page while the search dropdown said Departed.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import HorseForm
from core.models import Horse, Location, Owner, Placement, RateType
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
        self.staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
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
