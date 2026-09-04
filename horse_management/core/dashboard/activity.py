"""What changed on the yard: one chronological log.

Each source is a small, ordered, capped query; the results merge by date
and the newest survive. That is what "recent" means. The old widget took
the three newest rows of each type, which surfaced a feed-out from five
months ago as recent activity.

Two things make the log readable rather than a ledger:

- A health record with a cost and the charge it created are one thing, so
  the charge is folded into the record's row and not listed twice.
- The same thing done to several horses on the same day (the farrier's
  round, a batch of vaccinations, three arrivals into one location) is one
  row naming every horse, not one row per horse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from ..permissions import LEVEL_VIEW, has_feature_access

# Rows fetched per source; the merged log shows at most MAX_ROWS.
DEFAULT_LIMIT = 14
MAX_ROWS = 12

# Kinds that group when they share a day and a description.
GROUPABLE = frozenset({'arrival', 'move', 'departure', 'vaccination', 'farrier', 'worming', 'charge'})

# Tint per kind for the row icon; the template maps these to CSS classes.
KIND_GROUP = {
    'vaccination': 'health', 'farrier': 'health', 'vet': 'health',
    'worming': 'health', 'egg_count': 'health',
    'charge': 'money', 'payment': 'money', 'invoice': 'money',
    'arrival': 'yard', 'move': 'yard', 'departure': 'yard', 'feed': 'yard',
    'document': 'documents',
}


@dataclass
class Name:
    """One subject on a row: a horse (with its coat colour), an owner or a
    location. ``color`` is the horse's coat code for the dot; blank for the
    others."""

    name: str
    url: str
    color: str = ''


@dataclass
class Event:
    date: date
    kind: str
    what: str
    names: list = field(default_factory=list)
    url: str = ''
    site: str = ''
    amount: Decimal | None = None
    sort_ts: float = 0.0
    count: int = 1

    @property
    def subject(self):
        return self.names[0].name if self.names else ''

    @property
    def text(self):
        who = ', '.join(n.name for n in self.names)
        return f'{who} · {self.what}' if who else self.what

    @property
    def group(self):
        return KIND_GROUP.get(self.kind, 'yard')

    @property
    def sort_key(self):
        return (self.date, self.sort_ts)


def _ts(value):
    if isinstance(value, datetime):
        return value.timestamp()
    return 0.0


def _horse(horse):
    return Name(horse.name, reverse('horse_detail', args=[horse.pk]), horse.color or '')


def _placement_events(limit):
    """Arrivals, moves and departures from the placement log.

    A move closes one placement and opens another the next day; when both
    halves are in the window they merge into one "moved" event.
    """
    from ..models import Placement

    starts = list(Placement.objects.select_related('horse', 'location').order_by(
        '-start_date', '-pk',
    )[:limit])
    ends = list(Placement.objects.filter(end_date__isnull=False).select_related(
        'horse', 'location',
    ).order_by('-end_date', '-pk')[:limit])

    starts_by_horse_day = {(p.horse_id, p.start_date): p for p in starts}
    merged_starts = set()
    events = []
    for placement in ends:
        follow_on = starts_by_horse_day.get((placement.horse_id, placement.end_date + timedelta(days=1)))
        if follow_on is not None:
            merged_starts.add(follow_on.pk)
            events.append(Event(
                date=follow_on.start_date, kind='move',
                what=f'Moved from {placement.location.name} to {follow_on.location.name}',
                names=[_horse(placement.horse)],
                site=follow_on.location.site, sort_ts=_ts(follow_on.created_at),
            ))
        else:
            events.append(Event(
                date=placement.end_date, kind='departure',
                what=f'Left {placement.location.name}',
                names=[_horse(placement.horse)],
                site=placement.location.site, sort_ts=_ts(placement.updated_at),
            ))
    for placement in starts:
        if placement.pk in merged_starts:
            continue
        events.append(Event(
            date=placement.start_date, kind='arrival',
            what=f'Arrived at {placement.location.name}',
            names=[_horse(placement.horse)],
            site=placement.location.site, sort_ts=_ts(placement.created_at),
        ))
    return events


def _cost(record):
    return record.cost if record.cost and record.cost > 0 else None


def recent(user, *, site='', today=None, limit=DEFAULT_LIMIT, max_rows=MAX_ROWS):
    """The newest events the user may see, grouped by day."""
    today = today or timezone.localdate()
    can = lambda feature: has_feature_access(user, feature, LEVEL_VIEW)  # noqa: E731

    where = {}
    if site:
        from ..models import Placement
        where = {
            horse_id: loc_site
            for horse_id, loc_site in Placement.objects.filter(
                end_date__isnull=True,
            ).values_list('horse_id', 'location__site')
        }

    events = []

    if can('horses'):
        events.extend(_placement_events(limit))

        from ..models import Document
        for doc in Document.objects.select_related('horse', 'owner').order_by('-uploaded_at')[:limit]:
            if doc.horse_id:
                names = [_horse(doc.horse)]
            elif doc.owner_id:
                names = [Name(doc.owner.name, reverse('owner_detail', args=[doc.owner_id]))]
            else:
                names = []
            events.append(Event(
                date=timezone.localtime(doc.uploaded_at).date(), kind='document',
                what=f'{doc.get_doc_type_display()} added · {doc.title}',
                names=names, site=where.get(doc.horse_id, ''), sort_ts=_ts(doc.uploaded_at),
            ))

    if can('health'):
        from health.models import (
            FarrierVisit, Vaccination, VetVisit, WormEggCount, WormingTreatment,
        )
        for vax in Vaccination.objects.select_related('horse', 'vaccination_type').order_by(
            '-date_given', '-pk',
        )[:limit]:
            events.append(Event(
                date=vax.date_given, kind='vaccination',
                what=f'Vaccinated · {vax.vaccination_type.name}',
                names=[_horse(vax.horse)], amount=_cost(vax),
                site=where.get(vax.horse_id, ''), sort_ts=_ts(vax.created_at),
            ))
        for visit in FarrierVisit.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=visit.date, kind='farrier',
                what=f'Farrier · {visit.get_work_done_display()}',
                names=[_horse(visit.horse)], amount=_cost(visit),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for visit in VetVisit.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            reason = (visit.reason or '').strip()
            events.append(Event(
                date=visit.date, kind='vet',
                what='Vet visit' + (f' · {reason[:50]}' if reason else ''),
                names=[_horse(visit.horse)], amount=_cost(visit),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for treatment in WormingTreatment.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=treatment.date, kind='worming',
                what='Wormed' + (f' · {treatment.product_name}' if treatment.product_name else ''),
                names=[_horse(treatment.horse)], amount=_cost(treatment),
                site=where.get(treatment.horse_id, ''), sort_ts=_ts(treatment.created_at),
            ))
        for count in WormEggCount.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=count.date, kind='egg_count',
                what=f'Egg count · {count.count} EPG',
                names=[_horse(count.horse)],
                site=where.get(count.horse_id, ''), sort_ts=_ts(count.created_at),
            ))

    if can('charges'):
        from billing.models import ExtraCharge
        # Charges created by a health record are part of that record's row,
        # so only stand-alone charges are listed here.
        standalone = ExtraCharge.objects.filter(
            vaccination__isnull=True, farrier_visit__isnull=True,
            worming_treatment__isnull=True, vet_visit__isnull=True,
        )
        for charge in standalone.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=charge.date, kind='charge',
                what=f'{charge.get_charge_type_display()} charge',
                names=[_horse(charge.horse)], amount=charge.amount,
                url=reverse('charge_update', args=[charge.pk]),
                site=where.get(charge.horse_id, ''), sort_ts=_ts(charge.created_at),
            ))

    if can('invoices'):
        from invoicing.models import Invoice, Payment
        for payment in Payment.objects.select_related('invoice', 'invoice__owner').order_by('-date', '-pk')[:limit]:
            owner = payment.invoice.owner
            events.append(Event(
                date=payment.date, kind='payment',
                what=f'Payment received · {payment.invoice.invoice_number}',
                names=[Name(owner.name, reverse('owner_detail', args=[owner.pk]))],
                url=reverse('invoice_detail', args=[payment.invoice_id]),
                amount=payment.amount, sort_ts=_ts(payment.created_at),
            ))
        for invoice in Invoice.objects.filter(sent_at__isnull=False).select_related('owner').order_by('-sent_at')[:limit]:
            events.append(Event(
                date=timezone.localtime(invoice.sent_at).date(), kind='invoice',
                what=f'{invoice.invoice_number} sent',
                names=[Name(invoice.owner.name, reverse('owner_detail', args=[invoice.owner_id]))],
                url=reverse('invoice_detail', args=[invoice.pk]),
                amount=invoice.total, sort_ts=_ts(invoice.sent_at),
            ))

    if can('feed'):
        from billing.models import FeedOut
        for feed in FeedOut.objects.select_related('location').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=feed.date, kind='feed',
                what=f'{feed.get_feed_type_display()} fed out',
                names=[Name(feed.location.name, reverse('location_detail', args=[feed.location_id]) + '?tab=feed')],
                site=feed.location.site, sort_ts=_ts(feed.created_at),
            ))

    if site:
        events = [event for event in events if not event.site or event.site == site]
    events = [event for event in events if event.date <= today]
    events = _fold_matching_charges(events)
    events = _group(events)
    events.sort(key=lambda event: event.sort_key, reverse=True)
    events = events[:max_rows]

    days = []
    for event in events:
        if not days or days[-1]['date'] != event.date:
            label, with_date = _day_label(event.date, today)
            days.append({'date': event.date, 'label': label, 'with_date': with_date, 'events': []})
        days[-1]['events'].append(event)
    return days


# A stand-alone charge of this type, on the same horse and day as a record
# of this kind, is that record's bill entered by hand.
CHARGE_KIND_FOR_RECORD = {'farrier': 'farrier', 'vet': 'vet', 'vaccination': 'vaccination'}


def _fold_matching_charges(events):
    """Drop charges that duplicate a health record on the same horse and
    day, carrying their amount onto the record when it has none."""
    records = {}
    for event in events:
        if event.kind in CHARGE_KIND_FOR_RECORD and event.names:
            records[(event.names[0].url, event.date, event.kind)] = event
    if not records:
        return events
    kept = []
    for event in events:
        if event.kind == 'charge' and event.names:
            record_kind = _record_kind_for_charge(event.what)
            record = records.get((event.names[0].url, event.date, record_kind)) if record_kind else None
            if record is not None:
                if record.amount is None:
                    record.amount = event.amount
                continue
        kept.append(event)
    return kept


def _record_kind_for_charge(what):
    label = what.lower()
    if label.startswith('farrier'):
        return 'farrier'
    if label.startswith('vet'):
        return 'vet'
    if label.startswith('vaccination'):
        return 'vaccination'
    return None


def _group(events):
    """Merge same-day, same-description events of a groupable kind into one
    row that names every subject and sums any amounts."""
    merged = {}
    result = []
    for event in events:
        if event.kind not in GROUPABLE or not event.names:
            result.append(event)
            continue
        key = (event.date, event.kind, event.what)
        head = merged.get(key)
        if head is None:
            merged[key] = event
            result.append(event)
            continue
        if event.names[0].url not in {n.url for n in head.names}:
            head.names.append(event.names[0])
        head.count += 1
        if event.amount is not None:
            head.amount = (head.amount or Decimal('0')) + event.amount
        head.sort_ts = max(head.sort_ts, event.sort_ts)
        if head.url and head.url != event.url:
            head.url = ''
    for event in result:
        if event.count > 1:
            event.names.sort(key=lambda n: n.name.lower())
    return result


def _day_label(day, today):
    """``(label, with_date)``: relative wording for the last week, the date
    itself beyond that. ``with_date`` says whether the template should add
    the date after the label (only for bare weekday names)."""
    delta = (today - day).days
    if delta == 0:
        return 'Today', False
    if delta == 1:
        return 'Yesterday', False
    if delta < 7:
        return day.strftime('%A'), True
    return day.strftime('%a %-d %b'), False
