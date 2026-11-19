"""Workload facts for a horse on a date (docs/TRAINING_MODULE_PLAN.md, 9.5).

Plain functions over a horse's records, so tests can call them without a
database query of their own. Only Done records of a work type that is not
a rest count as work (5.2). The chips state facts; they give no advice and
claim no risk (5.9).
"""

from datetime import timedelta

from .models import Activity, WorkType

WINDOW_DAYS = 60          # how far back the rows are read
FIRST_WORK_GAP = 14       # "First work in N days" from this gap
STRIP_DAYS = 28


# ─── Reading ─────────────────────────────────────────────────────────

def rows_for(horse_ids, day, *, window=WINDOW_DAYS):
    """``{horse_id: [Activity, ...]}`` for the window up to ``day``."""
    rows = (
        Activity.objects.filter(horse_id__in=horse_ids, date__lte=day, date__gt=day - timedelta(days=window))
        .select_related('work_type').order_by('date', 'pk')
    )
    by_horse = {horse_id: [] for horse_id in horse_ids}
    for row in rows:
        by_horse.setdefault(row.horse_id, []).append(row)
    return by_horse


# ─── The facts ───────────────────────────────────────────────────────

def is_work(row):
    return row.status == Activity.Status.DONE and row.work_type.kind != WorkType.Kind.REST


def is_fast(row):
    return is_work(row) and row.effective_effort == WorkType.Effort.FAST


def _work_dates(rows, day, test=is_work):
    return {row.date for row in rows if row.date <= day and test(row)}


def last_work_date(rows, day):
    dates = _work_dates(rows, day)
    return max(dates) if dates else None


def days_since_work(rows, day):
    last = last_work_date(rows, day)
    return (day - last).days if last else None


def days_since_fast(rows, day):
    dates = _work_dates(rows, day, is_fast)
    return (day - max(dates)).days if dates else None


def days_worked_7(rows, day):
    start = day - timedelta(days=6)
    return len({d for d in _work_dates(rows, day) if d >= start})


def fast_sessions_7(rows, day):
    start = day - timedelta(days=6)
    return sum(1 for row in rows if start <= row.date <= day and is_fast(row))


def _minutes(rows, day, days):
    start = day - timedelta(days=days - 1)
    return sum(row.minutes or 0 for row in rows if start <= row.date <= day and is_work(row))


def minutes_7(rows, day):
    return _minutes(rows, day, 7)


def minutes_28(rows, day):
    return _minutes(rows, day, 28)


def fast_days_running(rows, day):
    """Days in a row with fast work, ending today or, if none today, yesterday."""
    fast = _work_dates(rows, day, is_fast)
    current = day if day in fast else day - timedelta(days=1)
    count = 0
    while current in fast:
        count += 1
        current -= timedelta(days=1)
    return count


def unconfirmed_days(rows, day):
    """Days after the last work and before ``day`` that hold Planned work."""
    last = last_work_date(rows, day - timedelta(days=1))
    return len({
        row.date for row in rows
        if row.status == Activity.Status.PLANNED and row.date < day and (last is None or row.date > last)
    })


def week_days_worked(rows, day):
    """Days with work this week, Monday to ``day`` (D1)."""
    monday = day - timedelta(days=day.weekday())
    return len({d for d in _work_dates(rows, day) if d >= monday})


def first_work_gap(rows, day):
    """The gap before work on ``day``, when it is FIRST_WORK_GAP days or more.

    Counts when the horse has work on ``day`` (planned or done) and its
    previous work is known. A horse with no earlier work gives None.
    """
    if not any(row.date == day and row.work_type.kind != WorkType.Kind.REST
               and row.status != Activity.Status.NOT_DONE for row in rows):
        return None
    earlier = _work_dates(rows, day - timedelta(days=1))
    if not earlier:
        return None
    gap = (day - max(earlier)).days
    return gap if gap >= FIRST_WORK_GAP else None


# ─── What the pages show ─────────────────────────────────────────────

