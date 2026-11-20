"""Training charges (docs/TRAINING_MODULE_PLAN.md, phase 5, D8 and D11).

A Done session of a priced work type makes one charge for the horse's
owner, unless the horse's rate on the date of the work includes exercise.
The rules follow ``health.views.sync_record_charge``:

- the price is taken when the work becomes Done, and kept (``Activity.cost``);
  a later price change does not change work already done;
- an unbilled charge follows its activity: new amount, date or horse;
- an activity that is no longer Done loses its unbilled charge;
- a charge that someone deleted is never made again;
- a charge on a live invoice is never changed. The views refuse the change
  first (``billed_invoice``).

A user without Full on Charges can confirm priced work (D11). Prices show
only to users with view on Charges.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .models import Activity

ZERO = Decimal('0.00')


def includes_exercise(horse_id, day):
    """True when the horse's placement on ``day`` has a rate that includes
    exercise. No placement: nothing includes exercise."""
    from core.models import Placement
    return Placement.objects.filter(
        horse_id=horse_id, start_date__lte=day, rate_type__includes_exercise=True,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).exists()


def is_billed(activity):
    """True when this activity's charge is on a live invoice."""
    charge = activity.extra_charge
    return charge is not None and not charge.is_deleted and charge.on_live_invoice


def billed_invoice(activity):
    """The live invoice that carries this activity's charge, or None when it
    is billed but the invoice cannot be named (or it is not billed)."""
    if not is_billed(activity):
        return None
    charge = activity.extra_charge
    item = (
        charge.invoice_items.select_related('invoice')
        .exclude(invoice__status='cancelled').order_by('-pk').first()
    )
    return item.invoice if item else charge.invoice


def refuse_if_billed(activities):
    """``(horse_name, invoice or None)`` for the first billed activity; None
    when none is billed."""
    for activity in activities:
        if activity.extra_charge_id and is_billed(activity):
            return activity.horse.name, billed_invoice(activity)
    return None


def billed_message(found, action='change'):
    horse, invoice = found
    where = f"invoice {invoice.invoice_number}" if invoice is not None else "an invoice"
    return (f"Not changed: the training charge for {horse} is on {where}. "
            f"Cancel or credit that invoice first, or {action} the work on another record.")


def sync(activities):
    """Bring the charges of these activities in line. Returns counts:
    ``{'created', 'updated', 'deleted', 'no_owner'}``."""
    counts = {'created': 0, 'updated': 0, 'deleted': 0, 'no_owner': 0}
    ids = [a.pk if isinstance(a, Activity) else int(a) for a in activities]
    if not ids:
        return counts
    with transaction.atomic():
        rows = list(
            Activity.objects.select_for_update().filter(pk__in=ids)
            .select_related('work_type', 'horse', 'extra_charge')
        )
        for row in rows:
            result = _sync_one(row)
            if result:
                counts[result] += 1
    return counts


def _sync_one(row):
    charge = row.extra_charge
    done = row.status == Activity.Status.DONE
    price = row.work_type.price
    # The common case costs no query: no price, no charge, nothing to do.
    if charge is None and (not done or not price) and row.cost is None:
        return None
    if charge is not None and charge.is_deleted:
        return None     # deleted on purpose: never bring it back
    if charge is not None and charge.on_live_invoice:
        return None     # billed: the views refuse the change before this
    if not done:
        _set_cost(row, None)
        return _drop(row, charge)
    if row.cost is None:
        _set_cost(row, price if price and price > 0 else None)
    if not row.cost or row.cost <= ZERO or includes_exercise(row.horse_id, row.date):
        return _drop(row, charge)
    owner = row.horse.current_owner
    if owner is None:
        _drop(row, charge)
        return 'no_owner'
    from billing.models import ExtraCharge
    description = f"Training - {row.work_type.name}"
    if charge is None:
        row.extra_charge = ExtraCharge.objects.create(
            horse=row.horse, owner=owner, amount=row.cost, date=row.date,
            charge_type=ExtraCharge.ChargeType.TRAINING, description=description,
        )
        Activity.objects.filter(pk=row.pk).update(extra_charge=row.extra_charge)
        return 'created'
    changed = []
    for name, value in (('amount', row.cost), ('date', row.date), ('description', description),
                        ('horse_id', row.horse_id)):
        if getattr(charge, name) != value:
            setattr(charge, name, value)
            changed.append(name.replace('_id', ''))
    if 'horse' in changed:
        charge.owner = owner
        changed.append('owner')
    if changed:
        charge.save(update_fields=changed + ['updated_at'])
        return 'updated'
    return None


def _set_cost(row, cost):
    if row.cost != cost:
        row.cost = cost
        Activity.objects.filter(pk=row.pk).update(cost=cost)


def _drop(row, charge):
    if charge is None:
        return None
    Activity.objects.filter(pk=row.pk).update(extra_charge=None)
    row.extra_charge = None
    charge.delete()
    return 'deleted'


def reset_cost(activity):
    """Called when the work type of an activity changes: the next sync takes
    the new work type's price. A billed activity is refused before this."""
    if activity.cost is not None:
        activity.cost = None
