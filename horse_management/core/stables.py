"""Barns and their stables (docs/BARNS_AND_STABLES_PLAN.md).

A barn is a ``Location`` with ``location_type = 'barn'``. Its stables are
``Stable`` rows that know only their order. Everything here turns that
order into something to look at, or keeps the barn's stored capacity in
step with its stables.

``plan_cells`` and ``next_labels`` are pure: no database, so the maths can
be tested on its own.
"""

from __future__ import annotations

import math
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Location, Stable

# A single line of stables wraps after this many, so a long barn does not
# become a strip nobody can read without scrolling sideways.
ROW_WRAP = 12

# The narrowest a cell draws, and the gap between cells, in CSS pixels.
# The plan's container needs them to know how wide to make its scroll area.
CELL_MIN_PX = 64
CELL_GAP_PX = 6

MAX_STABLES = 200


def _row_plan(count):
    cols = min(count, ROW_WRAP)
    lines = math.ceil(count / ROW_WRAP)
    cells = [(2 * (i // ROW_WRAP) + 1, i % ROW_WRAP + 1) for i in range(count)]
    features = [
        {'kind': 'walkway', 'row': 2 * line + 2, 'col': 1, 'row_span': 1, 'col_span': cols}
        for line in range(lines)
    ]
    return {'cols': cols, 'rows': 2 * lines, 'cells': cells, 'features': features}


def _aisle_plan(count):
    top = math.ceil(count / 2)
    cells = [(1, i + 1) for i in range(top)]
    cells += [(3, i + 1) for i in range(count - top)]
    features = [{'kind': 'aisle', 'row': 2, 'col': 1, 'row_span': 1, 'col_span': top}]
    return {'cols': top, 'rows': 3, 'cells': cells, 'features': features}


def _courtyard_plan(count):
    # A U, numbered clockwise from the top left. The top never has fewer
    # than three cells: with two, the sides touch and there is no yard.
    side = min(count // 3, (count - 3) // 2)
    top = count - 2 * side
    rows = side + 1
    cells = [(1, i + 1) for i in range(top)]
    cells += [(1 + j, top) for j in range(1, side + 1)]
    cells += [(rows - j + 1, 1) for j in range(1, side + 1)]
    features = [{'kind': 'yard', 'row': 2, 'col': 2, 'row_span': side, 'col_span': top - 2}]
    return {'cols': top, 'rows': rows, 'cells': cells, 'features': features}


def plan_cells(layout: str, count: int) -> dict:
    """Where each of a barn's cells is drawn.

    ``count`` is every cell, stables and gaps. Returns the grid size, a
    ``(row, col)`` for each cell in order (both from 1), and the features
    drawn between them — a yard, an aisle or a walkway.

    A layout that makes no sense at a small count falls back to a row: a
    courtyard needs five cells to have a yard, an aisle needs two to have
    two sides.
    """
    if count <= 0:
        return {'cols': 0, 'rows': 0, 'cells': [], 'features': []}
    if layout == Location.Layout.COURTYARD and count >= 5:
        return _courtyard_plan(count)
    if layout == Location.Layout.AISLE and count >= 2:
        return _aisle_plan(count)
    return _row_plan(count)


def next_labels(existing, count, first_number=None):
    """Labels for ``count`` new stables.

    They continue from the highest number already in the barn (a stable
    called "Foaling" has no number and is skipped), or start at
    ``first_number`` when one is given. A number that is already taken is
    stepped over, so the result never clashes with ``existing``.
    """
    taken = {str(label).strip() for label in existing}
    if first_number is None:
        numbers = [int(label) for label in taken if label.isdigit()]
        first_number = max(numbers) + 1 if numbers else 1
    labels = []
    number = first_number
    while len(labels) < count:
        if str(number) not in taken:
            labels.append(str(number))
        number += 1
    return labels


def sync_barn_capacity(barn):
    """Set a barn's capacity to its number of stables in use.

    ``Location.capacity`` stays the one stored number, so the ring, the
    board and the finance chart read a barn exactly as they read anything
    else. A barn with no stables keeps whatever was typed — a yard can
    start with a barn and add its stables later.
    """
    stables = barn.stables.filter(is_gap=False)
    if not stables.exists():
        return barn.capacity
    capacity = stables.filter(in_use=True).count()
    if barn.capacity != capacity:
        Location.objects.filter(pk=barn.pk).update(capacity=capacity)
        barn.capacity = capacity
    return capacity


def _renumber(barn):
    """Close any gaps in the order after a cell is added or removed."""
    changed = []
    for index, stable in enumerate(barn.stables.order_by('position', 'pk'), start=1):
        if stable.position != index:
            stable.position = index
            changed.append(stable)
    if changed:
        Stable.objects.bulk_update(changed, ['position'])


@transaction.atomic
def add_stables(barn, count, first_number=None):
    """Add ``count`` stables to the end of a barn. Returns them."""
    existing = list(barn.stables.values_list('label', flat=True))
    start = len(existing)
    stables = Stable.objects.bulk_create([
        Stable(location=barn, label=label, position=start + index)
        for index, label in enumerate(next_labels(existing, count, first_number), start=1)
    ])
    sync_barn_capacity(barn)
    return stables


@transaction.atomic
def add_gap(barn, label, after=None):
    """Put a gap (tack room, feed room, doorway) into a barn's order.

    ``after`` is the cell it follows; None puts it first. The cells after
    it each move one place on.
    """
    position = after.position + 1 if after is not None else 1
    barn.stables.filter(position__gte=position).update(position=F('position') + 1)
    gap = Stable(location=barn, label=label, position=position, is_gap=True)
    gap.full_clean()
    gap.save()
    _renumber(barn)
    return gap


@transaction.atomic
def remove_stable(stable):
    """Delete a cell and close the gap it leaves in the order."""
    barn = stable.location
    stable.delete()
    _renumber(barn)
    sync_barn_capacity(barn)


@transaction.atomic
def convert_to_barn(location, *, layout, stable_count=0, first_number=1, today=None):
    """Turn an ordinary location into a barn. There is no way back.

    A yard that kept horses in "Big Barn" before barns existed has it
    saved as ground. Its placements, its past usage and its outline all
    stay; only its open usage period is closed, because a barn has no
    land use to track from here on.
    """
    today = today or timezone.localdate()
    current = location.usage_periods.filter(end_date__isnull=True).first()
    if current is not None:
        if current.start_date >= today:
            current.delete()
        else:
            current.end_date = today - timedelta(days=1)
            current.save()
    location.location_type = Location.LocationType.BARN
    location.layout = layout
    location.usage = Location.Usage.HORSES
    location.save()
    if stable_count:
        add_stables(location, stable_count, first_number)
    return location


def barn_plan(barn):
    """Everything the plan and the list need to draw one barn.

    One query for the cells. Each comes back with its place on the grid
    and a ``state`` — ``gap``, ``off`` (out of use) or ``empty``.
    """
    stables = list(barn.stables.order_by('position', 'pk'))
    layout = plan_cells(barn.layout or Location.Layout.ROW, len(stables))
    cells = []
    for stable, (row, col) in zip(stables, layout['cells']):
        if stable.is_gap:
            state = 'gap'
        elif not stable.in_use:
            state = 'off'
        else:
            state = 'empty'
        cells.append({'stable': stable, 'row': row, 'col': col, 'state': state})
    real = [s for s in stables if not s.is_gap]
    cols = layout['cols']
    return {
        'cols': cols,
        'rows': layout['rows'],
        'cells': cells,
        'features': layout['features'],
        'min_width': cols * CELL_MIN_PX + max(cols - 1, 0) * CELL_GAP_PX,
        'stable_count': len(real),
        'in_use_count': sum(1 for s in real if s.in_use),
        'out_of_use_count': sum(1 for s in real if not s.in_use),
        'has_cells': bool(stables),
    }


def barn_summaries(barns):
    """``{barn pk: (stables, out of use)}`` for a page of location cards.

    One query however many barns are on the page.
    """
    rows = Stable.objects.filter(
        location__in=[barn.pk for barn in barns], is_gap=False,
    ).values('location_id').annotate(
        total=Count('pk'), off=Count('pk', filter=Q(in_use=False)),
    )
    return {row['location_id']: (row['total'], row['off']) for row in rows}

