"""What changed on the yard: one chronological log.

Each source is a small, ordered, capped query; the results merge by date
and the newest ``limit`` survive. That is what "recent" means. The old
widget took the three newest rows of each type, which surfaced a feed-out
from five months ago as recent activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from ..permissions import LEVEL_VIEW, has_feature_access

DEFAULT_LIMIT = 14

# Dot colour per kind lives in the template; this is the legend order.
KINDS = ('arrival', 'move', 'departure', 'vaccination', 'farrier', 'vet',
         'worming', 'egg_count', 'charge', 'payment', 'invoice', 'document',
         'feed')


@dataclass
class Event:
    date: date
    kind: str
    text: str
    url: str
    site: str = ''
    amount: object = None
    sort_ts: float = 0.0

    @property
    def sort_key(self):
        return (self.date, self.sort_ts)


def _ts(value):
    if isinstance(value, datetime):
        return value.timestamp()
    return 0.0


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
        horse = placement.horse
        url = reverse('horse_detail', args=[horse.pk])
        if follow_on is not None:
            merged_starts.add(follow_on.pk)
            events.append(Event(
                date=follow_on.start_date, kind='move',
                text=f'{horse.name} moved from {placement.location.name} to {follow_on.location.name}',
                url=url, site=follow_on.location.site, sort_ts=_ts(follow_on.created_at),
            ))
        else:
            events.append(Event(
                date=placement.end_date, kind='departure',
                text=f'{horse.name} left {placement.location.name}',
                url=url, site=placement.location.site, sort_ts=_ts(placement.updated_at),
            ))
    for placement in starts:
        if placement.pk in merged_starts:
            continue
        events.append(Event(
            date=placement.start_date, kind='arrival',
            text=f'{placement.horse.name} arrived at {placement.location.name}',
            url=reverse('horse_detail', args=[placement.horse_id]),
            site=placement.location.site, sort_ts=_ts(placement.created_at),
        ))
    return events


def recent(user, *, site='', today=None, limit=DEFAULT_LIMIT):
    """The newest ``limit`` events the user may see, grouped by day."""
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
            subject = doc.horse.name if doc.horse_id else (doc.owner.name if doc.owner_id else '')
            url = (reverse('horse_detail', args=[doc.horse_id]) if doc.horse_id
                   else reverse('owner_detail', args=[doc.owner_id]) if doc.owner_id
                   else reverse('horse_list'))
            events.append(Event(
                date=timezone.localtime(doc.uploaded_at).date(), kind='document',
                text=f'{doc.get_doc_type_display()} added for {subject}' if subject else f'{doc.title} added',
                url=url, site=where.get(doc.horse_id, ''), sort_ts=_ts(doc.uploaded_at),
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
                text=f'{vax.horse.name} vaccinated · {vax.vaccination_type.name}',
                url=reverse('horse_detail', args=[vax.horse_id]),
                site=where.get(vax.horse_id, ''), sort_ts=_ts(vax.created_at),
            ))
        for visit in FarrierVisit.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=visit.date, kind='farrier',
                text=f'{visit.horse.name} · farrier · {visit.get_work_done_display()}',
                url=reverse('horse_detail', args=[visit.horse_id]),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for visit in VetVisit.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            reason = (visit.reason or '').strip()
            events.append(Event(
                date=visit.date, kind='vet',
                text=f'{visit.horse.name} · vet' + (f' · {reason[:50]}' if reason else ''),
                url=reverse('horse_detail', args=[visit.horse_id]),
                site=where.get(visit.horse_id, ''), sort_ts=_ts(visit.created_at),
            ))
        for treatment in WormingTreatment.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=treatment.date, kind='worming',
                text=f'{treatment.horse.name} wormed' + (f' · {treatment.product_name}' if treatment.product_name else ''),
                url=reverse('horse_detail', args=[treatment.horse_id]),
                site=where.get(treatment.horse_id, ''), sort_ts=_ts(treatment.created_at),
            ))
        for count in WormEggCount.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=count.date, kind='egg_count',
                text=f'{count.horse.name} · egg count {count.count} EPG',
                url=reverse('horse_detail', args=[count.horse_id]),
                site=where.get(count.horse_id, ''), sort_ts=_ts(count.created_at),
            ))

    if can('charges'):
        from billing.models import ExtraCharge
        for charge in ExtraCharge.objects.select_related('horse').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=charge.date, kind='charge',
                text=f'{charge.horse.name} · {charge.get_charge_type_display()} charge',
                url=reverse('charge_update', args=[charge.pk]),
                site=where.get(charge.horse_id, ''), amount=charge.amount,
                sort_ts=_ts(charge.created_at),
            ))

    if can('invoices'):
        from invoicing.models import Invoice, Payment
        for payment in Payment.objects.select_related('invoice', 'invoice__owner').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=payment.date, kind='payment',
                text=f'Payment from {payment.invoice.owner.name} · {payment.invoice.invoice_number}',
                url=reverse('invoice_detail', args=[payment.invoice_id]),
                amount=payment.amount, sort_ts=_ts(payment.created_at),
            ))
        for invoice in Invoice.objects.filter(sent_at__isnull=False).select_related('owner').order_by('-sent_at')[:limit]:
            events.append(Event(
                date=timezone.localtime(invoice.sent_at).date(), kind='invoice',
                text=f'{invoice.invoice_number} sent to {invoice.owner.name}',
                url=reverse('invoice_detail', args=[invoice.pk]),
                amount=invoice.total, sort_ts=_ts(invoice.sent_at),
            ))

    if can('feed'):
        from billing.models import FeedOut
        for feed in FeedOut.objects.select_related('location').order_by('-date', '-pk')[:limit]:
            events.append(Event(
                date=feed.date, kind='feed',
                text=f'{feed.get_feed_type_display()} to {feed.location.name}',
                url=reverse('location_detail', args=[feed.location_id]) + '?tab=feed',
                site=feed.location.site, sort_ts=_ts(feed.created_at),
            ))

    if site:
        events = [event for event in events if not event.site or event.site == site]
    events = [event for event in events if event.date <= today]
    events.sort(key=lambda event: event.sort_key, reverse=True)
    events = events[:limit]

    days = []
    for event in events:
        if not days or days[-1]['date'] != event.date:
            label, with_date = _day_label(event.date, today)
            days.append({'date': event.date, 'label': label, 'with_date': with_date, 'events': []})
        days[-1]['events'].append(event)
    return days


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
