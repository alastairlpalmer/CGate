"""Deploy order (pre-launch review).

* The worker and beat wait for the web deploy's migrate before their new
  code starts (``wait_for_migrations``).
* A new NOT NULL column needs a database default, so the old code, still
  running while the deploy migrates, can insert rows without it.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.db.migrations import AddField
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, override_settings


class WaitForMigrationsTests(SimpleTestCase):
    def test_it_waits_until_nothing_is_left(self):
        pending = mock.Mock(app_label='core', name='0099_new')
        with mock.patch('core.management.commands.wait_for_migrations.pending_migrations',
                        side_effect=[[pending], [pending], []]) as check, \
                mock.patch('time.sleep'):
            out = StringIO()
            call_command('wait_for_migrations', interval=0, stdout=out)
        self.assertEqual(check.call_count, 3)
        self.assertIn('All migrations are applied', out.getvalue())

    def test_it_gives_up_after_the_timeout(self):
        pending = mock.Mock(app_label='core', name='0099_new')
        with mock.patch('core.management.commands.wait_for_migrations.pending_migrations',
                        return_value=[pending]), mock.patch('time.sleep'):
            with self.assertRaises(CommandError):
                call_command('wait_for_migrations', timeout=0, interval=0, stdout=StringIO())

    def test_the_worker_and_beat_wait_first(self):
        import json
        from pathlib import Path

        folder = Path(__file__).resolve().parents[2] / 'railway'
        for name in ('worker.json', 'beat.json'):
            command = json.loads((folder / name).read_text())['deploy']['startCommand']
            self.assertTrue(command.startswith('python manage.py wait_for_migrations && celery'), name)


# The last migration of each app when this rule came in (pre-launch
# review, November 2026). The ones before were applied in production
# already; changing them now would change nothing there. The rule checks
# every migration after these.
LAST_BEFORE_THE_RULE = {
    'core': 74, 'billing': 24, 'invoicing': 18, 'health': 16, 'training': 11,
    'cvet': 11, 'pedigree': 5, 'notifications': 1, 'xero_integration': 5,
}


@override_settings(MIGRATION_MODULES={})
class NewColumnsHaveADatabaseDefaultTests(SimpleTestCase):
    def test_every_new_not_null_column_has_a_db_default(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        missing = []
        for (app, name), migration in loader.disk_migrations.items():
            if app not in LAST_BEFORE_THE_RULE or not name[:4].isdigit():
                continue
            if int(name[:4]) <= LAST_BEFORE_THE_RULE[app]:
                continue
            for operation in migration.operations:
                if not isinstance(operation, AddField):
                    continue
                field = operation.field
                if field.null or field.many_to_many or field.has_db_default():
                    continue
                if isinstance(field, (models.AutoField,)):
                    continue
                missing.append(f'{app}.{name}: {operation.model_name}.{operation.name}')
        self.assertEqual(missing, [], (
            'A new NOT NULL column needs db_default=..., so the old code can still insert '
            'rows while the deploy runs. Add db_default, or make the column null=True.'
        ))

    def test_the_rule_finds_a_column_with_no_db_default(self):
        field = models.CharField(max_length=5, default='x')
        self.assertFalse(field.null or field.has_db_default())
        self.assertTrue(models.CharField(max_length=5, default='x', db_default='x').has_db_default())
