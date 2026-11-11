"""The yard's side of the farrier page (docs/FARRIER_PAGE_PLAN.md, section 6).

The yard checks each finished visit, sees who each line bills, and pays
the farrier once a month from a statement. These are staff functions:
the views that call them are gated by the staff features (charges,
costs, settings), never by ``farrier_portal``.
"""

from __future__ import annotations

import calendar
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import ProviderVisit


# --- One visit, line by line ------------------------------------------------

@dataclass
class YardLine:
    """A line of a farrier visit as the yard sees it."""

    record: object
    status: str            # 'open' | 'billed' | 'invoiced' | 'no_owner' | 'free'
    label: str             # one short sentence for the status column
    payers: list = field(default_factory=list)  # [(owner, share %)]
    invoices: list = field(default_factory=list)  # invoice numbers


def yard_lines(visit):
    """Each line of ``visit`` with who it bills and whether it is invoiced."""
    from invoicing.models import Invoice

    lines = []
    records = (
        visit.farrier_visits
        .select_related('horse', 'extra_charge__owner')
        .order_by('horse__name')
    )
    for record in records:
        charge = record.extra_charge
        if visit.is_open:
            lines.append(YardLine(record, 'open', 'Not sent yet: the visit is still open.'))
            continue
        if charge is None:
            if record.cost > 0:
                lines.append(YardLine(
                    record, 'no_owner',
                    'No owner: not billed. Add an owner, then save the farrier record.',
                ))
            else:
                lines.append(YardLine(record, 'free', 'No charge.'))
            continue
        owners = record.horse.current_owners if charge.split_by_ownership else []
        payers = list(owners) if len(owners) > 1 else [(charge.owner, Decimal('100'))]
        invoices = list(
            charge.invoice_items.exclude(invoice__status=Invoice.Status.CANCELLED)
            .values_list('invoice__invoice_number', flat=True).distinct()
        )
        names = ', '.join(
            f'{owner.name} ({Decimal(share).normalize():f}%)' if len(payers) > 1 else owner.name
            for owner, share in payers
        )
        if invoices:
            lines.append(YardLine(record, 'invoiced', f'On invoice {", ".join(invoices)}', payers, invoices))
        else:
            lines.append(YardLine(record, 'billed', f'On the next invoice: {names}', payers))
    return lines


def mark_checked(visit, user):
    """The yard has checked a finished visit. The farrier can no longer change it."""
    if visit.is_open:
        raise ValueError("An open visit cannot be checked yet.")
    if visit.checked_at is None:
        visit.checked_at = timezone.now()
        visit.checked_by = user
        visit.save(update_fields=['checked_at', 'checked_by', 'updated_at'])
    return visit


# --- The monthly statement --------------------------------------------------

@dataclass
class Statement:
    provider: object
    year: int
    month: int
    visits: list
    total: Decimal
    paid_total: Decimal
    unpaid_total: Decimal
    unpaid_visits: list
    open_visits: list

    @property
    def start(self):
        return date(self.year, self.month, 1)

    @property
    def end(self):
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def label(self):
        return self.start.strftime('%B %Y')


def statement(provider, year, month):
    """One provider's finished visits in one month, with what is paid."""
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    in_month = ProviderVisit.objects.filter(provider=provider, date__range=(start, end))
    visits = list(in_month.filter(status=ProviderVisit.Status.FINISHED).order_by('date', 'started_at'))
    for visit in visits:
        visit.line_total = visit.total
        visit.line_count = visit.farrier_visits.count()
    total = sum((v.line_total for v in visits), Decimal('0.00'))
    unpaid = [v for v in visits if not v.is_paid]
    unpaid_total = sum((v.line_total for v in unpaid), Decimal('0.00'))
    return Statement(
        provider=provider, year=year, month=month, visits=visits, total=total,
        paid_total=total - unpaid_total, unpaid_total=unpaid_total, unpaid_visits=unpaid,
        open_visits=list(in_month.filter(status=ProviderVisit.Status.OPEN)),
    )


class NothingToPay(ValueError):
    """No unpaid finished visits in the month."""


