"""Month-by-month figures the accountant can tick off against Xero, and an
explanation for every gap in the invoice numbers.

Rules, so the numbers can be checked by hand:

* **Invoiced** counts invoices that have been issued (not draft, not
  cancelled), by invoice date (``created_at``, the date on the PDF).
* **Received** counts payments by payment date. Voided payments are left
  out (the default manager hides them), and so are credit notes, which
  are in **Credited**.
* **Outstanding** at a month end is what those invoices total, less what
  was paid against them by that day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.utils import timezone

from .models import Invoice, Payment

ZERO = Decimal('0.00')
ISSUED = [Invoice.Status.SENT, Invoice.Status.PAID, Invoice.Status.OVERDUE]


@dataclass
class Month:
    month: date
    invoice_count: int = 0
    net: Decimal = ZERO
    vat: Decimal = ZERO
    total: Decimal = ZERO
    received: Decimal = ZERO
    credited: Decimal = ZERO
    outstanding: Decimal = ZERO


@dataclass
class Report:
    year: int
    months: list[Month] = field(default_factory=list)
    cancelled_count: int = 0

    @property
    def totals(self) -> Month:
        t = Month(month=date(self.year, 1, 1))
        for m in self.months:
            t.invoice_count += m.invoice_count
            t.net += m.net
            t.vat += m.vat
            t.total += m.total
            t.received += m.received
            t.credited += m.credited
        t.outstanding = self.months[-1].outstanding if self.months else ZERO
        return t


def _month_index(value, year):
    """0 for anything before ``year``, else the month number (1-12)."""
    if value.year < year:
        return 0
    return value.month


def monthly_report(year: int) -> Report:
    """Three grouped queries, whatever the size of the ledger.

    Before, every invoice and payment ever made was loaded and looped over
    once a month: a yard with years of history took about half a second.
    Invoices are bucketed by local month of ``created_at`` (``TruncMonth``
    uses the site time zone, as ``timezone.localtime`` did), so the figures
    are the same.
    """
    from django.db.models import Count, Sum
    from django.db.models.functions import TruncMonth

    report = Report(year=year)
    today = timezone.localdate()
    year_end = date(year, 12, 31)
    last_month = 12 if year < today.year else (today.month if year == today.year else 0)

    issued = Invoice.objects.filter(status__in=ISSUED)
    # Index 0 holds everything issued before the year (it counts towards
    # what is outstanding at each month end, not towards "invoiced").
    inv_count = [0] * 13
    inv_net = [ZERO] * 13
    inv_vat = [ZERO] * 13
    inv_total = [ZERO] * 13
    for row in (
        issued.annotate(m=TruncMonth('created_at')).values('m')
        .annotate(n=Count('id'), net=Sum('subtotal'), vat=Sum('vat_amount'), total=Sum('total'))
    ):
        if row['m'].year > year:
            continue
        i = _month_index(row['m'], year)
        inv_count[i] += row['n']
        inv_net[i] += row['net'] or ZERO
        inv_vat[i] += row['vat'] or ZERO
        inv_total[i] += row['total'] or ZERO

    received = [ZERO] * 13
    credited = [ZERO] * 13
    # paid[p][i]: paid in month p against invoices issued in month i.
    paid = [[ZERO] * 13 for _ in range(13)]
    for row in (
        Payment.objects.filter(invoice__status__in=ISSUED, date__lte=year_end)
        .annotate(pm=TruncMonth('date'), im=TruncMonth('invoice__created_at'))
        .values('pm', 'im', 'method').annotate(amount=Sum('amount'))
    ):
        if row['im'].year > year:
            continue
        p = _month_index(row['pm'], year)
        i = _month_index(row['im'], year)
        amount = row['amount'] or ZERO
        if p:
            if row['method'] == Payment.Method.CREDIT_NOTE:
                credited[p] += amount
            else:
                received[p] += amount
        paid[p][i] += amount

    issued_to_date = inv_total[0]
    for m in range(1, last_month + 1):
        issued_to_date += inv_total[m]
        paid_to_date = sum((paid[p][i] for p in range(m + 1) for i in range(m + 1)), ZERO)
        report.months.append(Month(
            month=date(year, m, 1),
            invoice_count=inv_count[m], net=inv_net[m], vat=inv_vat[m], total=inv_total[m],
            received=received[m], credited=credited[m],
            outstanding=issued_to_date - paid_to_date,
        ))
    report.cancelled_count = Invoice.objects.filter(
        status=Invoice.Status.CANCELLED, created_at__year=year,
    ).count()
    return report


# ── Number gaps ──────────────────────────────────────────────────────────────

@dataclass
class Gap:
    number: str
    explanation: str


def number_gaps(prefix: str) -> list[Gap]:
    """Every number missing from the ``prefix`` sequence, with what happened.

    HMRC wants a unique, sequential series; a gap is acceptable when it can
    be explained. The record history keeps every deleted invoice, so a draft
    deleted by mistake shows who deleted it and when.
    """
    from .models import Invoice as InvoiceModel

    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$')
    width = 5
    live = set()
    for number in InvoiceModel.objects.values_list('invoice_number', flat=True):
        match = pattern.match(number)
        if match:
            live.add(int(match.group(1)))
            width = len(match.group(1))
    if not live:
        return []
    deleted = {}
    for h in InvoiceModel.history.filter(history_type='-').select_related('history_user').order_by('history_date'):
        match = pattern.match(h.invoice_number)
        if match:
            who = (h.history_user.email or h.history_user.get_username()) if h.history_user else 'System'
            deleted[int(match.group(1))] = (
                f"Deleted while a {h.get_status_display().lower()} by {who} on "
                f"{timezone.localtime(h.history_date):%d %b %Y}"
            )
    gaps = []
    for n in range(min(live), max(live)):
        if n in live:
            continue
        gaps.append(Gap(
            number=f'{prefix}{n:0{width}d}',
            explanation=deleted.get(n, 'No record of this number. It may predate the audit trail.'),
        ))
    return gaps
