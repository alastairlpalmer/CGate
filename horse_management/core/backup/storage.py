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

    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        # Cloudflare R2 and Backblaze B2 need an endpoint; plain AWS S3
        # does not, and boto3 works out the right one from the region.
        endpoint_url=settings.BACKUP_S3_ENDPOINT or None,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
        region_name=settings.BACKUP_S3_REGION,
        config=Config(
            # R2 requires the v4 signature and path-style addressing.
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            # A backup that hangs would hold a worker slot all night.
            connect_timeout=30,
            read_timeout=300,
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


def upload(local_path, key):
    """Send one file. Returns the number of bytes uploaded."""
    client = _client()
    size = local_path.stat().st_size
    logger.info('Backup: uploading %s (%s bytes) to %s', local_path.name, size, key)
    client.upload_file(str(local_path), settings.BACKUP_S3_BUCKET, key)
    return size


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
