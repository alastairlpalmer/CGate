"""
Settings, rate types, and health check views.
"""

import logging
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection, models
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..dashboard_widgets import WIDGETS, WIDGETS_BY_KEY, widget_available
from ..permissions import feature_required, has_feature_access
from ..models import DashboardPreference, Location

logger = logging.getLogger(__name__)


def _location_groups(queryset):
    """Group locations by site with the archive/delete state each row needs.

    Working the counts out here keeps them out of the template and turns
    what would be several queries per row into one.
    """
    locations = list(
        queryset.annotate(
            placement_count=models.Count('placements', distinct=True),
            feed_out_count=models.Count('feed_outs', distinct=True),
            live_horse_count=models.Count(
                'placements__horse',
                filter=models.Q(placements__end_date__isnull=True),
                distinct=True,
            ),
        )
    )
    for loc in locations:
        loc.can_be_deleted = not (loc.placement_count or loc.feed_out_count)
        loc.can_be_archived = not loc.is_archived and not loc.live_horse_count

    groups = []
    for site, rows in groupby(locations, key=lambda l: l.site):
        rows = list(rows)
        groups.append({
            'site': site,
            'locations': rows,
            # A site is only deletable when every one of its fields is.
            'can_be_deleted': all(r.can_be_deleted for r in rows),
            'can_be_archived': any(r.can_be_archived for r in rows),
        })
    return groups


def health_check(request):
    """What an uptime monitor calls. No sign-in. 503 when a part is down.

    Checks the database, Redis (the task queue), and that a scheduled task
    has finished in the last SCHEDULED_TASK_STALE_HOURS, which catches a
    beat or worker that has stopped. The response names each part and its
    state, and nothing more: it is public.
    """
    checks = {
        'database': _check_database(),
        'redis': _check_redis(),
        'scheduled_tasks': _check_scheduled_tasks(),
    }
    healthy = all(state in ('ok', 'skipped', 'none yet') for state in checks.values())
    return JsonResponse(
        {'status': 'ok' if healthy else 'error', 'checks': checks},
        status=200 if healthy else 503,
    )


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception('Health check: database')
        return 'error'
    return 'ok'


def _check_redis():
    url = settings.CELERY_BROKER_URL
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False) or not url.startswith(('redis://', 'rediss://')):
        return 'skipped'
    import redis

    try:
        redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()
    except redis.RedisError:
        logger.exception('Health check: redis')
        return 'error'
    return 'ok'


def _check_scheduled_tasks():
    from datetime import timedelta

    from django.utils import timezone

    from ..models import ScheduledRun

    if 'django_celery_beat' not in settings.INSTALLED_APPS:
        return 'skipped'  # No worker on this host (Vercel).
    try:
        latest = (ScheduledRun.objects.filter(status=ScheduledRun.Status.SUCCESS)
                  .order_by('-finished_at').values_list('finished_at', flat=True).first())
    except Exception:
        logger.exception('Health check: scheduled tasks')
        return 'error'
    if latest is None:
        return 'none yet'  # A new deploy that has not reached its first run.
    limit = timedelta(hours=settings.SCHEDULED_TASK_STALE_HOURS)
    return 'ok' if timezone.now() - latest <= limit else 'stale'


@login_required
def app_settings(request):
    """Unified settings page.

    Every user gets the dashboard-preferences and account cards. The admin
    cards are assembled per feature: business config/rates/locations/
    providers/integrations need Business settings access; the Users & Roles
    card needs Users & Roles access. Templates hide the cards, but the POST
    branch re-checks — hiding isn't enforcement.
    """
    ctx = {'flat_items': _flat_prefs_items(request.user)}

    if has_feature_access(request.user, 'settings', 'full'):
        from billing.models import ServiceItem, ServiceProvider
        from health.models import VaccinationType

        from ..forms import BusinessSettingsForm
        from ..models import BusinessSettings, RateType

        business = BusinessSettings.get_settings()
        if request.method == 'POST' and 'save_business' in request.POST:
            biz_form = BusinessSettingsForm(request.POST, instance=business)
            if biz_form.is_valid():
                biz_form.save()
                messages.success(request, "Business settings saved.")
                return redirect('app_settings')
        else:
            biz_form = BusinessSettingsForm(instance=business)

        ctx.update({
            'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
            'biz_form': biz_form,
            'rate_types': RateType.objects.all(),
            # Grouped in the order the work happens (Category order), not
            # alphabetically by code.
            'service_items': sorted(
                ServiceItem.objects.select_related('vaccination_type'),
                key=lambda i: (ServiceItem.Category.values.index(i.category), i.sort_order, i.name),
            ),
            'vaccination_types': VaccinationType.objects.all(),
            'location_groups': _location_groups(
                Location.objects.active().order_by('site', 'name')
            ),
            'archived_location_groups': _location_groups(
                Location.objects.archived().order_by('site', 'name')
            ),
        })

    if has_feature_access(request.user, 'xero', 'full'):
        from xero_integration.models import XeroConnection
        ctx['xero_connection'] = XeroConnection.get_connection()

    if has_feature_access(request.user, 'users', 'full'):
        from django.contrib.auth import get_user_model
        from ..models import Role

        ctx.update({
            'app_users': (
                get_user_model().objects
                .select_related('role_assignment__role')
                .order_by('-is_active', 'first_name', 'username')
            ),
            'roles': Role.objects.annotate(
                member_count=models.Count('assignments')
            ).order_by('-is_system', 'name'),
        })

    from .. import mfa
    ctx['can_view_audit_log'] = mfa.is_administrator(request.user)
    if ctx['can_view_audit_log']:
        from ..models import FailedTask
        ctx['failed_task_count'] = FailedTask.objects.filter(resolved_at__isnull=True).count()

    return render(request, 'settings.html', ctx)


