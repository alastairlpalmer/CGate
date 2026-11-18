"""The dashboard's month calendar (docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP4).

A month grid, Monday first, with a dot per kind of thing due on each day,
and the list of what is due on one chosen day under it. It replaced the
"Next 14 days" strip and took the Needs action card's place beside the
map; Needs action itself moved to the notification centre.

Items come from the same attention collectors as everything else
(core.dashboard.attention.collect), asked to look as far ahead as the
grid's last day. An item still open from an earlier day shows on its own
date, marked overdue.
"""

from __future__ import annotations

import calendar as stdlib_calendar
from collections import Counter, defaultdict
from datetime import date, timedelta

from .attention import KIND_LABELS, SEVERITY_ORDER
from .upcoming import STRIP_KIND_KEYS, STRIP_KINDS

# How far the arrows go: a year back, a year ahead.
MONTHS_BACK = 12
MONTHS_AHEAD = 12


def parse_month(value, today):
    """``'2026-11'`` → the first of that month; anything else → this month.
    Kept within MONTHS_BACK and MONTHS_AHEAD of today."""
    this_month = today.replace(day=1)
    try:
        year, month = (int(part) for part in str(value).split('-', 1))
        first = date(year, month, 1)
    except (TypeError, ValueError):
        return this_month
    earliest = _shift(this_month, -MONTHS_BACK)
    latest = _shift(this_month, MONTHS_AHEAD)
    return min(max(first, earliest), latest)


def parse_day(value, month_first):
    """A day inside the month, or None."""
    try:
        day = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return day if (day.year, day.month) == (month_first.year, month_first.month) else None


def _shift(first, months):
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def grid_bounds(month_first):
    last = month_first.replace(day=stdlib_calendar.monthrange(month_first.year, month_first.month)[1])
    start = month_first - timedelta(days=month_first.weekday())
    end = last + timedelta(days=6 - last.weekday())
    return start, end


def horizon_days(month_first, today):
    """How far ahead the collectors must look to fill this month's grid."""
    _, end = grid_bounds(month_first)
    return max(1, (end - today).days + 1)


def build(items, today, month_first, selected=None):
    start, end = grid_bounds(month_first)
    by_day = defaultdict(list)
    for item in items:
        if item.due_date is not None and start <= item.due_date <= end:
            by_day[item.due_date].append(item)

    if selected is None:
        in_month = (today.year, today.month) == (month_first.year, month_first.month)
        selected = today if in_month else next(
            (d for d in sorted(by_day) if d.month == month_first.month), month_first,
        )

    weeks, week = [], []
    day = start
    while day <= end:
        day_items = sorted(by_day.get(day, []), key=lambda i: i.sort_key)
        counts = Counter(item.kind for item in day_items)
        kinds = [k for k in STRIP_KIND_KEYS if counts.get(k)]
        kinds += [k for k in counts if k not in kinds]
        week.append({
            'date': day,
            'in_month': day.month == month_first.month,
            'is_today': day == today,
            'is_past': day < today,
            'is_weekend': day.weekday() >= 5,
            'is_selected': day == selected,
            'count': len(day_items),
            'overdue': any(item.severity == 'overdue' for item in day_items),
            'dots': kinds[:3],
            'more': max(0, len(kinds) - 3),
            'labels': [
                {'kind': k, 'text': f'{KIND_LABELS.get(k, k)}{f" {counts[k]}" if counts[k] > 1 else ""}'}
                for k in kinds[:2]
            ],
            'titles': ', '.join(f'{KIND_LABELS.get(k, k)} {n}' for k, n in counts.most_common()),
        })
        if len(week) == 7:
            weeks.append(week)
            week = []
        day += timedelta(days=1)

    shown_kinds = {k for w in weeks for d in w for k in d['dots']}
    legend = [{'kind': k, 'label': label} for k, label in STRIP_KINDS if k in shown_kinds]

    in_month_items = [i for d, group in by_day.items() if d.month == month_first.month for i in group]
    this_month = today.replace(day=1)
    previous, following = _shift(month_first, -1), _shift(month_first, 1)
    return {
        'month': month_first,
        'weeks': weeks,
        'legend': legend,
        'selected': selected,
        'selected_items': sorted(by_day.get(selected, []), key=lambda i: (SEVERITY_ORDER[i.severity], i.title.lower())),
        'total': len(in_month_items),
        'overdue': sum(1 for i in in_month_items if i.severity == 'overdue'),
        'is_this_month': month_first == this_month,
        'previous': previous if previous >= _shift(this_month, -MONTHS_BACK) else None,
        'next': following if following <= _shift(this_month, MONTHS_AHEAD) else None,
        'this_month': this_month,
    }
