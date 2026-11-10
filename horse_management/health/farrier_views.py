"""Views for the farrier page (docs/FARRIER_PAGE_PLAN.md, section 5).

Step 2 built the gate and the scoped pages. Step 3 adds Today's visit:
the horse list (due first, search, location filter) and the price panel
that records a line. Finish visit (step 4) and editing prices (step 5)
come next. Every view here is wrapped in ``provider_page`` and
reads data only through the scoped helpers in ``health.farrier``.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import Horse

from . import farrier
from .farrier import LineError, provider_page
from .models import FarrierVisit


def _location_id(request):
    value = request.GET.get('location', '')
    return int(value) if value.isdigit() else None


def _list_context(request, provider, open_pk=None):
    search = request.GET.get('q', '').strip()[:100]
    location_id = _location_id(request)
    due, others = farrier.horse_rows(provider, search=search, location_id=location_id)
    return {
        'provider': provider,
        'visit': farrier.open_visit(provider),
        'due_horses': due,
        'other_horses': others,
        'search': search,
        'location_id': location_id,
        'locations': farrier.visit_locations(),
        'open_pk': open_pk,
        'prices': farrier.price_list(provider),
        'work_types': FarrierVisit.WorkType.choices,
        # Others are folded unless the farrier is looking for a horse.
        'others_open': bool(search or location_id),
    }


@provider_page
def today(request):
    """Today's visit: the farrier's main screen.

    An HTMX request (search or location filter) gets only the horse list.
    ``?open=<horse id>`` opens that horse's price panel without script.
    """
    provider = request.provider
    open_pk = request.GET.get('open')
    open_pk = int(open_pk) if open_pk and open_pk.isdigit() else None
    context = _list_context(request, provider, open_pk=open_pk)
    if getattr(request, 'htmx', False):
        return render(request, 'farrier/_horse_list.html', context)
    return render(request, 'farrier/today.html', context)


def _active_horse(pk):
    return get_object_or_404(Horse, pk=pk, is_active=True)


def _row_response(request, provider, horse_pk, *, open_panel=False, error=''):
    """One horse row, plus the visit counter swapped out of band."""
    due, others = farrier.horse_rows(provider)
    horse = next((h for h in due + others if h.pk == horse_pk), None)
    if horse is None:
        return redirect('farrier:today')
    return render(request, 'farrier/_horse_row_response.html', {
        'provider': provider,
        'horse': horse,
        'open_pk': horse.pk if open_panel else None,
        'error': error,
        'prices': farrier.price_list(provider),
        'work_types': FarrierVisit.WorkType.choices,
        'visit': farrier.open_visit(provider),
    })


@provider_page
def horse_panel(request, pk):
    """Open (or, with ?close=1, close) one horse's price panel."""
    provider = request.provider
    horse = _active_horse(pk)
    open_panel = request.GET.get('close') != '1'
    if not getattr(request, 'htmx', False):
        url = reverse('farrier:today')
        return redirect(f'{url}?open={horse.pk}#horse-{horse.pk}' if open_panel else url)
    return _row_response(request, provider, horse.pk, open_panel=open_panel)


@provider_page
@require_POST
def horse_add(request, pk):
    """Add or change this horse's line on today's visit."""
    provider = request.provider
    horse = _active_horse(pk)
    item_pk = request.POST.get('item', '')
    item = farrier.get_price(provider, int(item_pk)) if item_pk.isdigit() else None
    error = ''
    try:
        farrier.record_line(
            provider, request.user, horse,
            item=item,
            work_done=request.POST.get('work_done') or None,
            price=request.POST.get('price') or None,
            note=request.POST.get('note', ''),
        )
    except LineError as exc:
        error = str(exc)
    if getattr(request, 'htmx', False):
        return _row_response(request, provider, horse.pk, open_panel=bool(error), error=error)
    if error:
        messages.error(request, f"{horse.name}: {error}")
        return redirect(f"{reverse('farrier:today')}?open={horse.pk}#horse-{horse.pk}")
    return redirect(f"{reverse('farrier:today')}#horse-{horse.pk}")


@provider_page
@require_POST
def horse_remove(request, pk):
    """Take this horse off today's visit."""
    provider = request.provider
    horse = _active_horse(pk)
    error = ''
    try:
        farrier.remove_line(provider, horse)
    except LineError as exc:
        error = str(exc)
    if getattr(request, 'htmx', False):
        return _row_response(request, provider, horse.pk, open_panel=bool(error), error=error)
    if error:
        messages.error(request, f"{horse.name}: {error}")
    return redirect(f"{reverse('farrier:today')}#horse-{horse.pk}")


@provider_page
def visit_list(request):
    """My visits: every visit this farrier has recorded."""
    provider = request.provider
    return render(request, 'farrier/visit_list.html', {
        'provider': provider,
        'visits': farrier.visits(provider).prefetch_related('farrier_visits'),
    })


@provider_page
def visit_detail(request, pk):
    """One visit, its lines and its total."""
    provider = request.provider
    visit = farrier.get_visit(provider, pk)
    return render(request, 'farrier/visit_detail.html', {
        'provider': provider,
        'visit': visit,
        'lines': visit.farrier_visits.select_related('horse').order_by('horse__name'),
    })


@provider_page
def price_list(request):
    """My prices: this farrier's own price list."""
    provider = request.provider
    return render(request, 'farrier/price_list.html', {
        'provider': provider,
        'prices': farrier.price_list(provider, active_only=False),
    })
