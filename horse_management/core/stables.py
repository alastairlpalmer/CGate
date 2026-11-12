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

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Location, Placement, Stable, StableStay

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
    """Delete a cell and close the gap it leaves in the order.

    A stable that a horse has ever been in cannot go: its history would
    go with it. Take it out of use instead.
    """
    if StableStay.objects.filter(stable=stable).exists():
        raise ValidationError(
            f"Stable {stable.label} has a history of horses in it, so it cannot be "
            f"removed. Untick \"In use\" to stop using it."
        )
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


def stays_by_stable(barn):
    """``{stable pk: [open stays]}`` for a barn, each with its horse.

    One query. Stays in a stable are in name order, so a shared stable
    reads the same way every time it is drawn.
    """
    stays = (
        StableStay.objects.filter(stable__location=barn, end_date__isnull=True)
        .select_related('horse').order_by('horse__name')
    )
    found = {}
    for stay in stays:
        found.setdefault(stay.stable_id, []).append(stay)
    return found


def barn_plan(barn):
    """Everything the plan and the list need to draw one barn.

    Two queries: the cells, and who is in them. Each cell comes back with
    its place on the grid, a ``state`` — ``gap``, ``off`` (out of use),
    ``occupied`` or ``empty`` — and its ``stays``.
    """
    stables = list(barn.stables.order_by('position', 'pk'))
    layout = plan_cells(barn.layout or Location.Layout.ROW, len(stables))
    occupied = stays_by_stable(barn) if stables else {}
    cells = []
    for stable, (row, col) in zip(stables, layout['cells']):
        stays = occupied.get(stable.pk, [])
        if stable.is_gap:
            state = 'gap'
        elif stays:
            state = 'occupied'
        elif not stable.in_use:
            state = 'off'
        else:
            state = 'empty'
        cells.append({
            'stable': stable, 'row': row, 'col': col, 'state': state,
            'stays': stays,
            'horses': [stay.horse for stay in stays],
            'more': max(len(stays) - 1, 0),
        })
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
        'occupied_count': sum(1 for cell in cells if cell['state'] == 'occupied'),
        'stabled_horse_ids': {
            stay.horse_id for stays in occupied.values() for stay in stays
        },
        'has_cells': bool(stables),
    }


