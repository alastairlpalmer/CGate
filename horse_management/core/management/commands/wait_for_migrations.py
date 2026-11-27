"""Wait until the database has every migration this code needs.

On Railway the web service runs ``migrate`` before its new code starts
(preDeployCommand), but the worker and beat services deploy at the same
time and started their new code at once: a task could then read a column
that did not exist yet (pre-launch review). Their start commands run
this first:

    python manage.py wait_for_migrations && celery -A horse_management worker ...

It checks every few seconds and exits 0 when nothing is left to apply,
or exits 1 after ``--timeout`` seconds (the service then restarts and
waits again). It never applies a migration itself: that stays with the
web deploy, so two services never migrate at once.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


def pending_migrations(alias='default'):
    """The migrations this code has that the database has not applied."""
    connection = connections[alias]
    connection.prepare_database()
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return [migration for migration, _backwards in executor.migration_plan(targets)]


class Command(BaseCommand):
    help = 'Wait until every migration of this code is applied (for the worker and beat).'

    def add_arguments(self, parser):
        parser.add_argument('--timeout', type=int, default=900, help='Seconds to wait (default 900).')
        parser.add_argument('--interval', type=float, default=5, help='Seconds between checks (default 5).')

    def handle(self, *args, timeout, interval, **options):
        deadline = time.monotonic() + timeout
        said = False
        while True:
            try:
                pending = pending_migrations()
            except Exception as exc:  # The database is not reachable yet.
                pending = None
                if not said:
                    self.stdout.write(f'Waiting for the database: {exc}')
                    said = True
            else:
                if not pending:
                    self.stdout.write('All migrations are applied.')
                    return
                if not said:
                    names = ', '.join(f'{m.app_label}.{m.name}' for m in pending[:5])
                    self.stdout.write(f'Waiting for {len(pending)} migration(s): {names}')
                    said = True
            if time.monotonic() >= deadline:
                raise CommandError(
                    f'Migrations still not applied after {timeout} seconds. '
                    'Check the web deploy, which runs migrate.'
                )
            time.sleep(interval)
