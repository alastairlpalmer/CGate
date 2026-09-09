"""The restore test, and the guards that keep it away from production.

Every guard here exists because the failure it prevents is unrecoverable.
A restore writes over whatever it is aimed at, so most of these tests are
about refusing to run rather than about running.
"""

import tarfile
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.backup import restore

LIVE = {
    'DATABASES': {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': 'aws-1-eu-west-1.pooler.supabase.com',
            'PORT': '6543',
            'NAME': 'postgres',
            'USER': 'postgres.abc',
            'PASSWORD': 'x',
        }
    },
    'BACKUP_DATABASE_URL': 'postgresql://postgres.abc:x@aws-1-eu-west-1.pooler.supabase.com:5432/postgres',
}

SCRATCH = 'postgresql://u:p@scratch.railway.internal:5432/restore_test'


@override_settings(**LIVE)
class DatabaseTargetGuardTests(TestCase):
    @override_settings(RESTORE_TEST_DATABASE_URL='')
    def test_refuses_without_an_explicit_target(self):
        """No default and no fallback to DATABASE_URL. Restoring into
        production is not a mistake anyone recovers from."""
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_database_url()

    @override_settings(RESTORE_TEST_DATABASE_URL=LIVE['BACKUP_DATABASE_URL'])
    def test_refuses_the_backup_connection_string(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_database_url()

    @override_settings(
        RESTORE_TEST_DATABASE_URL=
        'postgresql://postgres.abc:x@aws-1-eu-west-1.pooler.supabase.com:6543/postgres'
    )
    def test_refuses_the_live_database_through_a_different_port(self):
        """The app reaches Supabase on 6543 and pg_dump on 5432. Same
        database, two doors — the port must not make it look scratch."""
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_database_url()

    @override_settings(RESTORE_TEST_DATABASE_URL='postgresql://scratch.railway.internal')
    def test_refuses_a_url_with_no_database_name(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_database_url()

    @override_settings(RESTORE_TEST_DATABASE_URL=SCRATCH)
    def test_accepts_a_genuinely_separate_database(self):
        self.assertEqual(restore.target_database_url(), SCRATCH)

    @override_settings(RESTORE_TEST_DATABASE_URL='  ' + SCRATCH + '  ')
    def test_surrounding_whitespace_does_not_defeat_the_comparison(self):
        self.assertEqual(restore.target_database_url(), SCRATCH)


@override_settings(MEDIA_S3_BUCKET='yardway-media', BACKUP_S3_BUCKET='yardway-backup')
class MediaBucketGuardTests(TestCase):
    @override_settings(RESTORE_TEST_MEDIA_BUCKET='')
    def test_refuses_without_an_explicit_bucket(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_media_bucket()

    @override_settings(RESTORE_TEST_MEDIA_BUCKET='yardway-media')
    def test_refuses_the_live_media_bucket(self):
        """Restoring an old archive over live uploads would replace
        today's documents with last night's."""
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_media_bucket()

    @override_settings(RESTORE_TEST_MEDIA_BUCKET='yardway-backup')
    def test_refuses_the_backup_bucket(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.target_media_bucket()

    @override_settings(RESTORE_TEST_MEDIA_BUCKET='yardway-restore-test')
    def test_accepts_a_separate_bucket(self):
        self.assertEqual(restore.target_media_bucket(), 'yardway-restore-test')


class NewestKeyTests(TestCase):
    def test_picks_the_latest_by_name_not_by_upload_time(self):
        """Names carry the timestamp. An object re-uploaded later would
        have a fresh modified time while holding older data."""
        keys = [
            'backups/database/yardway-db-2026-09-07T0200.dump',
            'backups/database/yardway-db-2026-09-09T0200.dump',
            'backups/database/yardway-db-2026-09-08T0200.dump',
        ]
        with mock.patch.object(restore.storage, 'list_keys', return_value=keys):
            self.assertEqual(
                restore.newest_key('backups/database/'),
                'backups/database/yardway-db-2026-09-09T0200.dump',
            )

    def test_an_empty_bucket_is_an_error_not_a_silent_no_op(self):
        with mock.patch.object(restore.storage, 'list_keys', return_value=[]):
            with self.assertRaises(restore.RestoreError):
                restore.newest_key('backups/database/')


class ArchiveExtractionTests(TestCase):
    def test_a_member_pointing_outside_the_target_is_refused(self):
        """Our own archives never do this. The archive is fetched from
        object storage, so 'our own' is an assumption, not a guarantee."""
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            payload = work / 'payload'
            payload.write_bytes(b'x')
            archive_path = work / 'evil.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                archive.add(payload, arcname='../escaped')

            destination = work / 'out'
            destination.mkdir()
            with tarfile.open(archive_path, 'r:gz') as archive:
                with self.assertRaises(restore.RestoreError):
                    restore._safe_extract(archive, destination)

            self.assertFalse((work / 'escaped').exists())

    def test_ordinary_members_extract(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            payload = work / 'bay.jpg'
            payload.write_bytes(b'photo')
            archive_path = work / 'media.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                archive.add(payload, arcname='media/horses/bay.jpg')

            destination = work / 'out'
            destination.mkdir()
            with tarfile.open(archive_path, 'r:gz') as archive:
                restore._safe_extract(archive, destination)

            self.assertEqual((destination / 'media' / 'horses' / 'bay.jpg').read_bytes(), b'photo')


@override_settings(**LIVE)
class CommandTests(TestCase):
    @override_settings(RESTORE_TEST_DATABASE_URL='')
    def test_the_command_reports_a_refusal_as_a_command_error(self):
        """A traceback in a deploy log is noise. The refusal has to read
        as an instruction, because the person seeing it is following one."""
        with self.assertRaises(CommandError):
            call_command('restore_test', '--database')

    @override_settings(RESTORE_TEST_DATABASE_URL=SCRATCH)
    def test_key_needs_the_caller_to_say_which_backup_it_names(self):
        with self.assertRaises(CommandError):
            call_command('restore_test', '--key', 'backups/database/x.dump')

    def test_list_shows_what_is_in_the_bucket_and_changes_nothing(self):
        keys = [
            'backups/database/yardway-db-2026-09-08T0200.dump',
            'backups/media/yardway-media-2026-09-08T0200.tar.gz',
        ]
        with mock.patch.object(restore.storage, 'list_keys', side_effect=[keys[:1], keys[1:]]):
            with mock.patch.object(restore, 'restore_database') as restore_db:
                call_command('restore_test', '--list')
        restore_db.assert_not_called()
