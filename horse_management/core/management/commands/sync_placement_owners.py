"""Point every open stay at the horse's primary owner.

The Ownership card is the one place ownership is shown, and the open
placement (the stay livery is billed against) follows it whenever the
shares are edited. Stays edited before that rule existed can still name
someone else. This lists them, and with ``--write`` fixes them.

    python manage.py sync_placement_owners           # dry run
    python manage.py sync_placement_owners --write   # save

Past stays are never touched: they were billed to whoever owned the
horse at the time.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Placement
from core.services import PlacementService


class Command(BaseCommand):
    help = "List (or with --write, fix) open stays whose owner differs from the horse's primary owner."

    def add_arguments(self, parser):
        parser.add_argument('--write', action='store_true', help='Save the changes. Without it nothing is written.')

    def handle(self, *args, **options):
        write = options['write']
        open_stays = (
            Placement.objects.filter(end_date__isnull=True)
            .select_related('horse', 'owner')
            .order_by('horse__name')
        )
        changes = []
        for placement in open_stays:
            primary = placement.horse.primary_owner
            if primary is None or primary.pk == placement.owner_id:
                continue
            changes.append((placement, primary))

        if not changes:
            self.stdout.write('Every open stay already names the horse\'s primary owner.')
            return

        for placement, primary in changes:
            self.stdout.write(
                f"{placement.horse.name}: stay billed to {placement.owner.name}, "
                f"Ownership card says {primary.name}"
            )
        if not write:
            self.stdout.write(
                f"\n{len(changes)} stay{'s' if len(changes) != 1 else ''} would change. "
                "Run again with --write to save."
            )
            return

        with transaction.atomic():
            for placement, primary in changes:
                PlacementService.sync_placement_owner(placement.horse)
        self.stdout.write(self.style.SUCCESS(f"{len(changes)} stay{'s' if len(changes) != 1 else ''} updated."))
