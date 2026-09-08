"""Scheduled maintenance tasks.

Currently the nightly off-site backup. See core/backup/ for what it does
and docs/BACKUP_RESTORE.md for how to restore from it.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import mail_admins, send_mail
from django.utils import timezone

from core.backup import runner, storage
from core.models import BackupRun, BusinessSettings

logger = logging.getLogger(__name__)


@shared_task
def run_nightly_backup():
    """Back up the database and media off-site, then prune old copies.

    Off unless BACKUP_ENABLED is true and the storage credentials are set,
    so a deployment that has not configured backups does not fail a task
    every night.

    On failure this does three things, and all three matter: it records the
    failure in BackupRun, it emails the yard, and it re-raises so Celery
    marks the task failed and Sentry captures it. A backup that fails
    quietly is worse than no backup, because you stop checking.
    """
    if not settings.BACKUP_ENABLED:
        logger.info('Backup: BACKUP_ENABLED is off, nothing to do')
        return 'disabled'

    if not storage.is_configured():
        logger.warning(
            'Backup: BACKUP_ENABLED is on but the storage credentials are '
            'missing. No backup is being taken.'
        )
        return 'not configured'

    run = BackupRun.objects.create()
    try:
        summary = runner.run(include_media=settings.BACKUP_INCLUDE_MEDIA)
    except Exception as exc:
        run.status = BackupRun.STATUS_FAILED
        run.finished_at = timezone.now()
        run.error = str(exc)[:2000]
        run.save(update_fields=['status', 'finished_at', 'error'])
        logger.exception('Backup failed')
        _notify_failure(exc)
        raise

    run.status = BackupRun.STATUS_SUCCESS
    run.finished_at = timezone.now()
    run.database_bytes = summary['database_bytes']
    run.media_bytes = summary['media_bytes']
    run.objects_pruned = summary['pruned']
    run.keys = '\n'.join(summary['keys'])
    run.save()

    logger.info(
        'Backup complete in %s: database %s bytes, media %s bytes, '
        '%s expired objects removed',
        run.duration, run.database_bytes, run.media_bytes, run.objects_pruned,
    )
    return 'ok'


def _notify_failure(exc):
    """Tell someone. Never let this hide the original failure."""
    subject = 'Yardway backup FAILED'
    body = (
        'The nightly off-site backup did not complete.\n\n'
        f'Error: {exc}\n\n'
        'Until this is fixed there is no recent off-site copy of the '
        'database or the uploaded documents.\n\n'
        'See docs/BACKUP_RESTORE.md.'
    )
    try:
        recipient = BusinessSettings.get_settings().email
    except Exception:
        recipient = ''

    try:
        if recipient:
            send_mail(
                subject, body, settings.DEFAULT_FROM_EMAIL, [recipient],
                fail_silently=False,
            )
        else:
            # No business address configured — fall back to ADMINS so the
            # failure still reaches somebody.
            mail_admins(subject, body, fail_silently=False)
    except Exception:
        # The task re-raises the original error either way. Losing the
        # email is bad; masking the backup failure behind an SMTP error
        # would be worse.
        logger.exception('Backup: could not send the failure notification')
