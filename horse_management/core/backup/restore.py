"""Put a backup back, into somewhere that is not production.

A backup nobody has restored is not a backup. This module is what makes
the restore test something you run from a dashboard in twenty minutes
rather than a morning's work with a terminal, which is the difference
between doing it twice a year and never doing it again.

Safety
------
Every guard here exists because the failure it prevents is unrecoverable.
A restore writes over whatever it is pointed at, so this module refuses
to run unless it is pointed somewhere explicitly marked as scratch:

* ``RESTORE_TEST_DATABASE_URL`` must be set. There is no default and no
  fallback to ``DATABASE_URL``.
* It must not name the same host and database as ``DATABASE_URL`` or
  ``BACKUP_DATABASE_URL``. The port is deliberately ignored in that
  comparison — the app reaches Supabase through a pooler on 6543 and
  pg_dump through one on 5432, and those are the same database.
* The target must have no tables, unless the caller says otherwise.

The same shape applies to media: a separate scratch bucket, which must be
neither the live media bucket nor the backup bucket.
"""

import logging
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.db import connections

from . import runner, storage

logger = logging.getLogger(__name__)


class RestoreNotPermitted(RuntimeError):
    """The restore was aimed somewhere it must not be aimed."""


class RestoreError(RuntimeError):
    """The restore was permitted but did not complete."""


def _identity(url):
    """(host, database) for a Postgres URL, lowercased.

    The port is left out on purpose. Two URLs differing only by port are
    two doors into one database, and restoring over it through either
    would be equally final.
    """
    parsed = urlparse(url)
    return ((parsed.hostname or '').lower(), (parsed.path or '').lstrip('/').lower())


def target_database_url():
    """The scratch database URL, once it has been proved to be scratch."""
    target = (getattr(settings, 'RESTORE_TEST_DATABASE_URL', '') or '').strip()
    if not target:
        raise RestoreNotPermitted(
            'RESTORE_TEST_DATABASE_URL is not set. The restore test needs a '
            'scratch database of its own — it will not fall back to '
            'DATABASE_URL. See docs/BACKUP_RESTORE.md.'
        )

    target_id = _identity(target)
    if not target_id[0] or not target_id[1]:
        raise RestoreNotPermitted(
            'RESTORE_TEST_DATABASE_URL must be a full connection string '
            'including a host and a database name.'
        )

    for name, url in (
        ('DATABASE_URL', _live_database_url()),
        ('BACKUP_DATABASE_URL', getattr(settings, 'BACKUP_DATABASE_URL', '')),
    ):
        if url and _identity(url) == target_id:
            raise RestoreNotPermitted(
                f'RESTORE_TEST_DATABASE_URL points at the same host and '
                f'database as {name}. A restore would overwrite live data. '
                f'Create a separate scratch database.'
            )
    return target


def _live_database_url():
    """The production database as a URL, rebuilt from DATABASES."""
    config = settings.DATABASES['default']
    host = config.get('HOST') or ''
    name = config.get('NAME') or ''
    if not host:
        return ''
    return f'postgresql://{host}/{name}'


def target_media_bucket():
    """The scratch media bucket, once it has been proved to be scratch."""
    target = (getattr(settings, 'RESTORE_TEST_MEDIA_BUCKET', '') or '').strip()
    if not target:
        raise RestoreNotPermitted(
            'RESTORE_TEST_MEDIA_BUCKET is not set. Restoring media needs a '
            'scratch bucket of its own.'
        )
    for name, bucket in (
        ('MEDIA_S3_BUCKET', getattr(settings, 'MEDIA_S3_BUCKET', '')),
        ('BACKUP_S3_BUCKET', getattr(settings, 'BACKUP_S3_BUCKET', '')),
    ):
        if bucket and bucket == target:
            raise RestoreNotPermitted(
                f'RESTORE_TEST_MEDIA_BUCKET is the same bucket as {name}. '
                f'Restoring into it would overwrite live files.'
            )
    return target


def newest_key(prefix):
    """The most recent backup under ``prefix``.

    Sorted by the timestamp in the name rather than the store's own
    modified time, because a re-uploaded object would carry a fresh
    modified time while holding older data.
    """
    keys = [k for k in storage.list_keys(prefix) if not k.endswith('/')]
    if not keys:
        raise RestoreError(
            f'No backups found under {prefix}. Take one first: run the '
            f'nightly-backup task, or `python manage.py backup`.'
        )
    return sorted(keys)[-1]


