"""The farrier page: who may open it, and the only queries it may run.

docs/FARRIER_PAGE_PLAN.md, sections 4 and 8. Two rules live here so the
views cannot get them wrong:

* **The gate.** ``provider_page`` lets a view run only for a signed-in
  user with Full on ``farrier_portal`` whose login is linked to an active
  ``ServiceProvider``. The provider is put on ``request.provider``. A user
  with the access but no linked provider sees a page that says so.
* **The scope.** Every query takes that provider. No view reads a
  provider id from the URL or a form, so one farrier can never reach
  another farrier's visits or prices: an id that is not theirs is a 404.
"""

from functools import wraps

from django.shortcuts import get_object_or_404, render

from billing.models import ServiceItem, ServiceProvider
from core.permissions import LEVEL_FULL, feature_required

from .models import ProviderVisit


def provider_page(view_func):
    """Gate a farrier page view; see the module docstring."""

    @wraps(view_func)
    @feature_required('farrier_portal', LEVEL_FULL)
    def _wrapped(request, *args, **kwargs):
        provider = ServiceProvider.for_user(request.user)
        if provider is None:
            return render(request, 'farrier/no_provider.html', status=403)
        request.provider = provider
        return view_func(request, *args, **kwargs)

    return _wrapped


# --- Queries, always scoped to one provider -------------------------------

def visits(provider):
    """This provider's visits, newest first."""
    return ProviderVisit.objects.filter(provider=provider)


def open_visit(provider):
    """This provider's open visit, or None."""
    return visits(provider).filter(status=ProviderVisit.Status.OPEN).first()


def get_visit(provider, pk):
    """One of this provider's visits, or 404 (also for another's id)."""
    return get_object_or_404(ProviderVisit, pk=pk, provider=provider)


def price_list(provider, active_only=True):
    """This provider's own prices, in display order."""
    items = ServiceItem.objects.for_provider(provider)
    if active_only:
        items = items.filter(is_active=True)
    return items.order_by('sort_order', 'name')


def get_price(provider, pk):
    """One of this provider's prices, or 404 (also for the yard's items)."""
    return get_object_or_404(ServiceItem, pk=pk, provider=provider)


# --- Today's visit: the horse list ----------------------------------------

DUE_WITHIN_DAYS = 14


def horse_rows(provider, *, search='', location_id=None, today=None):
    """Active horses for Today's visit, split into (due, others).

    Each horse carries its last farrier work (any farrier's, so the
    farrier sees the real history), its next due date, its current
    location, and this provider's line on the open visit, if any. Owners
    are never loaded (docs/FARRIER_PAGE_PLAN.md, section 8, rule 6).
    """
    from datetime import timedelta

    from django.db.models import OuterRef, Subquery
    from django.utils import timezone

    from core.models import Horse, Placement

    from .models import FarrierVisit

    today = today or timezone.localdate()
    visit = open_visit(provider)
    # History leaves out lines on visits not yet finished (this farrier's
    # or another's): a horse tapped a moment ago stays in its group, with
    # a tick, instead of jumping out of "Due now".
    latest = (
        FarrierVisit.objects.filter(horse=OuterRef('pk'))
        .exclude(provider_visit__status=ProviderVisit.Status.OPEN)
        .order_by('-date', '-pk')
    )
    placement = Placement.objects.filter(horse=OuterRef('pk'), end_date__isnull=True).order_by('-start_date')
    horses = (
        Horse.objects.filter(is_active=True)
        .annotate(
            last_date=Subquery(latest.values('date')[:1]),
            last_work=Subquery(latest.values('work_done')[:1]),
            next_due=Subquery(latest.values('next_due_date')[:1]),
            location_id=Subquery(placement.values('location_id')[:1]),
            location_name=Subquery(placement.values('location__name')[:1]),
        )
        .only('pk', 'name', 'photo_thumb', 'photo')
        .order_by('name')
    )
    if search:
        horses = horses.filter(name__icontains=search.strip())
    if location_id:
        horses = horses.filter(location_id=location_id)

    lines = {}
    if visit is not None:
        lines = {line.horse_id: line for line in visit.farrier_visits.all()}

    work_labels = dict(FarrierVisit.WorkType.choices)
    horizon = today + timedelta(days=DUE_WITHIN_DAYS)
    due, others = [], []
    for horse in horses:
        horse.last_work_label = work_labels.get(horse.last_work, '')
        horse.line = lines.get(horse.pk)
        if horse.next_due is not None:
            horse.days_overdue = (today - horse.next_due).days
        else:
            horse.days_overdue = None
        is_due = horse.next_due is not None and horse.next_due <= horizon
        (due if is_due else others).append(horse)
    due.sort(key=lambda h: (h.next_due, h.name))
    return due, others


