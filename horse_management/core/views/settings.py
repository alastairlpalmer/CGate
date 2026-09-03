"""
Settings, rate types, and health check views.
"""

from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection, models
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..dashboard_widgets import WIDGETS, WIDGETS_BY_KEY
from ..permissions import feature_required, has_feature_access
from ..models import DashboardPreference, Location


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
    """Lightweight DB ping. No auth required. Used by Vercel cron to keep Supabase awake."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


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
        from billing.models import ServiceProvider
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
        if levels[w['feature']] == 'hidden':
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
