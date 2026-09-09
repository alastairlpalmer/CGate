"""One place that knows how to talk to an S3-compatible object store.

Three callers need a client and all three need the same non-obvious
settings, so the configuration lives here rather than being copied:

* the nightly backup, writing to the backup bucket
* the media migration, writing to the media bucket
* the restore test, reading the backup bucket and writing a scratch one

They use different credentials on purpose — a leaked media key must not
reach the backups — so this takes them as arguments rather than reading
settings itself.
"""


def client(*, endpoint_url, access_key, secret_key, region='auto'):
    """A boto3 S3 client configured for Cloudflare R2 and friends.

    R2 rejects the older signature and virtual-host addressing, so both
    are pinned here. The timeouts exist because a hung transfer inside a
    Celery task would hold a worker slot until the task time limit.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        # R2 and B2 need an endpoint; plain AWS S3 works it out from the
        # region, and passing None is how boto3 is told to do that.
        endpoint_url=endpoint_url or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            connect_timeout=30,
            read_timeout=300,
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )
