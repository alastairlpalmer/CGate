"""
Settings, rate types, and health check views.
"""

from django.contrib import messages
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..mixins import staff_required
from ..models import Location

SETTINGS_TABS = [
    ('business', 'Business'),
    ('invoicing', 'Invoicing'),
    ('yard', 'Yard & Health'),
    ('locations', 'Locations'),
    ('rates', 'Rates'),
    ('providers', 'Providers'),
    ('integrations', 'Integrations'),
    ('account', 'Account'),
]


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
    from ..models import BusinessSettings, RateType, SettingsChangeLog

    business = BusinessSettings.get_settings()
    active_tab = 'business'

    if request.method == 'POST' and 'save_business' in request.POST:
        active_tab = request.POST.get('active_tab', 'business')
        biz_form = BusinessSettingsForm(request.POST, request.FILES, instance=business)
        if biz_form.is_valid():
            _save_with_audit(biz_form, request.user)
            messages.success(request, "Settings saved.")
            return redirect(f"{request.path}?tab={active_tab}")
    else:
        active_tab = request.GET.get('tab', 'business')
        biz_form = BusinessSettingsForm(instance=business)

    return render(request, 'settings.html', {
        'tabs': SETTINGS_TABS,
        'active_tab': active_tab,
        'business': business,
        'xero_connection': XeroConnection.get_connection(),
        'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
        'biz_form': biz_form,
        'rate_types': RateType.objects.all(),
        'vaccination_types': VaccinationType.objects.all(),
        'locations': Location.objects.order_by('site', 'name'),
        'change_log': SettingsChangeLog.objects.select_related('changed_by')[:20],
    })


def _save_with_audit(form, user):
    """Save a BusinessSettingsForm and write a SettingsChangeLog entry per changed field."""
    from ..models import SettingsChangeLog

    instance = form.instance
    changed_fields = form.changed_data

    # Capture old values before save
    old_values = {}
    for field_name in changed_fields:
        old_val = getattr(instance, field_name, '')
        old_values[field_name] = str(old_val) if old_val is not None else ''

    form.save()

    # Reload to get new values
    instance.refresh_from_db()
    log_entries = []
    for field_name in changed_fields:
        new_val = getattr(instance, field_name, '')
        # Skip binary/file fields — just log that they changed
        if hasattr(new_val, 'name'):
            new_display = new_val.name or '(cleared)'
        else:
            new_display = str(new_val) if new_val is not None else ''
        log_entries.append(SettingsChangeLog(
            changed_by=user,
            field_name=field_name,
            old_value=old_values.get(field_name, ''),
            new_value=new_display,
        ))
    if log_entries:
        SettingsChangeLog.objects.bulk_create(log_entries)


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
