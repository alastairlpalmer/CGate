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


MAX_PRICE = '9999.99'


def parse_price(value):
    """A price from a form, as Decimal to the penny, or LineError."""
    from decimal import Decimal, InvalidOperation

    try:
        price = Decimal(str(value).strip().lstrip('£')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise LineError("Enter a price, for example 95 or 95.00.") from None
    if price < 0 or price > Decimal(MAX_PRICE):
        raise LineError("Enter a price between £0 and £9,999.99.")
    return price


def _line_values(provider, *, item=None, work_done=None, price=None, note=''):
    """Check a line's work, price and note: ``(work_done, price, notes)``.

    ``item`` is one of the provider's own active prices; without it,
    ``work_done`` and ``price`` are required ("Other"). A ``price`` with
    an item overrides the item's price for this horse only.
    """
    from .models import FarrierVisit

    if item is not None:
        if item.provider_id != provider.pk or not item.is_active:
            raise LineError("That price is not on your list.")
        work_done = item.farrier_work or work_done or FarrierVisit.WorkType.REMEDIAL
        if price in (None, ''):
            price = item.price
    if work_done not in dict(FarrierVisit.WorkType.choices):
        raise LineError("Choose the work done.")
    if price in (None, ''):
        raise LineError("Enter a price, for example 95 or 95.00.")
    price = parse_price(price)
    note = (note or '').strip()[:500]
    # The price's name goes in the notes only when its work type does not
    # already say it (a custom price with no farrier work type).
    label = item.name if item is not None and not item.farrier_work else ''
    notes = ' · '.join(part for part in (label, note) if part)
    return work_done, price, notes


def record_line(provider, user, horse, *, item=None, work_done=None, price=None, note=''):
    """Add or change this horse's line on the provider's open visit.

    One line per horse per visit: a second tap replaces the first. See
    ``_line_values`` for the price rules.

    No charge is made here. Charges are made when the farrier taps Finish
    visit (step 4), through ``sync_record_charge``.
    """
    from django.db import transaction

    from .models import FarrierVisit

    if not horse.is_active:
        raise LineError("This horse has left the yard.")
    work_done, price, notes = _line_values(
        provider, item=item, work_done=work_done, price=price, note=note,
    )
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


# --- Finish visit ----------------------------------------------------------

class FinishedLine:
    """One line of a finished visit, as the farrier and the yard see it."""

    def __init__(self, record, status):
        self.record = record
        self.horse = record.horse
        self.work = record.get_work_done_display()
        self.cost = record.cost
        self.next_due = record.next_due_date
        self.note = record.notes
        # 'created' or 'updated': on the owner's next invoice. 'no_owner':
        # kept, not billed. 'invoiced': already on an invoice (unchanged).
        self.status = status
        charge = record.extra_charge
        self.billed_to = charge.owner if charge is not None else None

    @property
    def billed(self):
        """True when the line has a charge on an owner's account."""
        return self.record.extra_charge_id is not None


def finish_visit(provider, user):
    """Close the provider's open visit and bill every line.

    Each line goes through ``sync_record_charge`` (the one billing path,
    docs/FARRIER_PAGE_PLAN.md, section 8, rule 1): a charge for the
    horse's current owner, split between co-owners when the invoice is
    made. A horse with no owner keeps its record and is not billed.

    Runs once: the provider row is locked, and a second tap finds no open
    visit and returns None. After the transaction commits, the farrier
    and the yard each get a summary email.

    Returns ``(visit, lines)`` or None when there is nothing to finish.
    """
    from django.db import transaction
    from django.utils import timezone

    from billing.models import ServiceProvider

    from .views import sync_record_charge

    with transaction.atomic():
        ServiceProvider.objects.select_for_update().filter(pk=provider.pk).first()
        visit = open_visit(provider)
        if visit is None:
            return None
        records = list(
            visit.farrier_visits.select_for_update()
            .select_related('horse').order_by('horse__name')
        )
        if not records:
            raise LineError("Add at least one horse before you finish.")
        lines = []
        for record in records:
            status = sync_record_charge(record)
            record.refresh_from_db()
            lines.append(FinishedLine(record, status))
        visit.status = ProviderVisit.Status.FINISHED
        visit.finished_at = timezone.now()
        visit.save(update_fields=['status', 'finished_at', 'updated_at'])
        transaction.on_commit(lambda: send_visit_emails(visit.pk))
    return visit, lines


def finished_lines(visit):
    """The lines of a finished visit, rebuilt for the done screen."""
    lines = []
    for record in visit.farrier_visits.select_related('horse', 'extra_charge__owner').order_by('horse__name'):
        status = 'created' if record.extra_charge_id else ('no_owner' if record.cost > 0 else None)
        lines.append(FinishedLine(record, status))
    return lines


def farrier_email_address(provider):
    """Where the farrier's summary goes: the provider's email, else the login's."""
    if provider.email:
        return provider.email
    user = provider.user
    return getattr(user, 'email', '') or ''


def send_visit_emails(visit_pk):
    """Email the visit summary to the farrier and to the yard.

    A failed email never undoes the visit: the charges are already made.
    Returns ``(farrier_sent, yard_sent)``.
    """
    from notifications.emails import send_farrier_visit_summary, send_yard_visit_summary

    visit = ProviderVisit.objects.select_related('provider__user').get(pk=visit_pk)
    lines = finished_lines(visit)
    return (
        send_farrier_visit_summary(visit, lines, farrier_email_address(visit.provider)),
        send_yard_visit_summary(visit, lines),
    )


# --- Same-day changes to a finished visit ---------------------------------

def can_change_finished(visit, today=None):
    """True while the farrier may still change a finished visit.

    Same day only, and only until the yard has checked the visit
    (docs/FARRIER_PAGE_PLAN.md, section 5, screen 4). Each line is also
    refused once its charge is on a live invoice.
    """
    from django.utils import timezone

    today = today or timezone.localdate()
    return (
        not visit.is_open
        and visit.date == today
        and visit.checked_at is None
    )


def get_finished_line(provider, visit_pk, line_pk):
    """A line on one of this provider's visits, or 404."""
    from .models import FarrierVisit

    visit = get_visit(provider, visit_pk)
    line = get_object_or_404(
        FarrierVisit.objects.select_related('horse', 'extra_charge'), pk=line_pk, provider_visit=visit,
    )
    return visit, line


def _guard_finished_change(visit, line):
    if not can_change_finished(visit):
        raise LineError("This visit can no longer be changed here. Ask the yard.")
    if line.extra_charge is not None and line.extra_charge.on_live_invoice:
        raise LineError("This line is already on an invoice. Ask the yard to change it.")


def change_finished_line(provider, visit, line, *, item=None, work_done=None, price=None, note=''):
    """Change one line of a finished visit, and its charge, on the same day.

    The charge follows through ``sync_record_charge``: a new amount and
    description, a charge made if the line was free before, or the charge
    removed if the price is now £0.
    """
    from django.db import transaction

    from .views import sync_record_charge

    _guard_finished_change(visit, line)
    work_done, price, notes = _line_values(
        provider, item=item, work_done=work_done, price=price, note=note,
    )
    with transaction.atomic():
        line.work_done = work_done
        line.cost = price
        line.notes = notes
        line.save()
        status = sync_record_charge(line)
    return status


def remove_finished_line(provider, visit, line):
    """Take one line off a finished visit on the same day, with its charge."""
    from django.db import transaction

    from .views import sync_record_charge

    _guard_finished_change(visit, line)
    with transaction.atomic():
        line.cost = 0
        line.save(update_fields=['cost'])
        sync_record_charge(line)
        line.delete()


# --- My prices -------------------------------------------------------------

def save_price(provider, *, name, work_done, price, item=None):
    """Add a price to the provider's list, or change one of theirs."""
    from django.db.models import Max

    from .models import FarrierVisit

    name = (name or '').strip()
    if not name:
        raise LineError("Enter a name, for example Full set.")
    if len(name) > 120:
        raise LineError("Use 120 characters or fewer for the name.")
    if work_done and work_done not in dict(FarrierVisit.WorkType.choices):
        raise LineError("Choose a work type from the list.")
    price = parse_price(price)
    clash = price_list(provider, active_only=False).filter(name__iexact=name)
    if item is not None:
        clash = clash.exclude(pk=item.pk)
    if clash.exists():
        raise LineError("You already have a price with this name.")
    if item is None:
        top = price_list(provider, active_only=False).aggregate(top=Max('sort_order'))['top']
        item = ServiceItem(
            provider=provider,
            category=ServiceItem.Category.FARRIER,
            sort_order=(top or 0) + 1,
        )
    elif item.provider_id != provider.pk:
        raise LineError("That price is not on your list.")
    item.name = name
    item.farrier_work = work_done or ''
    item.price = price
    item.save()
    return item
