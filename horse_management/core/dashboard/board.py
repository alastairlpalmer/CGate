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


# Colour encodes capacity and nothing else — the three states of
# templates/horses/_capacity_ring.html, with the ring's default for a
# location that has no capacity to fill.
MAP_COLOURS = {
    'over': '#C0392B',
    'near': '#3D5A63',
    'ok': '#6A8990',
    None: '#6A8990',
}

# A location with a point but no boundary draws as a circle scaled by
# capacity, clamped so a two-horse pen stays tappable and a forty-horse
# field does not swamp the view. Metres, so it scales with the map.
CIRCLE_MIN_M, CIRCLE_MAX_M, CIRCLE_PER_HORSE_M, CIRCLE_DEFAULT_M = 18, 70, 2.5, 25


def capacity_state(capacity, availability):
    """``over`` / ``near`` / ``ok`` per the ring partial, or None with no capacity."""
    if not capacity or availability is None:
        return None
    if availability <= 0:
        return 'over'
    if availability <= 2:
        return 'near'
    return 'ok'


# ── Rest and rotation, for the phone map ──────────────────────────────
# The desktop map colours by capacity alone (MAP_COLOURS). In the field
# the question is the rotation: which ground is carrying horses, which is
# over its limit, and which has rested long enough to take them back. A
# fortnight is the point most yards call a field ready again.
REST_READY_DAYS = 14

REST_STATES = {
    'over': ('Over capacity', '#C0392B'),
    'grazing': ('Grazing', '#4F6F64'),
    'rested': ('Rested', '#8B968F'),
    'recovering': ('Recovering', '#A08343'),
    'empty': ('Empty', '#9CB2B8'),
    'other': ('', '#6A8990'),
}


def rest_state(*, count, capacity, holds_horses, rest_days):
    """Where one location sits in the rotation.

    ``other`` is ground that is not grazing at all — hay, arable — and it
    carries no label of its own here: the tile prints the land use, which
    says more than a second word for "not horses" would.
    """
    if capacity and count > capacity:
        return 'over'
    if count > 0:
        return 'grazing'
    if rest_days is not None:
        return 'rested' if rest_days >= REST_READY_DAYS else 'recovering'
    return 'empty' if holds_horses else 'other'


def circle_radius_m(capacity):
    if not capacity:
        return CIRCLE_DEFAULT_M
    return int(max(CIRCLE_MIN_M, min(CIRCLE_MAX_M, CIRCLE_MIN_M + capacity * CIRCLE_PER_HORSE_M)))


def map_locations(site):
    """The Yard board's tiles for one site, shaped for the map.

    The **only** place map data is built: it reuses ``sites_overview`` so
    the counts, capacities and land-use rules are the board's, and adds
    per tile the point, the boundary, a server-computed ``anchor`` for the
    badge (``representative_point`` inside a polygon — never the
    centroid, which can fall outside a concave field — or the circle's
    centre), the render ``kind`` and the capacity colour. A location with
    no point is included with ``kind`` None so the page can count it, but
    the map draws nothing for it.
    """
    bands = sites_overview(site=site)
    band = bands[0] if bands else {'name': site, 'tiles': [], 'horses': 0}
    return _shape_band(band)


def map_locations_by_site():
    """``{site: map_locations(site)}`` for every site, from one board query."""
    return {band['name']: _shape_band(band) for band in sites_overview()}


def _shape_band(band):
    from django.urls import reverse
    from ..boundary_import import anchor_for, area_hectares, display_geometry

    locations = []
    located = 0
    for tile in band['tiles']:
        loc = tile['location']
        kind = anchor = None
        if loc.boundary:
            anchor = anchor_for(loc.boundary)
            if anchor is not None:
                kind = 'polygon'
        if kind is None and loc.has_coordinates:
            kind = 'circle'
            anchor = (float(loc.latitude), float(loc.longitude))
        if kind:
            located += 1
        state = capacity_state(tile['capacity'], tile['availability'])
        rest = rest_state(
            count=tile['count'], capacity=tile['capacity'],
            holds_horses=tile['holds_horses'], rest_days=tile['rest_days'],
        )
        locations.append({
            'pk': loc.pk,
            'name': loc.name,
            'site': loc.site,
            'count': tile['count'],
            'capacity': tile['capacity'],
            'availability': tile['availability'],
            'usage': tile['usage'],
            'usage_label': tile['usage_label'],
            'holds_horses': tile['holds_horses'],
            'rest_days': tile['rest_days'],
            'lat': float(loc.latitude) if loc.latitude is not None else None,
            'lng': float(loc.longitude) if loc.longitude is not None else None,
            'boundary': (
                display_geometry(loc.boundary, key=(loc.pk, loc.boundary_updated_at))
                if kind == 'polygon' else None
            ),
            'anchor': list(anchor) if anchor else None,
            'area_ha': (
                area_hectares(loc.boundary, key=(loc.pk, loc.boundary_updated_at))
                if kind == 'polygon' else None
            ),
            'kind': kind,
            'state': state,
            'colour': MAP_COLOURS[state],
            'rest_state': rest,
            'rest_label': REST_STATES[rest][0] or tile['usage_label'],
            'rest_colour': REST_STATES[rest][1],
            'radius_m': circle_radius_m(tile['capacity']) if kind == 'circle' else None,
            'urls': {
                'detail': reverse('location_detail', kwargs={'pk': loc.pk}),
            },
        })
    return {
        'urls': {'list': reverse('location_list')},
        'site': band['name'],
        'horses': band['horses'],
        'total': len(locations),
        'located': located,
        'unlocated': len(locations) - located,
        'locations': locations,
    }


def site_names():
    """Distinct site names with an active location, for the site switch."""
    from ..models import Location
    return list(
        Location.objects.active().exclude(site='')
        .order_by('site').values_list('site', flat=True).distinct()
    )
