"""What needs doing on the yard, as one list.

This is the single source of truth for "needs attention". The home dashboard
renders it as the Needs action inbox, the Next 14 days strip and the
headline count; the Health overview renders the health kinds as its Action
Required and Coming Up lists. Because both pages call the same collectors,
they can never disagree about what is overdue.

Every collector is gated on the requesting user's feature access, so a role
with Invoices hidden never sees (or queries) an invoice. Items know which
location and site their horse stands on, so the dashboard's site switch can
narrow the list.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (
    DecimalField, Exists, ExpressionWrapper, F, OuterRef, Q, Sum, Value,
)
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from ..permissions import LEVEL_FULL, LEVEL_VIEW, has_feature_access

HORIZON_DAYS = 14

# Ordered most urgent first. ``info`` is for rows that need a decision but
# have no date (a high egg count, low feed stock).
SEVERITY_ORDER = {'overdue': 0, 'due': 1, 'info': 2}

# Kinds that belong in the inbox whatever their date: they have no natural
# "day" and would otherwise never surface.
INBOX_ALWAYS = frozenset({'egg_count', 'document', 'departure', 'feed'})

# Kinds the health bulk form can record for several horses at once.
BULK_ACTION_TYPES = {'vaccination': 'vaccination', 'farrier': 'farrier'}

KIND_LABELS = {
    'vaccination': 'Vaccination',
    'farrier': 'Farrier',
    'vet': 'Vet follow-up',
    'egg_count': 'Egg count',
    'ehv': 'EHV vaccination',
    'foal': 'Foal due',
    'document': 'Document',
    'departure': 'Departure to confirm',
    'departure_expected': 'Expected departure',
    'invoice': 'Invoice',
    'feed': 'Feed stock',
}

CATEGORY_OF_KIND = {
    'vaccination': 'health', 'farrier': 'health', 'vet': 'health',
    'egg_count': 'health', 'ehv': 'health',
    'foal': 'yard', 'departure': 'yard', 'departure_expected': 'yard',
    'feed': 'yard',
    'document': 'documents',
    'invoice': 'money',
}


@dataclass
class Action:
    """One button on an item row.

    ``popup_title`` set: the link opens in the pop-up sheet (hx-get).
    ``method == 'post'``: rendered as a one-click form (confirm, mark paid).
    Otherwise a plain link.
    """

    label: str
    url: str
    popup_title: str = ''
    method: str = 'get'
    style: str = 'ghost'  # 'primary' | 'ghost'


@dataclass
class AttentionItem:
    kind: str
    severity: str
    title: str
    detail: str
    url: str
    due_date: date | None = None
    delta: int | None = None  # days from today; negative = overdue
    horse: object = None
    horse_id: int | None = None
    location: str = ''
    site: str = ''
    amount: Decimal | None = None
    actions: list = field(default_factory=list)
    key: str = ''

    @property
    def category(self):
        return CATEGORY_OF_KIND.get(self.kind, 'yard')

    @property
    def label(self):
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def is_overdue(self):
        return self.severity == 'overdue'

    @property
    def sort_key(self):
        return (SEVERITY_ORDER[self.severity], self.due_date or date.max, self.title.lower())


@dataclass
class Row:
    """A rendered inbox row: one horse, one shared visit, or one loose item."""

    kind: str  # 'horse' | 'visit' | 'single'
    severity: str
    title: str
    subtitle: str
    items: list
    url: str = ''
    horse: object = None
    horses: list = field(default_factory=list)
    action: Action | None = None
    key: str = ''

    @property
    def due_date(self):
        dates = [i.due_date for i in self.items if i.due_date]
        return min(dates) if dates else None

    @property
    def sort_key(self):
        return (SEVERITY_ORDER[self.severity], self.due_date or date.max, self.title.lower())


class _Context:
    """Per-call state shared by the collectors: dates, access, placements."""

    def __init__(self, user, today, horizon_days):
        self.user = user
        self.today = today
        self.horizon = today + timedelta(days=horizon_days)
        self._placements = None

    def can(self, feature, level=LEVEL_VIEW):
        return has_feature_access(self.user, feature, level)

    @property
    def placements(self):
        """``{horse_id: (location name, site)}`` for every open placement."""
        if self._placements is None:
            from ..models import Placement
            self._placements = {
                horse_id: (name, site)
                for horse_id, name, site in Placement.objects.filter(
                    end_date__isnull=True, horse__is_active=True,
                ).values_list('horse_id', 'location__name', 'location__site')
            }
        return self._placements

    def where(self, horse_id):
        return self.placements.get(horse_id, ('', ''))

    def severity_for(self, due):
        if due is None:
            return 'info'
        return 'overdue' if due < self.today else 'due'

    def delta(self, due):
        return (due - self.today).days if due else None

    def horse_item(self, kind, horse, *, due, detail, actions=(), amount=None, key=''):
        location, site = self.where(horse.pk)
        return AttentionItem(
            kind=kind,
            severity=self.severity_for(due),
            title=horse.name,
            detail=detail,
            url=reverse('horse_detail', args=[horse.pk]),
            due_date=due,
            delta=self.delta(due),
            horse=horse,
            horse_id=horse.pk,
            location=location,
            site=site,
            amount=amount,
            actions=list(actions),
            key=key or f'{kind}-{horse.pk}',
        )

    def record_action(self, url_name, horse, label, popup_title, primary=False):
        """The pop-up quick action shared by the health kinds."""
        return Action(
            label=label,
            url=reverse(url_name) + f'?horse={horse.pk}',
            popup_title=f'{popup_title} for {horse.name}',
            style='primary' if primary else 'ghost',
        )


# ── Collectors ───────────────────────────────────────────────────────────────

def _vaccinations(ctx):
    from health.models import Vaccination, current_vaccinations

    qs = current_vaccinations(Vaccination.objects.filter(
        horse__is_active=True,
        next_due_date__isnull=False,
        next_due_date__lte=ctx.horizon,
    )).select_related('horse', 'vaccination_type').order_by('next_due_date')
    full = ctx.can('health', LEVEL_FULL)
    items = []
    for vax in qs:
        due = vax.next_due_date
        actions = []
        if full:
            actions.append(ctx.record_action(
                'vaccination_create', vax.horse, 'Record', 'Record vaccination',
                primary=due < ctx.today,
            ))
        items.append(ctx.horse_item(
            'vaccination', vax.horse, due=due,
            detail=f'Vaccination · {vax.vaccination_type.name}',
            actions=actions, key=f'vaccination-{vax.pk}',
        ))
    return items


def _farrier(ctx):
    from health.models import FarrierVisit, current_farrier_visits

    qs = current_farrier_visits(FarrierVisit.objects.filter(
        horse__is_active=True,
        next_due_date__isnull=False,
        next_due_date__lte=ctx.horizon,
    )).select_related('horse').order_by('next_due_date')
    full = ctx.can('health', LEVEL_FULL)
    items = []
    for visit in qs:
        due = visit.next_due_date
        actions = []
        if full:
            actions.append(ctx.record_action(
                'farrier_create', visit.horse, 'Record visit', 'Record farrier visit',
                primary=due < ctx.today,
            ))
        items.append(ctx.horse_item(
            'farrier', visit.horse, due=due,
            detail=f'Farrier · {visit.get_work_done_display()}',
            actions=actions, key=f'farrier-{visit.pk}',
        ))
    return items


def _vet_follow_ups(ctx):
    from health.models import VetVisit

    qs = VetVisit.objects.filter(
        horse__is_active=True,
        follow_up_date__isnull=False,
        follow_up_date__lte=ctx.horizon,
    ).select_related('horse').order_by('follow_up_date')
    full = ctx.can('health', LEVEL_FULL)
    items = []
    for visit in qs:
        due = visit.follow_up_date
        reason = (visit.reason or '').strip()
        actions = []
        if full:
            actions.append(ctx.record_action(
                'vet_visit_create', visit.horse, 'New visit', 'Record vet visit',
                primary=due < ctx.today,
            ))
        items.append(ctx.horse_item(
            'vet', visit.horse, due=due,
            detail='Vet follow-up' + (f' · {reason[:60]}' if reason else ''),
            actions=actions, key=f'vet-{visit.pk}',
        ))
    return items


def _egg_counts(ctx):
    """High counts (over 200 EPG) in the last 90 days, latest per horse,
    dropped once a worming treatment is recorded on or after the test."""
    from health.models import WormEggCount, WormingTreatment

    since = ctx.today - timedelta(days=90)
    counts = WormEggCount.objects.filter(
        horse__is_active=True, date__gte=since, count__gt=200,
    ).select_related('horse').order_by('-date', '-pk')
    if not counts:
        return []
    treated = defaultdict(list)
    for horse_id, treated_on in WormingTreatment.objects.filter(
        horse__is_active=True, date__gte=since,
    ).values_list('horse_id', 'date'):
        treated[horse_id].append(treated_on)

    full = ctx.can('health', LEVEL_FULL)
    seen = set()
    items = []
    for ec in counts:
        if ec.horse_id in seen:
            continue
        seen.add(ec.horse_id)
        if any(d >= ec.date for d in treated.get(ec.horse_id, ())):
            continue
        actions = []
        if full:
            actions.append(ctx.record_action(
                'worming_create', ec.horse, 'Record worming', 'Record worming',
                primary=True,
            ))
        items.append(ctx.horse_item(
            'egg_count', ec.horse, due=None,
            detail=f'Egg count {ec.count} EPG on {ec.date:%-d %b}',
            actions=actions, key=f'egg-{ec.pk}',
        ))
    return items


def _breeding(ctx):
    """EHV jabs inside their window and foals due, for mares in foal.

    The EHV window (14 days before to 7 days after months 5, 7 and 9 from
    covering) is the one the reminder email uses, so the page and the inbox
    agree with what the owner was told.
    """
    from health.models import BreedingRecord

    records = BreedingRecord.objects.filter(
        status=BreedingRecord.Status.CONFIRMED, mare__is_active=True,
    ).select_related('mare')
    health_full = ctx.can('health', LEVEL_FULL)
    include_ehv = ctx.can('health')
    items = []
    for record in records:
        mare = record.mare
        if include_ehv:
            for month, due in sorted(record.ehv_vaccination_dates.items()):
                if due - timedelta(days=14) <= ctx.today <= due + timedelta(days=7):
                    actions = []
                    if health_full:
                        actions.append(ctx.record_action(
                            'vaccination_create', mare, 'Record', 'Record EHV vaccination',
                            primary=due < ctx.today,
                        ))
                    items.append(ctx.horse_item(
                        'ehv', mare, due=due,
                        detail=f'EHV vaccination · month {month} of pregnancy',
                        actions=actions, key=f'ehv-{record.pk}-{month}',
                    ))
        due = record.date_foal_due
        if due and due <= ctx.horizon:
            overdue = due < ctx.today
            actions = []
            if ctx.can('breeding', LEVEL_FULL):
                actions.append(Action(
                    label='Update record' if overdue else 'Open record',
                    url=reverse('breeding_update', args=[record.pk]),
                    popup_title=f'Breeding record for {mare.name}',
                    style='primary' if overdue else 'ghost',
                ))
            items.append(ctx.horse_item(
                'foal', mare, due=due,
                detail=(
                    'Foal due date passed · record the foaling'
                    if overdue else f'Foal due · by {record.stallion_name}'
                    if record.stallion_name else 'Foal due'
                ),
                actions=actions, key=f'foal-{record.pk}',
            ))
    return items


def _documents(ctx):
    """Passports, insurance and other documents expired or expiring within
    30 days. The yard gets an email for these; the inbox is where they get
    dealt with."""
    from ..models import Document

    qs = Document.objects.filter(
        expiry_date__isnull=False,
        expiry_date__lte=ctx.today + timedelta(days=30),
    ).filter(
        Q(horse__isnull=True) | Q(horse__is_active=True),
    ).select_related('horse', 'owner').order_by('expiry_date')
    items = []
    for doc in qs:
        due = doc.expiry_date
        if doc.horse_id:
            location, site = ctx.where(doc.horse_id)
            subject = doc.horse.name
            url = reverse('horse_detail', args=[doc.horse_id])
        else:
            location, site = '', ''
            subject = doc.owner.name if doc.owner_id else ''
            url = reverse('owner_detail', args=[doc.owner_id]) if doc.owner_id else reverse('horse_list')
        state = 'expired' if due < ctx.today else 'expires'
        items.append(AttentionItem(
            kind='document',
            severity=ctx.severity_for(due),
            title=subject or doc.title,
            detail=f'{doc.get_doc_type_display()} {state} · {doc.title}',
            url=url,
            due_date=due,
            delta=ctx.delta(due),
            horse=doc.horse if doc.horse_id else None,
            horse_id=doc.horse_id,
            location=location,
            site=site,
            key=f'document-{doc.pk}',
        ))
    return items


def _departures_to_confirm(ctx):
    """Horses still flagged active whose last placement has ended.

    A horse with an open placement must never appear here: every move
    leaves a closed placement behind, and confirming those rows would
    depart horses that are still on the yard.
    """
    from ..models import Placement

    has_open = Placement.objects.filter(horse=OuterRef('horse'), end_date__isnull=True)
    closed = Placement.objects.filter(
        end_date__isnull=False,
        end_date__lte=ctx.today,
        horse__is_active=True,
    ).annotate(
        horse_is_placed=Exists(has_open),
    ).filter(
        horse_is_placed=False,
    ).select_related('horse', 'owner', 'location').order_by('-end_date', '-pk')

    latest = {}
    for placement in closed:
        latest.setdefault(placement.horse_id, placement)

    full = ctx.can('horses', LEVEL_FULL)
    items = []
    for placement in latest.values():
        horse = placement.horse
        actions = []
        if full:
            actions.append(Action(
                label='Confirm departed',
                url=reverse('confirm_departure', args=[horse.pk]),
                method='post', style='primary',
            ))
            actions.append(Action(
                label='Cancel departure',
                url=reverse('cancel_departure', args=[horse.pk]),
                method='post',
            ))
        owner = placement.owner.name if placement.owner_id else ''
        items.append(AttentionItem(
            kind='departure',
            severity='due',
            title=horse.name,
            detail=f'Left {placement.location.name} on {placement.end_date:%-d %b}'
                   + (f' · {owner}' if owner else ''),
            url=reverse('horse_detail', args=[horse.pk]),
            due_date=placement.end_date,
            delta=ctx.delta(placement.end_date),
            horse=horse,
            horse_id=horse.pk,
            location=placement.location.name,
            site=placement.location.site,
            actions=actions,
            key=f'departure-{horse.pk}',
        ))
    return items


def _expected_departures(ctx):
    from ..models import Placement

    qs = Placement.objects.filter(
        expected_departure__isnull=False,
        expected_departure__gte=ctx.today,
        expected_departure__lte=ctx.horizon,
        end_date__isnull=True,
        horse__is_active=True,
    ).select_related('horse', 'owner', 'location').order_by('expected_departure')
    items = []
    for placement in qs:
        owner = placement.owner.name if placement.owner_id else ''
        items.append(AttentionItem(
            kind='departure_expected',
            severity='due',
            title=placement.horse.name,
            detail=f'Expected to leave {placement.location.name}' + (f' · {owner}' if owner else ''),
            url=reverse('horse_detail', args=[placement.horse_id]),
            due_date=placement.expected_departure,
            delta=ctx.delta(placement.expected_departure),
            horse=placement.horse,
            horse_id=placement.horse_id,
            location=placement.location.name,
            site=placement.location.site,
            key=f'expected-{placement.pk}',
        ))
    return items


def _invoices(ctx):
    """Open invoices due within the horizon or already overdue, with the
    balance still owed (part-payments count)."""
    from invoicing.models import Invoice

    qs = Invoice.objects.filter(
        status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE],
        due_date__lte=ctx.horizon,
    ).select_related('owner').annotate(
        balance=ExpressionWrapper(
            F('total') - Coalesce(Sum('payments__amount'), Value(Decimal('0.00'))),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
    ).order_by('due_date')
    full = ctx.can('invoices', LEVEL_FULL)
    items = []
    for invoice in qs:
        if invoice.balance <= 0:
            continue
        due = invoice.due_date
        actions = []
        if full:
            actions.append(Action(
                label='Record payment',
                url=reverse('payment_create', args=[invoice.pk]),
                popup_title=f'Record payment for {invoice.invoice_number}',
                style='primary' if due < ctx.today else 'ghost',
            ))
            actions.append(Action(
                label='Mark paid',
                url=reverse('invoice_mark_paid', args=[invoice.pk]),
                method='post',
            ))
        partial = ' of £{:,.2f}'.format(invoice.total) if invoice.balance != invoice.total else ''
        items.append(AttentionItem(
            kind='invoice',
            severity=ctx.severity_for(due),
            title=invoice.invoice_number,
            detail=f'{invoice.owner.name} · £{invoice.balance:,.2f}{partial} outstanding',
            url=reverse('invoice_detail', args=[invoice.pk]),
            due_date=due,
            delta=ctx.delta(due),
            amount=invoice.balance,
            actions=actions,
            key=f'invoice-{invoice.pk}',
        ))
    return items


LOW_STOCK_THRESHOLD = 10


def _feed(ctx):
    """Feed stock that is low or gone, per site and feed type. Uses the same
    arithmetic and thresholds as the Feed Store page."""
    from billing.models import FeedOut, FeedStock, FeedType, FeedUnit

    stock_in = FeedStock.objects.values('site', 'feed_type', 'unit').annotate(total=Sum('quantity'))
    if not stock_in:
        return []
    stock_out = FeedOut.objects.filter(
        quantity_numeric__isnull=False, unit__gt='',
    ).values('location__site', 'feed_type', 'unit').annotate(total=Sum('quantity_numeric'))

    balances = {}
    for row in stock_in:
        key = (row['site'] or 'Unassigned', row['feed_type'], row['unit'])
        balances[key] = float(row['total'] or 0)
    for row in stock_out:
        key = (row['location__site'] or 'Unassigned', row['feed_type'], row['unit'])
        if key in balances:
            balances[key] -= float(row['total'] or 0)

    type_labels = dict(FeedType.choices)
    unit_labels = dict(FeedUnit.choices)
    full = ctx.can('feed', LEVEL_FULL)
    items = []
    for (site, feed_type, unit), balance in sorted(balances.items()):
        if balance >= LOW_STOCK_THRESHOLD:
            continue
        actions = []
        if full:
            actions.append(Action(
                label='Log delivery', url=reverse('feed_stock_create'),
                style='primary' if balance <= 0 else 'ghost',
            ))
        left = f'{balance:g} {unit_labels.get(unit, unit).lower()} left' if balance > 0 else 'Out of stock'
        items.append(AttentionItem(
            kind='feed',
            severity='overdue' if balance <= 0 else 'info',
            title=f'{type_labels.get(feed_type, feed_type)} · {site}',
            detail=f'Feed stock · {left}',
            url=reverse('feed_dashboard') + f'?site={site}&feed_type={feed_type}',
            site=site if site != 'Unassigned' else '',
            actions=actions,
            key=f'feed-{site}-{feed_type}-{unit}',
        ))
    return items


COLLECTORS = (
    ('vaccination', 'health', _vaccinations),
    ('farrier', 'health', _farrier),
    ('vet', 'health', _vet_follow_ups),
    ('egg_count', 'health', _egg_counts),
    ('breeding', 'breeding', _breeding),
    ('document', 'horses', _documents),
    ('departure', 'horses', _departures_to_confirm),
    ('departure_expected', 'horses', _expected_departures),
    ('invoice', 'invoices', _invoices),
    ('feed', 'feed', _feed),
)


# ── Public API ───────────────────────────────────────────────────────────────

def collect(user, *, site='', today=None, horizon_days=HORIZON_DAYS, kinds=None):
    """Every attention item the user may see, most urgent first.

    ``site`` keeps items standing on that site plus items with no site
    (an invoice belongs to an owner, not a location). ``kinds`` limits the
    collectors that run, for callers that only want the health kinds.
    """
    today = today or timezone.localdate()
    ctx = _Context(user, today, horizon_days)
    items = []
    for kind, feature, collector in COLLECTORS:
        if kinds is not None and kind not in kinds:
            continue
        if not ctx.can(feature):
            continue
        items.extend(collector(ctx))
    if site:
        items = [item for item in items if not item.site or item.site == site]
    items.sort(key=lambda item: item.sort_key)
    return items


def is_inbox(item, today):
    """Inbox rows are overdue, due today, or of a kind with no natural day."""
    return (
        item.severity == 'overdue'
        or item.kind in INBOX_ALWAYS
        or (item.due_date is not None and item.due_date == today)
    )


def split(items, today):
    """``(inbox, upcoming)``: what needs doing now, and what is dated in the
    horizon (today included, so the strip and the inbox agree on today)."""
    inbox = [item for item in items if is_inbox(item, today)]
    upcoming = [
        item for item in items
        if item.due_date is not None and item.due_date >= today
    ]
    return inbox, upcoming


def _bulk_action(kind, horses, user):
    """"Record for N" through the health bulk form, in the pop-up sheet."""
    if not has_feature_access(user, 'health', LEVEL_FULL):
        return None
    action_type = BULK_ACTION_TYPES.get(kind)
    if not action_type or len(horses) < 2:
        return None
    ids = '&'.join(f'horse_ids={h.pk}' for h in horses)
    label = 'farrier visit' if kind == 'farrier' else 'vaccination'
    return Action(
        label=f'Record for {len(horses)}',
        url=reverse('bulk_health_form') + f'?action_type={action_type}&{ids}',
        popup_title=f'Record {label} for {len(horses)} horses',
        style='primary',
    )


def rows(items, user):
    """Group inbox items into rows.

    1. Vaccinations or farrier visits due the same day for two or more
       horses become one *visit* row with a "Record for N" action, because
       that is one booking, not N.
    2. Everything else groups by horse, so a horse with two things due is
       one row with two actions.
    3. Items without a horse (invoices, feed, owner documents) stand alone.
    """
    used = set()
    result = []

    by_visit = defaultdict(list)
    for item in items:
        if item.kind in BULK_ACTION_TYPES and item.due_date and item.horse is not None:
            by_visit[(item.kind, item.due_date)].append(item)
    for (kind, due), group in by_visit.items():
        horses = []
        seen = set()
        for item in group:
            if item.horse_id not in seen:
                seen.add(item.horse_id)
                horses.append(item.horse)
        if len(horses) < 2:
            continue
        for item in group:
            used.add(item.key)
        severity = min(group, key=lambda i: SEVERITY_ORDER[i.severity]).severity
        details = sorted({item.detail for item in group})
        result.append(Row(
            kind='visit',
            severity=severity,
            title=', '.join(h.name for h in horses),
            subtitle=(details[0] if len(details) == 1 else f'{KIND_LABELS[kind]} · {len(horses)} horses'),
            items=group,
            url=reverse('health_dashboard') + ('?type=farrier' if kind == 'farrier' else '?type=vaccinations'),
            horses=horses,
            action=_bulk_action(kind, horses, user),
            key=f'visit-{kind}-{due:%Y%m%d}',
        ))

    by_horse = defaultdict(list)
    for item in items:
        if item.key in used:
            continue
        if item.horse_id is None:
            # The detail renders on the item line; no subtitle, or it reads twice.
            result.append(Row(
                kind='single', severity=item.severity, title=item.title,
                subtitle='', items=[item], url=item.url, key=item.key,
            ))
        else:
            by_horse[item.horse_id].append(item)
    for horse_id, group in by_horse.items():
        group.sort(key=lambda i: i.sort_key)
        first = group[0]
        where = ' · '.join(part for part in (first.location, first.site) if part)
        result.append(Row(
            kind='horse',
            severity=min(group, key=lambda i: SEVERITY_ORDER[i.severity]).severity,
            title=first.horse.name,
            subtitle=where,
            items=group,
            url=reverse('horse_detail', args=[horse_id]),
            horse=first.horse,
            key=f'horse-{horse_id}',
        ))

    result.sort(key=lambda row: row.sort_key)
    return result


def summary(inbox_rows, upcoming_items, today):
    """The headline numbers: rows that need doing, and chip counts.

    Chips filter rows, so they count rows: a horse with two health items is
    one row under Health, not two.
    """
    def rows_with(predicate):
        return sum(1 for row in inbox_rows if any(predicate(item) for item in row.items))

    later = [item for item in upcoming_items if item.due_date and item.due_date > today]
    return {
        'things': len(inbox_rows),
        'overdue': sum(1 for row in inbox_rows if row.severity == 'overdue'),
        'today': rows_with(lambda item: item.due_date == today),
        'upcoming': len(later),
        'money': rows_with(lambda item: item.category == 'money'),
        'health': rows_with(lambda item: item.category == 'health'),
        'yard': rows_with(lambda item: item.category in ('yard', 'documents')),
    }


def health_lists(user, *, today=None):
    """The Health overview's two lists, from the same collectors.

    Vaccinations and vet follow-ups look 30 days ahead, farrier 14, as that
    page always has. Returns ``(action_required, coming_up)`` in the dict
    shape its template expects.
    """
    today = today or timezone.localdate()
    items = collect(
        user, today=today, horizon_days=30, kinds={'vaccination', 'farrier', 'vet'},
    )
    action_labels = {'vaccination': 'Re-vaccinate', 'farrier': 'Book', 'vet': 'New Visit'}
    type_labels = {'vaccination': 'Vaccination', 'farrier': 'Farrier', 'vet': 'Vet Follow-up'}
    action_required, coming_up = [], []
    for item in items:
        if item.kind == 'farrier' and item.delta is not None and item.delta > 14:
            continue
        detail = item.detail.split(' · ', 1)[1] if ' · ' in item.detail else '-'
        entry = {
            'horse': item.horse,
            'type': type_labels[item.kind],
            'detail': detail,
            'due_date': item.due_date,
            'url': item.actions[0].url if item.actions else item.url,
            'action_label': action_labels[item.kind],
        }
        (action_required if item.severity == 'overdue' else coming_up).append(entry)
    action_required.sort(key=lambda e: e['due_date'])
    coming_up.sort(key=lambda e: e['due_date'])
    return action_required, coming_up
