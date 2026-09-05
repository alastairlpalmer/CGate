"""
Fill Location.latitude/longitude from the Google Maps links people pasted
into the description field.

Per active location without coordinates:
  1. find the first web address in ``description``;
  2. follow a maps.app.goo.gl / goo.gl short link (max 5 redirects, 10 s);
  3. read the coordinates out of the resolved URL (core.geo);
  4. validate them like the form would; write only with --write.

Safety:
  - Dry run by default; pass --write to save.
  - Idempotent: a location that already has coordinates is skipped, so the
    command can be run again after fixing the unresolved ones by hand.
  - One redirect request per second, so the short-link service is not hammered.
  - The link stays in ``description`` as the audit trail.

The report has three lists: resolved, unresolved (the manual work queue)
and rejected by validation. Expect a share to be unresolved — short links
expire, and some resolve to a place id with no coordinates.

Usage:
    python manage.py backfill_location_coords            # dry run
    python manage.py backfill_location_coords --write    # save
    python manage.py backfill_location_coords --location 3
"""

import time

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from core.geo import extract_url, is_short_link, parse_maps_url, resolve_short_link
from core.models import Location, validate_coordinate_pair

REQUEST_INTERVAL_S = 1.0


class Command(BaseCommand):
    help = "Fill location coordinates from Google Maps links in the description."

    def add_arguments(self, parser):
        parser.add_argument(
            '--write', action='store_true',
            help='Save the coordinates (default is a dry run).',
        )
        parser.add_argument(
            '--location', type=int, default=None,
            help='Only process the location with this primary key.',
        )
        parser.add_argument(
            '--no-resolve', action='store_true',
            help='Do not follow short links (offline; short links stay unresolved).',
        )

    def handle(self, *args, **options):
        write = options['write']
        resolve = not options['no_resolve']

        locations = Location.objects.active().order_by('site', 'name')
        if options['location'] is not None:
            locations = locations.filter(pk=options['location'])

        resolved, unresolved, rejected, skipped = [], [], [], 0
        last_request = 0.0

        for location in locations:
            if location.has_coordinates:
                skipped += 1
                continue

            url = extract_url(location.description)
            if not url:
                unresolved.append((location, 'no link in description'))
                continue

            if is_short_link(url):
                if not resolve:
                    unresolved.append((location, f'short link not followed: {url}'))
                    continue
                wait = REQUEST_INTERVAL_S - (time.monotonic() - last_request)
                if wait > 0:
                    time.sleep(wait)
                last_request = time.monotonic()
                try:
                    url = resolve_short_link(url)
                except Exception as exc:  # requests errors, too many redirects
                    unresolved.append((location, f'could not follow {url}: {exc}'))
                    continue

            coords = parse_maps_url(url)
            if coords is None:
                unresolved.append((location, f'no coordinates in {url}'))
                continue

            lat, lng = coords
            try:
                validate_coordinate_pair(lat, lng)
            except ValidationError as exc:
                rejected.append((location, f'{lat}, {lng}: ' + '; '.join(exc.messages)))
                continue

            resolved.append((location, lat, lng))
            if write:
                location.latitude = lat
                location.longitude = lng
                location.save(update_fields=['latitude', 'longitude', 'updated_at'])

        verb = 'WROTE' if write else 'would write'
        self.stdout.write(self.style.SUCCESS(f"Resolved ({len(resolved)}):"))
        for location, lat, lng in resolved:
            self.stdout.write(f"  {verb}  {location}  → {lat}, {lng}")

        self.stdout.write(self.style.NOTICE(f"\nUnresolved ({len(unresolved)}) — enter by hand:"))
        for location, reason in unresolved:
            self.stdout.write(f"  {location}  — {reason}")

        self.stdout.write(self.style.ERROR(f"\nRejected by validation ({len(rejected)}):"))
        for location, reason in rejected:
            self.stdout.write(f"  {location}  — {reason}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{len(resolved)} resolved, {len(unresolved)} unresolved, "
            f"{len(rejected)} rejected, {skipped} skipped (already have coordinates)."
        ))
        if not write:
            self.stdout.write(self.style.NOTICE(
                "Dry run — nothing saved. Re-run with --write to save."
            ))
