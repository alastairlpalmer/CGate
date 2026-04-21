"""
Dashboard views.
"""

import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from billing.models import ExtraCharge
from health.models import (
    BreedingRecord,
    FarrierVisit,
    Vaccination,
    VetVisit,
    WormEggCount,
)

from invoicing.models import Invoice

from ..dashboard_widgets import GROUPS, WIDGETS_BY_KEY
from ..models import DashboardPreference, Horse, Location, Placement

logger = logging.getLogger(__name__)


def _empty_context():
    """Safe zero-context used when the dashboard view errors out."""
    return {
        'total_horses': 0,
        'vaccinations_due_count': 0,
        'outstanding_invoices_count': 0,
        'vaccinations_due': [],
        'farrier_due': [],
        'outstanding_invoices': [],
        'unbilled_total': 0,
        'chart_data': {'monthly': {'labels': [], 'revenue': [], 'costs': [], 'forecastStart': 0}},
        'capacity_data': {'labels': [], 'horses': [], 'capacity': []},
        'activity': [],
        'pending_departures': [],
        'visible_widgets': {g: [] for g in GROUPS},
        'visible_keys': set(),
        'any_health_visible': False,
    }


@login_required
def dashboard(request):
    """Main dashboard view."""
    try:
        return _dashboard_inner(request)
    except Exception:
        logger.exception("Dashboard error")
        return render(request, 'dashboard.html', _empty_context())


def _dashboard_inner(request):
    """Dashboard queries (health alerts loaded via HTMX). Queries are skipped
    for widgets the user has hidden via their DashboardPreference."""
    import calendar
    from datetime import date
    from decimal import Decimal

    from django.db.models.functions import TruncMonth

    from billing.models import FeedOut, YardCost

    pref = DashboardPreference.get_for(request.user)
    visible_widgets = pref.visible_ordered_keys_by_group()
    visible = {k for keys in visible_widgets.values() for k in keys}

    today = timezone.now().date()
    thirty_days = today + timedelta(days=30)
    two_weeks = today + timedelta(days=14)

    # Vaccinations: list used by both kpi_vaccinations_due (count) and list_vaccinations_due (rows).
    if 'kpi_vaccinations_due' in visible or 'list_vaccinations_due' in visible:
        vaccinations_due = list(Vaccination.objects.filter(
            next_due_date__lte=thirty_days,
            next_due_date__gte=today,
            horse__is_active=True,
        ).select_related('horse', 'vaccination_type').order_by('next_due_date')[:10])
    else:
        vaccinations_due = []

    # Outstanding invoices: shared by kpi_outstanding_invoices (count) and table_outstanding (rows).
    if 'kpi_outstanding_invoices' in visible or 'table_outstanding' in visible:
        outstanding_invoices = list(Invoice.objects.filter(
            status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE]
        ).select_related('owner').order_by('due_date')[:10])
    else:
        outstanding_invoices = []

    total_horses = 0
    if 'kpi_total_horses' in visible:
        total_horses = Horse.objects.filter(
            is_active=True, placements__end_date__isnull=True,
        ).distinct().count()

    unbilled_total = 0
    if 'kpi_unbilled_charges' in visible:
        unbilled_total = ExtraCharge.objects.filter(invoiced=False).aggregate(
            total=Sum('amount')
        )['total'] or 0

    farrier_due = []
    if 'list_farrier_due' in visible:
        farrier_due = list(FarrierVisit.objects.filter(
            next_due_date__lte=two_weeks,
            next_due_date__gte=today,
            horse__is_active=True
        ).select_related('horse').order_by('next_due_date')[:10])

    # ── Revenue vs Cost Chart Data ──────────────────────────────
    chart_data = {'monthly': {'labels': [], 'revenue': [], 'costs': [], 'forecastStart': 0}}
    if 'chart_revenue' in visible:
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

        charge_qs = (
            ExtraCharge.objects.filter(date__gte=twelve_months_ago)
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        yard_qs = (
            YardCost.objects.filter(date__gte=twelve_months_ago)
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total=Sum('amount'))
            .order_by('month')
        )
        cost_map = {}
        for c in charge_qs:
            d = _to_date(c['month'])
            cost_map[d] = cost_map.get(d, 0) + float(c['total'])
        for y in yard_qs:
            d = _to_date(y['month'])
            cost_map[d] = cost_map.get(d, 0) + float(y['total'])

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
    capacity_data = {'labels': [], 'horses': [], 'capacity': []}
    if 'chart_capacity' in visible:
        horse_locations = Location.objects.filter(
            usage__in=[Location.Usage.HORSES, Location.Usage.MIXED],
        )
        site_capacity = {
            row['site']: row['total_capacity'] or 0
            for row in horse_locations.values('site').annotate(
                total_capacity=Sum('capacity'),
            )
        }
        site_horses = {
            row['site']: row['total_horses']
            for row in horse_locations.values('site').annotate(
                total_horses=Count(
                    'placements__horse',
                    filter=Q(
                        placements__end_date__isnull=True,
                        placements__horse__is_active=True,
                    ),
                    distinct=True,
                ),
            )
        }
        sites = sorted(site_capacity.keys())
        capacity_data = {
            'labels': sites,
            'horses': [site_horses.get(s, 0) for s in sites],
            'capacity': [site_capacity.get(s, 0) for s in sites],
        }

    # ── Recent Activity Timeline ────────────────────────────────
    activity = []
    if 'recent_activity' in visible:
        for p in Placement.objects.filter(end_date__isnull=True).select_related('horse', 'location').order_by('-start_date')[:3]:
            activity.append({'date': p.start_date, 'type': 'placement', 'desc': f"{p.horse.name} arrived at {p.location.name}", 'link': f'/horses/{p.horse.pk}/'})
        for v in Vaccination.objects.select_related('horse', 'vaccination_type').order_by('-date_given')[:3]:
            activity.append({'date': v.date_given, 'type': 'vaccination', 'desc': f"{v.horse.name} — {v.vaccination_type.name}", 'link': f'/horses/{v.horse.pk}/'})
        for f in FarrierVisit.objects.select_related('horse').order_by('-date')[:3]:
            activity.append({'date': f.date, 'type': 'farrier', 'desc': f"{f.horse.name} — {f.get_work_done_display()}", 'link': f'/horses/{f.horse.pk}/'})
        for v in VetVisit.objects.select_related('horse').order_by('-date')[:3]:
            activity.append({'date': v.date, 'type': 'vet_visit', 'desc': f"{v.horse.name} — {v.reason[:40]}", 'link': f'/horses/{v.horse.pk}/'})
        for fo in FeedOut.objects.select_related('location').order_by('-date')[:3]:
            activity.append({'date': fo.date, 'type': 'feed', 'desc': f"{fo.get_feed_type_display()} to {fo.location.name}", 'link': f'/locations/{fo.location.pk}/?tab=feed'})
        for c in ExtraCharge.objects.select_related('horse').order_by('-date')[:3]:
            activity.append({'date': c.date, 'type': 'charge', 'desc': f"{c.horse.name} — {c.get_charge_type_display()} £{c.amount}", 'link': f'/billing/charges/{c.pk}/edit/'})
        activity.sort(key=lambda x: x['date'], reverse=True)
        activity = activity[:12]

    # Pending departures (grouped by owner + date) for inline display
    pending_departures = []
    if 'pending_departures' in visible:
        pending_placements = Placement.objects.filter(
            end_date__lte=today,
            horse__is_active=True,
        ).exclude(
            end_date__isnull=True,
        ).select_related('horse', 'owner', 'location').order_by('owner__name', 'end_date')
        pending_groups = {}
        for p in pending_placements:
            key = (p.owner_id, p.owner.name if p.owner else 'Unknown', p.end_date)
            if key not in pending_groups:
                pending_groups[key] = {'owner_name': key[1], 'date': p.end_date, 'horses': [], 'horse_ids': []}
            pending_groups[key]['horses'].append(p.horse)
            pending_groups[key]['horse_ids'].append(str(p.horse.pk))
        pending_departures = list(pending_groups.values())

    context = {
        'total_horses': total_horses,
        'vaccinations_due': vaccinations_due,
        'vaccinations_due_count': len(vaccinations_due),
        'farrier_due': farrier_due,
        'outstanding_invoices': outstanding_invoices,
        'outstanding_invoices_count': len(outstanding_invoices),
        'unbilled_total': unbilled_total,
        'chart_data': chart_data,
        'capacity_data': capacity_data,
        'activity': activity,
        'pending_departures': pending_departures,
        'visible_widgets': visible_widgets,
        'visible_keys': visible,
        'any_health_visible': bool(visible_widgets.get('health')),
    }

    return render(request, 'dashboard.html', context)


