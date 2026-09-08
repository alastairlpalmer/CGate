"""The backup job: what it uploads, and how it behaves when it fails.

The failure tests carry the weight here. A backup that fails quietly is
worse than having none, because you stop checking it.
"""

from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.backup import runner, storage
from core.models import BackupRun, BusinessSettings
from core.tasks import run_nightly_backup

CONFIGURED = {
    'BACKUP_ENABLED': True,
    'BACKUP_S3_BUCKET': 'yardway-backups',
    'BACKUP_S3_ACCESS_KEY': 'key',
    'BACKUP_S3_SECRET_KEY': 'secret',
}

SUMMARY = {
    'database_bytes': 2048,
    'media_bytes': 4096,
    'pruned': 3,
    'keys': ['backups/database/yardway-db-2026-09-08T0200.dump'],
}


class StorageConfigurationTests(TestCase):
    @override_settings(**CONFIGURED)
    def test_configured_when_all_three_are_set(self):
        self.assertTrue(storage.is_configured())

    def test_a_missing_credential_means_not_configured(self):
        """Partly-configured must count as off. Otherwise the job runs
        every night and fails every night on a half-set-up deployment."""
        for missing in ('BACKUP_S3_BUCKET', 'BACKUP_S3_ACCESS_KEY', 'BACKUP_S3_SECRET_KEY'):
            partial = dict(CONFIGURED)
            partial[missing] = ''
            with self.subTest(missing=missing), override_settings(**partial):
                self.assertFalse(storage.is_configured())

    @override_settings(BACKUP_S3_BUCKET='', BACKUP_S3_ACCESS_KEY='', BACKUP_S3_SECRET_KEY='')
    def test_running_without_configuration_raises_rather_than_no_ops(self):
        with self.assertRaises(storage.BackupStorageNotConfigured):
            runner.run()


