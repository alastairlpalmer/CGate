"""Make the archives and put them off-site.

The failure rule for everything here: a backup that fails must say so
loudly. A backup that silently does nothing is worse than having none,
because you stop worrying about it.
"""

import logging
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import retention, storage

logger = logging.getLogger(__name__)

DB_PREFIX = 'backups/database/'
MEDIA_PREFIX = 'backups/media/'


class BackupError(RuntimeError):
    """A backup did not complete. Always raised, never swallowed."""


def stamp(now=None):
    """The timestamp used in every backup name: 2026-09-08T0300."""
    now = now or timezone.now()
    return now.strftime('%Y-%m-%dT%H%M')


def dump_database(destination_dir, now=None):
    """pg_dump the database into ``destination_dir``. Returns the path.

    Custom format (-Fc): compressed, and pg_restore can read a single table
    out of it, which plain SQL cannot.
    """
    url = settings.DATABASES['default'].get('NAME')
    engine = settings.DATABASES['default'].get('ENGINE', '')
    if 'postgresql' not in engine:
        raise BackupError(
            f'Database backup needs PostgreSQL, but ENGINE is {engine!r}. '
            'On a local SQLite run there is nothing here worth backing up.'
        )

    if shutil.which('pg_dump') is None:
        raise BackupError(
            'pg_dump is not installed on this host. Add the PostgreSQL '
            'client to the image (see horse_management/railpack.json).'
        )

    path = Path(destination_dir) / f'yardway-db-{stamp(now)}.dump'
    dsn = settings.BACKUP_DATABASE_URL or _dsn_from_settings()

    logger.info('Backup: running pg_dump for %s', url)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            'pg_dump',
            '--format=custom',
            '--no-owner',
            '--no-privileges',
            f'--file={path}',
            dsn,
        ],
        capture_output=True,
        text=True,
        timeout=settings.BACKUP_PG_DUMP_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        # stderr can name the host and user but never the password, which
        # pg_dump keeps out of its messages.
        raise BackupError(f'pg_dump failed ({result.returncode}): {result.stderr.strip()[:500]}')
    if not path.exists() or path.stat().st_size == 0:
        raise BackupError('pg_dump produced no output.')
    return path


def _dsn_from_settings():
    """Rebuild a connection URL from DATABASES when no explicit one is set."""
    config = settings.DATABASES['default']
    user = config.get('USER') or ''
    password = config.get('PASSWORD') or ''
    host = config.get('HOST') or 'localhost'
    port = config.get('PORT') or 5432
    name = config.get('NAME') or ''
    credentials = f'{user}:{password}@' if user else ''
    return f'postgresql://{credentials}{host}:{port}/{name}'


def archive_media(destination_dir, now=None):
    """Tar and gzip MEDIA_ROOT. Returns the path, or None if there is
    nothing to archive.

    None rather than an error when the directory is missing or empty: a
    brand-new deployment has no uploads yet, and that is not a failure.
    """
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.is_dir() or not any(media_root.iterdir()):
        logger.info('Backup: %s is empty, no media archive made', media_root)
        return None

    path = Path(destination_dir) / f'yardway-media-{stamp(now)}.tar.gz'
    logger.info('Backup: archiving %s', media_root)
    with tarfile.open(path, 'w:gz') as archive:
        archive.add(media_root, arcname='media')
    return path


def prune(prefix, today=None):
    """Delete backups under ``prefix`` that the retention policy expires."""
    today = today or timezone.localdate()
    keys = storage.list_keys(prefix)
    expired = retention.select_expired(
        keys,
        today=today,
        keep_daily=settings.BACKUP_KEEP_DAILY,
        keep_weekly=settings.BACKUP_KEEP_WEEKLY,
        keep_monthly=settings.BACKUP_KEEP_MONTHLY,
    )
    return storage.delete_keys(expired)


def run(include_media=True, now=None):
    """Take a full backup and prune old ones.

    Returns a summary dict. Raises BackupError on any failure — the caller
    (the management command or the Celery task) is responsible for making
    that visible.
    """
    if not storage.is_configured():
        raise storage.BackupStorageNotConfigured(
            'Backups need BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY and '
            'BACKUP_S3_SECRET_KEY. See docs/BACKUP_RESTORE.md.'
        )

    now = now or timezone.now()
    summary = {'database_bytes': 0, 'media_bytes': 0, 'pruned': 0, 'keys': []}

    # A temporary directory, cleaned up whatever happens. The dump holds
    # every owner's details in plain text, so it must not be left on disk.
    with tempfile.TemporaryDirectory(prefix='yardway-backup-') as workdir:
        db_path = dump_database(workdir, now=now)
        key = DB_PREFIX + db_path.name
        summary['database_bytes'] = storage.upload(db_path, key)
        summary['keys'].append(key)

        if include_media:
            media_path = archive_media(workdir, now=now)
            if media_path is not None:
                key = MEDIA_PREFIX + media_path.name
                summary['media_bytes'] = storage.upload(media_path, key)
                summary['keys'].append(key)

    today = timezone.localtime(now).date()
    summary['pruned'] = prune(DB_PREFIX, today=today)
    if include_media:
        summary['pruned'] += prune(MEDIA_PREFIX, today=today)

    return summary
