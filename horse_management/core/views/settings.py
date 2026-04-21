"""
Settings, rate types, and health check views.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..dashboard_widgets import WIDGETS, WIDGETS_BY_KEY
from ..mixins import staff_required
from ..models import DashboardPreference, Location


def health_check(request):
    """Lightweight DB ping. No auth required. Used by Vercel cron to keep Supabase awake."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@login_required
def app_settings(request):
    """Unified settings page.

    Non-staff users see only their dashboard preferences and account card.
    Staff users additionally see business config, rates, locations, providers,
    integrations, etc. — gated in the template via ``{% if user.is_staff %}``.
    """
    ctx = {'flat_items': _flat_prefs_items(request.user)}

    if request.user.is_staff:
        from billing.models import ServiceProvider
        from health.models import VaccinationType
        from xero_integration.models import XeroConnection

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
            'xero_connection': XeroConnection.get_connection(),
            'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
            'biz_form': biz_form,
            'rate_types': RateType.objects.all(),
            'vaccination_types': VaccinationType.objects.all(),
            'locations': Location.objects.order_by('site', 'name'),
        })

    return render(request, 'settings.html', ctx)


@staff_required
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


@staff_required
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
    """Ordered list of {key, name, visible} for all registered widgets.

    Order follows each widget's stored ``order`` (default = registry order).
    """
    pref = DashboardPreference.get_for(user)
    layout = pref.resolved_layout()
    items = []
    for w in WIDGETS:
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
