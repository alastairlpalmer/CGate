"""Views for the farrier page (docs/FARRIER_PAGE_PLAN.md, section 5).

Step 2 built the gate and the scoped pages. Step 3 adds Today's visit:
the horse list (due first, search, location filter) and the price panel
that records a line. Step 4 adds Finish visit, which bills every line
and sends the farrier and the yard a summary. Step 5 adds My prices (add,
edit, hide) and same-day changes to a finished visit. Every view here is wrapped in ``provider_page`` and
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
        'list_count': sum(1 for horse in due if horse.job),
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
@require_POST
def visit_finish(request):
    """Finish visit: bill every line and send the summaries.

    A second tap (or a slow network resending) finds no open visit and
    goes to the last finished visit instead of billing again.
    """
    provider = request.provider
    try:
        result = farrier.finish_visit(provider, request.user)
    except LineError as exc:
        messages.error(request, str(exc))
        return redirect('farrier:today')
    if result is None:
        last = farrier.visits(provider).filter(status='finished').first()
        if last is None:
            return redirect('farrier:today')
        return redirect(f"{reverse('farrier:visit_detail', args=[last.pk])}?done=1")
    visit, _ = result
    url = f"{reverse('farrier:visit_detail', args=[visit.pk])}?done=1"
    if getattr(request, 'htmx', False):
        from django_htmx.http import HttpResponseClientRedirect
        return HttpResponseClientRedirect(url)
    return redirect(url)


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
        'notes': visit.notes.select_related('horse').order_by('horse__name', 'created_at'),
        'done': request.GET.get('done') == '1' and not visit.is_open,
        'can_change': farrier.can_change_finished(visit),
        'email_to': farrier.farrier_email_address(provider),
    })


@provider_page
def price_list(request):
    """My prices: this farrier's own price list."""
    provider = request.provider
    return render(request, 'farrier/price_list.html', {
        'provider': provider,
        'prices': farrier.price_list(provider, active_only=False),
    })


def _price_form(request, provider, item=None):
    """Add or edit one of the farrier's prices (one view for both)."""
    values = {
        'name': item.name if item else '',
        'work_done': item.farrier_work if item else '',
        'price': f'{item.price:.2f}' if item else '',
    }
    error = ''
    if request.method == 'POST':
        values = {key: request.POST.get(key, '').strip() for key in values}
        try:
            farrier.save_price(
                provider, name=values['name'], work_done=values['work_done'],
                price=values['price'], item=item,
            )
        except LineError as exc:
            error = str(exc)
        else:
            messages.success(request, f"Saved {values['name']}.")
            return redirect('farrier:price_list')
    return render(request, 'farrier/price_form.html', {
        'provider': provider,
        'item': item,
        'values': values,
        'error': error,
        'work_types': FarrierVisit.WorkType.choices,
    })


@provider_page
def price_add(request):
    return _price_form(request, request.provider)


@provider_page
def price_edit(request, pk):
    return _price_form(request, request.provider, farrier.get_price(request.provider, pk))


@provider_page
@require_POST
def price_toggle(request, pk):
    """Hide a price from the visit screen, or show it again."""
    item = farrier.get_price(request.provider, pk)
    item.is_active = not item.is_active
    item.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f"{item.name} is {'shown' if item.is_active else 'hidden'}.")
    return redirect('farrier:price_list')


@provider_page
def line_edit(request, visit_pk, pk):
    """Change one line of a finished visit, on the same day."""
    provider = request.provider
    visit, line = farrier.get_finished_line(provider, visit_pk, pk)
    detail_url = reverse('farrier:visit_detail', args=[visit.pk])
    if not farrier.can_change_finished(visit):
        messages.error(request, "This visit can no longer be changed here. Ask the yard.")
        return redirect(detail_url)
    error = ''
    if request.method == 'POST':
        item_pk = request.POST.get('item', '')
        item = farrier.get_price(provider, int(item_pk)) if item_pk.isdigit() else None
        try:
            farrier.change_finished_line(
                provider, visit, line,
                item=item,
                work_done=request.POST.get('work_done') or None,
                price=request.POST.get('price') or None,
                note=request.POST.get('note', ''),
            )
        except LineError as exc:
            error = str(exc)
        else:
            messages.success(request, f"Changed {line.horse.name}. The yard's charge is updated.")
            return redirect(detail_url)
    return render(request, 'farrier/line_form.html', {
        'provider': provider,
        'visit': visit,
        'line': line,
        'prices': farrier.price_list(provider),
        'work_types': FarrierVisit.WorkType.choices,
        'error': error,
    })


@provider_page
@require_POST
def line_remove(request, visit_pk, pk):
    """Take one line off a finished visit, on the same day."""
    provider = request.provider
    visit, line = farrier.get_finished_line(provider, visit_pk, pk)
    name = line.horse.name
    try:
        farrier.remove_finished_line(provider, visit, line)
    except LineError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Removed {name} from this visit. The yard's charge is removed.")
    return redirect('farrier:visit_detail', visit.pk)


@provider_page
@require_POST
def horse_note(request, pk):
    """Leave a note on this horse, with no work and no charge."""
    provider = request.provider
    horse = _active_horse(pk)
    error = ''
    try:
        farrier.add_note(provider, request.user, horse, request.POST.get('text', ''))
    except LineError as exc:
        error = str(exc)
    if getattr(request, 'htmx', False):
        return _row_response(request, provider, horse.pk, open_panel=True, error=error)
    if error:
        messages.error(request, f"{horse.name}: {error}")
    else:
        messages.success(request, f"Note saved on {horse.name}.")
    return redirect(f"{reverse('farrier:today')}?open={horse.pk}#horse-{horse.pk}")


QR_BACKEND = 'core.auth_backends.FarrierQRBackend'


def who(request):
    """"Who is on the yard today?": the page the yard's QR code opens.

    The farrier picks their name and is signed in with no password, by the
    yard's choice (docs/FARRIER_PAGE_PLAN.md, section 15). Only farrier-only
    logins are offered (farrier.qr_sign_in_farriers). Every sign-in is in
    the audit log as "QR code, name picked", with the address it came from.
    """
    from django.contrib.auth import login

    if request.user.is_authenticated:
        # A farrier goes to Today's visit; staff get the page that
        # explains the farrier page (farrier/staff_notice.html).
        return redirect('farrier:today')
    farriers = farrier.qr_sign_in_farriers()
    error = ''
    if request.method == 'POST':
        chosen = next((p for p in farriers if str(p.pk) == request.POST.get('provider', '')), None)
        if chosen is None:
            error = 'Choose your name from the list.'
        else:
            login(request, chosen.user, backend=QR_BACKEND)
            return redirect('farrier:today')
    return render(request, 'farrier/who.html', {'farriers': farriers, 'error': error})
