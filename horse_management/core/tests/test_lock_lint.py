"""Row locks that PostgreSQL accepts.

``select_for_update()`` locks the rows of every table in the query. When
the query also joins a relation that can be empty (``select_related`` on a
nullable key is an outer join), PostgreSQL refuses it:

    FOR UPDATE cannot be applied to the nullable side of an outer join

SQLite has no row locks and ignores ``select_for_update``, and the test
suite runs on SQLite, so no other test can see this fault. On the live
database it broke Training (**Reopen lot**, **Move to**, and every action
that makes or removes a training charge: **Mark lot done**, **Done** on a
row) and the C-vet step that sends a note to the yard (26 Nov 2026).

The rule this file keeps: a lock that is used with ``select_related`` names
its own table, ``select_for_update(of=('self',))``. The lock is then on the
rows the code changes, and on no row of a joined table.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from training.models import Activity

PROJECT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {'node_modules', 'migrations', 'tests', 'static', 'templates', 'staticfiles', 'venv', '.venv'}
LOCK = re.compile(r'select_for_update\(([^)]*)\)')

# Locks with no ``of=`` that are safe as they are: every relation they join
# is a key that cannot be empty, so the join is an inner join. Add a line
# here only after you check the model. A new lock is better written with
# of=('self',).
INNER_JOINS_ONLY = (
    "Stable.objects.select_for_update().select_related('location')",        # core/stables.py
    "visit.farrier_visits.select_for_update() .select_related('horse')",    # health/farrier.py
    "Invoice.objects.select_for_update() .select_related('owner')",         # invoicing/tasks.py
)


def statements(text):
    """Each statement that takes a lock, as one line: from the line that
    holds the lock back to the start of its statement and on to its end."""
    lines = text.split('\n')
    for number, line in enumerate(lines):
        if 'select_for_update(' not in line:
            continue
        start = number
        while start > 0 and lines[start - 1].rstrip().endswith(('(', ',', '\\')):
            start -= 1
        end = number
        while end < len(lines) - 1:
            depth = sum(part.count('(') - part.count(')') for part in lines[start:end + 1])
            if depth <= 0 and not lines[end].rstrip().endswith('\\'):
                break
            end += 1
        yield number + 1, ' '.join(part.strip() for part in lines[start:end + 1])


def unsafe(statement):
    """A lock with ``select_related`` and no ``of=``, not on the checked list."""
    if 'select_related(' not in statement or any(known in statement for known in INNER_JOINS_ONLY):
        return False
    return any('of=' not in arguments for arguments in LOCK.findall(statement))


def project_files():
    for path in sorted(PROJECT.rglob('*.py')):
        relative = path.relative_to(PROJECT)
        if SKIP_DIRS & set(relative.parts) or path.name.startswith(('test_', 'tests')):
            continue
        yield relative, path.read_text()


class LockLintTests(SimpleTestCase):
    def test_the_scan_finds_the_locks(self):
        """A lint that matches nothing proves nothing."""
        with_related = [
            statement for _, text in project_files()
            for _, statement in statements(text) if 'select_related(' in statement
        ]
        self.assertGreaterEqual(len(with_related), 8)

    def test_the_rule_catches_the_fault(self):
        bad = ("rows = list(\n    Activity.objects.select_for_update().filter(pk__in=ids)\n"
               "    .select_related('work_type', 'horse', 'extra_charge')\n)\n")
        good = bad.replace('select_for_update()', "select_for_update(of=('self',))")
        self.assertEqual([unsafe(statement) for _, statement in statements(bad)], [True])
        self.assertEqual([unsafe(statement) for _, statement in statements(good)], [False])

    def test_a_lock_with_select_related_names_its_own_table(self):
        offenders = [
            f"{relative}:{number}: {statement[:140]}"
            for relative, text in project_files()
            for number, statement in statements(text) if unsafe(statement)
        ]
        self.assertEqual(
            offenders, [],
            "Use select_for_update(of=('self',)) with select_related(): PostgreSQL refuses "
            "FOR UPDATE on the nullable side of an outer join.",
        )

    def test_the_checked_list_names_real_code(self):
        """An entry that no longer matches any code must go."""
        every = ' '.join(statement for _, text in project_files() for _, statement in statements(text))
        for known in INNER_JOINS_ONLY:
            self.assertIn(known, every)


class WhyTests(SimpleTestCase):
    """The joins that made the fault in Training: a record's charge and its
    lot day can be empty, so Django joins them with LEFT OUTER JOIN. (The
    C-vet lock had the same fault on a note's horse and visit. Its models
    are not named here: only the C-vet app may name them.)"""

    def test_the_relations_that_broke_are_outer_joins(self):
        sql = str(Activity.objects.select_related('extra_charge', 'lot_day__lot').query)
        self.assertEqual(sql.count('LEFT OUTER JOIN'), 3)       # the charge, the lot day, its lot

    def test_the_fixed_lock_names_its_own_table(self):
        queryset = Activity.objects.select_for_update(of=('self',)).select_related('work_type', 'horse', 'extra_charge')
        self.assertTrue(queryset.query.select_for_update)
        self.assertEqual(tuple(queryset.query.select_for_update_of), ('self',))
