"""Test-only settings.

Skips migrations entirely and builds tables directly from model state via
syncdb — much faster than replaying the migration history for every test
run. (A historical migration-graph issue that blocked fresh ``migrate``
runs has since been fixed; ``migrate`` from an empty DB works.)

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
