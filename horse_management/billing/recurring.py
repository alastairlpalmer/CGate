"""Recurring yard costs repeat (Dev board, 21 Nov 2026: "make recurring
costs repeat").

A yard cost with "This is a recurring cost" ticked is the source. On each
due day after its own date, up to today and its end date, a copy is made:
same category, supplier, description, amount, VAT and notes, not
recurring itself, with ``recurrence_source`` set. A month step keeps the
source's day of the month, or the month's last day when it is shorter
(31 Jan, 28 Feb, 31 Mar).

The daily job (billing.tasks.create_recurring_yard_costs) and a save of
the cost form both call create_repeats. It is safe to run any number of
times: the source remembers the last day it made a copy for
(``recurrence_made_until``) and only makes copies after it, so a copy
someone deleted is not made again, even after the bin is emptied. Untick
the box, set an end date or delete the source, and no more copies are
made.
"""

from calendar import monthrange
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import YardCost

MONTHS = {
    YardCost.RecurrenceInterval.MONTHLY: 1,
    YardCost.RecurrenceInterval.QUARTERLY: 3,
    YardCost.RecurrenceInterval.ANNUAL: 12,
}
# A source many years old does not make thousands of rows in one run.
MAX_STEPS = 520


def add_months(day, months):
    """``day`` moved on by ``months``, on the same day of the month or the
    month's last day."""
    year, month = divmod(day.month - 1 + months, 12)
    year += day.year
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def due_dates(cost, until):
    """The days after ``cost.date``, up to ``until`` and the end date, on
    which the cost repeats. Empty for a cost that does not repeat."""
    if not cost.is_recurring or not cost.recurrence_interval:
        return []
    last = min(d for d in (until, cost.recurrence_end_date) if d)
    out = []
    for step in range(1, MAX_STEPS + 1):
        if cost.recurrence_interval == YardCost.RecurrenceInterval.WEEKLY:
            day = cost.date + timedelta(weeks=step)
        else:
            day = add_months(cost.date, MONTHS[cost.recurrence_interval] * step)
        if day > last:
            break
        out.append(day)
    return out


def create_repeats(today=None, costs=None):
    """Make the copies due up to ``today``. Answers how many were made."""
    today = today or timezone.localdate()
    if costs is None:
        costs = YardCost.objects.filter(
            is_recurring=True, recurrence_source__isnull=True, date__lt=today,
        ).exclude(recurrence_interval='')
    made = 0
    for cost in costs:
        if cost.recurrence_source_id or not cost.pk:
            continue
        days = [d for d in due_dates(cost, today)
                if cost.recurrence_made_until is None or d > cost.recurrence_made_until]
        if not days:
            continue
        have = set(YardCost.all_objects.filter(recurrence_source=cost).values_list('date', flat=True))
        for day in days:
            if day in have:
                continue
            try:
                with transaction.atomic():
                    YardCost.objects.create(
                        category=cost.category, date=day, supplier=cost.supplier,
                        description=cost.description, amount=cost.amount, vat_amount=cost.vat_amount,
                        notes=cost.notes, recurrence_source=cost,
                    )
            except IntegrityError:
                continue  # another run made it a moment ago
            made += 1
        YardCost.all_objects.filter(pk=cost.pk).update(recurrence_made_until=days[-1])
        cost.recurrence_made_until = days[-1]
    return made
