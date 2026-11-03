"""List where the database schema differs from the code (core/schema_drift.py).

Read-only. Run it in the web service's console before a deploy that adds
columns or tables, or after one fails in `migrate`. Exits non-zero when
it finds a difference.
"""

from django.core.management.base import BaseCommand, CommandError

from core.schema_drift import column_differences, migration_differences


class Command(BaseCommand):
    help = 'List tables, columns and migrations where the database differs from the code.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--columns-only', action='store_true',
            help='Compare tables and columns only, not the migration records.',
        )

    def handle(self, *args, **options):
        problems = column_differences()
        if not options['columns_only']:
            problems += migration_differences()
        if problems:
            for line in problems:
                self.stdout.write(self.style.ERROR(line))
            raise CommandError(
                f'{len(problems)} difference(s) between the database and the code.'
            )
        self.stdout.write(self.style.SUCCESS('The database matches the code.'))
