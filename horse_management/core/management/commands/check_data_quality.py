"""List rows that break a database check constraint (core/data_quality.py).

Run it before a deploy that adds a constraint: a row it names would stop
that deploy's migration. Exits non-zero when it finds any.
"""

from django.core.management.base import BaseCommand, CommandError

from core.data_quality import live_rules, report


class Command(BaseCommand):
    help = 'List rows that break the data rules (amounts, percentages, date ranges).'

    def handle(self, *args, **options):
        problems = report(live_rules())
        if problems:
            for line in problems:
                self.stdout.write(self.style.ERROR(line))
            raise CommandError(f'{len(problems)} rule(s) broken. Correct those rows, then run this again.')
        self.stdout.write(self.style.SUCCESS('Every row follows the data rules.'))