def chips(rows, day, *, threshold, days_per_week=None, on_board=True, since=None):
    """Short facts for a horse on a date, as ``[{'text', 'tone'}]``.

    ``on_board``: "No work for N days" shows only for a horse whose status
    goes on the board. ``since``: the date its status started, so a horse
    put in work yesterday is not "No work for 30 days".
    """
    out = []
    gap = first_work_gap(rows, day)
    if gap:
        out.append({'text': f'First work in {gap} days', 'tone': 'info'})
    elif on_board:
        last = last_work_date(rows, day)
        start = last or since
        idle = (day - start).days if start else None
        if idle is not None and idle >= threshold:
            waiting = unconfirmed_days(rows, day)
            if waiting:
                out.append({'text': f'{waiting} day{"s" if waiting != 1 else ""} not confirmed', 'tone': 'warn'})
            else:
                out.append({'text': f'No work for {idle} days', 'tone': 'warn'})
    running = fast_days_running(rows, day)
    if running >= 2:
        out.append({'text': f'Fast work {running} days running', 'tone': 'info'})
    if days_per_week:
        out.append({'text': f'{week_days_worked(rows, day)} of {days_per_week} days this week', 'tone': 'neutral'})
    return out


EFFORT_LETTER = {WorkType.Effort.EASY: 'E', WorkType.Effort.STEADY: 'S', WorkType.Effort.FAST: 'F'}


def day_cell(d, day_rows, today):
    """One square for date ``d``: ``{'date', 'letter', 'tone', 'label'}``.
    ``today`` decides whether Planned work is "not confirmed" (5.13)."""
    work = [r for r in day_rows if is_work(r)]
    if work:
        top = max(work, key=lambda r: r.effective_effort)
        letter, tone = EFFORT_LETTER.get(top.effective_effort, 'E'), {1: 'easy', 2: 'steady', 3: 'fast'}[top.effective_effort]
        words = ', '.join(f'{r.work_type.name}, {r.effort_label.lower()}' for r in work)
        label = f'{d:%a} {d.day} {d:%b}: {words}, done'
    elif any(r.status == Activity.Status.DONE for r in day_rows):
        letter, tone, label = 'R', 'rest', f'{d:%a} {d.day} {d:%b}: rest'
    elif any(r.status == Activity.Status.NOT_DONE for r in day_rows):
        reason = next(r for r in day_rows if r.status == Activity.Status.NOT_DONE).get_reason_display()
        letter, tone, label = '×', 'missed', f'{d:%a} {d.day} {d:%b}: did not work, {reason.lower()}'
    elif any(r.status == Activity.Status.PLANNED for r in day_rows):
        word = 'planned, not confirmed' if d < today else 'planned'
        letter, tone, label = '?', 'planned', f'{d:%a} {d.day} {d:%b}: {word}'
    else:
        letter, tone, label = '', 'none', f'{d:%a} {d.day} {d:%b}: no record'
    return {'date': d, 'letter': letter, 'tone': tone, 'label': label}


def _by_day(rows):
    by_day = {}
    for row in rows:
        by_day.setdefault(row.date, []).append(row)
    return by_day


def strip(rows, day, *, days=STRIP_DAYS):
    """The 28-day strip: one square for each day, oldest first (5.13)."""
    by_day = _by_day(rows)
    return [
        day_cell(d, by_day.get(d, []), day)
        for d in (day - timedelta(days=offset) for offset in range(days - 1, -1, -1))
    ]


def week(rows, today):
    """Monday to Sunday of ``today``'s week: one square for each day (dashboard
    plan, This week). Later days show planned work only."""
    by_day = _by_day(rows)
    monday = today - timedelta(days=today.weekday())
    return [day_cell(d, by_day.get(d, []), today) for d in (monday + timedelta(days=i) for i in range(7))]


def week_fast(rows, today):
    """Days with fast work this week, Monday to ``today``."""
    monday = today - timedelta(days=today.weekday())
    return len({d for d in _work_dates(rows, today, is_fast) if d >= monday})
