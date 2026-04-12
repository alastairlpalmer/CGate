"""
Settings, rate types, and health check views.
"""

from django.contrib import messages
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..mixins import staff_required
from ..models import Location


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

    return render(request, 'settings.html', {
        'xero_connection': XeroConnection.get_connection(),
        'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
        'biz_form': biz_form,
        'rate_types': RateType.objects.all(),
        'vaccination_types': VaccinationType.objects.all(),
        'locations': Location.objects.order_by('site', 'name'),
    })


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