@feature_required('settings')
def rate_type_create(request):
    """Create a new rate type."""
    from ..forms import RateTypeForm
    if request.method == 'POST':
        form = RateTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Rate type added.")
            return redirect('app_settings')
    else:
        form = RateTypeForm()
    return render(request, 'settings/rate_type_form.html', {'form': form})


@feature_required('settings')
def rate_type_update(request, pk):
    """Edit a rate type."""
    from ..forms import RateTypeForm
    from ..models import RateType
    rate = get_object_or_404(RateType, pk=pk)
    if request.method == 'POST':
        form = RateTypeForm(request.POST, instance=rate)
        if form.is_valid():
            form.save()
            messages.success(request, "Rate type updated.")
            return redirect('app_settings')
    else:
        form = RateTypeForm(instance=rate)
    return render(request, 'settings/rate_type_form.html', {'form': form, 'object': rate})


@feature_required('settings')
def service_item_create(request):
    """Add a priced service to the directory."""
    from billing.forms import ServiceItemForm
    if request.method == 'POST':
        form = ServiceItemForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Service added.")
            return redirect(f"{reverse('app_settings')}#services")
    else:
        form = ServiceItemForm()
    return render(request, 'settings/service_item_form.html', {'form': form})


@feature_required('settings')
def service_item_update(request, pk):
    """Edit a priced service (change the price, retire it, and so on)."""
    from billing.forms import ServiceItemForm
    from billing.models import ServiceItem
    item = get_object_or_404(ServiceItem, pk=pk)
    if request.method == 'POST':
        form = ServiceItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Service updated.")
            return redirect(f"{reverse('app_settings')}#services")
    else:
        form = ServiceItemForm(instance=item)
    return render(request, 'settings/service_item_form.html', {'form': form, 'object': item})


# ──────────────────────────────────────────────────────────────────────────
# Per-user dashboard preferences
# ──────────────────────────────────────────────────────────────────────────


def _flat_prefs_items(user):
    """Ordered list of {key, name, visible} for the widgets this user can see.

    Order follows each widget's stored ``order`` (default = registry order).
    Widgets for feature areas the user's role hides are omitted — there's no
    point toggling a widget that can never render.
    """
    from ..permissions import access_map
    levels = access_map(user)
    pref = DashboardPreference.get_for(user)
    layout = pref.resolved_layout()
    items = []
    for w in WIDGETS:
        if levels[w['feature']] == 'hidden' or not widget_available(w):
            continue
        meta = layout.get(w['key']) or {}
        items.append({
            'key': w['key'],
            'name': w['name'],
            'visible': bool(meta.get('visible', True)),
            'order': int(meta.get('order', 0)),
        })
    items.sort(key=lambda x: x['order'])
    return items


@login_required
@require_POST
def dashboard_toggle(request):
    """Toggle a single widget's visibility for the current user."""
    key = request.POST.get('key', '').strip()
    visible_raw = request.POST.get('visible', '').strip().lower()

    if key not in WIDGETS_BY_KEY:
        return HttpResponseBadRequest("unknown widget")
    if visible_raw not in ('true', 'false', '1', '0', 'on', 'off'):
        return HttpResponseBadRequest("invalid 'visible' value")
    visible = visible_raw in ('true', '1', 'on')

    pref = DashboardPreference.get_for(request.user)
    layout = pref.resolved_layout()
    layout[key]['visible'] = visible
    pref.layout = layout
    pref.save(update_fields=['layout', 'updated_at'])
    return HttpResponse(status=204)