def visit_locations():
    """Locations that hold an active horse now, for the location filter."""
    from core.models import Location

    return (
        Location.objects.filter(
            placements__end_date__isnull=True, placements__horse__is_active=True,
        )
        .distinct()
        .order_by('site', 'name')
    )


# --- Recording a line ------------------------------------------------------

class LineError(ValueError):
    """A line the farrier cannot save; the message says why."""


def _get_or_open_visit(provider, user):
    """This provider's open visit, made if there is none.

    Locks the provider row first, so two taps at once open one visit,
    not two (the database also allows only one open visit).
    """
    from billing.models import ServiceProvider

    ServiceProvider.objects.select_for_update().filter(pk=provider.pk).first()
    visit = open_visit(provider)
    if visit is None:
        visit = ProviderVisit.objects.create(provider=provider, created_by=user)
    return visit


def record_line(provider, user, horse, *, item=None, work_done=None, price=None, note=''):
    """Add or change this horse's line on the provider's open visit.

    One line per horse per visit: a second tap replaces the first. ``item``
    is one of the provider's own prices; without it, ``work_done`` and
    ``price`` are required ("Other"). A ``price`` with an item overrides
    the item's price for this horse only.

    No charge is made here. Charges are made when the farrier taps Finish
    visit (step 4), through ``sync_record_charge``.
    """
    from decimal import Decimal, InvalidOperation

    from django.db import transaction

    from .models import FarrierVisit

    work_types = dict(FarrierVisit.WorkType.choices)
    if not horse.is_active:
        raise LineError("This horse has left the yard.")
    if item is not None:
        if item.provider_id != provider.pk or not item.is_active:
            raise LineError("That price is not on your list.")
        work_done = item.farrier_work or work_done or FarrierVisit.WorkType.REMEDIAL
        if price in (None, ''):
            price = item.price
    if work_done not in work_types:
        raise LineError("Choose the work done.")
    try:
        price = Decimal(str(price)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise LineError("Enter a price, for example 95 or 95.00.") from None
    if price < 0 or price > Decimal('9999.99'):
        raise LineError("Enter a price between £0 and £9,999.99.")
    note = (note or '').strip()[:500]
    notes = ' · '.join(part for part in (item.name if item else '', note) if part)

    with transaction.atomic():
        visit = _get_or_open_visit(provider, user)
        line = visit.farrier_visits.select_for_update().filter(horse=horse).first()
        if line is None:
            line = FarrierVisit(horse=horse, provider_visit=visit, service_provider=provider)
        line.date = visit.date
        line.work_done = work_done
        line.cost = price
        line.notes = notes
        line.save()
    return line


def remove_line(provider, horse):
    """Take this horse off the provider's open visit. Returns True if removed.

    A visit that becomes empty stays open; the farrier can keep adding.
    """
    from django.db import transaction

    with transaction.atomic():
        visit = open_visit(provider)
        if visit is None:
            return False
        line = visit.farrier_visits.select_for_update().filter(horse=horse).first()
        if line is None:
            return False
        if line.extra_charge_id is not None:
            raise LineError("This line is already billed. Ask the yard to change it.")
        line.delete()
        return True
