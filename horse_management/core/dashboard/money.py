"""The month's billing position, kept compact on purpose.

Drafts waiting for review, what is owed (net of part-payments, aged), what
came in, what is not on an invoice yet, and whether the sending machinery
is healthy. Charts stay on the Finances page.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..permissions import LEVEL_VIEW, has_feature_access

ZERO = Decimal('0.00')

# Aged-debt buckets, oldest last, matching StatementService.BUCKETS.
BUCKETS = (
    ('current', 'Not yet due', 0),
    ('d30', '1–30 days', 30),
    ('d60', '31–60 days', 60),
    ('d90', '61–90 days', 90),
    ('d90plus', '90+ days', None),
)


def _bucket(days_late):
    if days_late <= 0:
        return 'current'
    if days_late <= 30:
        return 'd30'
    if days_late <= 60:
        return 'd60'
    if days_late <= 90:
        return 'd90'
    return 'd90plus'


def snapshot(user, *, today=None):
    """``{'invoices': {...} | None, 'unbilled': Decimal | None, 'xero': {...} | None}``.

    A block is ``None`` when the role cannot see that area.
    """
    today = today or timezone.localdate()
    data = {'invoices': None, 'unbilled': None, 'xero': None, 'any': False}

    if has_feature_access(user, 'invoices', LEVEL_VIEW):
        from invoicing.models import Invoice, Payment

        drafts = Invoice.objects.filter(status=Invoice.Status.DRAFT).aggregate(
            count=Count('id'), total=Coalesce(Sum('total'), Value(ZERO)),
        )
        open_invoices = Invoice.objects.filter(
            status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE],
        ).annotate(
            balance=ExpressionWrapper(
                F('total') - Coalesce(Sum('payments__amount'), Value(ZERO)),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        ).values('id', 'due_date', 'balance')

        outstanding = ZERO
        overdue_total = ZERO
        overdue_count = 0
        aged = {key: ZERO for key, _, _ in BUCKETS}
        for row in open_invoices:
            balance = row['balance'] or ZERO
            if balance <= 0:
                continue
            outstanding += balance
            days_late = (today - row['due_date']).days
            aged[_bucket(days_late)] += balance
            if days_late > 0:
                overdue_total += balance
                overdue_count += 1

        received = Payment.objects.filter(
            date__gte=today - timedelta(days=30),
        ).aggregate(total=Coalesce(Sum('amount'), Value(ZERO)), count=Count('id'))

        send_errors = Invoice.objects.exclude(send_error='').count()

        buckets = []
        for key, label, _ in BUCKETS:
            amount = aged[key]
            buckets.append({
                'key': key,
                'label': label,
                'amount': amount,
                'pct': round(float(amount / outstanding * 100)) if outstanding else 0,
            })

        data['invoices'] = {
            'drafts': drafts['count'],
            'drafts_total': drafts['total'],
            'outstanding': outstanding,
            'overdue_total': overdue_total,
            'overdue_count': overdue_count,
            'received_30d': received['total'],
            'received_30d_count': received['count'],
            'send_errors': send_errors,
            'aged': buckets,
        }
        data['any'] = True

    if has_feature_access(user, 'charges', LEVEL_VIEW):
        from billing.models import ExtraCharge
        data['unbilled'] = ExtraCharge.unbilled_total()
        data['any'] = True

    if has_feature_access(user, 'xero', LEVEL_VIEW):
        from xero_integration.models import XeroConnection, XeroInvoiceSync
        connection = XeroConnection.objects.filter(pk=1).first()
        data['xero'] = {
            'configured': connection is not None,
            'connected': bool(connection and connection.is_connected),
            'tenant': connection.xero_tenant_name if connection else '',
            'errors': XeroInvoiceSync.objects.filter(
                sync_status=XeroInvoiceSync.SyncStatus.ERROR,
            ).count(),
        }
        data['any'] = True

    return data
