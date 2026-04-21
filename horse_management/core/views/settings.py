"""
Settings, rate types, and health check views.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..dashboard_widgets import GROUPS, WIDGETS, WIDGETS_BY_KEY
from ..mixins import staff_required
from ..models import DashboardPreference, Location


def health_check(request):
    """Lightweight DB ping. No auth required. Used by Vercel cron to keep Supabase awake."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@staff_required
def app_settings(request):
    """Unified settings page for integrations, providers, business config."""
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

    ctx = {
        'xero_connection': XeroConnection.get_connection(),
        'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
        'biz_form': biz_form,
        'rate_types': RateType.objects.all(),
        'vaccination_types': VaccinationType.objects.all(),
        'locations': Location.objects.order_by('site', 'name'),
    }
    ctx.update(_prefs_context(request.user))
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


def _prefs_context(user):
    """Build the template context for the dashboard prefs UI."""
    pref = DashboardPreference.get_for(user)
    layout = pref.resolved_layout()
    groups = []
    for g in GROUPS:
        items = []
        for w in WIDGETS:
            if w['group'] != g:
                continue
            meta = layout[w['key']]
            items.append({
                'key': w['key'],
                'name': w['name'],
                'group': g,
                'visible': meta['visible'],
                'order': meta['order'],
            })
        items.sort(key=lambda x: x['order'])
        groups.append({'key': g, 'name': g.title(), 'items': items})
    return {'groups': groups}


@login_required
def dashboard_preferences(request):
    """Standalone per-user dashboard preferences page (GET only).

    Available to all authenticated users. Staff see the same UI embedded on
    the main settings page; non-staff visit this page directly.
    """
    return render(request, 'settings/dashboard_preferences.html', _prefs_context(request.user))


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


@login_required
@require_POST
def dashboard_reorder(request):
    """Reorder widgets within a single group for the current user.

    Accepts either:
      - `group=<g>` + `order` (repeated POST field or JSON array) — full new
        ordering of that group's keys. Unknown keys are ignored; missing keys
        keep their existing order after the provided ones.
      - `key=<k>` + `direction=up|down` — keyboard accessibility fallback:
        move one item up/down within its group.
    """
    pref = DashboardPreference.get_for(request.user)
    layout = pref.resolved_layout()

    key = request.POST.get('key', '').strip()
    direction = request.POST.get('direction', '').strip().lower()
    if key and direction in ('up', 'down'):
        if key not in WIDGETS_BY_KEY:
            return HttpResponseBadRequest("unknown widget")
        group = WIDGETS_BY_KEY[key]['group']
        sibling_keys = sorted(
            (k for k, w in WIDGETS_BY_KEY.items() if w['group'] == group),
            key=lambda k: layout[k]['order'],
        )
        idx = sibling_keys.index(key)
        swap_idx = idx - 1 if direction == 'up' else idx + 1
        if 0 <= swap_idx < len(sibling_keys):
            other = sibling_keys[swap_idx]
            layout[key]['order'], layout[other]['order'] = (
                layout[other]['order'], layout[key]['order'],
            )
            pref.layout = layout
            pref.save(update_fields=['layout', 'updated_at'])
        return HttpResponse(status=204)

    group = request.POST.get('group', '').strip()
    if group not in GROUPS:
        return HttpResponseBadRequest("unknown group")

    order_param = request.POST.getlist('order') or request.POST.getlist('order[]')
    if not order_param:
        # Allow a JSON body too.
        raw = request.POST.get('order_json') or request.body.decode('utf-8', errors='ignore')
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    order_param = parsed
                elif isinstance(parsed, dict) and isinstance(parsed.get('order'), list):
                    order_param = parsed['order']
            except json.JSONDecodeError:
                pass
    if not order_param:
        return HttpResponseBadRequest("missing 'order'")

    # Only accept keys that belong to this group.
    group_keys = [k for k, w in WIDGETS_BY_KEY.items() if w['group'] == group]
    group_key_set = set(group_keys)
    new_order = [k for k in order_param if k in group_key_set]
    # Append any group keys not mentioned, preserving their existing relative order.
    missing = [k for k in group_keys if k not in new_order]
    missing.sort(key=lambda k: layout[k]['order'])
    new_order += missing

    # Assign order values contiguous within the group, preserving cross-group
    # ordering by reusing the existing min-order of that group's keys.
    existing_orders = sorted(layout[k]['order'] for k in group_keys)
    for new_slot, k in zip(existing_orders, new_order):
        layout[k]['order'] = new_slot

    pref.layout = layout
    pref.save(update_fields=['layout', 'updated_at'])
    return HttpResponse(status=204)
