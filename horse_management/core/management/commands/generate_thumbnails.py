"""Backfill avatar thumbnails for horse photos uploaded before photo_thumb
existed. Safe to re-run; use --force to regenerate everything (e.g. after
changing THUMB_SIZE).

    python manage.py generate_thumbnails [--force]
"""

from django.core.management.base import BaseCommand

from core.models import Horse


class Command(BaseCommand):
    help = "Generate square avatar thumbnails for horse photos that lack one."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help="Regenerate thumbnails even where one already exists.",
        )

    def handle(self, *args, **options):
        horses = Horse.objects.exclude(photo='').exclude(photo__isnull=True)
        if not options['force']:
            from django.db.models import Q
            horses = horses.filter(Q(photo_thumb='') | Q(photo_thumb__isnull=True))

        done = failed = 0
        for horse in horses:
            if options['force'] and horse.photo_thumb:
                horse.photo_thumb.delete(save=False)
                horse.photo_thumb = None
            # save() runs _sync_photo_thumb; a generation failure leaves the
            # thumb empty and the avatar falls back to the original photo.
            horse.save(update_fields=None)
            horse.refresh_from_db()
            if horse.photo_thumb:
                done += 1
            else:
                failed += 1
                self.stderr.write(
                    f"Could not generate thumbnail for {horse.name} "
                    f"({horse.photo.name}) — source missing or unreadable."
                )

        self.stdout.write(self.style.SUCCESS(
            f"Thumbnails generated: {done}; skipped/unreadable: {failed}."
        ))
