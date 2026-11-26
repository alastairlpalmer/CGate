"""Test settings for a run on PostgreSQL, the database production uses.

The default test settings use SQLite, which ignores row locks
(``select_for_update``) and some constraints. This file keeps all of the
other test settings and changes only the database.

Usage:
    TEST_DATABASE_URL=postgres://user:pass@localhost:5432/cgate \
    DJANGO_SETTINGS_MODULE=horse_management.test_settings_pg \
    python manage.py test
"""

import environ

from .test_settings import *  # noqa: F401, F403

DATABASES = {
    'default': environ.Env().db_url(
        'TEST_DATABASE_URL', default='postgres://postgres:postgres@localhost:5432/cgate',
    ),
}
