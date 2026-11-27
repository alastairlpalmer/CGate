"""Livery that an issued invoice missed.

An invoice bills the stays that exist when it is made. A stay recorded
later (a horse that arrived in June, entered in July), or a back-dated
move or rate change, changes the livery for days that are already on an
invoice. The monthly run skips those days, because the invoice period
covers them, and a manual invoice for them is refused as an overlap. So
the money was lost (pre-launch review, blocker 4).

This module finds each live invoice whose livery for a horse is now less
than the stays say, and makes the difference into a charge
("livery adjustment") that goes on the owner's next invoice.

Only stays changed after the invoice was made are checked
(``Placement.updated_at``), so the check is cheap enough to run on a
page. Livery billed too high (a stay shortened after the invoice) is not
offered here: the yard makes a credit note for that.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from core.models import Placement

from .models import Invoice, InvoiceLineItem

# How far back the check looks. Older invoices are settled business.
LOOKBACK_DAYS = 400


@dataclass
class LiveryShortfall:
    invoice: Invoice
    horse: object
    billed: Decimal     # livery on the invoice, plus adjustments made
    due: Decimal        # livery the stays give now

    @property
    def amount(self):
        return self.due - self.billed

    @property
    def key(self):
        return f'{self.invoice.pk}-{self.horse.pk}'


def _candidate_invoices(horse=None, owner=None, since=None):
    """Live invoices that overlap a stay changed after the invoice."""
    since = since or timezone.localdate() - timedelta(days=LOOKBACK_DAYS)
    invoices = (
        Invoice.objects.exclude(status=Invoice.Status.CANCELLED)
        .filter(period_end__gte=since)
        .select_related('owner', 'horse')
    )
    if owner is not None:
        invoices = invoices.filter(owner=owner)
    changed = Placement.objects.filter(
        updated_at__gt=models.OuterRef('created_at'),
        start_date__lte=models.OuterRef('period_end'),
    ).filter(
        models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.OuterRef('period_start')),
    )
    if horse is not None:
        changed = changed.filter(horse=horse)
    return invoices.filter(models.Exists(changed)).order_by('period_start', 'pk')


def shortfalls(horse=None, owner=None, since=None):
    """Each (invoice, horse) whose livery is now less than its stays give.

    ``horse`` / ``owner`` narrow the check. Returns a list of
    ``LiveryShortfall``, oldest invoice first.
    """
    from billing.models import ExtraCharge

    from .services import InvoiceService

    found = []
    for invoice in _candidate_invoices(horse=horse, owner=owner, since=since):
        due = {}
        for charge in InvoiceService.calculate_livery_charges(
            invoice.owner, invoice.period_start, invoice.period_end,
        ):
            charge_horse = charge['horse']
            if invoice.horse_id and charge_horse.pk != invoice.horse_id:
                continue
            if horse is not None and charge_horse.pk != horse.pk:
                continue
            entry = due.setdefault(charge_horse.pk, [charge_horse, Decimal('0.00')])
            entry[1] += charge['amount']
        if not due:
            continue
        billed = dict(
            InvoiceLineItem.objects.filter(
                invoice=invoice, line_type=InvoiceLineItem.LineType.LIVERY,
                horse_id__in=list(due),
            ).values('horse_id').annotate(total=models.Sum('line_total'))
            .values_list('horse_id', 'total')
        )
        adjusted = dict(
            ExtraCharge.objects.filter(adjusts_invoice=invoice, horse_id__in=list(due))
            .values('horse_id').annotate(total=models.Sum('amount'))
            .values_list('horse_id', 'total')
        )
        for horse_pk, (charge_horse, amount) in due.items():
            already = (billed.get(horse_pk) or Decimal('0.00')) + (adjusted.get(horse_pk) or Decimal('0.00'))
            if amount - already >= Decimal('0.01'):
                found.append(LiveryShortfall(invoice, charge_horse, already, amount))
    return found


def find(invoice_pk, horse_pk):
    """The shortfall for one invoice and horse, or None (already settled)."""
    invoice = Invoice.objects.filter(pk=invoice_pk).select_related('owner').first()
    if invoice is None:
        return None
    from core.models import Horse

    horse = Horse.objects.filter(pk=horse_pk).first()
    if horse is None:
        return None
    for item in shortfalls(horse=horse, owner=invoice.owner):
        if item.invoice.pk == invoice.pk:
            return item
    return None


def add_charge(invoice_pk, horse_pk):
    """Make the shortfall into a charge for the owner's next invoice.

    Locks the invoice, so two clicks make one charge. Returns the charge,
    or None when there is nothing left to add.
    """
    from billing.models import ExtraCharge
    from core.templatetags.ui_extras import gbp

    with transaction.atomic():
        Invoice.objects.select_for_update().filter(pk=invoice_pk).first()
        item = find(invoice_pk, horse_pk)
        if item is None:
            return None
        invoice = item.invoice
        return ExtraCharge.objects.create(
            horse=item.horse,
            owner=invoice.owner,
            charge_type=ExtraCharge.ChargeType.OTHER,
            date=timezone.localdate(),
            description=(
                f'Livery adjustment: {invoice.period_start.day} {invoice.period_start:%b} to '
                f'{invoice.period_end.day} {invoice.period_end:%b %Y}, not on invoice {invoice.invoice_number}'
            )[:500],
            amount=item.amount,
            split_by_ownership=False,
            adjusts_invoice=invoice,
            notes=(
                f'Livery for these days is now {gbp(item.due)}. '
                f'Invoice {invoice.invoice_number} billed {gbp(item.billed)}.'
            ),
        )
