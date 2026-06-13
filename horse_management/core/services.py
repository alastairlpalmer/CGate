"""
Placement lifecycle services.

Encapsulates the business logic for horse arrivals, departures, and moves,
keeping views thin and logic testable.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Horse, Location, LocationUsagePeriod, OwnershipShare, Placement


class PlacementService:
    """Service for managing horse placement lifecycle."""

    @staticmethod
    @transaction.atomic
    def create_new_arrival(*, name, sex='', color='', date_of_birth=None,
                           sire_name='', passport_number='', has_passport=False,
                           owner, location, rate_type, arrival_date,
                           expected_departure=None, notes=''):
        """Create a new horse and place it at a location in one atomic step.

        Returns the created (horse, placement) tuple.
        """
        horse = Horse.objects.create(
            name=name,
            sex=sex,
            color=color,
            date_of_birth=date_of_birth,
            sire_name=sire_name,
            passport_number=passport_number,
            has_passport=has_passport,
            is_active=True,
        )
        OwnershipShare.objects.create(
            horse=horse,
            owner=owner,
            share_percentage=100,
            is_primary_contact=True,
        )
        placement = Placement(
            horse=horse,
            owner=owner,
            location=location,
            rate_type=rate_type,
            start_date=arrival_date,
            expected_departure=expected_departure,
            notes=notes,
        )
        placement.full_clean()
        placement.save()
        return horse, placement

    @staticmethod
    @transaction.atomic
    def move_horse(horse, *, new_location, move_date, new_owner=None,
                   new_rate_type=None, expected_departure=None, notes=''):
        """Move a horse to a new location.

        Ends the current placement and creates a new one.
        Returns the new placement.
        Raises ValidationError if the move is invalid.
        """
        current_placement = horse.current_placement

        # Resolve owner and rate type
        if not new_owner:
            new_owner = horse.primary_owner
        if not new_owner and current_placement:
            new_owner = current_placement.owner
        if not new_rate_type and current_placement:
            new_rate_type = current_placement.rate_type

        if not new_owner or not new_rate_type:
            raise ValidationError(
                "Owner and rate type are required when the horse has no current placement."
            )

        if current_placement and move_date <= current_placement.start_date:
            raise ValidationError(
                f"Move date must be after the current placement start date "
                f"({current_placement.start_date})."
            )

        new_placement = Placement(
            horse=horse,
            owner=new_owner,
            location=new_location,
            rate_type=new_rate_type,
            start_date=move_date,
            expected_departure=expected_departure,
            notes=notes,
        )

        old_location = current_placement.location if current_placement else None
        new_loc_was_empty = LocationUsageService._is_empty(new_location)

        # End current placement FIRST so overlap validation passes
        if current_placement:
            current_placement.end_date = move_date - timedelta(days=1)
            current_placement.save()
        new_placement.full_clean()
        new_placement.save()

        # Field emptied by the move rests from the move date; the destination
        # becomes a horses field if horses just arrived onto it.
        if old_location and old_location != new_location:
            LocationUsageService.rest_if_empty(old_location, move_date - timedelta(days=1))
        LocationUsageService.horses_arrived(new_location, move_date, was_empty=new_loc_was_empty)

        return new_placement

    @staticmethod
    @transaction.atomic
    def arrive_horse(horse, *, owner, location, rate_type, arrival_date,
                     expected_departure=None, notes=''):
        """Log a single horse arriving at a location.

        Returns the new placement.
        Raises ValidationError if the placement is invalid.
        """
        was_empty = LocationUsageService._is_empty(location)
        placement = Placement(
            horse=horse,
            owner=owner,
            location=location,
            rate_type=rate_type,
            start_date=arrival_date,
            expected_departure=expected_departure,
            notes=notes,
        )
        placement.full_clean()
        placement.save()
        LocationUsageService.horses_arrived(location, arrival_date, was_empty=was_empty)
        return placement

    @staticmethod
    @transaction.atomic
    def depart_horse(horse, departure_date):
        """Log a single horse departing.

        Sets end_date on current placement and deactivates horse if departure
        is today or in the past.
        Raises ValidationError if invalid.
        """
        current_placement = horse.current_placement
        if not current_placement:
            raise ValidationError(f"{horse.name} has no active placement.")

        if departure_date < current_placement.start_date:
            raise ValidationError(
                f"Departure date cannot be before arrival ({current_placement.start_date})."
            )

        location = current_placement.location
        current_placement.end_date = departure_date
        current_placement.save()

        if departure_date <= timezone.now().date():
            horse.is_active = False
            horse.save(update_fields=['is_active'])

        LocationUsageService.rest_if_empty(location, departure_date)

        return current_placement

    @staticmethod
    def confirm_departure(horse):
        """Mark a horse as departed (deactivate). Used for pending departures."""
        horse.is_active = False
        horse.save(update_fields=['is_active'])

    @staticmethod
    def cancel_departure(horse):
        """Undo a pending departure by re-opening the most recent ended placement."""
        placement = horse.placements.filter(
            end_date__isnull=False
        ).order_by('-end_date').first()
        if placement:
            placement.end_date = None
            placement.save()
            return placement
        return None

    @staticmethod
    def confirm_departures_bulk(horse_ids):
        """Confirm multiple horses as departed in one action.

        Returns the count of horses deactivated.
        """
        return Horse.objects.filter(
            pk__in=horse_ids, is_active=True
        ).update(is_active=False)

    @staticmethod
    @transaction.atomic
    def bulk_arrive(horses, *, owner, location, rate_type, arrival_date,
                    expected_departure=None, notes=''):
        """Log multiple horses arriving at a location.

        Returns (created_count, errors) tuple.
        """
        was_empty = LocationUsageService._is_empty(location)
        created = 0
        errors = []
        for horse in horses:
            placement = Placement(
                horse=horse,
                owner=owner,
                location=location,
                rate_type=rate_type,
                start_date=arrival_date,
                expected_departure=expected_departure,
                notes=notes,
            )
            try:
                placement.full_clean()
                placement.save()
                created += 1
            except ValidationError as e:
                errors.append(f"{horse.name}: {e}")
        if created:
            LocationUsageService.horses_arrived(location, arrival_date, was_empty=was_empty)
        return created, errors

    @staticmethod
    @transaction.atomic
    def bulk_depart(horse_ids, location, departure_date, notes=''):
        """Log departure of selected horses from a location.

        Returns the count of departed horses.
        """
        departed = 0
        depart_errors = []
        placements = Placement.objects.filter(
            horse_id__in=horse_ids,
            location=location,
            end_date__isnull=True,
        ).select_related('horse')

        for placement in placements:
            if departure_date < placement.start_date:
                depart_errors.append(
                    f"{placement.horse.name}: departure date cannot be before "
                    f"arrival ({placement.start_date})."
                )
                continue
            placement.end_date = departure_date
            if notes:
                placement.notes = (
                    (placement.notes or '') + f"\nDeparted: {notes}"
                    if placement.notes else notes
                )
            placement.save()
            if departure_date <= timezone.now().date():
                placement.horse.is_active = False
                placement.horse.save(update_fields=['is_active'])
            departed += 1

        if departed:
            LocationUsageService.rest_if_empty(location, departure_date)

        return departed, depart_errors


class LocationUsageService:
    """Service for recording field (Location) usage history over time.

    Maintains a chain of LocationUsagePeriod rows — one open period per
    location — and keeps Location.usage in sync. Usage changes are driven both
    manually (staff log a change, optionally backdated) and automatically
    (horses arriving onto an empty field, or a field emptying out).
    """

    @staticmethod
    @transaction.atomic
    def set_usage(location, *, usage, change_date,
                  source=LocationUsagePeriod.Source.MANUAL, notes=''):
        """Change a location's usage as of change_date.

        Closes the current open period (end_date = change_date - 1 day),
        opens a new one, and keeps Location.usage in sync. The single writer
        of usage history.

        Returns the new LocationUsagePeriod, or None if usage is unchanged.
        Raises ValidationError if change_date is not after the current start.
        """
        current = location.usage_periods.filter(end_date__isnull=True).first()

        # No-op: already in this usage state.
        if current and current.usage == usage:
            return None

        if current and change_date <= current.start_date:
            raise ValidationError(
                f"Change date must be after the current usage period start "
                f"({current.start_date})."
            )

        if current:
            current.end_date = change_date - timedelta(days=1)
            current.save()

        new_period = LocationUsagePeriod(
            location=location,
            usage=usage,
            start_date=change_date,
            source=source,
            notes=notes,
        )
        new_period.full_clean()
        new_period.save()

        if location.usage != usage:
            location.usage = usage
            location.save(update_fields=['usage'])

        return new_period

    @staticmethod
    def _is_empty(location, exclude_horse_ids=None):
        """True if the location has no active (open) placements."""
        qs = Placement.objects.filter(location=location, end_date__isnull=True)
        if exclude_horse_ids:
            qs = qs.exclude(horse_id__in=exclude_horse_ids)
        return not qs.exists()

    @staticmethod
    def _set_usage_auto(location, usage, change_date):
        """Best-effort automatic usage transition.

        Never breaks the calling placement operation — if the change can't be
        recorded cleanly (e.g. backdated before the current period start), the
        history is simply skipped.
        """
        try:
            LocationUsageService.set_usage(
                location,
                usage=usage,
                change_date=change_date,
                source=LocationUsagePeriod.Source.AUTO,
            )
        except ValidationError:
            pass

    @staticmethod
    def horses_arrived(location, arrival_date, *, was_empty):
        """Auto-mark a field as holding horses when horses arrive onto it.

        Only fires when the field was previously empty, so manual states such
        as 'mixed' or 'hay' on an occupied field are preserved.
        """
        if was_empty:
            LocationUsageService._set_usage_auto(
                location, Location.Usage.HORSES, arrival_date
            )

    @staticmethod
    def rest_if_empty(location, last_occupied_date):
        """Auto-mark a field as rested once the last horse has left.

        The field is empty from the day after the final occupied day, so the
        rest period starts then.
        """
        if LocationUsageService._is_empty(location):
            LocationUsageService._set_usage_auto(
                location, Location.Usage.RESTED,
                last_occupied_date + timedelta(days=1),
            )
