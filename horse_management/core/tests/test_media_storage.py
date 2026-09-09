"""Uploads held somewhere every service can read.

The bug these cover: a host volume attaches to ONE service, so with
uploads on the web service's volume the nightly backup — which runs on
the worker — saw an empty directory, archived nothing, and still reported
success. A restore would have produced every record with no documents
attached.
"""

import tarfile
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.backup import runner

S3_SETTINGS = {
    'MEDIA_S3_BUCKET': 'yardway-media',
    'MEDIA_S3_ENDPOINT': 'https://account.r2.cloudflarestorage.com',
    'MEDIA_S3_ACCESS_KEY': 'key',
    'MEDIA_S3_SECRET_KEY': 'secret',
    'MEDIA_S3_REGION': 'auto',
}


class MediaArchiveTests(TestCase):
    """archive_media reads through the storage backend, not the filesystem."""

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='yardway-media-')
        self.addCleanup(self.media.cleanup)
        self.workdir = tempfile.TemporaryDirectory(prefix='yardway-work-')
        self.addCleanup(self.workdir.cleanup)
        self.storage = FileSystemStorage(location=self.media.name)
        patcher = mock.patch.object(runner, 'default_storage', self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_uploads_is_not_a_failure(self):
        """A brand-new deployment has no uploads. That is not an error,
        and it must not be reported as an archive either."""
        self.assertIsNone(runner.archive_media(self.workdir.name))

    def test_a_missing_media_directory_is_not_a_failure(self):
        missing = FileSystemStorage(location=str(Path(self.media.name) / 'gone'))
        with mock.patch.object(runner, 'default_storage', missing):
            self.assertIsNone(runner.archive_media(self.workdir.name))

    def test_nested_files_are_archived_under_media(self):
        self.storage.save('horses/bay.jpg', ContentFile(b'photo-bytes'))
        self.storage.save('documents/passports/star.pdf', ContentFile(b'pdf-bytes'))

        path = runner.archive_media(self.workdir.name)

        self.assertIsNotNone(path)
        with tarfile.open(path, 'r:gz') as archive:
            names = sorted(archive.getnames())
            self.assertEqual(
                names,
                ['media/documents/passports/star.pdf', 'media/horses/bay.jpg'],
            )
            extracted = archive.extractfile('media/horses/bay.jpg').read()
        self.assertEqual(extracted, b'photo-bytes')

    def test_files_are_read_from_the_storage_backend(self):
        """The regression test for the volume bug: nothing on the local
        MEDIA_ROOT, everything in the storage backend, and the archive
        must still contain it."""
        self.storage.save('receipts/feed.pdf', ContentFile(b'receipt'))

        with override_settings(MEDIA_ROOT='/nonexistent/path'):
            path = runner.archive_media(self.workdir.name)

        with tarfile.open(path, 'r:gz') as archive:
            self.assertEqual(archive.getnames(), ['media/receipts/feed.pdf'])


class MediaStorageSelectionTests(TestCase):
    def test_local_disk_by_default(self):
        """Without a bucket configured the app keeps using the filesystem,
        so development and any deployment that has not moved still work."""
        self.assertIsInstance(default_storage, FileSystemStorage)


class MigrateMediaCommandTests(TestCase):
    def setUp(self):
        self.source = tempfile.TemporaryDirectory(prefix='yardway-source-')
        self.addCleanup(self.source.cleanup)

    def test_refuses_to_run_without_a_bucket(self):
        with override_settings(MEDIA_S3_BUCKET=''):
            with self.assertRaises(CommandError):
                call_command('migrate_media_to_s3', source=self.source.name)

    @override_settings(**S3_SETTINGS)
    def test_dry_run_uploads_nothing(self):
        Path(self.source.name, 'horses').mkdir()
        Path(self.source.name, 'horses', 'bay.jpg').write_bytes(b'photo')
        client = mock.Mock()
        client.list_objects_v2.return_value = {'Contents': [], 'IsTruncated': False}

        with mock.patch('core.management.commands.migrate_media_to_s3._client', return_value=client):
            call_command('migrate_media_to_s3', source=self.source.name, dry_run=True)

        client.upload_file.assert_not_called()

    @override_settings(**S3_SETTINGS)
    def test_uploads_under_paths_the_database_already_stores(self):
        """Keys must match the FileField values exactly, or every existing
        row points at a file that is not there."""
        Path(self.source.name, 'documents').mkdir()
        Path(self.source.name, 'documents', 'passport.pdf').write_bytes(b'pdf')
        client = mock.Mock()
        client.list_objects_v2.return_value = {'Contents': [], 'IsTruncated': False}

        with mock.patch('core.management.commands.migrate_media_to_s3._client', return_value=client):
            call_command('migrate_media_to_s3', source=self.source.name)

        args = client.upload_file.call_args
        self.assertEqual(args[0][1], 'yardway-media')
        self.assertEqual(args[0][2], 'documents/passport.pdf')

    @override_settings(**S3_SETTINGS)
    def test_a_second_run_skips_what_is_already_there(self):
        """The practical way to run this is in front of the start command
        for one deploy, so it runs again on every restart until removed."""
        Path(self.source.name, 'bay.jpg').write_bytes(b'photo')
        client = mock.Mock()
        client.list_objects_v2.return_value = {
            'Contents': [{'Key': 'bay.jpg'}], 'IsTruncated': False,
        }

        with mock.patch('core.management.commands.migrate_media_to_s3._client', return_value=client):
            call_command('migrate_media_to_s3', source=self.source.name)

        client.upload_file.assert_not_called()