def target_is_empty(url):
    """True when the scratch database holds no tables of its own.

    A connection failure is reported as a RestoreError rather than left
    to raise: the person reading this is following a runbook in a deploy
    log, and a psycopg traceback there tells them nothing they can act
    on.
    """
    parsed = urlparse(url)
    connections.databases['restore_test'] = {
        **connections.databases['default'],
        'NAME': parsed.path.lstrip('/'),
        'USER': parsed.username or '',
        'PASSWORD': parsed.password or '',
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or 5432),
        'OPTIONS': {},
    }
    try:
        with connections['restore_test'].cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            return cursor.fetchone()[0] == 0
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise RestoreError(
            f'Could not reach the scratch database named by '
            f'RESTORE_TEST_DATABASE_URL ({parsed.hostname}): {exc}'
        )
    finally:
        connections['restore_test'].close()
        del connections.databases['restore_test']


def restore_database(key=None, allow_nonempty=False):
    """Restore one database dump into the scratch database."""
    target = target_database_url()

    if shutil.which('pg_restore') is None:
        raise RestoreError(
            'pg_restore is not installed on this host. Add the PostgreSQL '
            'client to the image (see horse_management/railpack.json).'
        )

    if not allow_nonempty and not target_is_empty(target):
        raise RestoreNotPermitted(
            'The scratch database already holds tables. Empty it first, or '
            'pass --allow-nonempty if you know what is in there. This guard '
            'is what stops a mistyped URL from landing on something real.'
        )

    key = key or newest_key(runner.DB_PREFIX)

    with tempfile.TemporaryDirectory(prefix='yardway-restore-') as workdir:
        # The dump holds every owner's details in plain text. A temporary
        # directory means it is gone whatever happens next.
        local = Path(workdir) / posixpath.basename(key)
        size = storage.download(key, local)

        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                'pg_restore',
                '--no-owner',
                '--no-privileges',
                '--dbname', target,
                str(local),
            ],
            capture_output=True,
            text=True,
            timeout=getattr(settings, 'BACKUP_PG_DUMP_TIMEOUT', 1800),
            check=False,
        )

    if result.returncode != 0:
        raise RestoreError(
            f'pg_restore failed ({result.returncode}): '
            f'{result.stderr.strip()[:1000]}'
        )

    return {'key': key, 'bytes': size, 'warnings': result.stderr.strip()[:1000]}


def restore_media(key=None):
    """Unpack one media archive into the scratch media bucket."""
    bucket = target_media_bucket()

    from core.s3 import client as s3_client

    # Configuration first, network second: a missing credential should be
    # reported as itself, not as whatever the first remote call happens to
    # fail with.
    if not settings.RESTORE_TEST_S3_ACCESS_KEY or not settings.RESTORE_TEST_S3_SECRET_KEY:
        raise RestoreNotPermitted(
            'No credentials for the scratch media bucket. Set '
            'RESTORE_TEST_S3_ACCESS_KEY and RESTORE_TEST_S3_SECRET_KEY '
            '(they fall back to the MEDIA_S3_* ones, which will only work '
            'if that token can write this bucket).'
        )

    target = s3_client(
        endpoint_url=settings.RESTORE_TEST_S3_ENDPOINT,
        access_key=settings.RESTORE_TEST_S3_ACCESS_KEY,
        secret_key=settings.RESTORE_TEST_S3_SECRET_KEY,
        region=settings.RESTORE_TEST_S3_REGION,
    )

    key = key or newest_key(runner.MEDIA_PREFIX)

    restored = 0
    total = 0
    with tempfile.TemporaryDirectory(prefix='yardway-restore-media-') as workdir:
        archive_path = Path(workdir) / posixpath.basename(key)
        storage.download(key, archive_path)

        unpacked = Path(workdir) / 'unpacked'
        unpacked.mkdir()
        with tarfile.open(archive_path, 'r:gz') as archive:
            _safe_extract(archive, unpacked)

        # The archive stores paths as media/<relative path>; the bucket
        # keys are the relative path alone, matching what the database
        # already holds in every FileField.
        root = unpacked / 'media'
        base = root if root.is_dir() else unpacked
        for path in sorted(p for p in base.rglob('*') if p.is_file()):
            object_key = path.relative_to(base).as_posix()
            target.upload_file(str(path), bucket, object_key)
            restored += 1
            total += path.stat().st_size

    return {'key': key, 'files': restored, 'bytes': total, 'bucket': bucket}


def _safe_extract(archive, destination):
    """Extract, refusing any member that would land outside ``destination``.

    A tar can name ``../../etc/something``. Ours never does, but this code
    unpacks a file fetched from object storage, and "our own archive" is
    an assumption rather than a guarantee.
    """
    destination = destination.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk():
            continue
        resolved = (destination / member.name).resolve()
        if not str(resolved).startswith(str(destination)):
            raise RestoreError(f'Refusing to extract {member.name!r} outside the target directory.')
        archive.extract(member, destination)  # noqa: S202 - path checked above
