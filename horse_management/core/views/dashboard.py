"""
Dashboard views.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from ..permissions import feature_required
from django.db.models import (
    DecimalField, Exists, ExpressionWrapper, F, OuterRef, Q, Sum, Value,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from billing.models import ExtraCharge
from health.models import (
    BreedingRecord,
    FarrierVisit,
    Vaccination,
    VetVisit,
    WormEggCount,
    current_farrier_visits,
    current_vaccinations,
)

from invoicing.models import Invoice

from ..dashboard_widgets import GROUPS, WIDGETS_BY_KEY
from ..models import DashboardPreference, Horse, Location, Owner, Placement
from ..search import is_fuzzy_match

logger = logging.getLogger(__name__)


def _greeting():
    """Time-of-day greeting for the dashboard header."""
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _empty_context():
    """Safe zero-context used when the dashboard view errors out."""
    return {
        'greeting': _greeting(),
        'attention_count': 0,
        'all_caught_up': False,
        'total_horses': 0,
        'overdue_vax_count': 0,
        'overdue_invoice_count': 0,
        'vaccinations_due_count': 0,
        'outstanding_invoices_count': 0,
        'vaccinations_due': [],
        'farrier_due': [],
        'outstanding_invoices': [],
        'unbilled_total': 0,
        'activity': [],
        'field_rest': [],
        'pending_departures': [],
        'visible_widgets': {g: [] for g in GROUPS},
        'visible_keys': set(),
        'any_health_visible': False,
    }


@feature_required('dashboard')
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
    from billing.models import FeedOut

    pref = DashboardPreference.get_for(request.user)
    visible_widgets = pref.visible_ordered_keys_by_group()
    visible = {k for keys in visible_widgets.values() for k in keys}

    today = timezone.localdate()
    thirty_days = today + timedelta(days=30)
    two_weeks = today + timedelta(days=14)

    # Vaccinations: list used by both kpi_vaccinations_due (count) and list_vaccinations_due (rows).
    if 'kpi_vaccinations_due' in visible or 'list_vaccinations_due' in visible:
        # Includes overdue (no lower bound): an overdue vaccination is the
        # most urgent thing this list can show — it must not drop off the
        # dashboard the day it expires. Oldest overdue sorts first.
        # Latest record per (horse, type) only — superseded records keep a
        # past next_due_date forever and would show as permanently overdue.
        vaccinations_due = list(current_vaccinations(Vaccination.objects.filter(
            next_due_date__lte=thirty_days,
            horse__is_active=True,
        )).select_related('horse', 'vaccination_type').order_by('next_due_date')[:10])
    else:
        vaccinations_due = []

    # Outstanding invoices: shared by kpi_outstanding_invoices (count) and table_outstanding (rows).
    if 'kpi_outstanding_invoices' in visible or 'table_outstanding' in visible:
        outstanding_invoices = list(Invoice.objects.filter(
            status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE]
        ).select_related('owner').annotate(
            # Show what's still owed, not the face value — part-payments count.
            balance=ExpressionWrapper(
                F('total') - Coalesce(
                    Sum('payments__amount'), Value(Decimal('0.00'))
                ),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ).order_by('due_date')[:10])
    else:
        outstanding_invoices = []

    total_horses = 0
    if 'kpi_total_horses' in visible:
        total_horses = Horse.objects.filter(
            is_active=True, placements__end_date__isnull=True,
        ).distinct().count()

    unbilled_total = 0
    if 'kpi_unbilled_charges' in visible:
        unbilled_total = ExtraCharge.unbilled_total()

    farrier_due = []
    if 'list_farrier_due' in visible:
        # Includes overdue, same as vaccinations above.
        farrier_due = list(current_farrier_visits(FarrierVisit.objects.filter(
            next_due_date__lte=two_weeks,
            horse__is_active=True
        )).select_related('horse').order_by('next_due_date')[:10])

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

    # ── Field rest this year ────────────────────────────────────
    # Per-field days rested / with horses for the current calendar year, so the
    # yard can see grazing rotation at a glance. Sorted by rest days desc.
    # Two queries total (locations + their periods) — clip each period to the
    # year in Python rather than one query per field.
    field_rest = []
    if 'list_field_rest' in visible:
        from datetime import date
        from collections import defaultdict
        from ..models import LocationUsagePeriod

        year = today.year
        year_start, year_end = date(year, 1, 1), date(year, 12, 31)
        periods_by_loc = defaultdict(list)
        for p in LocationUsagePeriod.objects.filter(
            start_date__lte=year_end,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=year_start)):
            periods_by_loc[p.location_id].append(p)

        for loc in Location.objects.order_by('site', 'name'):
            rested = horses = 0
            for p in periods_by_loc.get(loc.pk, []):
                days = p.get_days_in_period(year_start, year_end)
                if p.usage == Location.Usage.RESTED:
                    rested += days
                elif p.usage == Location.Usage.HORSES:
                    horses += days
            if rested or horses:
                field_rest.append({
                    'location': loc, 'rested': rested, 'horses': horses,
                })
        field_rest.sort(key=lambda r: r['rested'], reverse=True)
        field_rest = field_rest[:8]

    # Pending departures (grouped by owner + date) for inline display
    pending_departures = []
    if 'pending_departures' in visible:
        # A horse is pending departure only when it is still flagged active
        # but no longer placed anywhere. Horses with an open placement must
        # never appear here: every past field move leaves a closed placement
        # behind, and confirming those rows would depart horses that are
        # still on the yard.
        has_open_placement = Placement.objects.filter(
            horse=OuterRef('horse'), end_date__isnull=True,
        )
        pending_placements = Placement.objects.filter(
            end_date__lte=today,
            horse__is_active=True,
        ).exclude(
            end_date__isnull=True,
        ).annotate(
            horse_is_placed=Exists(has_open_placement),
        ).filter(
            horse_is_placed=False,
        ).select_related('horse', 'owner', 'location').order_by('-end_date')
        # Keep only each horse's most recent closed placement — older ones
        # (from moves) are history, not separate departures.
        latest_by_horse = {}
        for p in pending_placements:
            latest_by_horse.setdefault(p.horse_id, p)
        pending_groups = {}
        ordered = sorted(
            latest_by_horse.values(),
            key=lambda p: (p.owner.name if p.owner else '', p.end_date),
        )
        for p in ordered:
            key = (p.owner_id, p.owner.name if p.owner else 'Unknown', p.end_date)
            if key not in pending_groups:
                pending_groups[key] = {'owner_name': key[1], 'date': p.end_date, 'horses': [], 'horse_ids': []}
            pending_groups[key]['horses'].append(p.horse)
            pending_groups[key]['horse_ids'].append(str(p.horse.pk))
        pending_departures = list(pending_groups.values())
    # Flat id list so the widget can offer one confirm-everything button
    pending_departure_ids = [
        pk for group in pending_departures for pk in group['horse_ids']
    ]

    # ── Header summary ──────────────────────────────────────────
    # Derived from lists already fetched for visible widgets — no extra
    # queries, and the count only reflects what's actually on the page.
    overdue_vax_count = sum(
        1 for v in vaccinations_due if v.next_due_date and v.next_due_date < today
    )
    overdue_invoice_count = sum(1 for i in outstanding_invoices if i.is_overdue)
    attention_count = overdue_vax_count + overdue_invoice_count

    # List widgets are enabled but every one of them is empty → "all caught
    # up" banner instead of a blank stretch of page. Each term is gated on
    # its own list widget because some lists are also fetched for KPIs.
    all_caught_up = bool(visible_widgets.get('list')) and not (
        ('pending_departures' in visible and pending_departures)
        or ('list_vaccinations_due' in visible and vaccinations_due)
        or ('list_farrier_due' in visible and farrier_due)
        or ('table_outstanding' in visible and outstanding_invoices)
        or ('recent_activity' in visible and activity)
        or ('list_field_rest' in visible and field_rest)
    )

    context = {
        'greeting': _greeting(),
        'attention_count': attention_count,
        'all_caught_up': all_caught_up,
        'total_horses': total_horses,
        'overdue_vax_count': overdue_vax_count,
        'overdue_invoice_count': overdue_invoice_count,
        'vaccinations_due': vaccinations_due,
        'vaccinations_due_count': len(vaccinations_due),
        'farrier_due': farrier_due,
        'outstanding_invoices': outstanding_invoices,
        'outstanding_invoices_count': len(outstanding_invoices),
        'unbilled_total': unbilled_total,
        'activity': activity,
        'field_rest': field_rest,
        'pending_departures': pending_departures,
        'pending_departure_ids': pending_departure_ids,
        'visible_widgets': visible_widgets,
        'visible_keys': visible,
        'any_health_visible': bool(visible_widgets.get('health')),
    }

    return render(request, 'dashboard.html', context)


@feature_required('dashboard')
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

    today = timezone.localdate()
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
        # Grid wrapper renders only when something is worth showing; an empty
        # response lets the dashboard's outerHTML swap remove the loader.
        'any_alerts': bool(
            ehv_due or high_egg_counts or vet_follow_ups or upcoming_departures
        ),
    }

    return render(request, 'partials/dashboard_health_alerts.html', context)


# ── Quick find ──────────────────────────────────────────────────────────────

QUICK_FIND_MIN_CHARS = 2
QUICK_FIND_PER_GROUP = 4


@feature_required('dashboard')
def quick_find(request):
    """HTMX partial: typo-tolerant search across horses, owners and locations.

    Same in-Python fuzzy matching as the list searches (core.search) — the
    dataset is a few hundred rows, so three values_list queries are cheap.
    """
    query = request.GET.get('q', '').strip()
    if len(query) < QUICK_FIND_MIN_CHARS:
        return HttpResponse('')

    from ..permissions import has_feature_access

    # Include departed horses (labelled) — searching by name for a horse
    # that left last month should still find its record. Groups the user's
    # role can't view are skipped entirely so hidden areas don't leak here.
    horses = []
    if has_feature_access(request.user, 'horses'):
        horses = sorted(
            (
                {'pk': pk, 'name': name, 'is_active': is_active}
                for pk, name, is_active in Horse.objects.values_list(
                    'pk', 'name', 'is_active'
                )
                if is_fuzzy_match(query, name)
            ),
            key=lambda h: not h['is_active'],  # active horses first
        )[:QUICK_FIND_PER_GROUP]

    owners = []
    if has_feature_access(request.user, 'owners'):
        owners = [
            {'pk': pk, 'name': name}
            for pk, name in Owner.objects.values_list('pk', 'name')
            if is_fuzzy_match(query, name)
        ][:QUICK_FIND_PER_GROUP]

    locations = []
    if has_feature_access(request.user, 'locations'):
        locations = [
            {'pk': pk, 'name': name, 'site': site}
            for pk, name, site in Location.objects.values_list('pk', 'name', 'site')
            if is_fuzzy_match(query, name) or is_fuzzy_match(query, site)
        ][:QUICK_FIND_PER_GROUP]

    return render(request, 'partials/dashboard/quick_find_results.html', {
        'query': query,
        'horses': horses,
        'owners': owners,
        'locations': locations,
        'has_results': bool(horses or owners or locations),
    })
