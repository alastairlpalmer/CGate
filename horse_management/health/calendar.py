"""The foaling calendar: one season's pregnancies on a month timeline.

A season here is the *foaling* year: the records whose foal is (or was)
due in that year. Coverings for it happen the year before, so the timeline
window runs from the earliest covering to the latest due date, snapped to
whole months, and each row is one mare's bar from covering to due with
the scans, EHV doses and the foaling marked on it.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db.models import Q

from .models import BreedingRecord

EHV_MONTHS = (5, 7, 9)


def seasons_with_foalings():
    """Foaling years that have at least one record due or born, newest first."""
    years = set()
    for due, dob in BreedingRecord.objects.filter(
        mare__is_active=True,
    ).exclude(status='barren').values_list('date_foal_due', 'foal_dob'):
        if dob:
            years.add(dob.year)
        elif due:
            years.add(due.year)
    return sorted(years, reverse=True)


def default_season(today, seasons):
    """This year if it has foalings, else the nearest season that does."""
    if not seasons:
        return today.year
    if today.year in seasons:
        return today.year
    later = [y for y in seasons if y > today.year]
    return min(later) if later else seasons[0]


def _month_start(day):
    return day.replace(day=1)


def _next_month(day):
    year = day.year + (day.month // 12)
    month = day.month % 12 + 1
    return date(year, month, 1)


def _months_between(start, end):
    """First-of-month dates from start's month through end's month."""
    cur = _month_start(start)
    out = []
    while cur <= end:
        out.append(cur)
        cur = _next_month(cur)
    return out


def season_calendar(season, *, today):
    """Rows and month columns for one foaling season.

    Returns ``{'season', 'months', 'rows', 'not_carrying', 'per_month',
    'window_start', 'window_end', 'today_pct'}``. Percentages are of the
    window width so the template positions bars with plain CSS.
    """
    records = list(
        BreedingRecord.objects.filter(mare__is_active=True)
        .filter(Q(foal_dob__year=season) | Q(foal_dob__isnull=True, date_foal_due__year=season))
        .select_related('mare', 'foal').prefetch_related('coverings', 'scans')
        .order_by('date_foal_due', 'mare__name')
    )
    carrying = [r for r in records if r.status in ('covered', 'confirmed', 'born')]
    not_carrying = [r for r in records if r.status in ('lost', 'barren')]

    if carrying:
        starts = [r.first_covering_date or r.date_covered for r in carrying]
        ends = [r.foal_dob or r.date_foal_due for r in carrying]
        window_start = _month_start(min(starts))
        window_end = max(ends)
    else:
        window_start = date(season - 1, 4, 1)
        window_end = date(season, 6, 30)
    # Give the last month room and never end before today when it is in range.
    last_month = _month_start(window_end)
    window_end = _next_month(last_month) - timedelta(days=1)
    months = _months_between(window_start, window_end)
    span = max((window_end - window_start).days, 1)

    def pct(day):
        return round(max(0, min(span, (day - window_start).days)) * 100 / span, 2)

    month_cols = []
    for m in months:
        days_in = calendar.monthrange(m.year, m.month)[1]
        month_cols.append({
            'date': m,
            'label': m.strftime('%b'),
            'year': m.year,
            'left': pct(m),
            'width': round(days_in * 100 / span, 2),
            'is_current': m.year == today.year and m.month == today.month,
        })

    per_month = {}
    rows = []
    for r in carrying:
        start = r.first_covering_date or r.date_covered
        end = r.foal_dob or r.date_foal_due
        due = r.date_foal_due
        key = (end.year, end.month) if end else None
        if key:
            per_month.setdefault(key, {'due': 0, 'born': 0})
            per_month[key]['born' if r.status == 'born' else 'due'] += 1
        markers = []
        for c in r.coverings.all():
            markers.append({'kind': 'covering', 'left': pct(c.date), 'title': f'Covered {c.date:%d %b %Y}'})
        for sc in r.scans.all():
            markers.append({
                'kind': 'scan_pos' if sc.positive else ('scan_neg' if sc.result == 'not_in_foal' else 'scan_other'),
                'left': pct(sc.date),
                'title': f'{sc.get_scan_type_display()} {sc.date:%d %b}: {sc.get_result_display()}',
            })
        if r.status != 'born':
            for month, ehv_date in sorted(r.ehv_vaccination_dates.items()):
                if window_start <= ehv_date <= window_end:
                    markers.append({
                        'kind': 'ehv_sent' if month in r.sent_ehv_months else 'ehv',
                        'left': pct(ehv_date),
                        'title': f'EHV month {month}: {ehv_date:%d %b}',
                    })
        if r.status == 'born' and r.foal_dob:
            markers.append({'kind': 'born', 'left': pct(r.foal_dob), 'title': f'Foaled {r.foal_dob:%d %b %Y}'})
        rows.append({
            'record': r,
            'mare': r.mare,
            'left': pct(start),
            'width': max(round(pct(end) - pct(start), 2), 0.5) if end else 0,
            'due': due,
            'end': end,
            'progress': r.gestation_progress if r.status != 'born' else 100,
            'markers': markers,
            'watch': r.on_foaling_watch,
            'past_due': r.is_past_due,
        })

    per_month_cols = [
        {**col, **per_month.get((col['year'], col['date'].month), {'due': 0, 'born': 0})}
        for col in month_cols
    ]
    return {
        'season': season,
        'months': per_month_cols,
        'rows': rows,
        'not_carrying': not_carrying,
        'window_start': window_start,
        'window_end': window_end,
        'today_pct': pct(today) if window_start <= today <= window_end else None,
        'span_days': span,
    }


def foaling_watch(*, today):
    """Mares within three weeks of their due date or past it, soonest first."""
    records = (
        BreedingRecord.objects.filter(
            status__in=BreedingRecord.ACTIVE_STATUSES, mare__is_active=True,
            date_foal_due__lte=today + timedelta(days=BreedingRecord.FOALING_WATCH_DAYS),
        )
        .select_related('mare').order_by('date_foal_due')
    )
    return list(records)
