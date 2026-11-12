"""Day turnout (docs/BARNS_AND_STABLES_PLAN.md, phase 3).

A horse that lives in a barn can have one standing turnout location: the
field it goes out in. ``Turnout`` rows hold it; ``TurnoutService`` is
their one writer.

It is an arrangement, not a diary. Nothing here records a horse going out
or coming in, and nothing should: forty horses twice a day is eighty taps
nobody will keep up, and the data would be wrong by the second week.

What it buys: the field knows who uses it. It shows them, counts them
against its capacity, and is not marked "rested" while they graze it.
Billing never reads any of this — the horse's rate is its placement's.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .models import Location, LocationUsagePeriod, Placement, Turnout


def open_turnout(horse):
    """The horse's current turnout, or None."""
    return (
        Turnout.objects.filter(horse=horse, end_date__isnull=True)
        .select_related('location').first()
    )


def lives_in_a_barn(horse):
    """The barn the horse lives in, or None."""
    placement = (
        Placement.objects.filter(horse=horse, end_date__isnull=True)
        .select_related('location').first()
    )
    if placement is None or not placement.location.is_barn:
        return None
    return placement.location


# ── the field's side of it ────────────────────────────────────────────────
#
# A turnout starting or ending is, to the field, what an arrival or a
# departure is: it may stop being empty, or become empty. These three keep
# the land-use history right, through the same LocationUsageService the
# placements use.

def _usage():
    from .services import LocationUsageService
    return LocationUsageService


def _field_gained(location, day, *, was_empty):
    usage = _usage()
    usage.clear_auto_rest_from(location, day)
    if was_empty:
        usage._set_usage_auto(location, Location.Usage.TURNOUT, day)


def _field_lost(location, last_day):
    _usage().rest_if_empty(location, last_day)


def _field_never_had(location, start):
    """A turnout that began on ``start`` is being removed as if it never
    was. If it is what turned an empty field into "turnout", take that
    period out and re-open the one before, rather than resting the field
    from today with a false history behind it."""
    usage = _usage()
    if not usage._is_empty(location):
        return
    current = location.usage_periods.filter(end_date__isnull=True).first()
    if not (
        current and current.usage == Location.Usage.TURNOUT
        and current.source == LocationUsagePeriod.Source.AUTO
        and current.start_date >= start
    ):
        usage.rest_if_empty(location, timezone.localdate())
        return
    previous = location.usage_periods.filter(
        end_date=current.start_date - timedelta(days=1)
    ).order_by('-start_date').first()
    current.delete()
    if previous:
        previous.end_date = None
        previous.save()
        restored = previous.usage
    else:
        restored = Location.Usage.RESTED
        LocationUsagePeriod.objects.create(
            location=location, usage=restored, start_date=current.start_date,
            source=LocationUsagePeriod.Source.AUTO,
        )
    Location.objects.filter(pk=location.pk).update(usage=restored)
    location.usage = restored


def _end_or_drop(turnout, last_day, *, with_placement=False):
    """End a turnout on ``last_day``; one that would end before it began
    never happened, so it is removed."""
    location = turnout.location
    if last_day < turnout.start_date:
        start = turnout.start_date
        turnout.delete()
        _field_never_had(location, start)
        return None
    turnout.end_date = last_day
    turnout.closed_with_placement = with_placement
    turnout.save(update_fields=['end_date', 'closed_with_placement'])
    _field_lost(location, last_day)
    return turnout


