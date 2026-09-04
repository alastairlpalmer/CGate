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
    ('vet', 'Vet'),
    ('invoice', 'Invoice due'),
    ('departure_expected', 'Departure'),
    ('foal', 'Foal due'),
    ('ehv', 'EHV'),
    ('document', 'Document'),
)
STRIP_KIND_KEYS = [kind for kind, _ in STRIP_KINDS]

# Kinds that are a provider coming to the yard: group them by day so one
# row is one booking.
VISIT_KINDS = ('farrier', 'vaccination', 'vet')

MAX_VISITS = 8


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
        'visits': visits[:MAX_VISITS],
        'more_visits': max(0, len(visits) - MAX_VISITS),
        'total': len(dated),
        'legend': legend,
        'start': today,
        'end': horizon,
    }
