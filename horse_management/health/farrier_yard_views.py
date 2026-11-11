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
        'notes': visit.notes.select_related('horse').order_by('horse__name', 'created_at'),
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


# --- The yard's list for a farrier visit (plan section 14) ----------------------

def _list_day(value):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return timezone.localdate()


@feature_required('health', LEVEL_VIEW)
def farrier_job_list(request):
    """The list of horses and work the yard wants on a farrier visit."""
    from datetime import timedelta

    from core.models import Horse

    from .models import FarrierVisit

    providers = list(_farrier_providers().filter(is_active=True))
    provider_id = request.GET.get('provider') or request.POST.get('provider') or ''
    provider = next((p for p in providers if str(p.pk) == provider_id), providers[0] if providers else None)
    day = _list_day(request.GET.get('date') or request.POST.get('date'))
    can_edit = has_feature_access(request.user, 'health', LEVEL_FULL)

    if request.method == 'POST':
        if not can_edit or provider is None:
            messages.error(request, "You cannot change the farrier's list.")
        else:
            horse = Horse.objects.filter(pk=request.POST.get('horse') or 0).first()
            if horse is None:
                messages.error(request, 'Choose a horse.')
            else:
                try:
                    farrier_yard.add_job(
                        provider, day, horse,
                        work_needed=request.POST.get('work_needed', ''),
                        note=request.POST.get('note', ''), user=request.user,
                    )
                    messages.success(request, f"{horse.name} is on the list for {day:%-d %b}.")
                except farrier_yard.JobError as exc:
                    messages.error(request, str(exc))
        url = reverse('farrier_job_list')
        return redirect(f"{url}?provider={provider.pk if provider else ''}&date={day.isoformat()}")

    rows = farrier_yard.job_rows(provider, day) if provider else []
    listed = {row.job.horse_id for row in rows}
    return render(request, 'health/farrier_job_list.html', {
        'providers': providers,
        'provider': provider,
        'day': day,
        'previous_day': day - timedelta(days=1),
        'next_day': day + timedelta(days=1),
        'today': timezone.localdate(),
        'rows': rows,
        'horses': Horse.objects.filter(is_active=True).exclude(pk__in=listed).only('pk', 'name').order_by('name'),
        'work_types': FarrierVisit.WorkType.choices,
        'can_edit': can_edit,
        'upcoming': farrier_yard.upcoming_list_dates(provider) if provider else [],
    })


@feature_required('health', LEVEL_FULL)
@require_POST
def farrier_job_remove(request, pk):
    from .models import FarrierJob

    job = get_object_or_404(FarrierJob, pk=pk)
    name, provider_pk, day = job.horse.name, job.provider_id, job.date
    job.delete()
    messages.success(request, f"{name} is off the list.")
    return redirect(f"{reverse('farrier_job_list')}?provider={provider_pk}&date={day.isoformat()}")


# --- The Farrier page: one place for all farrier work ----------------------------

def farrier_hub(request):
    """The Farrier page, Records tab: every farrier record, any farrier.

    The page's other tabs are the visits from the farrier page and the
    farrier's list (their own views, with the same header). Health: view
    opens this tab; someone with only Charges: view lands on the visits.
    """
    from django.contrib.auth.views import redirect_to_login

    from core.permissions import deny

    from .views import farrier_records_context

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not has_feature_access(request.user, 'health', LEVEL_VIEW):
        if has_feature_access(request.user, 'charges', LEVEL_VIEW):
            return redirect('provider_visit_list')
        return deny(request)
    context = farrier_records_context(request, timezone.localdate())
    context.update({
        'hub_tab': 'records',
        'records_url': reverse('farrier_list'),
        'pagination_url': reverse('farrier_list'),
    })
    if getattr(request, 'htmx', False) and request.headers.get('HX-Target') == 'health-table-area':
        return render(request, 'health/partials/farrier_content.html', context)
    return render(request, 'health/farrier_hub.html', context)
