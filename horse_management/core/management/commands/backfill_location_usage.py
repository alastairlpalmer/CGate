"""
Reconstruct historical field-usage periods from placement records.

The LocationUsagePeriod history only starts accruing from the day the feature
was deployed (the 0019 migration seeds a single open period per field from its
current usage). This command rebuilds the *real* history from the Placement
table so yearly analytics are meaningful from day one:

  - Every span where a field held one or more horses becomes a ``horses`` period
    (overlapping/contiguous placements are merged into one span).
  - The gaps between those spans become ``rested`` periods (the empty default,
    matching the live auto-transition rules).
  - The final period (covering today) is left open, reflecting current state.

Safety:
  - Dry run by default; pass --apply to write.
  - Fields whose history already contains a MANUAL period are skipped (so staff
    decisions are never destroyed) unless --force is given.
  - Fields with no placement history are left untouched (their seeded usage —
    e.g. a deliberately set 'hay' or 'rested' — is preserved).

Usage:
    python manage.py backfill_location_usage              # dry run, all fields
    python manage.py backfill_location_usage --apply      # write changes
    python manage.py backfill_location_usage --location 3 # one field only
    python manage.py backfill_location_usage --apply --force  # rebuild even
                                                              # over manual history
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Location, LocationUsagePeriod


def _merged_occupied_intervals(placements, today):
    """Merge placements into contiguous (start, end) occupied spans.

    end_date is inclusive (the departure day still counts as occupied), so two
    placements touching at end/end+1 form one continuous span. Ongoing
    placements (end_date is None) extend to today.
    """
    intervals = []
    for p in placements:
        if p.start_date > today:
            continue  # future-dated placement: not yet part of history
        end = p.end_date if p.end_date else today
        if end > today:
            end = today
        intervals.append((p.start_date, end))
    intervals.sort()

    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + timedelta(days=1):
            # Overlapping or contiguous → extend the current span.
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _build_periods(location, today):
    """Return a list of (usage, start_date, end_date) for a field's history.

    end_date is None on the final (open) period. Returns None when the field
    has no placement history (nothing to reconstruct).
    """
    placements = list(location.placements.all())
    occupied = _merged_occupied_intervals(placements, today)
    if not occupied:
        return None

    timeline_start = min(
        location.created_at.date(),
        min(start for start, _ in occupied),
    )

    periods = []
    cursor = timeline_start
    for start, end in occupied:
        if start > cursor:
            periods.append((Location.Usage.RESTED, cursor, start - timedelta(days=1)))
        periods.append((Location.Usage.HORSES, start, end))
        cursor = end + timedelta(days=1)

    if cursor <= today:
        periods.append((Location.Usage.RESTED, cursor, today))

    # The last period covers today → leave it open to reflect current state.
    usage, start, _ = periods[-1]
    periods[-1] = (usage, start, None)
    return periods


class Command(BaseCommand):
    help = "Rebuild field usage history (LocationUsagePeriod) from placements."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually write changes (default is a dry run).',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Rebuild even fields that already have manual usage history '
                 '(destroys those manual periods).',
        )
        parser.add_argument(
            '--location', type=int, default=None,
            help='Only process the field with this primary key.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        force = options['force']
        today = timezone.now().date()

        locations = Location.objects.all().order_by('site', 'name')
        if options['location'] is not None:
            locations = locations.filter(pk=options['location'])

        rebuilt = skipped_manual = skipped_no_data = 0

        for location in locations:
            existing = list(location.usage_periods.all())
            has_manual = any(
                p.source == LocationUsagePeriod.Source.MANUAL for p in existing
            )
            if has_manual and not force:
                skipped_manual += 1
                self.stdout.write(self.style.NOTICE(
                    f"  SKIP  {location}  — has manual history (use --force to override)"
                ))
                continue

            periods = _build_periods(location, today)
            if periods is None:
                skipped_no_data += 1
                self.stdout.write(
                    f"  skip  {location}  — no placements; seeded usage kept"
                )
                continue

            horses_spans = sum(1 for u, _, _ in periods if u == Location.Usage.HORSES)
            rest_spans = sum(1 for u, _, _ in periods if u == Location.Usage.RESTED)
            open_usage = periods[-1][0]
            self.stdout.write(self.style.SUCCESS(
                f"  {'REBUILD' if apply else 'would rebuild'}  {location}  — "
                f"{len(periods)} periods ({horses_spans} horses, {rest_spans} rested), "
                f"open as '{Location.Usage(open_usage).label}'"
            ))

            if apply:
                with transaction.atomic():
                    location.usage_periods.all().delete()
                    LocationUsagePeriod.objects.bulk_create([
                        LocationUsagePeriod(
                            location=location,
                            usage=usage,
                            start_date=start,
                            end_date=end,
                            source=LocationUsagePeriod.Source.AUTO,
                            notes='Reconstructed from placement history.',
                        )
                        for usage, start, end in periods
                    ])
                    if location.usage != open_usage:
                        location.usage = open_usage
                        location.save(update_fields=['usage'])
            rebuilt += 1

        self.stdout.write("")
        summary = (
            f"{rebuilt} field(s) {'rebuilt' if apply else 'to rebuild'}, "
            f"{skipped_manual} skipped (manual history), "
            f"{skipped_no_data} skipped (no placements)."
        )
        self.stdout.write(self.style.SUCCESS(summary))
        if not apply:
            self.stdout.write(self.style.NOTICE(
                "Dry run — no changes made. Re-run with --apply to write."
            ))