@override_settings(**CONFIGURED)
class NightlyTaskTests(TestCase):
    def test_a_successful_run_is_recorded(self):
        with mock.patch.object(runner, 'run', return_value=SUMMARY):
            self.assertEqual(run_nightly_backup(), 'ok')

        run = BackupRun.objects.get()
        self.assertEqual(run.status, BackupRun.STATUS_SUCCESS)
        self.assertEqual(run.database_bytes, 2048)
        self.assertEqual(run.media_bytes, 4096)
        self.assertEqual(run.objects_pruned, 3)
        self.assertIn('yardway-db-2026-09-08T0200.dump', run.keys)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(BackupRun.last_success(), run)

    def test_a_failure_is_recorded_and_re_raised(self):
        """Re-raising is what makes Celery mark the task failed, which is
        what makes Sentry report it."""
        with mock.patch.object(runner, 'run', side_effect=RuntimeError('pg_dump exploded')):
            with self.assertRaises(RuntimeError):
                run_nightly_backup()

        run = BackupRun.objects.get()
        self.assertEqual(run.status, BackupRun.STATUS_FAILED)
        self.assertIn('pg_dump exploded', run.error)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(BackupRun.last_success())

    def test_a_failure_emails_the_yard(self):
        from django.core import mail

        business = BusinessSettings.get_settings()
        business.email = 'yard@example.com'
        business.save()

        with mock.patch.object(runner, 'run', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                run_nightly_backup()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('FAILED', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['yard@example.com'])

    def test_a_broken_mailer_does_not_hide_the_backup_failure(self):
        """If notification fails too, the original error must still surface."""
        with mock.patch.object(runner, 'run', side_effect=RuntimeError('the real problem')), \
             mock.patch('core.tasks.send_mail', side_effect=OSError('smtp down')), \
             mock.patch('core.tasks.mail_admins', side_effect=OSError('smtp down')):
            with self.assertRaises(RuntimeError) as caught:
                run_nightly_backup()

        self.assertIn('the real problem', str(caught.exception))
        self.assertEqual(BackupRun.objects.get().status, BackupRun.STATUS_FAILED)

    @override_settings(BACKUP_ENABLED=False)
    def test_disabled_means_no_run_and_no_row(self):
        with mock.patch.object(runner, 'run') as run_backup:
            self.assertEqual(run_nightly_backup(), 'disabled')
        run_backup.assert_not_called()
        self.assertFalse(BackupRun.objects.exists())

    @override_settings(BACKUP_S3_BUCKET='')
    def test_enabled_but_unconfigured_warns_instead_of_failing_nightly(self):
        with self.assertLogs('core.tasks', level='WARNING'):
            self.assertEqual(run_nightly_backup(), 'not configured')
        self.assertFalse(BackupRun.objects.exists())

    @override_settings(BACKUP_INCLUDE_MEDIA=False)
    def test_the_media_switch_is_passed_through(self):
        with mock.patch.object(runner, 'run', return_value=SUMMARY) as run_backup:
            run_nightly_backup()
        run_backup.assert_called_once_with(include_media=False)


@override_settings(**CONFIGURED)
class ManagementCommandTests(TestCase):
    def test_it_takes_a_backup_and_records_it(self):
        with mock.patch.object(runner, 'run', return_value=SUMMARY):
            call_command('backup')
        self.assertEqual(BackupRun.objects.get().status, BackupRun.STATUS_SUCCESS)

    def test_a_failure_exits_non_zero(self):
        """A cron or CI step calling this must be able to tell it failed."""
        with mock.patch.object(runner, 'run', side_effect=RuntimeError('nope')):
            with self.assertRaises(CommandError):
                call_command('backup')
        self.assertEqual(BackupRun.objects.get().status, BackupRun.STATUS_FAILED)

    @override_settings(BACKUP_S3_BUCKET='')
    def test_it_refuses_clearly_when_not_configured(self):
        with self.assertRaises(CommandError) as caught:
            call_command('backup')
        self.assertIn('BACKUP_S3_BUCKET', str(caught.exception))

    def test_no_media_skips_the_media_archive(self):
        with mock.patch.object(runner, 'run', return_value=SUMMARY) as run_backup:
            call_command('backup', '--no-media')
        run_backup.assert_called_once_with(include_media=False)

    def test_prune_only_writes_no_backup_run_row(self):
        with mock.patch.object(runner, 'prune', return_value=2), \
             mock.patch.object(runner, 'run') as run_backup:
            call_command('backup', '--prune-only')
        run_backup.assert_not_called()
        self.assertFalse(BackupRun.objects.exists())


class DumpDatabaseTests(TestCase):
    def test_it_refuses_to_pretend_sqlite_is_a_backup(self):
        """The test and local databases are SQLite. Silently producing
        nothing here would look like a working backup."""
        with self.assertRaises(runner.BackupError) as caught:
            runner.dump_database('/tmp')
        self.assertIn('PostgreSQL', str(caught.exception))

    @override_settings(DATABASES={'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'yardway', 'USER': 'u', 'PASSWORD': 'p',
        'HOST': 'db.example', 'PORT': 5432,
    }})
    def test_a_missing_pg_dump_says_so_plainly(self):
        with mock.patch('core.backup.runner.shutil.which', return_value=None):
            with self.assertRaises(runner.BackupError) as caught:
                runner.dump_database('/tmp')
        self.assertIn('pg_dump is not installed', str(caught.exception))


class BackupRunModelTests(TestCase):
    def test_last_success_ignores_failed_and_running_rows(self):
        BackupRun.objects.create(status=BackupRun.STATUS_FAILED)
        BackupRun.objects.create(status=BackupRun.STATUS_RUNNING)
        self.assertIsNone(BackupRun.last_success())

        good = BackupRun.objects.create(status=BackupRun.STATUS_SUCCESS)
        self.assertEqual(BackupRun.last_success(), good)

    def test_duration_is_none_until_the_run_finishes(self):
        run = BackupRun.objects.create()
        self.assertIsNone(run.duration)
