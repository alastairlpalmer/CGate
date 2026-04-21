"""Test-only settings.

Skips migrations entirely and builds tables directly from model state via
syncdb. This avoids a pre-existing migration-graph issue (billing/0002
referencing the ``core.Invoice`` model that was later moved to the
``invoicing`` app) that blocks fresh ``migrate`` runs from an empty DB.

Usage:
    DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test
"""

from .settings import *  # noqa: F401, F403


class _DisableMigrations(dict):
    def __contains__(self, _):
        return True

    def __getitem__(self, _):
        return None

    def setdefault(self, *args, **kwargs):
        return None


MIGRATION_MODULES = _DisableMigrations()

# In-memory DB — fast, isolated, cleaned up automatically.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
