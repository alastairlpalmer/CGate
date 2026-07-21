"""
Finances overview: revenue/cost trend, forecast and site capacity charts,
plus headline financial KPIs. Moved here from the dashboard so the dashboard
stays operational and this page can grow into a fuller financial view.
"""

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from core.permissions import feature_required
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.utils import timezone

from billing.models import ExtraCharge, YardCost
from invoicing.models import Invoice, Payment

from ..models import Location, Placement

logger = logging.getLogger(__name__)


def _empty_context():
    """Safe zero-context used when the finances view errors out."""
    return {
        'chart_data': {'monthly': {'labels': [], 'revenue': [], 'costs': [], 'forecastStart': 0}},
        'capacity_data': {'labels': [], 'horses': [], 'capacity': []},
        'outstanding_total': 0,
        'outstanding_count': 0,
        'unbilled_total': 0,
        'revenue_this_month': 0,
        'costs_this_month': 0,
    }


@feature_required('finances')
def finances(request):
    """Finances overview page."""
    try:
        return _finances_inner(request)
    except Exception:
        logger.exception("Finances page error")
        return render(request, 'finances.html', _empty_context())


def _finances_inner(request):
    today = timezone.localdate()

    # ── Revenue vs Cost Chart Data ──────────────────────────────
    twelve_months_ago = today.replace(day=1) - timedelta(days=365)

    revenue_qs = (
        Invoice.objects.filter(
            status__in=['paid', 'sent'],
            period_end__gte=twelve_months_ago,
        )
        .annotate(month=TruncMonth('period_end'))
        .values('month')
        .annotate(total=Sum('total'))
        .order_by('month')
    )

    def _to_date(val):
        return val.date() if hasattr(val, 'date') and callable(val.date) else val

    revenue_map = {_to_date(r['month']): float(r['total']) for r in revenue_qs}

    # .order_by() clears the models' Meta.ordering ('-date'): an ORDER BY
    # inside a UNION arm is a DatabaseError on SQLite and breaks the
    # month grouping everywhere else.
    charge_qs = (
        ExtraCharge.objects.filter(date__gte=twelve_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum('amount'))
        .order_by()
    )
    yard_qs = (
        YardCost.objects.filter(date__gte=twelve_months_ago)
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum('amount'))
        .order_by()
    )
    cost_map = {}
    # Single round trip for both cost sources; months can repeat across the
    # two arms (all=True), merged below.
    for c in charge_qs.union(yard_qs, all=True):
        d = _to_date(c['month'])
        cost_map[d] = cost_map.get(d, 0) + float(c['total'])

    month_labels = []
    revenue_data = []
    cost_data = []
    for i in range(12):
        m_offset = 11 - i
        m = today.month - m_offset
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        d = date(y, m, 1)
        month_labels.append(d.strftime('%b %y'))
        revenue_data.append(revenue_map.get(d, 0))
        cost_data.append(cost_map.get(d, 0))

    active_placements = list(Placement.objects.filter(
        end_date__isnull=True
    ).select_related('rate_type').only('expected_departure', 'rate_type__daily_rate'))
    forecast_labels = []
    forecast_revenue = []
    forecast_cost = []
    avg_cost = sum(cost_data[-3:]) / 3 if any(cost_data[-3:]) else 0
    for i in range(1, 7):
        m = today.month + i
        y = today.year
        while m > 12:
            m -= 12
            y += 1
        d = date(y, m, 1)
        days_in_month = calendar.monthrange(y, m)[1]
        forecast_labels.append(d.strftime('%b %y'))
        month_rev = Decimal('0')
        for p in active_placements:
            if p.expected_departure and p.expected_departure < d:
                continue
            month_rev += p.rate_type.daily_rate * days_in_month
        forecast_revenue.append(float(month_rev))
        forecast_cost.append(round(avg_cost, 2))

    chart_data = {
        'monthly': {
            'labels': month_labels + forecast_labels,
            'revenue': revenue_data + forecast_revenue,
            'costs': cost_data + forecast_cost,
            'forecastStart': len(month_labels),
        },
    }

    # ── Site Capacity Data ──────────────────────────────────────
    # One query: per-location counts (correct under the placements join),
    # then sum per site in Python. Annotating Sum(capacity) and the
    # placement Count together would inflate capacity across joined rows.
    location_rows = Location.objects.filter(
        usage__in=[Location.Usage.HORSES, Location.Usage.MIXED],
    ).annotate(
        horse_count=Count(
            'placements__horse',
            filter=Q(
                placements__end_date__isnull=True,
                placements__horse__is_active=True,
            ),
            distinct=True,
        ),
    ).values('site', 'capacity', 'horse_count')
    site_capacity = {}
    site_horses = {}
    for row in location_rows:
        site_capacity[row['site']] = site_capacity.get(row['site'], 0) + (row['capacity'] or 0)
        site_horses[row['site']] = site_horses.get(row['site'], 0) + row['horse_count']
    sites = sorted(site_capacity.keys())
    capacity_data = {
        'labels': sites,
        'horses': [site_horses.get(s, 0) for s in sites],
        'capacity': [site_capacity.get(s, 0) for s in sites],
    }

    # ── Headline KPIs ───────────────────────────────────────────
    # Outstanding = invoice totals minus recorded part-payments, so the KPI
    # stays honest the moment anyone pays half an invoice.
    outstanding = Invoice.objects.filter(
        status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE]
    ).aggregate(total=Sum('total'), count=Count('id'))
    paid_against_open = Payment.objects.filter(
        invoice__status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    outstanding['total'] = (outstanding['total'] or Decimal('0.00')) - paid_against_open

    unbilled_total = ExtraCharge.unbilled_total()

    # This month's actuals come straight out of the chart maps — no extra queries.
    this_month = today.replace(day=1)

    context = {
        'chart_data': chart_data,
        'capacity_data': capacity_data,
        'outstanding_total': outstanding['total'] or 0,
        'outstanding_count': outstanding['count'] or 0,
        'unbilled_total': unbilled_total,
        'revenue_this_month': revenue_map.get(this_month, 0),
        'costs_this_month': cost_map.get(this_month, 0),
    }

    return render(request, 'finances.html', context)
