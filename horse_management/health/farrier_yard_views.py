"""Staff pages for farrier visits made on the farrier page (plan section 6).

Gated by the staff features: Charges to see visits and check them, Costs
to see and pay the monthly statement, Settings to print the QR code.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import ServiceProvider
from core.permissions import LEVEL_FULL, LEVEL_VIEW, feature_required, has_feature_access
from core.templatetags.ui_extras import gbp

from . import farrier_yard
from .models import ProviderVisit

STATUS_FILTERS = {
    'unchecked': ('To check', lambda qs: qs.filter(status='finished', checked_at__isnull=True)),
    'unpaid': ('Not paid yet', lambda qs: qs.filter(status='finished', paid_on__isnull=True)),
    'open': ('Open now', lambda qs: qs.filter(status='open')),
}


def _farrier_providers():
    return ServiceProvider.objects.filter(
        provider_type=ServiceProvider.ProviderType.FARRIER,
    ).order_by('name')


@feature_required('charges', LEVEL_VIEW)
def provider_visit_list(request):
    """Every visit recorded on the farrier page, newest first."""
    visits = ProviderVisit.objects.select_related('provider').prefetch_related('farrier_visits')
    provider_id = request.GET.get('provider', '')
    if provider_id.isdigit():
        visits = visits.filter(provider_id=int(provider_id))
    status = request.GET.get('status', '')
    if status in STATUS_FILTERS:
        visits = STATUS_FILTERS[status][1](visits)
    visits = list(visits[:200])
    for visit in visits:
        visit.line_count = len(visit.farrier_visits.all())
        visit.line_total = sum((r.cost for r in visit.farrier_visits.all()), 0)
    today = timezone.localdate()
    return render(request, 'health/provider_visit_list.html', {
        'visits': visits,
        'providers': _farrier_providers(),
        'provider_id': int(provider_id) if provider_id.isdigit() else None,
        'status': status,
        'status_filters': [(key, label) for key, (label, _) in STATUS_FILTERS.items()],
        'this_month': (today.year, today.month),
        'can_pay': has_feature_access(request.user, 'costs', LEVEL_VIEW),
    })


@feature_required('charges', LEVEL_VIEW)
def provider_visit_detail(request, pk):
    """One visit: each line, who it bills, and the Checked button."""
    visit = get_object_or_404(ProviderVisit.objects.select_related('provider', 'checked_by'), pk=pk)
    return render(request, 'health/provider_visit_detail.html', {
        'visit': visit,
        'lines': farrier_yard.yard_lines(visit),
        'can_check': has_feature_access(request.user, 'charges', LEVEL_FULL),
        'can_edit_records': has_feature_access(request.user, 'health', LEVEL_FULL),
        'can_pay': has_feature_access(request.user, 'costs', LEVEL_VIEW),
    })


@feature_required('charges', LEVEL_FULL)
@require_POST
def provider_visit_check(request, pk):
    """Mark a finished visit as checked by the yard."""
    visit = get_object_or_404(ProviderVisit, pk=pk)
    try:
        farrier_yard.mark_checked(visit, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Checked. {visit.provider.name} can no longer change this visit.")
    return redirect('provider_visit_detail', visit.pk)


@feature_required('costs', LEVEL_VIEW)
def provider_statement(request, provider_pk, year, month):
    """One farrier's month: visits, total, and Record payment to farrier."""
    provider = get_object_or_404(ServiceProvider, pk=provider_pk)
    if not 1 <= month <= 12:
        return redirect('provider_visit_list')
    stmt = farrier_yard.statement(provider, year, month)
    return render(request, 'health/provider_statement.html', {
        'statement': stmt,
        'provider': provider,
        'months': farrier_yard.months_with_visits(provider),
        'can_pay': has_feature_access(request.user, 'costs', LEVEL_FULL),
    })


@feature_required('costs', LEVEL_FULL)
@require_POST
def provider_statement_pay(request, provider_pk, year, month):
    """Record one payment for the month's unpaid visits, as a yard cost."""
    provider = get_object_or_404(ServiceProvider, pk=provider_pk)
    try:
        cost = farrier_yard.record_payment(provider, year, month)
    except farrier_yard.NothingToPay as exc:
        messages.info(request, str(exc))
    else:
        messages.success(
            request,
            f"Recorded {gbp(cost.amount)} paid to {provider.name}, saved as a yard cost.",
        )
    return redirect('provider_statement', provider.pk, year, month)


@feature_required('settings', LEVEL_FULL)
def farrier_qr(request):
    """A printable sheet with the QR code that opens the farrier page."""
    from core.models import BusinessSettings

    url = request.build_absolute_uri(reverse('farrier:today'))
    return render(request, 'health/farrier_qr.html', {
        'url': url,
        'qr_svg': farrier_yard.qr_svg(url),
        'business': BusinessSettings.get_settings(),
    })
