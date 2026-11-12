"""Horses in stables (docs/BARNS_AND_STABLES_PLAN.md, phase 2a).

Setting a barn up — its stables, the gaps between them — is in
``locations.py`` and needs the ``locations`` feature. Everything here is
about a *horse*: which stable it is in. That is a horse move, so it needs
``horses`` at full access, as ``horse_move`` does, plus enough of
``locations`` to see the barn at all.

Every form lives in the pop-up sheet. A save answers 204 + ``popup:saved``:
the sheet closes and the barn redraws beneath it, with the message — and
its Undo — showing on that redraw.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import HorseStableForm, SwapStableForm
from ..models import Horse, Location, Placement, Stable, StableStay
from ..permissions import LEVEL_VIEW, feature_required
from ..stables import StableOccupied, StableService, open_stay, picker_options
from ._next import safe_next
from ._popup import is_popup_request, popup_saved_response
from ._undo import undo_tag

CLASH = (
    "That change clashed with another update to the same horse. "
    "Refresh and check where it is before trying again."
)


def _require_barns():
    if not settings.BARNS_ENABLED:
        raise Http404


def horse_moves(view):
    """A stable change: ``horses`` to make it, ``locations`` to see where."""
    return feature_required('horses')(feature_required('locations', LEVEL_VIEW)(view))


def _barn_of(horse):
    """The barn the horse lives in, or None."""
    placement = (
        Placement.objects.filter(horse=horse, end_date__isnull=True)
        .select_related('location').first()
    )
    if placement is None or not placement.location.is_barn:
        return None
    return placement.location


def _back(request, fallback):
    return safe_next(request, '') or fallback


def _done(request, back):
    return popup_saved_response() if is_popup_request(request) else redirect(back)


def _undo_url(stay, previous_stable=None):
    url = reverse('stable_stay_undo', args=[stay.pk])
    if previous_stable is not None:
        url += '?' + urlencode({'previous': previous_stable.pk})
    return url


def _say_assigned(request, horse, stay, previous):
    """The success message for a stable change, with its Undo."""
    if previous is not None and previous.stable_id != stay.stable_id:
        text = f"{horse.name} moved from stable {previous.stable.label} to stable {stay.stable.label}."
    else:
        text = f"{horse.name} is in stable {stay.stable.label}."
    messages.success(
        request, text,
        extra_tags=undo_tag(_undo_url(stay, previous.stable if previous else None)),
    )


@feature_required('locations', LEVEL_VIEW)
def stable_options(request):
    """The stable picker for one location, for a move or arrival form.

    The form's location select fetches this when it changes, so the
    parameter is named after whichever field that is. Empty for a location
    with no stables to offer: the picker then takes up no room.
    """
    _require_barns()
    chosen = request.GET.get('new_location') or request.GET.get('location') or ''
    location = (
        Location.objects.active().filter(pk=chosen).first() if chosen.isdigit() else None
    )
    return render(request, 'locations/partials/stable_options.html', {
        'picker': picker_options(location),
    })


@feature_required('locations', LEVEL_VIEW)
def stable_panel(request, pk, stable_pk):
    """One stable: who is in it, and what can be done about it.

    Opens in the pop-up sheet from a cell of the plan or a row of the
    list. Anyone who can see the barn can open it; the actions inside are
    offered by what the reader may do.
    """
    _require_barns()
    barn = get_object_or_404(Location.objects.active(), pk=pk, location_type=Location.LocationType.BARN)
    stable = get_object_or_404(Stable, pk=stable_pk, location=barn, is_gap=False)
    if not is_popup_request(request):
        return redirect(f"{reverse('location_detail', args=[barn.pk])}?stable={stable.pk}")

    stays = list(
        StableStay.objects.filter(stable=stable, end_date__isnull=True)
        .select_related('horse').order_by('horse__name')
    )
    today = timezone.localdate()
    for stay in stays:
        stay.days = (today - stay.start_date).days
    in_stables = StableStay.objects.filter(
        stable__location=barn, end_date__isnull=True,
    ).values('horse_id')
    waiting = Horse.objects.filter(
        placements__location=barn, placements__end_date__isnull=True, is_active=True,
    ).exclude(pk__in=in_stables).distinct().order_by('name')
    return render(request, 'locations/partials/stable_panel.html', {
        'location': barn,
        'stable': stable,
        'stays': stays,
        'waiting': waiting,
        'history': (
            StableStay.objects.filter(stable=stable, end_date__isnull=False)
            .select_related('horse').order_by('-end_date', '-pk')[:5]
        ),
        'can_swap': StableStay.objects.filter(
            stable__location=barn, end_date__isnull=True,
        ).exclude(stable=stable).exists(),
    })


@horse_moves
@require_POST
def stable_assign(request, pk, stable_pk):
    """Put a horse in this stable, today. The one-tap path from the panel."""
    _require_barns()
    barn = get_object_or_404(Location.objects.active(), pk=pk, location_type=Location.LocationType.BARN)
    stable = get_object_or_404(Stable, pk=stable_pk, location=barn)
    horse = get_object_or_404(Horse, pk=request.POST.get('horse') or 0)
    back = reverse('location_detail', args=[barn.pk])
    previous = open_stay(horse)
    try:
        stay = StableService.assign(
            horse, stable, share=request.POST.get('share') == '1',
        )
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    except IntegrityError:
        messages.error(request, CLASH)
    else:
        _say_assigned(request, horse, stay, previous)
    return _done(request, back)


@horse_moves
def horse_stable(request, pk):
    """Choose a stable for a horse that is in a barn (or change the one it has)."""
    _require_barns()
    horse = get_object_or_404(Horse, pk=pk)
    barn = _barn_of(horse)
    back = _back(request, reverse('horse_detail', args=[horse.pk]))
    if barn is None:
        messages.error(request, f"{horse.name} is not in a barn, so it has no stable to choose.")
        return _done(request, back)

    previous = open_stay(horse)
    initial = {'day': timezone.localdate()}
    wanted = request.GET.get('stable', '')
    if wanted.isdigit():
        initial['stable'] = wanted
    form = HorseStableForm(request.POST or None, horse=horse, barn=barn, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            stay = StableService.assign(
                horse, form.cleaned_data['stable'], form.cleaned_data['day'],
                share=bool(form.cleaned_data.get('share_stable')),
            )
        except StableOccupied as e:
            form.add_error('share_stable' if 'share_stable' in form.fields else None, e)
        except ValidationError as e:
            form.add_error(None, e)
        except IntegrityError:
            form.add_error(None, CLASH)
        else:
            _say_assigned(request, horse, stay, previous)
            return _done(request, back)

    if not is_popup_request(request):
        return redirect(back)
    return render(request, 'includes/popup_form.html', {
        'form': form,
        'in_popup': True,
        'popup_submit_label': 'Change stable' if previous else 'Put in stable',
    })


@horse_moves
@require_POST
def horse_stable_vacate(request, pk):
    """Take a horse out of its stable. It stays in the barn."""
    _require_barns()
    horse = get_object_or_404(Horse, pk=pk)
    back = _back(request, reverse('horse_detail', args=[horse.pk]))
    try:
        ended = StableService.vacate(horse)
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    else:
        if ended is None:
            messages.info(request, f"{horse.name} has no stable.")
        else:
            messages.success(
                request,
                f"{horse.name} is out of stable {ended.stable.label}. "
                f"It is still in {ended.stable.location.name}.",
            )
    return _done(request, back)


@horse_moves
def horse_stable_swap(request, pk):
    """Swap this horse's stable with another horse's in the same barn."""
    _require_barns()
    horse = get_object_or_404(Horse, pk=pk)
    stay = open_stay(horse)
    back = _back(request, reverse('horse_detail', args=[horse.pk]))
    if stay is None:
        messages.error(request, f"{horse.name} has no stable to swap.")
        return _done(request, back)

    form = SwapStableForm(
        request.POST or None, horse=horse, stay=stay,
        initial={'day': timezone.localdate()},
    )
    if request.method == 'POST' and form.is_valid():
        other = form.cleaned_data['other']
        try:
            StableService.swap(horse, other, form.cleaned_data['day'])
        except ValidationError as e:
            form.add_error(None, e)
        except IntegrityError:
            form.add_error(None, CLASH)
        else:
            messages.success(request, f"{horse.name} and {other.name} swapped stables.")
            return _done(request, back)

    if not is_popup_request(request):
        return redirect(back)
    return render(request, 'includes/popup_form.html', {
        'form': form,
        'in_popup': True,
        'popup_submit_label': 'Swap stables',
    })


@horse_moves
@require_POST
def stable_stay_undo(request, pk):
    """The Undo on a stable change's success message."""
    _require_barns()
    stay = get_object_or_404(StableStay.objects.select_related('horse', 'stable__location'), pk=pk)
    horse, barn = stay.horse, stay.stable.location
    previous = None
    wanted = request.GET.get('previous', '')
    if wanted.isdigit():
        previous = Stable.objects.filter(pk=wanted, location=barn).first()
    try:
        back_in = StableService.undo(stay, previous_stable=previous)
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    else:
        if back_in is not None:
            messages.success(request, f"{horse.name} is back in stable {back_in.stable.label}.")
        else:
            messages.success(request, f"{horse.name} is back in {barn.name} with no stable.")
    return redirect(_back(request, reverse('location_detail', args=[barn.pk])))
