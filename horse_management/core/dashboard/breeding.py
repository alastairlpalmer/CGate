"""Mares in foal, for the seasonal block.

The block renders only when at least one mare has a confirmed pregnancy,
so a yard with no breeding never sees it.
"""

from __future__ import annotations

from django.utils import timezone

from ..permissions import LEVEL_VIEW, has_feature_access

GESTATION_DAYS = 340


def in_foal(user, *, site='', today=None):
    """One entry per confirmed pregnancy, soonest foal first."""
    if not has_feature_access(user, 'breeding', LEVEL_VIEW):
        return []
    from health.models import BreedingRecord

    today = today or timezone.localdate()
    records = list(BreedingRecord.objects.filter(
        status=BreedingRecord.Status.CONFIRMED, mare__is_active=True,
    ).select_related('mare').order_by('date_foal_due'))
    if not records:
        return []

    where = {}
    if site:
        from ..models import Placement
        where = {
            horse_id: loc_site
            for horse_id, loc_site in Placement.objects.filter(
                end_date__isnull=True, horse_id__in=[r.mare_id for r in records],
            ).values_list('horse_id', 'location__site')
        }

    entries = []
    for record in records:
        mare_site = where.get(record.mare_id, '')
        if site and mare_site and mare_site != site:
            continue
        due = record.date_foal_due
        days_to_go = (due - today).days if due else None
        day_of = (today - record.date_covered).days if record.date_covered else None
        progress = 0
        if day_of is not None:
            progress = max(0, min(100, round(day_of * 100 / GESTATION_DAYS)))
        next_ehv = None
        for month, ehv_date in sorted(record.ehv_vaccination_dates.items()):
            if ehv_date >= today:
                next_ehv = {'month': month, 'date': ehv_date, 'delta': (ehv_date - today).days}
                break
        entries.append({
            'record': record,
            'mare': record.mare,
            'stallion': record.stallion_name,
            'due': due,
            'days_to_go': days_to_go,
            'overdue': days_to_go is not None and days_to_go < 0,
            'day_of': day_of,
            'progress': progress,
            'next_ehv': next_ehv,
            'scanned': bool(record.date_scanned_heartbeat or record.date_scanned_14_days),
        })
    return entries
