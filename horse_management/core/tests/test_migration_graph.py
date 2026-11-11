"""The migration history must be one chain per app.

Two branches that each add a migration with the same parent merge cleanly
in Git, but Django then refuses to migrate ("Conflicting migrations
detected"), and the deploy stops. The test settings switch migrations off
for speed, so ``makemigrations --check`` in CI cannot see this. This test
reads the real migration files.
"""

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, override_settings


class MigrationGraphTests(SimpleTestCase):
    @override_settings(MIGRATION_MODULES={})
    def test_no_app_has_two_latest_migrations(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        conflicts = loader.detect_conflicts()
        self.assertEqual(
            conflicts, {},
            'Two migrations share a parent. Run `python manage.py makemigrations --merge` '
            f'(with real settings) for: {conflicts}',
        )
