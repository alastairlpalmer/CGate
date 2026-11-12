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
        # Not a check constraint — it spans two tables — but the same kind
        # of rule: a horse's stable is in the barn the horse lives in.
        from core.stables import stray_stays
        strays = list(stray_stays().order_by('pk').values_list('pk', flat=True)[:10])
        if strays:
            problems.append(
                'core.StableStay (open stay outside the horse\'s barn): '
                f'ids {", ".join(map(str, strays))}'
            )
        if problems:
            for line in problems:
                self.stdout.write(self.style.ERROR(line))
            raise CommandError(f'{len(problems)} rule(s) broken. Correct those rows, then run this again.')
        self.stdout.write(self.style.SUCCESS('Every row follows the data rules.'))