@login_required
def dashboard_health_alerts(request):
    """HTMX partial: health alerts loaded after initial dashboard render.

    Only queries widgets the user has made visible; returns an empty body if
    none are enabled."""
    pref = DashboardPreference.get_for(request.user)
    visible_widgets = pref.visible_ordered_keys_by_group()
    health_keys = visible_widgets.get('health', [])
    visible = set(health_keys)

    if not visible:
        return render(request, 'partials/dashboard_health_alerts.html', {
            'health_keys': [],
            'ehv_due': [],
            'high_egg_counts': [],
            'vet_follow_ups': [],
            'upcoming_departures': [],
        })

    today = timezone.now().date()
    thirty_days = today + timedelta(days=30)
    seven_days = today + timedelta(days=7)

    ehv_due = []
    if 'health_ehv_due' in visible:
        ehv_due = BreedingRecord.objects.filter(
            status='confirmed',
            mare__is_active=True,
        ).select_related('mare')[:10]

    high_egg_counts = []
    if 'health_egg_counts' in visible:
        high_egg_counts = WormEggCount.objects.filter(
            count__gt=200,
            horse__is_active=True,
        ).select_related('horse').order_by('-date')[:10]

    vet_follow_ups = []
    if 'health_vet_followups' in visible:
        vet_follow_ups = VetVisit.objects.filter(
            follow_up_date__gte=today,
            follow_up_date__lte=thirty_days,
            horse__is_active=True,
        ).select_related('horse', 'vet').order_by('follow_up_date')[:10]

    upcoming_departures = []
    if 'health_upcoming_dep' in visible:
        upcoming_departures = Placement.objects.filter(
            expected_departure__gt=today,
            expected_departure__lte=seven_days,
            end_date__isnull=True,
            horse__is_active=True,
        ).select_related('horse', 'owner', 'location').order_by('expected_departure')[:10]

    context = {
        'health_keys': health_keys,
        'ehv_due': ehv_due,
        'high_egg_counts': high_egg_counts,
        'vet_follow_ups': vet_follow_ups,
        'upcoming_departures': upcoming_departures,
    }

    return render(request, 'partials/dashboard_health_alerts.html', context)
