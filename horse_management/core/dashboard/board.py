"""Sites and their locations at a glance: occupancy, land use and rest."""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone


def sites_overview(*, site='', today=None, flagged=frozenset()):
    """One band per site, one tile per active location.

    ``flagged`` is a set of ``(site, location name)`` pairs that have an
    attention item; their tiles get a marker. Two queries regardless of the
    number of locations.
    """
    from ..models import Location, LocationUsagePeriod

    today = today or timezone.localdate()

    locations = Location.objects.active().annotate(
        horse_count=Count(
            'placements__horse',
            filter=Q(
                placements__end_date__isnull=True,
                placements__horse__is_active=True,
            ),
            distinct=True,
        ),
    ).order_by('site', 'name')
    if site:
        locations = locations.filter(site=site)
    locations = list(locations)

    open_periods = {
        period.location_id: period
        for period in LocationUsagePeriod.objects.filter(
            end_date__isnull=True,
            location_id__in=[loc.pk for loc in locations],
        )
    }

    sites = {}
    for loc in locations:
        period = open_periods.get(loc.pk)
        rest_days = None
        if loc.usage == Location.Usage.RESTED and period is not None:
            rest_days = max(0, (today - period.start_date).days)
        holds_horses = loc.usage in (Location.Usage.HORSES, Location.Usage.MIXED)
        capacity = loc.capacity if holds_horses else None
        availability = (capacity - loc.horse_count) if capacity is not None else None
        pct = 0
        if capacity:
            pct = min(100, round(loc.horse_count * 100 / capacity))
        tile = {
            'location': loc,
            'count': loc.horse_count,
            'capacity': capacity,
            'availability': availability,
            'pct': pct,
            'usage': loc.usage,
            'usage_label': loc.get_usage_display(),
            'holds_horses': holds_horses,
            'rest_days': rest_days,
            'rest_since': period.start_date if rest_days is not None else None,
            'flagged': (loc.site, loc.name) in flagged,
        }
        band = sites.setdefault(loc.site, {
            'name': loc.site,
            'tiles': [],
            'horses': 0,
            'capacity': 0,
            'has_capacity': False,
            'resting': 0,
            'flagged': 0,
        })
        band['tiles'].append(tile)
        band['horses'] += loc.horse_count
        if capacity is not None:
            band['capacity'] += capacity
            band['has_capacity'] = True
        if loc.usage == Location.Usage.RESTED:
            band['resting'] += 1
        if tile['flagged']:
            band['flagged'] += 1

    return list(sites.values())


def site_names():
    """Distinct site names with an active location, for the site switch."""
    from ..models import Location
    return list(
        Location.objects.active().exclude(site='')
        .order_by('site').values_list('site', flat=True).distinct()
    )
