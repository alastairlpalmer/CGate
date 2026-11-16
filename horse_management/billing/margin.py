"""The yard's margin on charges it passes on to owners.

A vet, a farrier or another supplier bills the yard. The yard bills each
owner, and normally adds a margin; sometimes it adds none. This module
puts a margin on many unbilled charges in one step, for the Add margin
page (billing.views.charge_margin).

Three rules:

* **From the cost, every time.** A charge remembers what the yard paid
  (``ExtraCharge.cost_amount``, set here on the first use). The new
  amount is always cost + margin. So 10% set twice is 10%, not 21%, and
  0% takes the margin off again.
* **Never a charge that is on an invoice.** What an owner was billed does
  not change. The question is ``ExtraCharge.on_live_invoice``, the same
  one the charge screens and the health record sync ask.
* **The record keeps step.** A charge made from a health record (a vet
  visit, a vaccination, a farrier visit) is rebuilt from the record's
  cost when the record is saved. The record's cost is set to the new
  amount too, or the next edit to the record would undo the margin.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from .models import ExtraCharge

PENNY = Decimal('0.01')
MAX_PERCENT = Decimal('500')


def parse_percent(text):
    """A margin typed by a person: 0 to 500, at most 2 decimal places. Else None."""
    try:
        percent = Decimal(str(text).strip().rstrip('%').strip())
    except (InvalidOperation, ValueError):
        return None
    if not percent.is_finite() or percent < 0 or percent > MAX_PERCENT:
        return None
    if percent != percent.quantize(PENNY):
        return None
    return percent


def cost_of(charge):
    """What the yard paid: the recorded cost, else the amount as it stands."""
    return charge.cost_amount if charge.cost_amount is not None else charge.amount


def with_margin(cost, percent):
    """The amount to bill for a cost and a margin percentage."""
    return (Decimal(cost) * (Decimal('100') + Decimal(percent)) / Decimal('100')).quantize(PENNY, ROUND_HALF_UP)


def unbilled_charges():
    """Charges a margin can still go on: on no live invoice, not deleted."""
    from invoicing.models import Invoice, InvoiceLineItem

    live_lines = InvoiceLineItem.objects.filter(charge=OuterRef('pk')).exclude(
        invoice__status=Invoice.Status.CANCELLED,
    )
    return ExtraCharge.objects.filter(invoiced=False).annotate(
        _on_invoice=Exists(live_lines),
    ).filter(_on_invoice=False)


def filtered(charge_type='', provider_id='', date_from=None, date_to=None, search=''):
    """The unbilled charges that match the Add margin page's filters."""
    charges = unbilled_charges().select_related('horse', 'owner', 'service_provider')
    if charge_type == 'vet_all':
        charges = charges.filter(charge_type__in=[
            ExtraCharge.ChargeType.VET, ExtraCharge.ChargeType.VACCINATION,
        ])
    elif charge_type:
        charges = charges.filter(charge_type=charge_type)
    if provider_id:
        charges = charges.filter(service_provider_id=provider_id)
    if date_from:
        charges = charges.filter(date__gte=date_from)
    if date_to:
        charges = charges.filter(date__lte=date_to)
    if search:
        charges = charges.filter(
            Q(description__icontains=search) | Q(horse__name__icontains=search) | Q(owner__name__icontains=search)
        )
    return charges.order_by('-date', 'horse__name', 'pk')


def preview(charges, percent):
    """Each charge with its cost and its amount at ``percent``, and the totals."""
    rows = []
    cost_total = now_total = new_total = Decimal('0.00')
    for charge in charges:
        cost = cost_of(charge)
        new = with_margin(cost, percent) if percent is not None else charge.amount
        rows.append({'charge': charge, 'cost': cost, 'new': new, 'changes': new != charge.amount})
        cost_total += cost
        now_total += charge.amount
        new_total += new
    return {
        'rows': rows,
        'cost_total': cost_total,
        'now_total': now_total,
        'new_total': new_total,
        'margin_total': new_total - cost_total,
    }


def apply_margin(charge_ids, percent):
    """Set the margin on these charges. Returns (changed, skipped).

    ``skipped`` counts the charges that went onto an invoice, or were
    deleted, between the page being shown and the button being pressed.
    """
    changed = skipped = 0
    for pk in charge_ids:
        with transaction.atomic():
            charge = ExtraCharge.objects.select_for_update().filter(pk=pk).first()
            if charge is None or charge.on_live_invoice:
                skipped += 1
                continue
            cost = cost_of(charge)
            amount = with_margin(cost, percent)
            if charge.cost_amount is None or charge.amount != amount:
                charge.cost_amount = cost
                charge.amount = amount
                charge.save(update_fields=['cost_amount', 'amount', 'updated_at'])
                record = charge.linked_health_record()
                if record is not None and record.cost != amount:
                    record.cost = amount
                    record.save(update_fields=['cost'])
            changed += 1
    return changed, skipped