class TurnoutService:
    """The one writer of turnouts. The shape of ``StableService``: what a
    person does, then what a placement does."""

    @staticmethod
    @transaction.atomic
    def start(horse, location, day=None):
        """Turn a stabled horse out on ``location`` from ``day``.

        Also how the location is *changed*: whatever turnout the horse had
        ends the day before. Returns the turnout.
        """
        from .services import LocationUsageService, _refuse_future_date

        day = day or timezone.localdate()
        _refuse_future_date(day, 'A turnout change')
        if location.is_barn:
            raise ValidationError(f"{location.name} is a barn. Choose the ground the horse goes out on.")
        if location.is_archived:
            raise ValidationError(f"{location.name} is archived.")
        placement = (
            Placement.objects.filter(horse=horse, end_date__isnull=True)
            .select_related('location').first()
        )
        if placement is None or not placement.location.is_barn:
            raise ValidationError(
                f"{horse.name} does not live in a barn. A horse that lives out "
                f"is moved to its field, not turned out on it."
            )
        if day < placement.start_date:
            raise ValidationError(
                f"{horse.name} arrived in {placement.location.name} on "
                f"{placement.start_date:%-d %b %Y}. Turnout cannot start before that."
            )
        current = open_turnout(horse)
        if current is not None and current.location_id == location.pk:
            return current
        if current is not None:
            if day < current.start_date:
                raise ValidationError(
                    f"{horse.name} has gone out in {current.location.name} since "
                    f"{current.start_date:%-d %b %Y}. The change cannot be before that."
                )
            _end_or_drop(current, day - timedelta(days=1))

        was_empty = LocationUsageService._is_empty(location)
        turnout = Turnout(horse=horse, location=location, start_date=day)
        turnout.full_clean()
        turnout.save()
        _field_gained(location, day, was_empty=was_empty)
        return turnout

    # The same act under the name the person has for it.
    change = start

    @staticmethod
    @transaction.atomic
    def stop(horse, day=None):
        """Stop a horse's turnout. Returns the turnout that ended, or None."""
        from .services import _refuse_future_date

        day = day or timezone.localdate()
        _refuse_future_date(day, 'A turnout change')
        current = open_turnout(horse)
        if current is None:
            return None
        if day < current.start_date:
            raise ValidationError(
                f"{horse.name} has gone out in {current.location.name} since "
                f"{current.start_date:%-d %b %Y}. It cannot stop before that."
            )
        _end_or_drop(current, day - timedelta(days=1))
        return current

    # ── what a placement does ────────────────────────────────────────

    @staticmethod
    def placement_saved(placement, *, creating, was_open, old_end):
        """Keep the horse's turnout in step with a placement just saved.

        The rule is looser than a stable's: a turnout needs the horse to
        live in *a* barn, not a particular one. So it survives a rate
        change and a move from one barn to another, and ends when the
        horse leaves the yard or goes to live in a field.

        Called once for every ``Placement.save()``; safe when nothing
        changed.
        """
        horse_id = placement.horse_id
        turnout = (
            Turnout.objects.filter(horse_id=horse_id, end_date__isnull=True)
            .select_related('location').first()
        )
        if placement.end_date is not None:
            if was_open and turnout is not None:
                _end_or_drop(turnout, placement.end_date, with_placement=True)
            return

        in_barn = placement.location.is_barn
        if turnout is not None:
            if not in_barn:
                _end_or_drop(turnout, placement.start_date - timedelta(days=1))
            return
        if not in_barn or not (creating or not was_open):
            return
        # Opened in a barn: pick up the turnout that ended with the
        # placement before it (or with this one, if it is being re-opened).
        ended_on = placement.start_date - timedelta(days=1) if creating else old_end
        if ended_on is None:
            return
        earlier = (
            Turnout.objects.filter(
                horse_id=horse_id, end_date=ended_on, closed_with_placement=True,
            ).select_related('location').order_by('-start_date', '-pk').first()
        )
        if earlier is None or earlier.location.is_archived:
            return
        earlier.end_date = None
        earlier.closed_with_placement = False
        earlier.save(update_fields=['end_date', 'closed_with_placement'])
        # Ending it may have rested the field a moment ago; that rest is
        # as false as the one a same-field move leaves behind.
        _usage().undo_auto_rest(earlier.location)

    @staticmethod
    def placement_deleted(horse_id, start_date):
        """An open placement was deleted. With no barn to live in, the
        horse has no turnout either."""
        turnout = (
            Turnout.objects.filter(horse_id=horse_id, end_date__isnull=True)
            .select_related('location').first()
        )
        if turnout is None:
            return
        if Placement.objects.filter(
            horse_id=horse_id, end_date__isnull=True,
            location__location_type=Location.LocationType.BARN,
        ).exists():
            return
        _end_or_drop(turnout, start_date - timedelta(days=1), with_placement=True)


# ── reading ───────────────────────────────────────────────────────────────

def annotate_turnout(horses):
    """Add ``turnout_name`` and ``turnout_location_id`` to a Horse queryset.

    Subqueries, so a list of any length costs no extra query.
    """
    turnout = Turnout.objects.filter(horse=OuterRef('pk'), end_date__isnull=True)
    return horses.annotate(
        turnout_name=Subquery(turnout.values('location__name')[:1]),
        turnout_location_id=Subquery(turnout.values('location_id')[:1]),
    )


def turnout_horses(location):
    """The stabled horses that go out on ``location``, with the barn each
    lives in as ``home_name``. Two queries."""
    turnouts = list(
        Turnout.objects.filter(location=location, end_date__isnull=True)
        .select_related('horse').order_by('horse__name')
    )
    homes = dict(
        Placement.objects.filter(
            horse_id__in=[turnout.horse_id for turnout in turnouts], end_date__isnull=True,
        ).values_list('horse_id', 'location__name')
    )
    horses = []
    for turnout in turnouts:
        horse = turnout.horse
        horse.home_name = homes.get(horse.pk, '')
        horse.turnout_since = turnout.start_date
        horses.append(horse)
    return horses


def turnout_counts(location_ids=None):
    """``{location pk: horses out by day}`` in one query."""
    from django.db.models import Count

    rows = Turnout.objects.filter(end_date__isnull=True)
    if location_ids is not None:
        rows = rows.filter(location_id__in=location_ids)
    return dict(rows.values('location_id').annotate(n=Count('pk')).values_list('location_id', 'n'))


def turnout_choices(barn):
    """Grouped choices for the turnout picker: ground on the barn's own
    site first, then every other site. Barns are not ground."""
    fields = list(
        Location.objects.active().fields().order_by('site', 'name').values_list('pk', 'name', 'site')
    )
    here = [(pk, name) for pk, name, site in fields if site == barn.site]
    choices = []
    if here:
        choices.append((barn.site or 'This site', here))
    others = {}
    for pk, name, site in fields:
        if site != barn.site:
            others.setdefault(site or 'Other', []).append((pk, name))
    choices.extend(others.items())
    return choices


def stray_turnouts():
    """Open turnouts for a horse that does not live in a barn. Always
    empty while ``TurnoutService`` is the only writer."""
    from django.db.models import Exists

    in_a_barn = Placement.objects.filter(
        horse=OuterRef('horse'), end_date__isnull=True,
        location__location_type=Location.LocationType.BARN,
    )
    return (
        Turnout.objects.filter(end_date__isnull=True)
        .annotate(in_a_barn=Exists(in_a_barn)).filter(in_a_barn=False)
    )
