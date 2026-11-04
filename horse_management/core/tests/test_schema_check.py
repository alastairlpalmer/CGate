"""manage.py schema_check: where the database differs from the models."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access
from core.schema_drift import column_differences, migration_differences


class ColumnDifferencesTests(TestCase):
    def _sql(self, *statements):
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def test_a_database_built_from_the_models_matches(self):
        self.assertEqual(column_differences(), [])

    def test_leftover_columns_and_tables_are_listed(self):
        # What an abandoned branch left in production in April 2026.
        self._sql(
            'CREATE TABLE invoicing_invoicerun (id integer PRIMARY KEY)',
            'ALTER TABLE core_invoice ADD COLUMN run_id bigint NULL',
            'ALTER TABLE core_businesssettings ADD COLUMN next_run_number integer NOT NULL DEFAULT 1',
        )
        self.assertEqual(column_differences(), [
            'Extra column: core_businesssettings.next_run_number (NOT NULL: inserts may fail)',
            'Extra column: core_invoice.run_id (allows NULL)',
            'Extra table: invoicing_invoicerun',
        ])

    def test_the_command_fails_and_lists_each_difference(self):
        self._sql('ALTER TABLE core_invoice ADD COLUMN run_id bigint NULL')
        out = StringIO()
        with self.assertRaisesMessage(CommandError, '1 difference(s)'):
            call_command('schema_check', '--columns-only', stdout=out)
        self.assertIn('Extra column: core_invoice.run_id (allows NULL)', out.getvalue())

    def test_the_command_passes_on_a_matching_database(self):
        out = StringIO()
        call_command('schema_check', '--columns-only', stdout=out)
        self.assertIn('The database matches the code.', out.getvalue())


class DatabaseCheckPageTests(TestCase):
    """Settings → Database Check: the same check, for Administrators, no console."""

    def test_an_administrator_sees_the_result(self):
        self.client.force_login(make_admin())
        response = self.client.get(reverse('database_check'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['columns'], [])
        settings_page = self.client.get(reverse('app_settings'))
        self.assertContains(settings_page, reverse('database_check'))

    def test_differences_are_listed(self):
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE core_invoice ADD COLUMN run_id bigint NULL')
        self.client.force_login(make_admin())
        response = self.client.get(reverse('database_check'))
        self.assertContains(response, 'Extra column: core_invoice.run_id (allows NULL)')

    def test_other_users_are_refused(self):
        self.client.force_login(make_user_with_access('office', users='full', settings='full'))
        self.assertEqual(self.client.get(reverse('database_check')).status_code, 403)


class DropAprilLeftoversMigrationTests(TransactionTestCase):
    """core 0049 removes what Database Check listed in production."""

    def _migration(self):
        import importlib
        return importlib.import_module('core.migrations.0049_drop_april_leftovers')

    def _run(self):
        from django.apps import apps as global_apps
        with connection.schema_editor() as editor:
            self._migration().drop_leftovers(global_apps, editor)

    def test_leftovers_are_dropped_and_the_database_matches(self):
        from django.db.migrations.recorder import MigrationRecorder
        MigrationRecorder(connection).ensure_schema()
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE invoicing_invoicerun (id integer PRIMARY KEY)')
            cursor.execute('CREATE TABLE core_settingschangelog (id integer PRIMARY KEY)')
            cursor.execute(
                'ALTER TABLE core_invoice ADD COLUMN run_id bigint NULL '
                'REFERENCES invoicing_invoicerun (id)'
            )
            for column in ('currency_symbol', 'farrier_revisit_weeks', 'next_run_number',
                           'invoice_due_warning_days', 'worm_egg_threshold',
                           'xero_invoice_status'):
                cursor.execute(
                    f'ALTER TABLE core_businesssettings ADD COLUMN {column} '
                    "varchar(10) NOT NULL DEFAULT '1'"
                )
            for app, name in self._migration().LEFTOVER_MIGRATION_RECORDS:
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "VALUES (%s, %s, CURRENT_TIMESTAMP)", [app, name],
                )
        self.assertEqual(len(column_differences()), 9)

        self._run()

        self.assertEqual(column_differences(), [])
        self.assertNotIn('Applied but not in the code', ' '.join(migration_differences()))

    def test_it_runs_on_a_database_without_leftovers(self):
        self._run()
        self._run()
        self.assertEqual(column_differences(), [])

    def test_no_migration_file_has_a_deleted_record_name(self):
        from django.db.migrations.loader import MigrationLoader
        on_disk = set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
        for record in self._migration().LEFTOVER_MIGRATION_RECORDS:
            self.assertNotIn(record, on_disk)
