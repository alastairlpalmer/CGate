"""The yard at a glance: the figures across the top of the dashboard.

Each figure belongs to a feature area and shows only to a role that can
see that area, so an administrator sees every figure and a groom sees the
horses and the ground, not the money. A few cheap aggregate queries; no
figure lists rows.

``build`` returns a list of tiles:

    {'key', 'label', 'value', 'meta', 'url', 'tone'}

``meta`` is one short line under the figure (a per-site split, what is
overdue). ``tone`` is '' or 'alert', for a figure that needs a look.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from ..permissions import LEVEL_VIEW, has_feature_access
from core.templatetags.ui_extras import gbp

ZERO = Decimal('0')


def build(user, *, site='', today=None, needs_action=0, in_foal=None):
    """The tiles this user may see, in reading order.

    ``needs_action`` is the headline count the dashboard has already
    worked out; ``in_foal`` is the In foal list when the dashboard has
    loaded it (None: count it here).
    """
    today = today or timezone.localdate()

    def can(feature):
        return has_feature_access(user, feature, LEVEL_VIEW)

    tiles = []
    if can('horses') or can('owners'):
        stays = _open_stays(site)
        if can('horses'):
            tiles.append(_horses(site, stays))
        if can('owners'):
            tiles.append(_owners(stays))
    if can('locations'):
        tiles.append(_ground(site))
    tiles.append({
        'key': 'action',
        'label': 'Notifications',
        'value': needs_action,
        'meta': 'Need action' if needs_action else 'All clear',
        'url': '',
        'tone': 'alert' if needs_action else '',
    })
    if can('breeding'):
        tile = _in_foal(user, site, today, in_foal)
        # Like the In foal zone, the figure shows only in the season.
        if tile['value']:
            tiles.append(tile)
    if can('invoices'):
        tiles.append(_outstanding(today))
    return tiles


def _open_stays(site):
    """``(horse, owner, site)`` for every open stay of an active horse: one
    query that both the Horses and the Owners figures read."""
    from ..models import Placement

    stays = Placement.objects.filter(end_date__isnull=True, horse__is_active=True)
    if site:
        stays = stays.filter(location__site=site)
    return list(stays.values_list('horse_id', 'owner_id', 'location__site'))


def _horses(site, stays):
    """Horses on the yard, and how they split across sites."""
    horses = {horse for horse, _, _ in stays}
    by_site = {}
    for horse, _, name in stays:
        by_site.setdefault(name or '', set()).add(horse)
    meta = ''
    if len(by_site) > 1:
        meta = ' · '.join(
            f'{name or "No site"} {len(ids)}'
            for name, ids in sorted(by_site.items(), key=lambda kv: kv[0] or '~')
        )
    elif site:
        meta = site
    return {
        'key': 'horses', 'label': 'Horses', 'value': len(horses), 'meta': meta,
        'url': reverse('horse_list'), 'tone': '',
    }


def _owners(stays):
    """Owners with a horse on the yard, out of the owners on the books."""
    from ..models import Owner

    with_horses = len({owner for _, owner, _ in stays if owner})
    total = Owner.objects.active().count()
    return {
        'key': 'owners', 'label': 'Owners', 'value': with_horses,
        'meta': f'{total} on the books' if total != with_horses else 'All have horses here',
        'url': reverse('owner_list'), 'tone': '',
    }


def _ground(site):
    """Capacity: the places horses live in, and how many are taken.

    With barns on (``BARNS_ENABLED``) the line under the figure splits the
    fields from the stables, because they fill and empty differently.
    Otherwise it names the ground that needs a look: over capacity, and
    resting.
    """
    from django.conf import settings
    from ..models import Location

    locations = Location.objects.active().with_occupancy()
    if site:
        locations = locations.filter(site=site)
    fields = [0, 0]   # used, capacity
    stables = [0, 0]
    over = resting = 0
    for loc in locations:
        if loc.usage == Location.Usage.RESTED:
            resting += 1
        if loc.usage not in Location.HORSE_USAGES or not loc.capacity:
            continue
        if loc.horse_count + loc.turnout_count > loc.capacity:
            over += 1
        if loc.usage not in Location.RESIDENT_USAGES:
            continue
        bucket = stables if loc.is_barn else fields
        bucket[0] += loc.horse_count
        bucket[1] += loc.capacity
    used = fields[0] + stables[0]
    capacity = fields[1] + stables[1]

    if settings.BARNS_ENABLED and stables[1]:
        parts = [f'Fields {fields[0]}/{fields[1]}', f'Stables {stables[0]}/{stables[1]}']
        if over:
            parts.append(f'{over} over')
    else:
        parts = []
        if over:
            parts.append(f'{over} over capacity')
        if resting:
            parts.append(f'{resting} resting')
    return {
        'key': 'ground',
        'label': 'Capacity',
        'value': f'{used}/{capacity}' if capacity else used,
        'meta': ' · '.join(parts) or 'Nothing over capacity',
        'url': reverse('location_list'),
        'tone': 'alert' if over else '',
    }


def _in_foal(user, site, today, entries):
    if entries is None:
        from . import breeding
        entries = breeding.in_foal(user, site=site, today=today)
    meta = 'None in foal'
    upcoming = [e['due'] for e in entries if e.get('due') and e['due'] >= today]
    if upcoming:
        meta = f'Next due {min(upcoming):%-d %b}'
    elif entries:
        meta = f'{len(entries)} confirmed'
    return {
        'key': 'in_foal', 'label': 'In foal', 'value': len(entries), 'meta': meta,
        'url': reverse('breeding_list'), 'tone': '',
    }


def _outstanding(today):
    """What owners owe on sent invoices, and how much of it is late."""
    from invoicing.models import LIVE_PAYMENTS, Invoice

    rows = Invoice.objects.filter(
        status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE],
    ).annotate(
        balance=ExpressionWrapper(
            F('total') - Coalesce(Sum('payments__amount', filter=LIVE_PAYMENTS), Value(ZERO)),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
    ).values('due_date', 'balance')
    owed = ZERO
    late = 0
    for row in rows:
        balance = row['balance'] or ZERO
        if balance <= 0:
            continue
        owed += balance
        if row['due_date'] and row['due_date'] < today:
            late += 1
    return {
        'key': 'outstanding', 'label': 'Outstanding', 'value': gbp(owed),
        'meta': f'{late} overdue' if late else 'Nothing overdue',
        'url': reverse('invoice_list'), 'tone': 'alert' if late else '',
    }
