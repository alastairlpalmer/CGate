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


def _placement_events(limit, site=''):
    """Arrivals, moves and departures from the placement log.

    A move closes one placement and opens another the next day; when both
    halves are in the window they merge into one "moved" event.
    """
    from ..models import Placement

    placements = Placement.objects.all()
    if site:
        placements = placements.filter(location__site=site)
    starts = list(placements.select_related('horse', 'location', 'rate_type').order_by(
        '-start_date', '-pk',
    )[:limit])
    ends = list(placements.filter(end_date__isnull=False).select_related(
        'horse', 'location', 'rate_type',
    ).order_by('-end_date', '-pk')[:limit])

    starts_by_horse_day = {(p.horse_id, p.start_date): p for p in starts}
    merged_starts = set()
    events = []
    for placement in ends:
        follow_on = starts_by_horse_day.get((placement.horse_id, placement.end_date + timedelta(days=1)))
        if follow_on is not None and follow_on.location_id == placement.location_id:
            # Not a move at all. A change of rate or owner is saved as a
            # new placement in the same place; "Moved from Top Barn to
            # Top Barn" is noise. Say what changed, if it was the rate,
            # and nothing if it was only the owner.
            merged_starts.add(follow_on.pk)
            if follow_on.rate_type_id != placement.rate_type_id:
                events.append(Event(
                    date=follow_on.start_date, kind='move',
                    what=f'Rate changed to {follow_on.rate_type.name}',
                    names=[_horse(placement.horse)],
                    site=follow_on.location.site, sort_ts=_ts(follow_on.created_at),
                ))
        elif follow_on is not None:
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


def _stable_events(limit, site=''):
    """Horses going into stables (core.models.StableStay).

    One wording for every horse put into a barn's stables that day, so a
    morning spent stabling ten arrivals folds into one row the way ten
    arrivals do. A horse that came from another stable says which it went
    to instead: that one is news about the horse, not about the barn.
    """
    from django.conf import settings

    if not settings.BARNS_ENABLED:
        return []
    from ..models import StableStay

    stays = StableStay.objects.select_related('horse', 'stable', 'stable__location')
    if site:
        stays = stays.filter(stable__location__site=site)
    stays = list(stays.order_by('-start_date', '-pk')[:limit])
    # Did the horse leave another stable the day before? Then it moved.
    moved = set(
        StableStay.objects.filter(
            horse_id__in=[stay.horse_id for stay in stays], end_date__isnull=False,
            closed_with_placement=False,
        ).values_list('horse_id', 'end_date')
    )
    events = []
    for stay in stays:
        barn = stay.stable.location
        if (stay.horse_id, stay.start_date - timedelta(days=1)) in moved:
            what = f'Moved to stable {stay.stable.label} in {barn.name}'
        else:
            what = f'Given a stable in {barn.name}'
        events.append(Event(
            date=stay.start_date, kind='move', what=what,
            names=[_horse(stay.horse)],
            url=reverse('location_detail', args=[barn.pk]) + f'?stable={stay.stable_id}',
            site=barn.site, sort_ts=_ts(stay.created_at),
        ))
    return events


def recent(user, *, site='', today=None, limit=DEFAULT_LIMIT, max_rows=MAX_ROWS):
    """The newest events the user may see, grouped by day."""
    today = today or timezone.localdate()
    can = lambda feature: has_feature_access(user, feature, LEVEL_VIEW)  # noqa: E731
    # A record's cost is money, so only users who can see charges see it.
    cost = _cost if can('charges') else (lambda record: None)  # noqa: E731

    where = {}
    if site:
        from ..models import Placement
        where = {
            horse_id: loc_site
            for horse_id, loc_site in Placement.objects.filter(
                end_date__isnull=True,
            ).values_list('horse_id', 'location__site')
        }

    # Filter each source by site before taking its newest ``limit`` rows:
    # filtering after, a busy site filled every source and a quiet site's
    # events never reached the page. Records of horses standing on another
    # site are dropped here; horses with no open stay (no site) stay, as
    # the final filter keeps events with no site.
    elsewhere = {horse_id for horse_id, loc_site in where.items() if loc_site != site}

    def on_site(queryset):
        return queryset.exclude(horse_id__in=elsewhere) if site else queryset

    events = []

    if can('horses'):
        events.extend(_placement_events(limit, site))
        events.extend(_stable_events(limit, site))

        from ..models import Document
        for doc in on_site(Document.objects).select_related('horse', 'owner').order_by('-uploaded_at')[:limit]:
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
        for vax in on_site(Vaccination.objects).select_related('horse', 'vaccination_type').order_by(
            '-date_given', '-pk',
        )[:limit]:
            events.append(Event(
                date=vax.date_given, kind='vaccination',
                what=f'Vaccinated · {vax.vaccination_type.name}',
                names=[_horse(vax.horse)], amount=cost(vax),
                site=where.get(vax.horse_id, ''), sort_ts=_ts(vax.created_at),
            ))
        for visit in on_site(FarrierVisit.objects).select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=visit.date, kind='farrier',
                what=f'Farrier · {visit.get_work_done_display()}',
                names=[_horse(visit.horse)], amount=cost(visit),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for visit in on_site(VetVisit.objects).select_related('horse').order_by('-date', '-pk')[:limit]:
            reason = (visit.reason or '').strip()
            events.append(Event(
                date=visit.date, kind='vet',
                what='Vet visit' + (f' · {reason[:50]}' if reason else ''),
                names=[_horse(visit.horse)], amount=cost(visit),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for treatment in on_site(WormingTreatment.objects).select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=treatment.date, kind='worming',
                what='Wormed' + (f' · {treatment.product_name}' if treatment.product_name else ''),
                names=[_horse(treatment.horse)], amount=cost(treatment),
                site=where.get(treatment.horse_id, ''), sort_ts=_ts(treatment.created_at),
            ))
        for count in on_site(WormEggCount.objects).select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=count.date, kind='egg_count',
                what=f'Egg count · {count.count} EPG',
                names=[_horse(count.horse)], amount=cost(count),
                site=where.get(count.horse_id, ''), sort_ts=_ts(count.created_at),
            ))

    if can('charges'):
        from billing.models import ExtraCharge
        # Charges created by a health record are part of that record's row,
        # so only stand-alone charges are listed here.
        standalone = ExtraCharge.objects.filter(
            vaccination__isnull=True, farrier_visit__isnull=True,
            worming_treatment__isnull=True, vet_visit__isnull=True,
            worm_egg_count__isnull=True,
        )
        for charge in on_site(standalone).select_related('horse').order_by('-date', '-pk')[:limit]:
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
            # A credit note is a Payment row but not money received.
            received = 'Credit note' if payment.method == Payment.Method.CREDIT_NOTE else 'Payment received'
            events.append(Event(
                date=payment.date, kind='payment',
                what=f'{received} · {payment.invoice.invoice_number}',
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
        for feed in (FeedOut.objects.filter(location__site=site) if site else FeedOut.objects).select_related('location').order_by('-date', '-pk')[:limit]:
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
