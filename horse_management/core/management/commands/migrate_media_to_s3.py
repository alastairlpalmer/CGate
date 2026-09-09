"""Copy uploads from a local media directory into the media bucket.

Run this once when moving uploads off a local disk. It reads the files
that are already on the volume and writes them to the bucket under the
same relative paths, which is what the database rows already store — so
no data migration is needed, and every existing FileField keeps working.

    python manage.py migrate_media_to_s3 --dry-run   # show what would go
    python manage.py migrate_media_to_s3             # do it

Safe to run more than once: a key that is already in the bucket is left
alone unless --overwrite is given. That matters because the practical way
to run this on a host where the volume is attached to the web service is
to put it in front of the start command for one deploy, which means it
runs again on every restart until you take it out.
"""

import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _client():
    """A boto3 client for the media bucket.

    Built from the MEDIA_S3_* settings directly rather than through the
    storage backend, so the copy can run while uploads are still being
    served from the local disk — that is the whole point of a migration.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=settings.MEDIA_S3_ENDPOINT or None,
        aws_access_key_id=settings.MEDIA_S3_ACCESS_KEY,
        aws_secret_access_key=settings.MEDIA_S3_SECRET_KEY,
        region_name=settings.MEDIA_S3_REGION,
        config=Config(
            # R2 requires the v4 signature and path-style addressing.
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            connect_timeout=30,
            read_timeout=300,
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


class Command(BaseCommand):
    help = 'Copy uploaded media from a local directory into the media bucket.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source', default=None,
            help='Directory to copy from. Defaults to MEDIA_ROOT.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would be copied and change nothing.',
        )
        parser.add_argument(
            '--overwrite', action='store_true',
            help='Replace objects that are already in the bucket. Off by '
                 'default so a repeated run is cheap and harmless.',
        )

    def handle(self, *args, **options):
        if not settings.MEDIA_S3_BUCKET:
            raise CommandError(
                'MEDIA_S3_BUCKET, MEDIA_S3_ACCESS_KEY and MEDIA_S3_SECRET_KEY '
                'must be set before media can be copied to the bucket.'
            )

        source = Path(options['source'] or settings.MEDIA_ROOT)
        if not source.is_dir():
            raise CommandError(f'{source} is not a directory. Nothing to copy.')

        files = sorted(path for path in source.rglob('*') if path.is_file())
        if not files:
            self.stdout.write(f'{source} holds no files. Nothing to copy.')
            return

        client = _client()
        existing = self._existing_keys(client)
        copied = skipped = 0
        copied_bytes = 0

        for path in files:
            key = path.relative_to(source).as_posix()
            if key in existing and not options['overwrite']:
                skipped += 1
                continue

            size = path.stat().st_size
            if options['dry_run']:
                self.stdout.write(f'would copy {key} ({size} bytes)')
            else:
                content_type, _ = mimetypes.guess_type(path.name)
                client.upload_file(
                    str(path),
                    settings.MEDIA_S3_BUCKET,
                    key,
                    ExtraArgs={'ContentType': content_type} if content_type else None,
                )
                logger.info('Media migration: copied %s (%s bytes)', key, size)
            copied += 1
            copied_bytes += size

        verb = 'would copy' if options['dry_run'] else 'copied'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {copied} files ({copied_bytes} bytes), '
            f'skipped {skipped} already in the bucket.'
        ))

    def _existing_keys(self, client):
        """Keys already in the bucket, so a repeat run copies nothing.

        Paginated: without it this would stop at 1000 objects and start
        re-uploading everything past that point on the second run.
        """
        keys = set()
        token = None
        while True:
            kwargs = {'Bucket': settings.MEDIA_S3_BUCKET}
            if token:
                kwargs['ContinuationToken'] = token
            response = client.list_objects_v2(**kwargs)
            keys.update(item['Key'] for item in response.get('Contents', []))
            if not response.get('IsTruncated'):
                return keys
            token = response.get('NextContinuationToken')
