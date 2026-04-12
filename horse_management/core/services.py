"""
Placement lifecycle services.

Encapsulates the business logic for horse arrivals, departures, and moves,
keeping views thin and logic testable.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Horse, OwnershipShare, Placement


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

        # End current placement FIRST so overlap validation passes
        if current_placement:
            current_placement.end_date = move_date - timedelta(days=1)
            current_placement.save()
        new_placement.full_clean()
        new_placement.save()

        return new_placement

    @staticmethod
    @transaction.atomic
    def arrive_horse(horse, *, owner, location, rate_type, arrival_date,
                     expected_departure=None, notes=''):
        """Log a single horse arriving at a location.

        Returns the new placement.
        Raises ValidationError if the placement is invalid.
        """
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

        current_placement.end_date = departure_date
        current_placement.save()

        if departure_date <= timezone.now().date():
            horse.is_active = False
            horse.save(update_fields=['is_active'])

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

        return departed, depart_errors
