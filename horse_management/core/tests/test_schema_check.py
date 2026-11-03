"""manage.py schema_check: where the database differs from the models."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access
from core.schema_drift import column_differences


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