def picker_options(location, *, selected=None, exclude_horse=None):
    """The stables a horse could go into at ``location``, for a form.

    None when the location has no stables to offer — it is not a barn, or
    it is a barn with none yet — so the form shows no picker at all.
    Each option says who is in it, because "is 7 free?" is the question
    the person choosing is asking.
    """
    if location is None or not location.is_barn:
        return None
    stables = list(
        location.stables.filter(is_gap=False, in_use=True).order_by('position', 'pk')
    )
    if not stables:
        return None
    occupied = stays_by_stable(location)
    options = []
    for stable in stables:
        names = [
            stay.horse.name for stay in occupied.get(stable.pk, [])
            if exclude_horse is None or stay.horse_id != exclude_horse.pk
        ]
        options.append({
            'stable': stable,
            'names': _join_names(names),
            'selected': str(stable.pk) == str(selected or ''),
        })
    return {
        'location': location,
        'options': options,
        'any_occupied': any(option['names'] for option in options),
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


# ──────────────────────────────────────────────────────────────────────────
# Who is in which stable (phase 2a)
# ──────────────────────────────────────────────────────────────────────────

class StableOccupied(ValidationError):
    """The stable already has a horse in it and nobody said to share it.

    Carries the horses there (``occupants``) so the form can name them and
    ask the question, rather than only refuse.
    """

    def __init__(self, stable, occupants):
        self.stable = stable
        self.occupants = list(occupants)
        names = _join_names([horse.name for horse in self.occupants])
        super().__init__(
            f"Stable {stable.label} has {names} in it. "
            f"Tick \"Share the stable\" to put them together."
        )


def _join_names(names):
    names = list(names)
    if len(names) <= 1:
        return ''.join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def open_stay(horse):
    """The horse's current stable stay, or None."""
    return (
        StableStay.objects.filter(horse=horse, end_date__isnull=True)
        .select_related('stable', 'stable__location').first()
    )


def occupants_of(stable, exclude_horse=None):
    """The horses in a stable now."""
    from .models import Horse

    horses = Horse.objects.filter(
        stable_stays__stable=stable, stable_stays__end_date__isnull=True,
    )
    if exclude_horse is not None:
        horses = horses.exclude(pk=exclude_horse.pk)
    return list(horses.order_by('name'))


def _end_or_drop(stay, last_day, *, with_placement=False):
    """End a stay on ``last_day``; a stay that would end before it began
    never happened, so it is removed instead."""
    if last_day < stay.start_date:
        stay.delete()
        return None
    stay.end_date = last_day
    stay.closed_with_placement = with_placement
    stay.save(update_fields=['end_date', 'closed_with_placement'])
    return stay


class StableService:
    """The one writer of stable stays.

    Two halves. ``assign``, ``vacate``, ``swap`` and ``undo`` are what a
    person does. ``placement_saved`` and ``placement_deleted`` are called
    from ``Placement.save()`` and ``Placement.delete()`` and keep the rule
    that holds everything together:

        a horse's open stay is in the location of its open placement.
    """

    # ── what a person does ───────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def assign(horse, stable, day=None, *, share=False):
        """Put a horse in a stable from ``day``. Returns the new stay.

        The horse must live in the stable's barn. Whatever stable it had
        is left the day before. An occupied stable raises
        ``StableOccupied`` unless ``share`` is true — a mare and her foal
        share one, but nobody should double a stable up by mistake.

        The stable's row is locked while its occupants are read. The
        placement services take no locks to copy; without one here two
        people filling the same empty stable at the same moment would
        both be told it was free.
        """
        from .services import _refuse_future_date

        day = day or timezone.localdate()
        _refuse_future_date(day, 'A stable change')
        stable = (
            Stable.objects.select_for_update().select_related('location').get(pk=stable.pk)
        )
        if stable.is_gap:
            raise ValidationError(f"{stable.label} is not a stable.")
        if not stable.in_use:
            raise ValidationError(f"Stable {stable.label} is out of use.")

        placement = horse.placements.filter(end_date__isnull=True).first()
        if placement is None or placement.location_id != stable.location_id:
            raise ValidationError(f"{horse.name} is not in {stable.location.name}.")
        if day < placement.start_date:
            raise ValidationError(
                f"{horse.name} arrived in {stable.location.name} on "
                f"{placement.start_date:%-d %b %Y}. The stable change cannot be before that."
            )

        current = open_stay(horse)
        if current is not None and current.stable_id == stable.pk:
            return current
        if current is not None and day < current.start_date:
            raise ValidationError(
                f"{horse.name} went into stable {current.stable.label} on "
                f"{current.start_date:%-d %b %Y}. The change cannot be before that."
            )

        others = occupants_of(stable, exclude_horse=horse)
        if others and not share:
            raise StableOccupied(stable, others)

        if current is not None:
            _end_or_drop(current, day - timedelta(days=1))
        stay = StableStay(horse=horse, stable=stable, start_date=day)
        stay.full_clean()
        stay.save()
        return stay

    @staticmethod
    @transaction.atomic
    def vacate(horse, day=None):
        """Take a horse out of its stable. It stays in the barn.

        Returns the stay that ended, or None if it had no stable.
        """
        from .services import _refuse_future_date

        day = day or timezone.localdate()
        _refuse_future_date(day, 'A stable change')
        current = open_stay(horse)
        if current is None:
            return None
        if day < current.start_date:
            raise ValidationError(
                f"{horse.name} went into stable {current.stable.label} on "
                f"{current.start_date:%-d %b %Y}. It cannot leave before that."
            )
        _end_or_drop(current, day - timedelta(days=1))
        return current

    @staticmethod
    @transaction.atomic
    def swap(horse_a, horse_b, day=None):
        """Two horses in the same barn change stables. Returns both new stays."""
        from .services import _refuse_future_date

        day = day or timezone.localdate()
        _refuse_future_date(day, 'A stable change')
        stay_a, stay_b = open_stay(horse_a), open_stay(horse_b)
        if stay_a is None or stay_b is None:
            missing = horse_a if stay_a is None else horse_b
            raise ValidationError(f"{missing.name} has no stable to swap.")
        if stay_a.stable.location_id != stay_b.stable.location_id:
            raise ValidationError("Horses can only swap stables inside one barn.")
        if stay_a.stable_id == stay_b.stable_id:
            raise ValidationError(f"{horse_a.name} and {horse_b.name} share a stable already.")
        for stay in (stay_a, stay_b):
            if day < stay.start_date:
                raise ValidationError(
                    f"{stay.horse.name} went into stable {stay.stable.label} on "
                    f"{stay.start_date:%-d %b %Y}. The swap cannot be before that."
                )
        # Lock both, lowest first, so two swaps cannot wait on each other.
        list(Stable.objects.select_for_update().filter(
            pk__in=[stay_a.stable_id, stay_b.stable_id]).order_by('pk'))
        stable_a, stable_b = stay_a.stable, stay_b.stable
        last_day = day - timedelta(days=1)
        _end_or_drop(stay_a, last_day)
        _end_or_drop(stay_b, last_day)
        new_a = StableStay.objects.create(horse=horse_a, stable=stable_b, start_date=day)
        new_b = StableStay.objects.create(horse=horse_b, stable=stable_a, start_date=day)
        return new_a, new_b

    @staticmethod
    @transaction.atomic
    def undo(stay, previous_stable=None):
        """Take back the change that made ``stay``.

        Only the horse's current stay can be undone — anything older has
        been built on. The horse goes back to ``previous_stable`` (the
        stable it was in before, if it had one): the stay it left there is
        picked up again where there is one to pick up, so the history
        shows no break; otherwise a new one starts on the same day.

        Returns the stay the horse is back in, or None.
        """
        if stay.end_date is not None:
            raise ValidationError("That change has been followed by another and cannot be undone.")
        horse, day = stay.horse, stay.start_date
        stay.delete()
        if previous_stable is None:
            return None
        earlier = StableStay.objects.filter(
            horse=horse, stable=previous_stable, end_date=day - timedelta(days=1),
        ).first()
        if earlier is not None:
            earlier.end_date = None
            earlier.closed_with_placement = False
            earlier.save(update_fields=['end_date', 'closed_with_placement'])
            return earlier
        if previous_stable.is_gap or not previous_stable.in_use:
            return None
        return StableStay.objects.create(horse=horse, stable=previous_stable, start_date=day)

    # ── what a placement does ────────────────────────────────────────

    @staticmethod
    def placement_saved(placement, *, creating, was_open, old_end, old_location_id):
        """Keep the horse's stable in step with a placement that was just saved.

        Called at the end of ``Placement.save()``, once for every save, so
        it must be safe when nothing changed.

        ========================================  ==========================
        The placement was...                      The stay...
        ========================================  ==========================
        closed (a departure, or the first half    ends the same day, marked
        of any move)                              ``closed_with_placement``
        opened in the same barn the day after     is picked up again: a rate
        such a stay ended                         or owner change is not a
                                                  change of stable
        re-opened (a departure undone)            is picked up again
        open, and corrected to another location   ends the day before the
                                                  placement starts
        open, and its start moved later           starts no earlier than
                                                  the horse was in the barn
        ========================================  ==========================
        """
        horse_id = placement.horse_id
        is_open = placement.end_date is None
        stay = (
            StableStay.objects.filter(horse_id=horse_id, end_date__isnull=True)
            .select_related('stable').first()
        )

        if not is_open:
            if was_open and stay is not None:
                _end_or_drop(stay, placement.end_date, with_placement=True)
            return

        if stay is not None:
            if stay.stable.location_id != placement.location_id:
                # Moved, or an open placement corrected to somewhere else.
                _end_or_drop(stay, placement.start_date - timedelta(days=1))
            elif not creating and was_open:
                arrived = _run_start(placement)
                if stay.start_date < arrived:
                    stay.start_date = arrived
                    stay.save(update_fields=['start_date'])
            return

        newly_open = creating or not was_open
        if not newly_open:
            return
        # Opened: is there a stay to pick up? Created — the one that ended
        # the day before it starts. Re-opened — the one that ended with it.
        ended_on = placement.start_date - timedelta(days=1) if creating else old_end
        if ended_on is None:
            return
        earlier = (
            StableStay.objects.filter(
                horse_id=horse_id, end_date=ended_on, closed_with_placement=True,
                stable__location_id=placement.location_id,
            ).select_related('stable').order_by('-start_date', '-pk').first()
        )
        if earlier is None:
            return
        stable = earlier.stable
        if stable.is_gap or not stable.in_use:
            return
        if StableStay.objects.filter(stable=stable, end_date__isnull=True).exists():
            # Somebody else has the stable now. The horse is in the barn
            # with no stable, which is allowed, and visible on the plan.
            return
        earlier.end_date = None
        earlier.closed_with_placement = False
        earlier.save(update_fields=['end_date', 'closed_with_placement'])

    @staticmethod
    def placement_deleted(horse_id, location_id, start_date):
        """An open placement was deleted: the horse was never there from
        ``start_date``. A stay that began in it goes too; one that began
        before it (a rate change is two placements, one stay) ends the day
        before, as the placement before it did."""
        stay = (
            StableStay.objects.filter(
                horse_id=horse_id, end_date__isnull=True,
                stable__location_id=location_id,
            ).first()
        )
        if stay is None:
            return
        if Placement.objects.filter(
            horse_id=horse_id, end_date__isnull=True, location_id=location_id,
        ).exists():
            return
        _end_or_drop(stay, start_date - timedelta(days=1), with_placement=True)


def _run_start(placement):
    """The day the horse came to this location, across back-to-back
    placements: a rate change opens a new placement the day after the old
    one ends, and the horse did not go anywhere."""
    start = placement.start_date
    earlier = Placement.objects.filter(
        horse_id=placement.horse_id, location_id=placement.location_id,
        end_date__isnull=False, end_date__lt=start,
    ).order_by('-end_date').values_list('start_date', 'end_date')
    for earlier_start, earlier_end in earlier:
        if earlier_end != start - timedelta(days=1):
            break
        start = earlier_start
    return start


def annotate_stable(horses):
    """Add ``stable_label``, ``stable_position`` and ``stable_pk`` to a
    Horse queryset, from each horse's open stay.

    Subqueries, so a list of any length costs no extra query — the horse
    list, the quick find and the farrier's rows all print a stable beside
    a horse.
    """
    from django.db.models import OuterRef, Subquery

    stay = StableStay.objects.filter(horse=OuterRef('pk'), end_date__isnull=True)
    return horses.annotate(
        stable_label=Subquery(stay.values('stable__label')[:1]),
        stable_position=Subquery(stay.values('stable__position')[:1]),
        stable_pk=Subquery(stay.values('stable_id')[:1]),
    )


def stray_stays():
    """Open stays that break the rule: the horse's open placement is not
    in the stable's barn (or it has none). Always empty when
    ``StableService`` is the only writer — this is the check that it is.
    """
    from django.db.models import Exists, OuterRef

    at_home = Placement.objects.filter(
        horse=OuterRef('horse'), end_date__isnull=True,
        location=OuterRef('stable__location'),
    )
    return (
        StableStay.objects.filter(end_date__isnull=True)
        .annotate(at_home=Exists(at_home)).filter(at_home=False)
    )

