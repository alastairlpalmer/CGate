"""Upload, list and delete backups in an S3-compatible object store.

A thin wrapper on boto3 rather than django-storages: this is not the app's
file storage, it is a separate destination with separate credentials, and
mixing the two would mean a compromise of one reaches the other.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class BackupStorageNotConfigured(RuntimeError):
    """Raised when a backup is asked for without somewhere to put it."""


def is_configured():
    return all((
        getattr(settings, 'BACKUP_S3_BUCKET', ''),
        getattr(settings, 'BACKUP_S3_ACCESS_KEY', ''),
        getattr(settings, 'BACKUP_S3_SECRET_KEY', ''),
    ))


def _client():
    if not is_configured():
        raise BackupStorageNotConfigured(
            'Backups need BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY and '
            'BACKUP_S3_SECRET_KEY. See docs/BACKUP_RESTORE.md.'
        )

    from core.s3 import client

    return client(
        endpoint_url=settings.BACKUP_S3_ENDPOINT,
        access_key=settings.BACKUP_S3_ACCESS_KEY,
        secret_key=settings.BACKUP_S3_SECRET_KEY,
        region=settings.BACKUP_S3_REGION,
    )


def upload(local_path, key):
    """Send one file. Returns the number of bytes uploaded."""
    client = _client()
    size = local_path.stat().st_size
    logger.info('Backup: uploading %s (%s bytes) to %s', local_path.name, size, key)
    client.upload_file(str(local_path), settings.BACKUP_S3_BUCKET, key)
    return size


def download(key, local_path):
    """Fetch one object into ``local_path``. Returns the byte count."""
    client = _client()
    logger.info('Backup: downloading %s', key)
    client.download_file(settings.BACKUP_S3_BUCKET, key, str(local_path))
    return local_path.stat().st_size


def list_keys(prefix):
    """Every object key under ``prefix``, following pagination.

    Without pagination this would silently stop at 1000 objects, and
    retention would then never delete anything past that point.
    """
    client = _client()
    keys = []
    token = None
    while True:
        kwargs = {'Bucket': settings.BACKUP_S3_BUCKET, 'Prefix': prefix}
        if token:
            kwargs['ContinuationToken'] = token
        response = client.list_objects_v2(**kwargs)
        keys.extend(item['Key'] for item in response.get('Contents', []))
        if not response.get('IsTruncated'):
            return keys
        token = response.get('NextContinuationToken')


def delete_keys(keys):
    """Remove objects. Returns how many were asked for."""
    if not keys:
        return 0
    client = _client()
    # delete_objects takes at most 1000 keys per call.
    for start in range(0, len(keys), 1000):
        batch = keys[start:start + 1000]
        client.delete_objects(
            Bucket=settings.BACKUP_S3_BUCKET,
            Delete={'Objects': [{'Key': key} for key in batch]},
        )
        logger.info('Backup: deleted %s expired objects', len(batch))
    return len(keys)
