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

# A superseding return may shorten the previous stay by at most this many
# already-elapsed days. Small trims are the yard-reality case (departed
# dated today/future, return backdated a few days); anything larger is
# almost certainly a typo'd date, and silently rewriting weeks of recorded
# — often already invoiced — history is worse than asking the user to edit
# the placement deliberately.
MAX_SUPERSEDE_TRIM_DAYS = 30


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
        trimmed = None
        if current_placement:
            current_placement.end_date = move_date - timedelta(days=1)
            current_placement.save()
        else:
            # A departed horse being moved back on/before its recorded
            # departure date: the return supersedes that departure.
            trimmed = PlacementService._supersede_recorded_departure(
                horse, move_date, destination=new_location
            )
        new_placement.full_clean()
        new_placement.save()

        # Field emptied by the move rests from the move date; the destination
        # becomes a horses field if horses just arrived onto it.
        if old_location and old_location != new_location:
            LocationUsageService.rest_if_empty(old_location, move_date - timedelta(days=1))
        LocationUsageService.horses_arrived(new_location, move_date, was_empty=new_loc_was_empty)

        # A horse being moved between fields is on site — if it was flagged
        # departed (e.g. moved back after a stint away), reactivate it so it
        # returns to the Current list rather than staying under Departed.
        PlacementService.reactivate(horse)

        # Keep ownership in sync with the new placement owner so current_owner,
        # reminders and extra-charge defaults follow the horse. Only singly
        # owned horses are auto-adjusted; genuine fractional co-ownership must
        # be edited explicitly on the ownership screen.
        PlacementService._sync_single_owner_share(horse, new_owner)

        # Views surface this so a trimmed departure is never silent.
        new_placement.superseded_trim = trimmed
        return new_placement

    @staticmethod
    def _supersede_recorded_departure(horse, arrival_date, destination=None):
        """Let a departed horse arrive back on or before its recorded departure.

        Yard reality: a horse is departed (often dated today, or even a future
        date) and later logged as arriving back, backdated to when it really
        returned. The recorded departure is superseded by the return — trim
        the most recent ended placement to the day before the new arrival so
        the overlap check passes and no day is billed twice.

        Also repairs the old field's usage history: the departure may have
        auto-rested it from the wrong date (or the horse never really left,
        when the return is to the same field).

        Returns the trimmed placement (with ``superseded_from`` set to the
        old end date) or None if nothing needed trimming. Raises
        ValidationError when the return predates the previous stay's start,
        or when the trim would erase more than MAX_SUPERSEDE_TRIM_DAYS of
        already-elapsed history.
        """
        last = horse.placements.filter(
            end_date__isnull=False
        ).order_by('-end_date').select_related('location').first()
        if not last or last.end_date < arrival_date:
            return None
        if arrival_date <= last.start_date:
            raise ValidationError(
                f"{horse.name}'s previous stay at {last.location.name} only "
                f"started on {last.start_date}, so an arrival on {arrival_date} "
                f"would predate it. Edit that placement's dates instead."
            )

        # Only days that have already elapsed count as history being erased —
        # trimming a future-dated (planned) departure is always fine.
        today = timezone.now().date()
        erased_until = min(last.end_date, today)
        erased_days = (erased_until - arrival_date).days + 1
        if erased_days > MAX_SUPERSEDE_TRIM_DAYS:
            raise ValidationError(
                f"This arrival would rewrite {erased_days} days of "
                f"{horse.name}'s recorded stay at {last.location.name} "
                f"({last.start_date} – {last.end_date}), which may already be "
                f"invoiced. If the dates really are wrong, edit that "
                f"placement directly instead."
            )

        old_end = last.end_date
        last.end_date = arrival_date - timedelta(days=1)
        last.save()
        last.superseded_from = old_end

        # Usage-history repair for the old field: returning to the same field
        # means it never actually emptied, so the departure's auto-rest is
        # bogus; returning elsewhere means it emptied earlier than recorded.
        if destination is not None and last.location_id == destination.pk:
            LocationUsageService.undo_auto_rest(last.location)
        else:
            LocationUsageService.align_auto_rest_start(
                last.location, last.end_date + timedelta(days=1)
            )
        return last

    @staticmethod
    def reactivate(horse):
        """Bring a departed horse back onto the Current lists.

        Horse.is_active drives the Current/Departed split (lists, search
        dropdown) while the open placement drives the record-page buttons.
        Any operation that puts a horse back on site must set both, or the
        horse stays labelled Departed despite having a live placement.
        """
        if not horse.is_active:
            horse.is_active = True
            horse.save(update_fields=['is_active'])

    @staticmethod
    def _sync_single_owner_share(horse, owner):
        """Point a singly-owned horse's ownership share at ``owner``.

        No-op for horses with fractional co-ownership (2+ shares), horses with
        no shares at all (billing falls back to the placement owner), or when
        the single share already matches.
        """
        shares = list(horse.ownership_shares.all())
        if len(shares) == 1 and shares[0].owner_id != owner.id:
            share = shares[0]
            share.owner = owner
            share.save()

    @staticmethod
    @transaction.atomic
    def arrive_horse(horse, *, owner, location, rate_type, arrival_date,
                     expected_departure=None, notes=''):
        """Log a single horse arriving at a location.

        Returns the new placement.
        Raises ValidationError if the placement is invalid.
        """
        trimmed = PlacementService._supersede_recorded_departure(
            horse, arrival_date, destination=location
        )
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
        # A departed horse arriving back must also flip is_active, otherwise
        # the placement saves but the horse stays in the Departed list.
        PlacementService.reactivate(horse)
        # Views surface this so a trimmed departure is never silent.
        placement.superseded_trim = trimmed
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
    @transaction.atomic
    def confirm_departure(horse):
        """Mark a horse as departed (deactivate). Used for pending departures.

        Defensively closes any still-open placement so the horse can't end up
        deactivated while apparently occupying a field — that stranded state
        hides the Log Arrival button while the search dropdown says Departed.
        """
        open_placement = horse.placements.filter(end_date__isnull=True).first()
        if open_placement:
            today = timezone.now().date()
            open_placement.end_date = max(today, open_placement.start_date)
            open_placement.save()
            LocationUsageService.rest_if_empty(
                open_placement.location, open_placement.end_date
            )
        horse.is_active = False
        horse.save(update_fields=['is_active'])

    @staticmethod
    @transaction.atomic
    def cancel_departure(horse):
        """Undo a departure.

        A stranded horse (flagged departed while its placement is still open)
        just needs the flag cleared — never re-open an older placement on top
        of the current one. Otherwise the most recent ended placement is
        re-opened. Returns the open placement, or None if the horse has no
        placement history.
        """
        open_placement = horse.placements.filter(end_date__isnull=True).first()
        if open_placement:
            PlacementService.reactivate(horse)
            return open_placement
        placement = horse.placements.filter(
            end_date__isnull=False
        ).order_by('-end_date').first()
        if placement:
            placement.end_date = None
            placement.save()
            # If the departure auto-rested the field, un-rest it too.
            LocationUsageService.undo_auto_rest(placement.location)
            # If the departure had already been confirmed, re-opening the
            # placement alone would strand the horse as inactive-but-placed.
            PlacementService.reactivate(horse)
            return placement
        return None

    @staticmethod
    @transaction.atomic
    def confirm_departures_bulk(horse_ids):
        """Confirm multiple horses as departed in one action.

        Returns the count of horses deactivated.
        """
        horses = list(Horse.objects.filter(pk__in=horse_ids, is_active=True))
        for horse in horses:
            PlacementService.confirm_departure(horse)
        return len(horses)

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
                # Per-horse savepoint so a rejected arrival doesn't leave the
                # superseded-departure trim behind for that horse.
                with transaction.atomic():
                    PlacementService._supersede_recorded_departure(
                        horse, arrival_date, destination=location
                    )
                    placement.full_clean()
                    placement.save()
                    PlacementService.reactivate(horse)
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

        # Write through a queryset: the caller's instance may hold a stale
        # usage value (placement FKs cache their own Location objects), and
        # comparing against it can wrongly skip the DB update.
        Location.objects.filter(pk=location.pk).update(usage=usage)
        location.usage = usage

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
    def clear_auto_rest_from(location, occupied_from):
        """Remove an automatic rest period contradicted by an occupancy.

        A field occupied from ``occupied_from`` cannot have an automatic rest
        period starting on or after that date — such a period is left over
        from a departure that a new arrival has just superseded (or from the
        close half of a same-field move). Manual usage states are never
        touched.
        """
        current = location.usage_periods.filter(end_date__isnull=True).first()
        if (
            current
            and current.usage == Location.Usage.RESTED
            and current.source == LocationUsagePeriod.Source.AUTO
            and current.start_date >= occupied_from
        ):
            LocationUsageService.undo_auto_rest(location)

    @staticmethod
    @transaction.atomic
    def align_auto_rest_start(location, empty_from):
        """Move an automatic rest period's start back to ``empty_from``.

        Used when a superseded departure reveals the field actually emptied
        earlier than recorded. Only touches an open AUTO rest period, and
        only to backdate it; the preceding period's end moves with it.
        """
        current = location.usage_periods.filter(end_date__isnull=True).first()
        if (
            not current
            or current.usage != Location.Usage.RESTED
            or current.source != LocationUsagePeriod.Source.AUTO
            or current.start_date <= empty_from
        ):
            return
        previous = location.usage_periods.filter(
            end_date=current.start_date - timedelta(days=1)
        ).order_by('-start_date').first()
        if previous:
            if empty_from <= previous.start_date:
                # Backdating this far would invert the previous period —
                # leave the history alone rather than corrupt it.
                return
            previous.end_date = empty_from - timedelta(days=1)
            previous.save()
        current.start_date = empty_from
        current.save()

    @staticmethod
    @transaction.atomic
    def undo_auto_rest(location):
        """Remove an automatic 'rested' period created by a departure that is
        being undone, re-opening the usage period it had closed.

        No-op unless the current open period is an AUTO rest — manual usage
        changes are never touched.
        """
        current = location.usage_periods.filter(end_date__isnull=True).first()
        if (
            not current
            or current.usage != Location.Usage.RESTED
            or current.source != LocationUsagePeriod.Source.AUTO
        ):
            return
        previous = location.usage_periods.filter(
            end_date=current.start_date - timedelta(days=1)
        ).order_by('-start_date').first()
        current.delete()
        if previous:
            previous.end_date = None
            previous.save()
            restored_usage = previous.usage
        else:
            # The rest period had no predecessor to re-open; the field holds
            # horses again, so at least keep the label truthful.
            restored_usage = Location.Usage.HORSES
        # Queryset write — see set_usage: instance state can be stale.
        Location.objects.filter(pk=location.pk).update(usage=restored_usage)
        location.usage = restored_usage

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
