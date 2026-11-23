"""The next 14 days: a day strip, and the visits worth booking together."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from .attention import HORIZON_DAYS, KIND_LABELS, _bulk_action

# Kinds that get a dot on the strip, in legend order. Anything else dated
# still counts toward the day's total.
STRIP_KINDS = (
    ('farrier', 'Farrier'),
    ('vaccination', 'Vaccination'),
    ('worming', 'Worming'),
    ('vet', 'Vet'),
    ('invoice', 'Invoice due'),
    ('departure_expected', 'Departure'),
    ('foal', 'Foal due'),
    ('ehv', 'EHV'),
    ('document', 'Document'),
    ('calendar', 'Calendar item'),
)
STRIP_KIND_KEYS = [kind for kind, _ in STRIP_KINDS]

# Kinds where a day's worth is one job rather than N of them: a provider
# coming to the yard, or — for worming — the yard dosing its horses
# together. Group them by day so one row is one booking.
VISIT_KINDS = ('farrier', 'vaccination', 'vet', 'worming')

MAX_VISITS = 8

# The phone's list in place of the month grid: today and the next 7 days
# (docs/MOBILE_APP_PLAN.md, item C3).
WEEK_DAYS = 8


def build(items, user, today, days=HORIZON_DAYS):
    """``{'days': [...], 'visits': [...], 'total': n, 'legend': [...]}``.

    ``items`` is the full attention list; only dated items inside the
    horizon are used. Today is included so the strip agrees with the inbox.
    """
    horizon = today + timedelta(days=days - 1)
    dated = [
        item for item in items
        if item.due_date is not None and today <= item.due_date <= horizon
    ]

    by_day = defaultdict(list)
    for item in dated:
        by_day[item.due_date].append(item)

    day_list = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        day_items = by_day.get(day, [])
        counts = Counter(item.kind for item in day_items)
        dots = [kind for kind in STRIP_KIND_KEYS if counts.get(kind)]
        day_list.append({
            'date': day,
            'is_today': offset == 0,
            'is_weekend': day.weekday() >= 5,
            'items': day_items,
            'count': len(day_items),
            'dots': dots[:3],
            'more': max(0, len(dots) - 3),
            'titles': ', '.join(
                f'{KIND_LABELS.get(kind, kind)} {n}' for kind, n in counts.most_common()
            ),
        })

    visits = []
    grouped = defaultdict(list)
    for item in dated:
        if item.kind in VISIT_KINDS and item.horse is not None:
            grouped[(item.due_date, item.kind)].append(item)
    for (day, kind), group in sorted(grouped.items()):
        horses, seen = [], set()
        for item in group:
            if item.horse_id not in seen:
                seen.add(item.horse_id)
                horses.append(item.horse)
        label = KIND_LABELS.get(kind, kind)
        details = sorted({
            item.detail.split(' · ', 1)[1] if item.detail.startswith(f'{label} · ') else item.detail
            for item in group
        })
        visits.append({
            'date': day,
            'delta': (day - today).days,
            'kind': kind,
            'label': label,
            'horses': horses,
            'items': group,
            'detail': details[0] if len(details) == 1 else f'{len(horses)} horses',
            'action': _bulk_action(kind, horses, user),
            'single_action': group[0].actions[0] if len(horses) == 1 and group[0].actions else None,
        })

    legend = [
        {'kind': kind, 'label': label}
        for kind, label in STRIP_KINDS
        if any(kind in d['dots'] for d in day_list)
    ]

    return {
        'days': day_list,
        'week': week(by_day, user, today),
        'visits': visits[:MAX_VISITS],
        'more_visits': max(0, len(visits) - MAX_VISITS),
        'visit_count': len(visits),
        'total': len(dated),
        'legend': legend,
        'start': today,
        'end': horizon,
    }


def _day_label(day, today):
    offset = (day - today).days
    if offset == 0:
        return 'Today'
    if offset == 1:
        return 'Tomorrow'
    return day.strftime('%A')


def week(by_day, user, today, days=WEEK_DAYS):
    """Today and the next 7 days as a list of jobs, for a phone.

    ``[{'date', 'label', 'lines': [...]}]``. Today is always in the list;
    another day is in it only when something is due. A line is one job:

    - one horse: ``title`` is its name, ``sub`` says what is due and where;
    - the same job for two or more horses that day (a farrier's morning, a
      vaccination round): ``title`` says what is due, ``horses`` are named
      when the line is opened, and ``action`` records for all of them;
    - anything else dated (an invoice, a calendar item): its own title.

    ``action`` is the one button of the line, or None.
    """
    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        day_items = sorted(by_day.get(day, []), key=lambda item: item.sort_key)
        if not day_items and offset:
            continue
        lines, jobs = [], {}
        for item in day_items:
            if item.kind in VISIT_KINDS and item.horse is not None:
                key = (item.kind, item.detail)
                if key not in jobs:
                    jobs[key] = {'kind': item.kind, 'title': item.detail, 'items': [], 'horses': []}
                    lines.append(jobs[key])
                job = jobs[key]
                job['items'].append(item)
                if item.horse not in job['horses']:
                    job['horses'].append(item.horse)
            else:
                lines.append({
                    'kind': item.kind, 'title': item.title, 'url': item.url,
                    'sub': ' · '.join(part for part in (item.detail, item.location) if part),
                    'horses': [], 'count': 1,
                    'action': item.actions[0] if item.actions else None,
                })
        for job in jobs.values():
            items = job.pop('items')
            job['count'] = len(job['horses'])
            if job['count'] == 1:
                first = items[0]
                job.update({
                    'title': first.title, 'url': first.url, 'horses': [],
                    'sub': ' · '.join(part for part in (first.detail, first.location) if part),
                    'action': first.actions[0] if first.actions else None,
                })
            else:
                job.update({
                    'url': '', 'sub': f"{job['count']} horses",
                    'action': _bulk_action(job['kind'], job['horses'], user),
                })
        result.append({
            'date': day,
            'label': _day_label(day, today),
            'is_today': offset == 0,
            'lines': lines,
        })
    return result
