"""
Fix horses that are in limbo: is_active=True but no active placement.

These horses had their placement end_date set (via bulk departure) but
were not deactivated. This command:

1. Finds all limbo horses (active, no current placement)
2. Shows their details (owner, last placement, departure date)
3. Sets is_active=False on each (with --apply flag)

Usage:
    # Dry run (just show what would be fixed):
    python manage.py fix_departed_horses

    # Actually fix the data:
    python manage.py fix_departed_horses --apply

    # Filter by owner name:
    python manage.py fix_departed_horses --owner "Andrew Hine" --apply
"""

from django.core.management.base import BaseCommand
from core.models import Horse, Placement


class Command(BaseCommand):
    help = "Deactivate horses that have departed but are still marked active"

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually apply the fix (default is dry run)',
        )
        parser.add_argument(
            '--owner',
            type=str,
            default='',
            help='Filter by owner name (partial match)',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        owner_filter = options['owner']

        # Find active horses with NO active placement
        limbo_horses = Horse.objects.filter(
            is_active=True,
        ).exclude(
            placements__end_date__isnull=True,
        ).prefetch_related('placements__owner', 'placements__location')

        if owner_filter:
            limbo_horses = limbo_horses.filter(
                placements__owner__name__icontains=owner_filter,
            ).distinct()

        if not limbo_horses.exists():
            self.stdout.write(self.style.SUCCESS(
                "No limbo horses found (all active horses have current placements)."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"\nFound {limbo_horses.count()} horse(s) in limbo "
            f"(is_active=True but no active placement):\n"
        ))

        for horse in limbo_horses:
            last_p = horse.placements.order_by('-end_date').first()
            owner_name = last_p.owner.name if last_p and last_p.owner else 'Unknown'
            location = last_p.location.name if last_p and last_p.location else 'Unknown'
            end_date = last_p.end_date if last_p else 'N/A'

            self.stdout.write(
                f"  {horse.name} (pk={horse.pk})"
                f"  |  Owner: {owner_name}"
                f"  |  Last location: {location}"
                f"  |  Departed: {end_date}"
            )

        if apply:
            count = limbo_horses.update(is_active=False)
            self.stdout.write(self.style.SUCCESS(
                f"\nDeactivated {count} horse(s)."
            ))
        else:
            self.stdout.write(self.style.NOTICE(
                "\nDry run - no changes made. Use --apply to fix."
            ))
