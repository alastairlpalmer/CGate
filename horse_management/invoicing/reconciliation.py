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
from calendar import monthrange
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


def monthly_report(year: int) -> Report:
    report = Report(year=year)
    invoices = list(
        Invoice.objects.filter(status__in=ISSUED).only(
            'pk', 'created_at', 'subtotal', 'vat_amount', 'total',
        )
    )
    payments = list(Payment.objects.filter(invoice__status__in=ISSUED).only('invoice_id', 'date', 'amount', 'method'))
    today = timezone.localdate()
    for m in range(1, 13):
        start = date(year, m, 1)
        if start > today:
            break
        end = date(year, m, monthrange(year, m)[1])
        row = Month(month=start)
        for inv in invoices:
            issued = timezone.localtime(inv.created_at).date()
            if start <= issued <= end:
                row.invoice_count += 1
                row.net += inv.subtotal
                row.vat += inv.vat_amount
                row.total += inv.total
        in_month = [p for p in payments if start <= p.date <= end]
        row.received = sum((p.amount for p in in_month if not p.is_credit_note), ZERO)
        row.credited = sum((p.amount for p in in_month if p.is_credit_note), ZERO)
        issued_by_end = {
            inv.pk: inv.total for inv in invoices if timezone.localtime(inv.created_at).date() <= end
        }
        paid_by_end = sum(
            (p.amount for p in payments if p.invoice_id in issued_by_end and p.date <= end), ZERO,
        )
        row.outstanding = sum(issued_by_end.values(), ZERO) - paid_by_end
        report.months.append(row)
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
