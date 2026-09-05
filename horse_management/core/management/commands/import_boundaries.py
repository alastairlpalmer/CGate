"""
Import field boundaries from a Land App / RPA GeoJSON export (phase 4a).

Reads the file with ``core.boundary_import`` (validation, British National
Grid conversion, winding order, self-intersection, simplification), matches
each shape to an active location — a location whose point sits inside the
shape first, then by name — and writes the boundaries.

Safety:
  - Dry run by default; pass --write to save.
  - The whole write is one transaction: a failure part-way leaves nothing.
  - A location that already has a boundary is overwritten only with
    --overwrite; the old boundary is kept in LocationBoundaryHistory.
  - --create makes a new location for each unmatched shape (named after the
    shape, on --site) so a whole farm can be brought in at once.

Usage:
    python manage.py import_boundaries plan.geojson --site Somerford
    python manage.py import_boundaries plan.geojson --site Somerford --write
    python manage.py import_boundaries plan.geojson --site Somerford --write --create
    python manage.py import_boundaries plan.geojson --assign 3=12 --assign 4=15 --write
"""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.boundary_import import (
    BoundaryImportError, apply_boundary, parse_geojson, suggest_matches,
)
from core.models import BoundarySource, Location


class Command(BaseCommand):
    help = "Import field boundaries from a Land App GeoJSON export onto locations."

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .geojson / .json export.')
        parser.add_argument(
            '--site', default='',
            help='Match only locations on this site (and create new ones there).',
        )
        parser.add_argument('--write', action='store_true', help='Save (default is a dry run).')
        parser.add_argument(
            '--overwrite', action='store_true',
            help='Replace boundaries on locations that already have one.',
        )
        parser.add_argument(
            '--create', action='store_true',
            help='Create a location for every shape that matches nothing (needs --site).',
        )
        parser.add_argument(
            '--assign', action='append', default=[], metavar='SHAPE=LOCATION_PK',
            help='Force a match: shape number (from the report) = location pk. Repeatable.',
        )
        parser.add_argument(
            '--weak', action='store_true',
            help='Also apply name-only (weak) suggestions. Default applies spatial matches only.',
        )

    def handle(self, *args, **options):
        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'No such file: {path}')
        try:
            report = parse_geojson(path.read_bytes())
        except BoundaryImportError as exc:
            raise CommandError(str(exc))

        site = options['site'].strip()
        if options['create'] and not site:
            raise CommandError('--create needs --site so the new locations have a site.')

        locations = Location.objects.active()
        if site:
            locations = locations.filter(site=site)
        locations = list(locations.order_by('name'))
        by_pk = {loc.pk: loc for loc in locations}

        forced = {}
        for item in options['assign']:
            try:
                shape_no, pk = item.split('=')
                forced[int(shape_no)] = int(pk)
            except ValueError:
                raise CommandError(f'--assign expects SHAPE=LOCATION_PK, got {item!r}')
            if forced[int(shape_no)] not in by_pk:
                raise CommandError(f'No active location with pk {pk}' + (f' on {site}' if site else ''))

        suggestions = suggest_matches(report.shapes, locations)

        for note in report.notes:
            self.stdout.write(self.style.NOTICE(note))
        self.stdout.write(
            f"{len(report.shapes)} shape(s) in the file, {len(locations)} active location(s)"
            + (f" on {site}" if site else "") + "."
        )

        plan = []       # (shape, location or None, label)
        for shp in report.shapes:
            if not shp.importable:
                self.stdout.write(self.style.ERROR(
                    f"  #{shp.index:>2} {shp.name:<28} {shp.hectares:6.2f} ha  SKIP — invalid geometry: {shp.invalid}"
                ))
                continue
            loc = None
            how = 'no match'
            if shp.index in forced:
                loc, how = by_pk[forced[shp.index]], 'assigned'
            elif shp.index in suggestions:
                s = suggestions[shp.index]
                if s['strength'] == 'strong' or options['weak']:
                    loc, how = s['location'], s['strength'] + ' match'
                else:
                    how = f"weak match ({s['location'].name}) — use --weak or --assign"
            if loc is not None and loc.boundary is not None and not options['overwrite']:
                self.stdout.write(self.style.NOTICE(
                    f"  #{shp.index:>2} {shp.name:<28} {shp.hectares:6.2f} ha  keep — {loc.name} already has a "
                    "boundary (use --overwrite)"
                ))
                continue
            extra = ' (simplified)' if shp.simplified else ''
            if loc is not None:
                self.stdout.write(self.style.SUCCESS(
                    f"  #{shp.index:>2} {shp.name:<28} {shp.hectares:6.2f} ha  → {loc.name}  [{how}]{extra}"
                ))
                plan.append((shp, loc))
            elif options['create']:
                self.stdout.write(self.style.SUCCESS(
                    f"  #{shp.index:>2} {shp.name:<28} {shp.hectares:6.2f} ha  → NEW location “{shp.name}” on {site}{extra}"
                ))
                plan.append((shp, None))
            else:
                self.stdout.write(f"  #{shp.index:>2} {shp.name:<28} {shp.hectares:6.2f} ha  — {how}")

        self.stdout.write("")
        creates = sum(1 for _, loc in plan if loc is None)
        writes = len(plan) - creates
        self.stdout.write(self.style.SUCCESS(
            f"{writes} boundary write(s), {creates} new location(s), "
            f"{len(report.invalid)} invalid shape(s) skipped."
        ))
        if not options['write']:
            self.stdout.write(self.style.NOTICE("Dry run — nothing saved. Re-run with --write to save."))
            return

        now = timezone.now()
        try:
            with transaction.atomic():
                for shp, loc in plan:
                    if loc is None:
                        loc = Location(name=shp.name[:200], site=site, usage=Location.Usage.OTHER)
                        loc.full_clean()
                        loc.save()
                    apply_boundary(loc, shp, source=BoundarySource.LANDAPP, now=now)
        except ValidationError as exc:
            raise CommandError('Nothing saved: ' + '; '.join(exc.messages))
        self.stdout.write(self.style.SUCCESS("Saved."))
