"""Take an off-site backup now.

    python manage.py backup                # database and media
    python manage.py backup --no-media     # database only
    python manage.py backup --prune-only   # apply retention, back nothing up

Nightly runs go through the Celery task in core/tasks.py, which calls the
same code. This command exists so a backup can be taken by hand before a
risky change, and so the configuration can be checked without waiting for
3am.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.backup import runner, storage
from core.models import BackupRun

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Back up the database and uploaded media to off-site storage.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-media', action='store_true',
            help='Back up the database only. Media files are not in a '
                 'database dump, so this is not a full backup.',
        )
        parser.add_argument(
            '--prune-only', action='store_true',
            help='Delete expired backups without taking a new one.',
        )

    def handle(self, *args, **options):
        if not storage.is_configured():
            raise CommandError(
                'Backups are not configured. Set BACKUP_S3_BUCKET, '
                'BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY. '
                'See docs/BACKUP_RESTORE.md.'
            )

        if options['prune_only']:
            removed = runner.prune(runner.DB_PREFIX)
            removed += runner.prune(runner.MEDIA_PREFIX)
            self.stdout.write(self.style.SUCCESS(f'Pruned {removed} expired objects.'))
            return

        run = BackupRun.objects.create()
        try:
            summary = runner.run(include_media=not options['no_media'])
        except Exception as exc:
            run.status = BackupRun.STATUS_FAILED
            run.finished_at = timezone.now()
            run.error = str(exc)[:2000]
            run.save(update_fields=['status', 'finished_at', 'error'])
            # Re-raised as CommandError so the exit code is non-zero: a
            # cron or CI step calling this must be able to tell.
            raise CommandError(f'Backup failed: {exc}') from exc

        run.status = BackupRun.STATUS_SUCCESS
        run.finished_at = timezone.now()
        run.database_bytes = summary['database_bytes']
        run.media_bytes = summary['media_bytes']
        run.objects_pruned = summary['pruned']
        run.keys = '\n'.join(summary['keys'])
        run.save()

        self.stdout.write(self.style.SUCCESS(
            f'Backup complete in {run.duration}. '
            f'Database {summary["database_bytes"]:,} bytes, '
            f'media {summary["media_bytes"]:,} bytes, '
            f'{summary["pruned"]} expired objects removed.'
        ))
        for key in summary['keys']:
            self.stdout.write(f'  {key}')