def record_payment(provider, year, month, *, paid_on=None):
    """Pay every unpaid finished visit in the month with one yard cost.

    Makes one ``YardCost`` (Professional Services, the provider as
    supplier, no VAT: docs/FARRIER_PAGE_PLAN.md, section 12) and marks
    each visit paid with a link to it. Runs once: the visits are locked,
    and a second payment finds nothing unpaid.
    """
    from billing.models import YardCost

    paid_on = paid_on or timezone.localdate()
    with transaction.atomic():
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        unpaid = list(
            ProviderVisit.objects.select_for_update()
            .filter(
                provider=provider, status=ProviderVisit.Status.FINISHED,
                date__range=(start, end), paid_on__isnull=True,
            )
            .order_by('date')
        )
        if not unpaid:
            raise NothingToPay("Nothing to pay for this month.")
        total = sum((v.total for v in unpaid), Decimal('0.00'))
        cost = YardCost.objects.create(
            category=YardCost.CostCategory.PROFESSIONAL,
            date=paid_on,
            supplier=provider.name,
            description=(
                f"{provider.name}: farrier visits, {start.strftime('%B %Y')} "
                f"({len(unpaid)} visit{'s' if len(unpaid) != 1 else ''})"
            ),
            amount=total,
            vat_amount=Decimal('0.00'),
        )
        ProviderVisit.objects.filter(pk__in=[v.pk for v in unpaid]).update(
            paid_on=paid_on, yard_cost=cost, updated_at=timezone.now(),
        )
    return cost


def months_with_visits(provider):
    """(year, month) pairs that have a visit for this provider, newest first."""
    dates = (
        ProviderVisit.objects.filter(provider=provider)
        .dates('date', 'month', order='DESC')
    )
    return [(d.year, d.month) for d in dates]


def visits_needing_attention():
    """Finished visits not yet checked, for the staff list's filter."""
    return ProviderVisit.objects.filter(
        Q(status=ProviderVisit.Status.FINISHED) & Q(checked_at__isnull=True)
    )


# --- The QR code for the yard door --------------------------------------------

def qr_svg(url):
    """``url`` as an inline SVG QR code (the library two-step sign-in uses)."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode('utf-8')


# --- The yard's list for a farrier visit (plan section 14) ----------------------

@dataclass
class JobRow:
    job: object
    line: object = None   # the farrier's record for this horse on the date

    @property
    def status(self):
        if self.line is not None:
            return 'done'
        return 'missed' if self.job.date < timezone.localdate() else 'to_do'


def job_rows(provider, day):
    """The list for ``provider`` on ``day``, each item with the farrier's
    record for that horse on that date, if any."""
    from .models import FarrierJob, FarrierVisit

    jobs = list(FarrierJob.objects.filter(provider=provider, date=day).select_related('horse'))
    lines = {}
    if jobs:
        records = (
            FarrierVisit.objects.filter(date=day, horse_id__in=[j.horse_id for j in jobs])
            .filter(Q(provider_visit__provider=provider) | Q(service_provider=provider))
            .order_by('pk')
        )
        lines = {record.horse_id: record for record in records}
    return [JobRow(job, lines.get(job.horse_id)) for job in jobs]


class JobError(ValueError):
    """A list item the yard cannot save; the message says why."""


def add_job(provider, day, horse, *, work_needed='', note='', user=None):
    """Put ``horse`` on the list for ``day``, or change its item if it is there."""
    from .models import FarrierJob, FarrierVisit

    if not horse.is_active:
        raise JobError(f"{horse.name} is not at the yard.")
    if work_needed and work_needed not in FarrierVisit.WorkType.values:
        raise JobError("Choose the work from the list.")
    job, _ = FarrierJob.objects.update_or_create(
        provider=provider, date=day, horse=horse,
        defaults={'work_needed': work_needed, 'note': (note or '').strip()[:200], 'created_by': user},
    )
    return job


def upcoming_list_dates(provider, today=None):
    """Dates from today on that have a list, with the number of horses."""
    from django.db.models import Count

    from .models import FarrierJob

    today = today or timezone.localdate()
    return list(
        FarrierJob.objects.filter(provider=provider, date__gte=today)
        .values('date').annotate(horses=Count('id')).order_by('date')[:10]
    )
