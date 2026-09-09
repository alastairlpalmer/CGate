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


@override_settings(
    MEDIA_S3_BUCKET='yardway-media',
    BACKUP_S3_BUCKET='yardway-backup',
    RESTORE_TEST_MEDIA_BUCKET='yardway-restore-test',
)
class MediaCredentialTests(TestCase):
    """The scratch bucket gets its own token, not production's.

    An R2 token scoped to one bucket cannot write another, so falling
    back to the live media credentials only works if that token was
    widened to cover the scratch bucket — which would leave production
    media writable by a testing credential long after the test.
    """

    @override_settings(
        RESTORE_TEST_S3_ACCESS_KEY='',
        RESTORE_TEST_S3_SECRET_KEY='',
    )
    def test_refuses_when_there_are_no_credentials_at_all(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.restore_media()

    @override_settings(
        RESTORE_TEST_S3_ENDPOINT='https://scratch.example.com',
        RESTORE_TEST_S3_ACCESS_KEY='scratch-key',
        RESTORE_TEST_S3_SECRET_KEY='scratch-secret',
        RESTORE_TEST_S3_REGION='auto',
        MEDIA_S3_ACCESS_KEY='live-key',
        MEDIA_S3_SECRET_KEY='live-secret',
    )
    def test_the_scratch_token_is_used_not_the_live_media_one(self):
        made = {}

        def fake_client(**kwargs):
            made.update(kwargs)
            return mock.Mock()

        with mock.patch('core.s3.client', side_effect=fake_client):
            with mock.patch.object(restore, 'newest_key', return_value='backups/media/x.tar.gz'):
                with mock.patch.object(restore.storage, 'download') as download:
                    download.side_effect = self._write_empty_archive
                    restore.restore_media()

        self.assertEqual(made['access_key'], 'scratch-key')
        self.assertEqual(made['secret_key'], 'scratch-secret')
        self.assertEqual(made['endpoint_url'], 'https://scratch.example.com')

    @staticmethod
    def _write_empty_archive(key, local_path):
        with tarfile.open(local_path, 'w:gz'):
            pass
        return local_path.stat().st_size


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


@override_settings(**LIVE, RESTORE_TEST_DATABASE_URL=SCRATCH)
class PgRestoreExitCodeTests(TestCase):
    """pg_restore exits 1 whenever it ignored ANY error.

    A Supabase dump carries CREATE EXTENSION supabase_vault, which plain
    PostgreSQL does not have, so restoring one into a scratch database
    always reports three errors and carries on. Trusting the exit code
    called that a failed restore; ignoring it would call a genuinely
    broken one a success.
    """

    VAULT_NOISE = (
        'pg_restore: error: could not execute query: ERROR:  extension '
        '"supabase_vault" is not available\n'
        'pg_restore: warning: errors ignored on restore: 3'
    )

    def _run(self, returncode, stderr, complete=True):
        completed = mock.Mock(returncode=returncode, stderr=stderr)
        with mock.patch.object(restore, 'target_is_empty', return_value=True), \
             mock.patch.object(restore, 'restored_looks_complete', return_value=complete), \
             mock.patch.object(restore, 'newest_key', return_value='backups/database/x.dump'), \
             mock.patch.object(restore.storage, 'download', return_value=1234), \
             mock.patch.object(restore.shutil, 'which', return_value='/usr/bin/pg_restore'), \
             mock.patch.object(restore.subprocess, 'run', return_value=completed):
            return restore.restore_database()

    def test_a_clean_restore_succeeds(self):
        result = self._run(0, '')
        self.assertFalse(result['ignored_errors'])

    def test_ignored_errors_stand_when_the_data_arrived(self):
        """The exact case that stopped the first real restore."""
        result = self._run(1, self.VAULT_NOISE, complete=True)
        self.assertTrue(result['ignored_errors'])
        self.assertIn('supabase_vault', result['warnings'])

    def test_ignored_errors_fail_when_the_data_did_not_arrive(self):
        """Exit 1 is only forgiven because the tables were checked."""
        with self.assertRaises(restore.RestoreError) as caught:
            self._run(1, self.VAULT_NOISE, complete=False)
        self.assertIn('missing the app tables', str(caught.exception))

    def test_a_hard_failure_is_still_a_failure(self):
        """Exit 2 and above is pg_restore giving up. No second-guessing."""
        with self.assertRaises(restore.RestoreError):
            self._run(2, 'pg_restore: error: could not open input file')


@override_settings(**LIVE)
class ResetTests(TestCase):
    """The most destructive operation here, so the guards come first."""

    @override_settings(RESTORE_TEST_DATABASE_URL='')
    def test_reset_refuses_without_an_explicit_target(self):
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.reset_scratch_database()

    @override_settings(RESTORE_TEST_DATABASE_URL=LIVE['BACKUP_DATABASE_URL'])
    def test_reset_refuses_a_production_target(self):
        """Dropping the public schema of the live database would end the
        business. The same guard that protects the restore protects this."""
        with self.assertRaises(restore.RestoreNotPermitted):
            restore.reset_scratch_database()

    @override_settings(RESTORE_TEST_DATABASE_URL=SCRATCH)
    def test_reset_drops_and_recreates_the_public_schema(self):
        cursor = mock.MagicMock()
        holder = mock.MagicMock()
        holder.__enter__.return_value = cursor
        with mock.patch.object(restore, '_scratch_cursor', return_value=holder):
            restore.reset_scratch_database()
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements, [
            'DROP SCHEMA IF EXISTS public CASCADE',
            'CREATE SCHEMA public',
        ])


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
