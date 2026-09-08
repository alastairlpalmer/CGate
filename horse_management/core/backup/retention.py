"""Which backups to keep, and which to delete.

Kept separate from the storage and dump code because this is the part with
the interesting edge cases, and it is pure: it takes a list of names and
returns the names to delete. That makes it testable without a database, a
pg_dump binary or a network.

The policy is grandfather-father-son: keep every backup from the last few
days, then one per week, then one per month. It answers both "undo
yesterday's mistake" and "we only noticed the corruption two months later"
without storing a copy for every night of the year.
"""

import re
from datetime import date

# backups/database/yardway-db-2026-09-08T0300.dump
NAME_PATTERN = re.compile(
    r'yardway-(?P<kind>db|media)-'
    r'(?P<date>\d{4}-\d{2}-\d{2})'
    r'T(?P<time>\d{4})'
)


def parse_date(name):
    """The date in a backup's name, or None if the name is not ours.

    Returning None rather than raising matters: an unrecognised object in
    the bucket must be left alone, never deleted. Somebody else may have
    put it there.
    """
    match = NAME_PATTERN.search(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group('date'))
    except ValueError:
        return None


def _iso_week(day):
    year, week, _ = day.isocalendar()
    return (year, week)


def _month(day):
    return (day.year, day.month)


def select_expired(names, today, keep_daily=7, keep_weekly=4, keep_monthly=12):
    """The subset of ``names`` that may be deleted.

    Three passes, applied in order, each adding to the keep set:

    1. Everything within ``keep_daily`` days of ``today``.
    2. The newest backup in each of the most recent ``keep_weekly`` ISO
       weeks.
    3. The newest backup in each of the most recent ``keep_monthly``
       months.

    Passes 2 and 3 pick from *all* backups, not only the ones the earlier
    pass left over. A backup that two passes both choose is simply kept
    once, so the policy cannot be made to keep more by reordering.

    Two rules exist to stop this deleting something it should not:

    * A name this module does not recognise is never returned, so an
      unrelated object in the bucket is safe.
    * A name dated in the future is kept. A clock that has jumped forward
      is not a reason to delete data.
    """
    dated = []
    for name in names:
        when = parse_date(name)
        if when is not None:
            dated.append((when, name))

    # Newest first, and by name within a day so the choice is deterministic
    # when two backups share a date.
    dated.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)

    keep = set()

    # 1. The daily window, plus anything dated ahead of today.
    for when, name in dated:
        if when > today or (today - when).days < keep_daily:
            keep.add(name)

    # 2. The newest in each recent ISO week.
    keep |= _newest_per_period(dated, _iso_week, keep_weekly)

    # 3. The newest in each recent month.
    keep |= _newest_per_period(dated, _month, keep_monthly)

    return [name for _, name in dated if name not in keep]


def _newest_per_period(dated, period_of, how_many):
    """The newest backup in each of the most recent ``how_many`` periods.

    ``dated`` must already be sorted newest first, which makes the first
    name seen for a period its newest.
    """
    if how_many <= 0:
        return set()
    newest = {}
    for when, name in dated:
        newest.setdefault(period_of(when), name)
    recent = sorted(newest, reverse=True)[:how_many]
    return {newest[period] for period in recent}
