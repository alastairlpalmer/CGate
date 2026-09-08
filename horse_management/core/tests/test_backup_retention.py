"""Which backups the retention policy keeps, and which it deletes.

Deleting the wrong thing here loses the only copy of data, so the
"never delete" cases below matter more than the pruning ones.
"""

from datetime import date

from django.test import SimpleTestCase

from core.backup import retention

TODAY = date(2026, 9, 8)  # a Tuesday


def name(day, kind='db', time='0200'):
    return f'backups/database/yardway-{kind}-{day.isoformat()}T{time}.dump'


def days_back(n):
    return date.fromordinal(TODAY.toordinal() - n)


class ParseDateTests(SimpleTestCase):
    def test_a_backup_name_yields_its_date(self):
        self.assertEqual(
            retention.parse_date('backups/database/yardway-db-2026-09-08T0200.dump'),
            date(2026, 9, 8),
        )

    def test_a_media_archive_name_also_parses(self):
        self.assertEqual(
            retention.parse_date('backups/media/yardway-media-2026-01-31T0200.tar.gz'),
            date(2026, 1, 31),
        )

    def test_an_unrelated_object_is_not_ours(self):
        """Something else in the bucket must never be deleted."""
        for other in (
            'notes.txt',
            'backups/database/somebody-elses-2026-09-08.dump',
            'yardway-db-not-a-date.dump',
            '',
        ):
            with self.subTest(other=other):
                self.assertIsNone(retention.parse_date(other))

    def test_an_impossible_date_is_refused_not_guessed(self):
        self.assertIsNone(
            retention.parse_date('yardway-db-2026-02-31T0200.dump')
        )


class SelectExpiredTests(SimpleTestCase):
    def test_nothing_to_do_with_no_backups(self):
        self.assertEqual(retention.select_expired([], today=TODAY), [])

    def test_recent_backups_are_all_kept(self):
        names = [name(days_back(n)) for n in range(7)]
        self.assertEqual(retention.select_expired(names, today=TODAY), [])

    def test_unrecognised_objects_are_never_deleted(self):
        names = ['notes.txt', 'somebody-elses-file.dump', name(days_back(400))]
        expired = retention.select_expired(names, today=TODAY)
        self.assertNotIn('notes.txt', expired)
        self.assertNotIn('somebody-elses-file.dump', expired)

    def test_a_future_dated_backup_is_kept(self):
        """A clock that jumped forward is not a reason to delete data."""
        future = date(2027, 1, 1)
        self.assertEqual(retention.select_expired([name(future)], today=TODAY), [])

    def test_one_backup_a_week_survives_beyond_the_daily_window(self):
        # Two a day for eight weeks.
        names = []
        for n in range(56):
            day = days_back(n)
            names.extend([name(day, time='0200'), name(day, time='1400')])

        expired = set(retention.select_expired(
            names, today=TODAY, keep_daily=7, keep_weekly=4, keep_monthly=12,
        ))
        kept = [n for n in names if n not in expired]

        # Everything inside the daily window survives.
        for n in range(7):
            for time in ('0200', '1400'):
                self.assertIn(name(days_back(n), time=time), kept)

        # Beyond it, at most one per ISO week within a month. Two can share
        # a week only where that week straddles a month boundary, because
        # each month keeps its own newest copy.
        older = [n for n in kept if (TODAY - retention.parse_date(n)).days >= 7]
        buckets = {}
        for n in older:
            when = retention.parse_date(n)
            buckets.setdefault((when.isocalendar()[:2], when.month), []).append(n)
        for bucket, entries in buckets.items():
            self.assertEqual(len(entries), 1, f'{len(entries)} kept in {bucket}')

    def test_a_year_of_backups_is_reduced_but_old_months_survive(self):
        names = [name(days_back(n)) for n in range(365)]
        expired = set(retention.select_expired(names, today=TODAY))
        kept = [n for n in names if n not in expired]

        self.assertLess(len(kept), 40, 'retention barely deleted anything')
        self.assertGreater(len(kept), 15, 'retention deleted too much')

        # A copy from more than six months ago still exists.
        oldest = min(retention.parse_date(n) for n in kept)
        self.assertLess(oldest, days_back(180))

    def test_keeping_zero_daily_still_keeps_the_weekly_and_monthly_copies(self):
        names = [name(days_back(n)) for n in range(30)]
        expired = retention.select_expired(
            names, today=TODAY, keep_daily=0, keep_weekly=2, keep_monthly=1,
        )
        self.assertLess(len(expired), len(names), 'everything was deleted')

    def test_the_result_never_includes_a_name_that_is_kept(self):
        names = [name(days_back(n)) for n in range(120)]
        expired = retention.select_expired(names, today=TODAY)
        self.assertEqual(len(expired), len(set(expired)), 'duplicates in the delete list')
        self.assertTrue(set(expired).issubset(set(names)))
